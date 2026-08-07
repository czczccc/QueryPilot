"""API 测试：健康检查、输入校验、成功响应与受控 503。"""

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.intent import ParseOutcome, rule_based_intent
from app.services.search import SearchService


class FakeParser:
    async def parse(self, query: str) -> ParseOutcome:
        return ParseOutcome(intent=rule_based_intent(query), fallback_used=True)


class FakeProvider:
    name = "fake"

    async def search(self, query: str, limit: int):
        from app.models import RawSearchResult

        return [RawSearchResult(title="示例结果", url="https://example.com/x",
                                snippet="摘要", provider=self.name)]


class FailingProvider:
    name = "fake"

    async def search(self, query: str, limit: int):
        from app.providers.base import ProviderError

        raise ProviderError(self.name, "http_500")


def _make_client(providers=None) -> TestClient:
    svc = SearchService(parser=FakeParser(), providers=providers or [FakeProvider()])
    app = create_app(service=svc)
    return TestClient(app)


def test_health():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_search_success_shape():
    client = _make_client()
    resp = client.post("/api/search", json={"query": "四人联机游戏"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"]
    assert body["intent"]["resource_type"] == "game"
    assert body["providers"][0]["status"] == "ok"
    assert body["metrics"]["fallback_used"] is True
    assert len(body["results"]) >= 1


def test_search_empty_query_422():
    client = _make_client()
    assert client.post("/api/search", json={"query": ""}).status_code == 422
    assert client.post("/api/search", json={"query": "短"}).status_code == 422
    assert client.post("/api/search", json={"query": "x" * 201}).status_code == 422


def test_search_missing_query_422():
    client = _make_client()
    assert client.post("/api/search", json={}).status_code == 422


def test_search_all_providers_down_503_no_stack():
    client = _make_client(providers=[FailingProvider()])
    resp = client.post("/api/search", json={"query": "四人联机游戏"})
    assert resp.status_code == 503
    body = resp.text
    assert "Traceback" not in body
    assert "ProviderError" not in body
