"""夸克网盘链接搜索编排。

链路：解析资源名 → 引擎并发（Tavily 多查询 + 深度抓取 / 夸克云搜）
→ 按分享码去重 → 并发验证可达性 → 指标汇总。
"""

import asyncio
import logging
import time
import uuid

import httpx

from app.models import (
    DoubanMeta,
    ProviderStatus,
    QuarkLink,
    QuarkSearchResponse,
    SearchMetrics,
    SearchRequest,
)
from app.providers.base import ProviderError, SearchProvider
from app.services.douban import extract_douban_id, fetch_douban_meta
from app.services.intent import IntentParser
from app.services.quark import (
    BLOCKED_DOMAINS,
    UA,
    deep_fetch_links,
    extract_links_with_pwd,
    make_entry,
    search_bing,
    search_qkyunso,
    verify_quark,
)

logger = logging.getLogger(__name__)


class SearchUnavailableError(Exception):
    """全部搜索引擎均不可用。"""


class QuarkSearchService:
    """唯一编排入口：任一引擎成功即返回链接清单，全部失败时抛受控错误。"""

    def __init__(
        self,
        parser: IntentParser,
        tavily: SearchProvider,
        use_qkyunso: bool = True,
        use_bing: bool = True,
        max_tasks: int = 8,
        timeout: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._parser = parser
        self._tavily = tavily
        self._use_qkyunso = use_qkyunso
        self._use_bing = use_bing
        self._max_tasks = max_tasks
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True
        )

    async def search(self, req: SearchRequest) -> QuarkSearchResponse:
        started = time.monotonic()
        request_id = uuid.uuid4().hex

        # 豆瓣链接识别：拿到片名/年份后作为搜索查询
        douban_meta: DoubanMeta | None = None
        query = req.query
        douban_id = extract_douban_id(req.query)
        if douban_id:
            meta = await fetch_douban_meta(douban_id, self._client, self._timeout)
            if meta:
                query = meta["title"]
                if meta.get("year"):
                    query += f" {meta['year']}"
                douban_meta = DoubanMeta(
                    subject_id=douban_id,
                    url=f"https://movie.douban.com/subject/{douban_id}/",
                    title=meta["title"],
                    year=meta.get("year"),
                    kind=meta.get("kind"),
                )
                logger.info("豆瓣链接识别: %s -> %s", douban_id, query)

        outcome = await self._parser.parse(query)
        parsed = outcome.parsed

        providers: dict[str, ProviderStatus] = {
            "tavily": ProviderStatus(name="tavily"),
        }
        if self._use_qkyunso:
            providers["qkyunso"] = ProviderStatus(name="qkyunso")
        if self._use_bing:
            providers["bing"] = ProviderStatus(name="bing")

        links: list[QuarkLink] = []

        # 引擎 1：Tavily 多查询 + 深度抓取
        try:
            t0 = time.monotonic()
            links += await self._tavily_pipeline(parsed.search_suggestions, providers["tavily"])
            providers["tavily"].duration_ms += int((time.monotonic() - t0) * 1000)
        except Exception:
            logger.exception("Tavily 流水线异常")
            providers["tavily"].error_type = "pipeline_error"

        # 引擎 2：夸克云搜（规则清洗后的资源名）
        if self._use_qkyunso:
            try:
                t0 = time.monotonic()
                qk_links = await search_qkyunso(parsed.resource, self._client, self._timeout)
                providers["qkyunso"].result_count = len(qk_links)
                links += qk_links
                providers["qkyunso"].duration_ms += int((time.monotonic() - t0) * 1000)
            except Exception:
                logger.exception("夸克云搜异常")
                providers["qkyunso"].error_type = "pipeline_error"

        # 引擎 3：Bing 中文（免费）
        if self._use_bing:
            try:
                t0 = time.monotonic()
                bing_links = await search_bing(parsed.resource, self._client, self._timeout)
                providers["bing"].result_count = len(bing_links)
                links += bing_links
                providers["bing"].duration_ms += int((time.monotonic() - t0) * 1000)
            except Exception:  # 单引擎兜底，不中断整体
                logger.exception("Bing 引擎异常")
                providers["bing"].error_type = "pipeline_error"

        for status in providers.values():
            if status.error_type:
                status.status = "error"

        if not links and all(s.status == "error" for s in providers.values()):
            raise SearchUnavailableError()

        # 按分享码去重
        uniq: dict[str, QuarkLink] = {}
        for link in links:
            uniq.setdefault(link.share, link)
        # 验证上限：防合集站单页几百条链接导致的验证风暴，超过部分不验证（不展示）
        MAX_VERIFY = 60
        final = list(uniq.values())[:MAX_VERIFY]

        # 并发验证有效性（限制 8 并发，避免打满夸克服务）
        verify_sem = asyncio.Semaphore(8)

        async def check(link: QuarkLink) -> None:
            async with verify_sem:
                link.http, link.state = await verify_quark(
                    link.share, self._client, timeout=8.0, pwd=link.pwd
                )

        await asyncio.gather(*(check(link) for link in final))

        # 排序：有效优先，未知居中，失效最后；同状态按置信度
        state_rank = {"valid": 0, "unknown": 1, "invalid": 2}
        conf_rank = {"高": 0, "中": 1, "低": 2}
        final.sort(
            key=lambda l: (
                state_rank.get(l.state, 1),
                conf_rank.get(l.conf, 1),
                l.share,
            )
        )

        metrics = SearchMetrics(
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_result_count=len(links),
            deduplicated_result_count=len(final),
            fallback_used=outcome.fallback_used,
        )
        return QuarkSearchResponse(
            request_id=request_id,
            query=req.query,
            parsed=parsed,
            links=final,
            providers=list(providers.values()),
            metrics=metrics,
            douban=douban_meta,
        )

    async def _tavily_pipeline(
        self, suggestions: list[str], status: ProviderStatus
    ) -> list[QuarkLink]:
        """对每个查询变体调 Tavily，从 URL+摘要提取链接，并对资源页深度抓取。"""
        links: list[QuarkLink] = []
        fetch_plan: list[tuple[str, str, str]] = []  # (url, title, pub)
        semaphore = asyncio.Semaphore(self._max_tasks)

        async def run(query: str) -> None:
            async with semaphore:
                t0 = time.monotonic()
                try:
                    results = await self._tavily.search(query, limit=5)
                    status.result_count += len(results)
                    for r in results:
                        text = f"{r.url} {r.snippet}"
                        for sid, pwd in extract_links_with_pwd(text):
                            links.append(make_entry(r.title, sid, r.url, "", pwd))
                        if r.url and not any(b in r.url for b in BLOCKED_DOMAINS):
                            fetch_plan.append((r.url, r.title, ""))
                except ProviderError as exc:
                    logger.warning("Tavily 查询失败: %s", exc.error_type)
                    status.error_type = exc.error_type
                status.duration_ms += int((time.monotonic() - t0) * 1000)

        await asyncio.gather(*(run(q) for q in suggestions))

        # 深度抓取（并发，最多 4 个；限制抓取数量控制耗时）
        fetch_plan = fetch_plan[:10]
        if fetch_plan:
            deep_sem = asyncio.Semaphore(4)

            async def fetch(item: tuple[str, str, str]) -> list[tuple[str, str | None]]:
                async with deep_sem:
                    return await deep_fetch_links(item[0], self._client, self._timeout)

            results = await asyncio.gather(*(fetch(item) for item in fetch_plan))
            for item, found in zip(fetch_plan, results):
                url, title, pub = item
                for sid, pwd in found:
                    links.append(make_entry(title, sid, url + " [正文]", pub, pwd))
        return links
