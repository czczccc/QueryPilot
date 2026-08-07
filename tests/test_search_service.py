"""SearchService 编排测试：使用假解析器与假提供方，验证部分成功与受控并发。"""

import asyncio

import pytest

from app.models import RawSearchResult, SearchRequest
from app.providers.base import ProviderError
from app.services.intent import ParseOutcome, rule_based_intent
from app.services.search import SearchService, SearchUnavailableError


class FakeParser:
    def __init__(self, intent=None, fallback=False):
        self._intent = intent or rule_based_intent("四人联机游戏")
        self._fallback = fallback

    async def parse(self, query: str) -> ParseOutcome:
        return ParseOutcome(intent=self._intent, fallback_used=self._fallback)


class FakeProvider:
    name = "fake"

    def __init__(self, results=None, error=None, delay=0.0):
        self._results = results or [RawSearchResult(title=f"r{i}", url=f"https://example.com/{i}",
                                                    provider=self.name) for i in range(3)]
        self._error = error
        self._delay = delay
        self.calls = 0
        self.max_concurrency = 0

    async def search(self, query: str, limit: int) -> list[RawSearchResult]:
        self.calls += 1
        if self._delay:
            self.max_concurrency = max(self.max_concurrency, self._inflight)
        if self._error:
            raise ProviderError(self.name, self._error)
        await asyncio.sleep(self._delay) if self._delay else None
        return self._results


def _req(query: str = "四人联机游戏") -> SearchRequest:
    return SearchRequest(query=query)


async def test_success_returns_ranked_results():
    provider = FakeProvider()
    svc = SearchService(parser=FakeParser(), providers=[provider], max_results=12)
    resp = await svc.search(_req())
    assert len(resp.results) == 3
    assert resp.request_id
    assert resp.providers[0].status == "ok"
    assert resp.metrics.raw_result_count == 9  # 3 个变体 × 每条 3 个结果
    assert resp.metrics.deduplicated_result_count == 3
    assert provider.calls == 3  # 3 个查询变体


async def test_fallback_flag_propagates():
    svc = SearchService(parser=FakeParser(fallback=True), providers=[FakeProvider()])
    resp = await svc.search(_req())
    assert resp.metrics.fallback_used is True


async def test_partial_failure_keeps_results():
    # 多个变体查询中首个失败、后续成功 → 部分成功保留结果
    class Flaky(FakeProvider):
        def __init__(self):
            super().__init__()
            self.fail_count = 1

        async def search(self, query, limit):
            if self.fail_count > 0:
                self.fail_count -= 1
                raise ProviderError(self.name, "http_429")
            return self._results

    svc = SearchService(parser=FakeParser(), providers=[Flaky()])
    resp = await svc.search(_req())
    assert resp.providers[0].status == "ok"
    assert len(resp.results) > 0


async def test_all_failures_raise_unavailable():
    provider = FakeProvider(error="http_500")
    svc = SearchService(parser=FakeParser(), providers=[provider])
    with pytest.raises(SearchUnavailableError):
        await svc.search(_req())


async def test_concurrency_capped():
    class Slow(FakeProvider):
        async def search(self, query, limit):
            self._inflight = getattr(self, "_inflight", 0) + 1
            self.max_concurrency = max(self.max_concurrency, self._inflight)
            await asyncio.sleep(0.05)
            self._inflight -= 1
            return self._results

    provider = Slow()
    svc = SearchService(parser=FakeParser(), providers=[provider], max_tasks=2)
    await svc.search(_req())
    assert provider.max_concurrency <= 2


async def test_empty_variants_never_breaks():
    provider = FakeProvider()
    svc = SearchService(parser=FakeParser(), providers=[provider])
    await svc.search(_req())
    assert provider.calls >= 1
