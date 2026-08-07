"""Tavily 适配器测试：全部使用假 HTTP 客户端，不调用真实 API。"""

import httpx
import pytest

from app.providers.base import ProviderError
from app.providers.tavily import TAVILY_URL, USER_AGENT, TavilyProvider


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _tavily_payload(results: list[dict]) -> dict:
    return {"results": results, "query": "x", "response_time": 0.1}


def _item(title="标题", url="https://example.com/a", content="摘要", score=0.8) -> dict:
    return {"title": title, "url": url, "content": content, "score": score}


async def test_success_converts_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == TAVILY_URL
        assert request.headers["Authorization"] == "Bearer sk-test"
        assert request.headers["User-Agent"] == USER_AGENT
        return httpx.Response(200, json=_tavily_payload([_item()]))

    provider = TavilyProvider(api_key="sk-test", client=_mock_client(handler))
    results = await provider.search("测试", limit=5)
    assert len(results) == 1
    r = results[0]
    assert r.title == "标题"
    assert r.url == "https://example.com/a"
    assert r.snippet == "摘要"
    assert r.provider == "tavily"
    assert r.relevance == 0.8


async def test_empty_results():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_tavily_payload([]))

    provider = TavilyProvider(api_key="sk-test", client=_mock_client(handler))
    assert await provider.search("无结果", limit=5) == []


async def test_filters_items_without_url():
    async def handler(request: httpx.Request) -> httpx.Response:
        items = [_item(url="https://example.com/ok"), _item(url="")]
        return httpx.Response(200, json=_tavily_payload(items))

    provider = TavilyProvider(api_key="sk-test", client=_mock_client(handler))
    results = await provider.search("测试", limit=5)
    assert len(results) == 1
    assert results[0].url == "https://example.com/ok"


async def test_missing_api_key():
    provider = TavilyProvider(api_key="", client=_mock_client(lambda r: httpx.Response(200)))
    with pytest.raises(ProviderError) as exc:
        await provider.search("测试", limit=5)
    assert exc.value.error_type == "missing_key"


async def test_http_error_maps_to_provider_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    provider = TavilyProvider(api_key="sk-test", client=_mock_client(handler))
    with pytest.raises(ProviderError) as exc:
        await provider.search("测试", limit=5)
    assert exc.value.error_type == "http_429"


async def test_timeout_maps_to_provider_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    provider = TavilyProvider(api_key="sk-test", client=_mock_client(handler))
    with pytest.raises(ProviderError) as exc:
        await provider.search("测试", limit=5)
    assert exc.value.error_type == "timeout"


async def test_bad_json_maps_to_provider_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    provider = TavilyProvider(api_key="sk-test", client=_mock_client(handler))
    with pytest.raises(ProviderError) as exc:
        await provider.search("测试", limit=5)
    assert exc.value.error_type == "bad_response"


async def test_invalid_score_uses_none():
    async def handler(request: httpx.Request) -> httpx.Response:
        items = [_item(score="not-a-number")]
        return httpx.Response(200, json=_tavily_payload(items))

    provider = TavilyProvider(api_key="sk-test", client=_mock_client(handler))
    results = await provider.search("测试", limit=5)
    assert results[0].relevance is None
