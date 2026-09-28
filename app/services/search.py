"""夸克网盘链接搜索编排。

链路：解析资源名 → 引擎并发（Tavily 多查询 + 深度抓取 / 夸克云搜 / Bing）
→ 按分享码去重 → 并发验证可达性并识别质量 → 排序 → 指标汇总。
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

import httpx

from app.models import (
    DoubanMeta,
    ParsedResource,
    ProviderStatus,
    QuarkLink,
    QuarkSearchResponse,
    SearchMetrics,
    SearchRequest,
    UserPrefs,
)
from app.providers.base import ProviderError, SearchProvider
from app.services.douban import extract_douban_id, fetch_douban_meta
from app.services.intent import IntentParser
from app.services.memory import LinkStore, resource_key
from app.services.quality import parse_quality
from app.services.quark import (
    BLOCKED_DOMAINS,
    UA,
    deep_fetch_links,
    extract_links_with_pwd,
    make_entry,
    search_bing,
    search_qkyunso,
    verify_quark_files,
)
from app.services.relevance import build_target, judge
from app.services.sources import search_pansou, search_sites, search_telegram

logger = logging.getLogger(__name__)

# 验证上限：防合集站单页几百条链接导致的验证风暴，超过部分不验证（不展示）
MAX_VERIFY = 60
# 记忆：多少小时内验证过的有效链接视为「新鲜」，直接复用不再复验
FRESH_HOURS = 6.0
# 记忆中新鲜有效链接达到该数量时，跳过全网搜索直接返回
MEMORY_FAST_MIN = 5


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
        store: LinkStore | None = None,
        tg_channels: list[str] | tuple[str, ...] = (),
        tg_client: httpx.AsyncClient | None = None,
        extra_sites: list[str] | tuple[str, ...] = (),
        pansou_url: str = "",
        pansou_token: str = "",
        pansou_timeout: float = 6.0,
        pansou_src: str = "plugin",
    ) -> None:
        self._pansou_url = pansou_url.strip()
        self._pansou_src = pansou_src if pansou_src in ("plugin", "tg", "all") else "plugin"
        self._pansou_token = pansou_token
        self._pansou_timeout = pansou_timeout
        self._parser = parser
        self._store = store
        self._tg_channels = list(tg_channels)
        self._extra_sites = list(extra_sites)
        self._tavily = tavily
        self._use_qkyunso = use_qkyunso
        self._use_bing = use_bing
        self._max_tasks = max_tasks
        self._timeout = timeout
        self._client = client or httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True
        )
        self._tg_client = tg_client or self._client

    @property
    def store(self) -> LinkStore | None:
        return self._store

    # ---------------- 可复用的搜索原语（编排与 agent 共用） ----------------

    async def prepare(self, raw_query: str) -> tuple[ParsedResource, bool, DoubanMeta | None]:
        """豆瓣链接识别 + 意图解析，返回 `(解析结果, 是否降级, 豆瓣元信息)`。"""
        douban_meta: DoubanMeta | None = None
        query = raw_query
        douban_id = extract_douban_id(raw_query)
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
        return outcome.parsed, outcome.fallback_used, douban_meta

    async def prefs_for(self, client_id: str | None) -> UserPrefs:
        if not (self._store and client_id):
            return UserPrefs()
        return await self._store.get_prefs(client_id)

    def new_providers(self) -> dict[str, ProviderStatus]:
        providers = {"tavily": ProviderStatus(name="tavily")}
        if self._use_qkyunso:
            providers["qkyunso"] = ProviderStatus(name="qkyunso")
        if self._use_bing:
            providers["bing"] = ProviderStatus(name="bing")
        if self._tg_channels:
            providers["telegram"] = ProviderStatus(name="telegram")
        if self._extra_sites:
            providers["sites"] = ProviderStatus(name="sites")
        if self._pansou_url:
            providers["pansou"] = ProviderStatus(name="pansou")
        return providers

    alerts = None  # Alerter：main 注入

    @property
    def http(self) -> httpx.AsyncClient:
        """访问夸克分享页用的 HTTP 客户端（agent 的 inspect_share 工具复用）。"""
        return self._client

    @property
    def pansou_enabled(self) -> bool:
        return bool(self._pansou_url)

    async def blocked(self, key: str) -> tuple[set[str], set[str]]:
        """用户反馈：(失效的, 不是这部的) 分享码；记忆关闭时为空。"""
        return await self._store.blocked(key) if self._store else (set(), set())

    async def recall(self, key: str) -> list[QuarkLink]:
        return await self._store.recall(key) if self._store else []

    async def known_invalid(self, links: list[QuarkLink]) -> set[str]:
        """近期已确认失效的分享码（记忆关闭时为空）。"""
        if not self._store:
            return set()
        return await self._store.known_invalid([x.share for x in links if not x.from_memory])

    async def remember(
        self, key: str, query: str, links: list[QuarkLink], from_memory: bool
    ) -> None:
        if self._store:
            await self._store.save(key, links)
            await self._store.log_search(
                key, query, sum(1 for x in links if x.state == "valid"), from_memory
            )

    async def search(self, req: SearchRequest) -> QuarkSearchResponse:
        """固定流水线：记忆 → 三引擎并发召回 → 去重 → 跳过已知失效 → 验证 → 排序。"""
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        parsed, fallback_used, douban_meta = await self.prepare(req.query)
        prefs = await self.prefs_for(req.client_id)
        target = build_target(parsed, req.query, douban_meta.year if douban_meta else None)
        providers = self.new_providers()

        # 记忆：先取该资源下记住的有效链接
        key = resource_key(parsed.resource)
        remembered = await self.recall(key)
        fresh_after = time.time() - FRESH_HOURS * 3600
        fresh = [m for m in remembered if is_fresh(m, fresh_after)]
        served_from_memory = (
            self._store is not None and not req.refresh and len(fresh) >= MEMORY_FAST_MIN
        )

        links: list[QuarkLink] = []
        if served_from_memory:
            for status in providers.values():
                status.status = "skipped"
        else:
            links = await self.collect(
                parsed.search_suggestions, parsed.resource, providers
            )
            if (
                not links
                and not remembered
                and all(s.status == "error" for s in providers.values())
            ):
                raise SearchUnavailableError()

        # 记忆在前：去重时保留记忆版本（带历史质量与验证时间）
        candidates = dedupe([*remembered, *links])
        skipped = await self.known_invalid(candidates)
        final = [c for c in candidates if c.share not in skipped][:MAX_VERIFY]

        # 新鲜的记忆链接不再复验，其余并发验证
        await self.verify([link for link in final if not is_fresh(link, fresh_after)])
        for link in final:
            judge(link, target)
        sort_links(final, prefs)
        await self.remember(key, req.query, final, served_from_memory)

        metrics = SearchMetrics(
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_result_count=len(links),
            deduplicated_result_count=len(final),
            fallback_used=fallback_used,
            memory_hits=sum(1 for f in final if f.from_memory),
            skipped_invalid=len(skipped),
            served_from_memory=served_from_memory,
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

    async def collect(
        self,
        queries: list[str],
        keyword: str,
        providers: dict[str, ProviderStatus],
        keyword_engines: bool = True,
        pansou: bool = True,
        tavily: bool = True,
    ) -> list[QuarkLink]:
        """各引擎并发召回：Tavily 用 `queries`，云搜/Bing/Telegram/资源站/PanSou 用 `keyword`。

        `keyword_engines=False` 时不跑云搜/Bing/Telegram/资源站（关键词和上一轮相同，结果不会变）。
        `pansou`：同时查 PanSou（配了才查；关键词用 PanSou 搜过时 agent 传 False 避免重复）。
        `tavily=False`：不跑 Tavily（只查 PanSou 时用）。
        单引擎失败只标记状态，不中断整体。
        """

        async def run_tavily() -> list[QuarkLink]:
            return await self._tavily_pipeline(queries, providers["tavily"])

        async def run_qkyunso() -> list[QuarkLink]:
            return await search_qkyunso(keyword, self._client, self._timeout)

        async def run_bing() -> list[QuarkLink]:
            return await search_bing(keyword, self._client, self._timeout)

        async def run_telegram() -> list[QuarkLink]:
            return await search_telegram(keyword, self._tg_channels, self._tg_client)

        async def run_sites() -> list[QuarkLink]:
            return await search_sites(keyword, self._extra_sites, self._client)

        async def run_pansou() -> list[QuarkLink]:
            return await search_pansou(
                keyword, self._pansou_url, self._client, self._pansou_timeout,
                self._pansou_token, src=self._pansou_src,
            )

        engines: list[tuple[str, Callable[[], Awaitable[list[QuarkLink]]]]] = (
            [("tavily", run_tavily)] if tavily else []
        )
        for name, fn in (
            ("qkyunso", run_qkyunso),
            ("bing", run_bing),
            ("telegram", run_telegram),
            ("sites", run_sites),
        ):
            if keyword_engines and name in providers:
                engines.append((name, fn))
        if pansou and "pansou" in providers:  # 放最后：同一分享码优先保留自有来源的标注
            engines.append(("pansou", run_pansou))

        async def guarded(
            name: str, fn: Callable[[], Awaitable[list[QuarkLink]]]
        ) -> list[QuarkLink]:
            t0 = time.monotonic()
            try:
                found = await fn()
                if name != "tavily":  # tavily 在流水线内自行累计 result_count
                    providers[name].result_count += len(found)
                return found
            except Exception:
                logger.exception("%s 引擎异常", name)
                providers[name].error_type = "pipeline_error"
                return []
            finally:
                providers[name].duration_ms += int((time.monotonic() - t0) * 1000)

        per_engine = await asyncio.gather(*(guarded(n, fn) for n, fn in engines))
        if self.alerts is not None:  # 某个源连续报错时告警站长
            for n, _ in engines:
                await self.alerts.source_result(n, not providers[n].error_type)
        for status in providers.values():
            if status.error_type:
                status.status = "error"
        # 按固定引擎顺序拼接，保证去重结果确定
        return [link for found in per_engine for link in found]

    async def verify(self, links: list[QuarkLink]) -> None:
        """并发验证有效性并识别质量（限制 8 并发，避免打满夸克服务）。"""
        sem = asyncio.Semaphore(8)

        async def check(link: QuarkLink) -> None:
            async with sem:
                link.http, link.state, files, title = await verify_quark_files(
                    link.share, self._client, timeout=8.0, pwd=link.pwd
                )
                link.last_checked = time.time()
                if link.state == "valid":
                    link.quality = parse_quality(files, title or link.name)
                    link.share_title = title
                    link.files_preview = [
                        str(f.get("file_name")) for f in files[:5] if f.get("file_name")
                    ]

        await asyncio.gather(*(check(link) for link in links))

    async def reverify_stale(self, older_than_hours: float = 24, limit: int = 50) -> int:
        """后台复验记忆中最久未验证的有效链接，返回本轮失效数。"""
        if not self._store:
            return 0
        stale = await self._store.stale_valid(older_than_hours, limit)
        await self.verify(stale)
        died = 0
        for link in stale:
            if link.state == "unknown":
                continue  # 网络问题不改记忆
            died += link.state == "invalid"
            await self._store.update_state(link)
        return died

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


def is_fresh(link: QuarkLink, fresh_after: float) -> bool:
    """记忆链接在新鲜期内验证过，可直接复用不复验。"""
    return link.from_memory and (link.last_checked or 0) >= fresh_after


def dedupe(links: list[QuarkLink]) -> list[QuarkLink]:
    """按分享码去重，保留先出现的版本。"""
    uniq: dict[str, QuarkLink] = {}
    for link in links:
        uniq.setdefault(link.share, link)
    return list(uniq.values())


def preference_score(link: QuarkLink, prefs: UserPrefs | None) -> int:
    """质量分 + 偏好加分（偏好字幕 / HDR 且链接满足时各 +8）。"""
    q = link.quality
    if q is None:
        return 0
    score = q.score
    if prefs and prefs.prefer_subtitle and q.has_subtitle:
        score += 8
    if prefs and prefs.prefer_hdr and q.hdr:
        score += 8
    return score


def sort_links(links: list[QuarkLink], prefs: UserPrefs | None = None) -> None:
    """有效优先，未知居中，失效最后；同状态内：相关 > 待定 > 不相关，
    再按质量分（含偏好加分）、被复制次数、置信度。"""
    state_rank = {"valid": 0, "unknown": 1, "invalid": 2}
    relevance_rank = {"match": 0, "uncertain": 1, "mismatch": 2}
    conf_rank = {"高": 0, "中": 1, "低": 2}
    links.sort(
        key=lambda link: (
            state_rank.get(link.state, 1),
            relevance_rank.get(link.relevance, 1),
            -preference_score(link, prefs),
            -link.copy_count,
            conf_rank.get(link.conf, 1),
            link.share,
        )
    )
