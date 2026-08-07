"""搜索编排：意图解析 → 受控并发查询 → 去重排序 → 指标汇总。"""

import asyncio
import logging
import time
import uuid
from collections.abc import Iterable

from app.models import (
    ProviderStatus,
    RawSearchResult,
    SearchMetrics,
    SearchRequest,
    SearchResponse,
)
from app.providers.base import ProviderError, SearchProvider
from app.services.intent import IntentParser
from app.services.ranking import score_and_rank

logger = logging.getLogger(__name__)


class SearchUnavailableError(Exception):
    """全部搜索提供方均不可用。"""


class SearchService:
    """唯一编排入口：部分成功时返回已有结果，全部失败时抛受控错误。"""

    def __init__(
        self,
        parser: IntentParser,
        providers: Iterable[SearchProvider],
        max_results: int = 12,
        max_tasks: int = 6,
    ) -> None:
        self._parser = parser
        self._providers = list(providers)
        self._max_results = max_results
        self._max_tasks = max_tasks

    async def search(self, req: SearchRequest) -> SearchResponse:
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        outcome = await self._parser.parse(req.query)

        statuses = {p.name: ProviderStatus(name=p.name) for p in self._providers}
        fails: dict[str, int] = {}
        raw: list[RawSearchResult] = []
        semaphore = asyncio.Semaphore(self._max_tasks)

        async def run(query: str, provider: SearchProvider) -> None:
            status = statuses[provider.name]
            async with semaphore:
                t0 = time.monotonic()
                try:
                    items = await provider.search(query, limit=5)
                    status.result_count += len(items)
                    raw.extend(items)
                except ProviderError as exc:
                    logger.warning("provider=%s query=%r error=%s", provider.name, query, exc.error_type)
                    fails[provider.name] = fails.get(provider.name, 0) + 1
                    status.error_type = exc.error_type
                status.duration_ms += int((time.monotonic() - t0) * 1000)

        tasks = [run(q, p) for p in self._providers for q in outcome.intent.query_variants]
        await asyncio.gather(*tasks)

        for p in self._providers:
            status = statuses[p.name]
            if fails.get(p.name, 0) >= len(outcome.intent.query_variants):
                status.status = "error"  # 该提供方全部查询失败

        if all(s.status == "error" for s in statuses.values()):
            raise SearchUnavailableError()

        ranked = score_and_rank(raw, outcome.intent.keywords, limit=self._max_results)
        metrics = SearchMetrics(
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_result_count=len(raw),
            deduplicated_result_count=len(ranked),
            fallback_used=outcome.fallback_used,
        )
        return SearchResponse(
            request_id=request_id,
            intent=outcome.intent,
            results=ranked,
            providers=list(statuses.values()),
            metrics=metrics,
        )
