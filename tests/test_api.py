"""API 测试：健康检查、输入校验、网盘链接成功响应与受控 503。"""

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import RawSearchResult
from app.services.intent import ParseOutcome, rule_based_parsed
from app.services.search import QuarkSearchService


class FakeParser:
    async def parse(self, query: str) -> ParseOutcome:
        return ParseOutcome(parsed=rule_based_parsed(query), fallback_used=True)


class FakeProvider:
    name = "tavily"

    async def search(self, query: str, limit: int):
        return [RawSearchResult(
            title="示例资源页",
            url="https://pan.quark.cn/s/abc1234567",
            snippet="提取码：8888",
            provider=self.name,
        )]


class FailingProvider:
    name = "tavily"

    async def search(self, query: str, limit: int):
        from app.providers.base import ProviderError

        raise ProviderError(self.name, "http_500")


def _make_client(provider=None) -> TestClient:
    svc = QuarkSearchService(parser=FakeParser(), tavily=provider or FakeProvider(), use_qkyunso=False)
    app = create_app(service=svc)
    return TestClient(app)


def test_health():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_search_success_shape():
    client = _make_client()
    resp = client.post("/api/search", json={"query": "漫长的季节 4K"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"]
    assert body["parsed"]["resource"]
    assert body["links"][0]["share"] == "abc1234567"
    assert body["links"][0]["pwd"] == "8888"
    assert body["providers"][0]["status"] == "ok"
    assert body["metrics"]["fallback_used"] is True


def test_search_empty_query_422():
    client = _make_client()
    assert client.post("/api/search", json={"query": ""}).status_code == 422
    assert client.post("/api/search", json={"query": "短"}).status_code == 422
    assert client.post("/api/search", json={"query": "x" * 201}).status_code == 422


def test_search_missing_query_422():
    client = _make_client()
    assert client.post("/api/search", json={}).status_code == 422


def test_search_all_providers_down_503_no_stack():
    client = _make_client(provider=FailingProvider())
    resp = client.post("/api/search", json={"query": "漫长的季节"})
    assert resp.status_code == 503
    body = resp.text
    assert "Traceback" not in body
    assert "ProviderError" not in body
