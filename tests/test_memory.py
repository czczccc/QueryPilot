"""记忆库（SQLite 链接库）与搜索编排中的记忆逻辑测试。"""

import socket
import time

import httpx
import pytest

from app.models import QualityInfo, QuarkLink, RawSearchResult, SearchRequest
from app.services.intent import ParseOutcome, rule_based_parsed
from app.services.memory import LinkStore, resource_key
from app.services.search import MEMORY_FAST_MIN, QuarkSearchService


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """固定 DNS 解析，测试不依赖真实网络。"""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


def _link(share: str, state: str = "valid", score: int = 30) -> QuarkLink:
    return QuarkLink(
        name="流浪地球2", share=share, source="bing", time="未知", state=state,
        http=200, quality=QualityInfo(resolution="1080p", score=score) if state == "valid" else None,
    )


def test_resource_key_normalizes():
    assert resource_key("流浪地球 2") == resource_key("流浪地球2") == "流浪地球2"
    assert resource_key("Dune: Part Two") == "duneparttwo"
    assert resource_key("《漫长的季节》") == "漫长的季节"


async def test_save_and_recall_valid_only():
    store = LinkStore(":memory:")
    await store.save("k", [_link("a1"), _link("a2", "invalid"), _link("a3", "unknown")])
    recalled = await store.recall("k")
    assert [r.share for r in recalled] == ["a1"]
    assert recalled[0].from_memory is True
    assert recalled[0].quality.resolution == "1080p"
    assert await store.recall("other") == []
    stats = await store.stats()
    assert stats["valid"] == 1 and stats["invalid"] == 1 and "unknown" not in stats


async def test_known_invalid_and_state_transition():
    store = LinkStore(":memory:")
    await store.save("k", [_link("a1"), _link("a2", "invalid")])
    assert await store.known_invalid(["a1", "a2", "zz"]) == {"a2"}
    # 之前有效的链接失效后不再被召回
    await store.save("k", [_link("a1", "invalid")])
    assert await store.recall("k") == []
    assert await store.known_invalid(["a1"]) == {"a1"}


async def test_link_shared_across_resources():
    store = LinkStore(":memory:")
    await store.save("流浪地球2", [_link("a1")])
    await store.save("thewanderingearth2", [_link("a1")])
    assert [r.share for r in await store.recall("thewanderingearth2")] == ["a1"]


async def test_stale_valid_ordering():
    store = LinkStore(":memory:")
    await store.save("k", [_link("old")])
    await store.save("k", [_link("new")])
    time.sleep(0.01)
    assert await store.stale_valid(older_than_hours=0, limit=10) != []
    assert await store.stale_valid(older_than_hours=1, limit=10) == []


# ---------------- 编排 ----------------

class FakeParser:
    async def parse(self, query: str) -> ParseOutcome:
        return ParseOutcome(parsed=rule_based_parsed("流浪地球2"), fallback_used=False)


class CountingTavily:
    name = "tavily"

    def __init__(self, urls: list[str]):
        self.urls = urls
        self.calls = 0

    async def search(self, query: str, limit: int) -> list[RawSearchResult]:
        self.calls += 1
        return [
            RawSearchResult(title="资源页", url=u, snippet="", provider="tavily") for u in self.urls
        ]


def _verify_client(invalid: set[str] = frozenset(), counter: list | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/token"):
            sid = request.read().decode()
            if counter is not None:
                counter.append(sid)
            if any(bad in sid for bad in invalid):
                return httpx.Response(404, json={"code": 41006})
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        if request.url.path.endswith("/sharepage/detail"):
            return httpx.Response(200, json={
                "code": 0,
                "data": {"share": {"status": 1}, "list": [{"file_name": "a.1080p.mkv"}]},
            })
        return httpx.Response(200, text="")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _svc(tavily, client, store):
    return QuarkSearchService(parser=FakeParser(), tavily=tavily, use_qkyunso=False,
                              use_bing=False, client=client, store=store)


def _urls(n: int, prefix: str = "s") -> list[str]:
    return [f"https://pan.quark.cn/s/{prefix}{i:09d}" for i in range(n)]


async def test_second_search_served_from_memory():
    store = LinkStore(":memory:")
    tavily = CountingTavily(_urls(MEMORY_FAST_MIN))
    svc = _svc(tavily, _verify_client(), store)

    first = await svc.search(SearchRequest(query="流浪地球2"))
    assert first.metrics.served_from_memory is False
    assert sum(link.state == "valid" for link in first.links) == MEMORY_FAST_MIN
    calls_after_first = tavily.calls

    second = await svc.search(SearchRequest(query="流浪地球 2"))  # 写法不同，归一化后命中
    assert second.metrics.served_from_memory is True
    assert tavily.calls == calls_after_first  # 没有再全网搜索
    assert second.metrics.memory_hits == MEMORY_FAST_MIN
    assert all(link.from_memory for link in second.links)
    assert {p.status for p in second.providers} == {"skipped"}

    third = await svc.search(SearchRequest(query="流浪地球2", refresh=True))
    assert third.metrics.served_from_memory is False
    assert tavily.calls > calls_after_first


async def test_known_invalid_links_skip_verification():
    store = LinkStore(":memory:")
    tavily = CountingTavily(_urls(2, "d"))
    verified: list[str] = []
    svc = _svc(tavily, _verify_client(invalid={"d000000000"}, counter=verified), store)

    await svc.search(SearchRequest(query="流浪地球2"))
    verified.clear()
    resp = await svc.search(SearchRequest(query="流浪地球2"))
    assert resp.metrics.skipped_invalid == 1
    assert all(link.share != "d000000000" for link in resp.links)
    # 有效链接来自新鲜记忆，也不需要复验
    assert verified == []


async def test_memory_links_kept_when_engines_fail():
    store = LinkStore(":memory:")
    await store.save(resource_key("流浪地球2"), [_link("m000000001")])

    class Broken:
        name = "tavily"

        async def search(self, query, limit):
            raise RuntimeError("boom")

    resp = await _svc(Broken(), _verify_client(), store).search(SearchRequest(query="流浪地球2"))
    assert [link.share for link in resp.links] == ["m000000001"]


async def test_reverify_stale_marks_dead_links():
    store = LinkStore(":memory:")
    await store.save("k", [_link("alive00001"), _link("dead000001")])
    svc = _svc(CountingTavily([]), _verify_client(invalid={"dead000001"}), store)
    time.sleep(0.01)
    died = await svc.reverify_stale(older_than_hours=0, limit=10)
    assert died == 1
    assert [r.share for r in await store.recall("k")] == ["alive00001"]
