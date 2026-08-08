"""QuarkSearchService 编排测试：假解析器 + 假 Tavily + MockTransport 深度抓取/验证。"""

import socket

import httpx
import pytest

from app.models import RawSearchResult, SearchRequest
from app.providers.base import ProviderError
from app.services.intent import ParseOutcome, rule_based_parsed
from app.services.search import QuarkSearchService, SearchUnavailableError


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """固定 DNS 解析为公网 IP：避免本机代理 fake-ip 导致 SSRF 校验误拦截。"""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


class FakeParser:
    def __init__(self, parsed=None, fallback=False):
        self._parsed = parsed or rule_based_parsed("漫长的季节")
        self._fallback = fallback
        self.last_query = None

    async def parse(self, query: str) -> ParseOutcome:
        self.last_query = query
        return ParseOutcome(parsed=self._parsed, fallback_used=self._fallback)


class FakeTavily:
    name = "tavily"

    def __init__(self, results=None, error=None):
        self._results = results or []
        self._error = error

    async def search(self, query: str, limit: int) -> list[RawSearchResult]:
        if self._error:
            raise ProviderError(self.name, self._error)
        return self._results


def _quark_result(url: str) -> RawSearchResult:
    return RawSearchResult(
        title="资源页", url=url, snippet="提取码：1234", provider="tavily"
    )


def _ok_client() -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        if request.url.path.endswith("/sharepage/detail"):
            return httpx.Response(200, json={
                "code": 0,
                "data": {"share": {"status": 1}, "list": [{"file_name": "电影.mkv"}]},
            })
        return httpx.Response(200, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _req(query: str = "漫长的季节 4K") -> SearchRequest:
    return SearchRequest(query=query)


async def test_tavily_url_extracts_quark_links():
    provider = FakeTavily(results=[_quark_result("https://pan.quark.cn/s/abc1234567")])
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider, use_qkyunso=False, use_bing=False,
                             client=_ok_client())
    resp = await svc.search(_req())
    assert len(resp.links) == 1
    assert resp.links[0].share == "abc1234567"
    assert resp.links[0].pwd == "1234"
    assert resp.links[0].source == "https://pan.quark.cn/s/abc1234567"


async def test_dedupe_by_share_id():
    provider = FakeTavily(results=[
        _quark_result("https://pan.quark.cn/s/abc1234567"),
        _quark_result("https://example.com/other 说 https://pan.quark.cn/s/abc1234567"),
    ])
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider, use_qkyunso=False, use_bing=False,
                             client=_ok_client())
    resp = await svc.search(_req())
    shares = [l.share for l in resp.links]
    assert shares == ["abc1234567"]


async def test_deep_fetch_adds_links():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="正文里提到 https://pan.quark.cn/s/xyz9876543 提取码 abcd")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = FakeTavily(results=[_quark_result("https://example.com/page")])
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider, use_qkyunso=False, use_bing=False, client=client)
    resp = await svc.search(_req())
    shares = {l.share for l in resp.links}
    assert "xyz9876543" in shares


async def test_verify_marks_http_status():
    client = _ok_client()
    provider = FakeTavily(results=[_quark_result("https://pan.quark.cn/s/abc1234567")])
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider, use_qkyunso=False, use_bing=False, client=client)
    resp = await svc.search(_req())
    assert resp.links[0].http == 200


async def test_all_engines_fail_raises_unavailable():
    provider = FakeTavily(error="http_500")
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider, use_qkyunso=False, use_bing=False)
    with pytest.raises(SearchUnavailableError):
        await svc.search(_req())


async def test_metrics_shape():
    provider = FakeTavily(results=[_quark_result("https://pan.quark.cn/s/abc1234567")])
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider, use_qkyunso=False, use_bing=False,
                             client=_ok_client())
    resp = await svc.search(_req())
    assert resp.request_id
    # 规则降级生成 4 个查询变体，每个返回同一链接
    assert resp.metrics.raw_result_count == 4
    assert resp.metrics.deduplicated_result_count == 1
    assert resp.providers[0].status == "ok"


async def test_fallback_flag_propagates():
    provider = FakeTavily(results=[_quark_result("https://pan.quark.cn/s/abc1234567")])
    svc = QuarkSearchService(parser=FakeParser(fallback=True), tavily=provider, use_qkyunso=False, use_bing=False,
                             client=_ok_client())
    resp = await svc.search(_req())
    assert resp.metrics.fallback_used is True


async def test_verify_capped_at_max():
    """链接过多时只验证前 60 条，避免合集站页面造成的验证风暴。"""
    provider = FakeTavily(results=[
        _quark_result(f"https://pan.quark.cn/s/sid{i:010d}") for i in range(70)
    ])
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider, use_qkyunso=False,
                             use_bing=False, client=_ok_client())
    resp = await svc.search(_req())
    assert len(resp.links) <= 60
    assert resp.metrics.raw_result_count == 280  # 4 个变体 × 70 条


async def test_douban_link_resolves_to_title():
    """输入豆瓣链接时，先用移动版页面识别片名，再交给解析器搜索。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "m.douban.com":
            return httpx.Response(
                200,
                text='<meta property="og:title" content="漫长的季节 (2023) - 电视剧" />',
            )
        if request.url.path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        if request.url.path.endswith("/sharepage/detail"):
            return httpx.Response(200, json={
                "code": 0,
                "data": {"share": {"status": 1}, "list": [{"file_name": "电影.mkv"}]},
            })
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    parser = FakeParser()
    provider = FakeTavily(results=[_quark_result("https://pan.quark.cn/s/abc1234567")])
    svc = QuarkSearchService(parser=parser, tavily=provider, use_qkyunso=False,
                             use_bing=False, client=client)
    resp = await svc.search(SearchRequest(query="https://movie.douban.com/subject/35320175/"))
    # 解析器收到的是识别出的片名而非原始 URL
    assert parser.last_query == "漫长的季节 2023"
    assert resp.douban is not None
    assert resp.douban.title == "漫长的季节"
    assert resp.douban.year == "2023"
    assert resp.douban.kind == "电视剧"
