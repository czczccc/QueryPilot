"""PanSou 搜索源：只取夸克、提取码两处来源、错误隔离、未配置不启用。"""

import json

import httpx
import pytest

from app.services.search import dedupe
from app.services.sources import search_pansou
from tests.test_agent import ScriptedTavily, make_service, shares

RESP = {"total": 4, "merged_by_type": {
    "quark": [
        {"url": "https://pan.quark.cn/s/abc123def456", "password": "x1y2", "note": "凡人修仙传 4K",
         "datetime": "2026-09-20T10:00:00Z", "source": "plugin:quark4k"},
        {"url": "https://pan.quark.cn/s/fff000111222?pwd=ab12", "password": "", "note": "凡人修仙传",
         "datetime": "", "source": "tg:quarkshare"},
        {"url": "https://pan.quark.cn/s/abc123def456", "note": "重复"},
    ],
    "baidu": [{"url": "https://pan.baidu.com/s/1xyz", "note": "百度"}],
    "magnet": [{"url": "magnet:?xt=urn:btih:abc", "note": "磁力"}],
}}


def pansou_client(seen: list, status: int = 200, body=RESP):
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_only_quark_links_with_passwords():
    seen: list = []
    links = await search_pansou("凡人修仙传", "http://pansou:8888/", pansou_client(seen),
                                token="jwt")
    assert [(lk.share, lk.pwd, lk.source) for lk in links] == [
        ("abc123def456", "x1y2", "PanSou·plugin:quark4k"),
        ("fff000111222", "ab12", "PanSou·tg:quarkshare"),
    ]
    assert links[0].name == "凡人修仙传 4K"
    req = seen[0]
    assert str(req.url) == "http://pansou:8888/api/search"
    assert json.loads(req.content) == {
        "kw": "凡人修仙传", "cloud_types": ["quark"], "res": "merge", "src": "plugin"}
    assert req.headers["authorization"] == "Bearer jwt"


async def test_wrapped_data_and_limit():
    body = {"code": 0, "data": RESP}
    links = await search_pansou("x", "http://p", pansou_client([], body=body), limit=1)
    assert len(links) == 1


async def test_http_error_raises():
    with pytest.raises(httpx.HTTPStatusError):
        await search_pansou("x", "http://p", pansou_client([], status=500))


async def test_collect_marks_pansou_error_without_breaking_others():
    service = make_service(ScriptedTavily({}, default=shares("ok", 3)))
    service._pansou_url = "http://pansou:8888"
    real_client = service._client

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pansou":
            return httpx.Response(502)
        return httpx.Response(404)

    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    providers = service.new_providers()
    assert "pansou" in providers
    links = await service.collect(["q"], "凡人修仙传", providers)
    assert providers["pansou"].status == "error"
    assert len(dedupe(links)) == 3  # Tavily 的结果不受影响
    service._client = real_client


async def test_pansou_off_by_default_and_last_in_order():
    service = make_service(ScriptedTavily({}, default=[]))
    assert "pansou" not in service.new_providers()

    service._pansou_url = "http://pansou:8888"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pansou":
            return httpx.Response(200, json=RESP)
        return httpx.Response(404)

    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    providers = service.new_providers()
    assert list(providers)[-1] == "pansou"
    links = await service.collect(["q"], "凡人修仙传", providers)
    assert providers["pansou"].result_count == 2 and providers["pansou"].status == "ok"
    assert {lk.share for lk in links} >= {"abc123def456", "fff000111222"}
