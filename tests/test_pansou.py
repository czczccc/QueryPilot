"""PanSou 搜索源：只取夸克、提取码两处来源、错误隔离、未配置不启用。"""

import json

import httpx
import pytest

from app.models import SearchRequest
from app.services.agent import SearchAgent, tools_for
from app.services.search import dedupe
from app.services.sources import search_pansou
from tests.test_agent import ScriptedTavily, deepseek_client, make_service, shares

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
    links = await service.collect(["q"], "凡人修仙传", providers, pansou=True)
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
    links = await service.collect(["q"], "凡人修仙传", providers, pansou=True)
    assert providers["pansou"].result_count == 2 and providers["pansou"].status == "ok"
    assert {lk.share for lk in links} >= {"abc123def456", "fff000111222"}


# ---------------- agent 工具 ----------------

PS_SHARES = shares("ps", 4)


def pansou_and_quark(seen: list):
    """PanSou 返回 4 个夸克分享；其余请求当夸克验证接口（都有效）。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pansou":
            seen.append(json.loads(request.content)["kw"])
            return httpx.Response(200, json={"merged_by_type": {"quark": [
                {"url": f"https://pan.quark.cn/s/{s}", "note": "流浪地球2", "source": "plugin:x"}
                for s in PS_SHARES]}})
        if request.url.path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        if request.url.path.endswith("/sharepage/detail"):
            return httpx.Response(200, json={"code": 0, "data": {
                "share": {"status": 1}, "list": [{"file_name": "流浪地球2.1080p.mkv"}]}})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def pansou_service(seen: list):
    service = make_service(ScriptedTavily({}, default=shares("ok", 2)),
                           client=pansou_and_quark(seen))
    service._pansou_url = "http://pansou:8888"
    return service


async def test_search_always_includes_pansou():
    seen: list = []
    resp = await SearchAgent(pansou_service(seen)).run(SearchRequest(query="流浪地球2"))
    tools = [s.tool for s in resp.steps]
    assert resp.planner == "rules"
    assert tools[:3] == ["recall_memory", "search", "verify"]
    assert seen == ["流浪地球2"]  # search 顺带查了 PanSou
    # PanSou 的结果和其他来源一样经过验证与相关性判定
    got = {lk.share: lk for lk in resp.links}
    assert all(got[s].state == "valid" and got[s].relevance == "match" for s in PS_SHARES)
    assert got[PS_SHARES[0]].source == "PanSou·plugin:x"
    assert resp.matching_count == 6


async def test_llm_sees_pansou_tool_only_when_configured():
    assert "pansou_search" not in [t["function"]["name"] for t in tools_for(False)]
    seen_llm: list[dict] = []
    llm = deepseek_client([
        [("search", {"queries": ["流浪地球2 夸克"], "keyword": "流浪地球2"})],
        [("pansou_search", {"keyword": "The Wandering Earth 2"})],
        [("pansou_search", {"keyword": "The Wandering Earth 2"})],
        [("verify", {"limit": 30}), ("finish", {"reason": "够了"})],
    ], seen_llm)
    seen: list = []
    resp = await SearchAgent(pansou_service(seen), api_key="k", client=llm).run(
        SearchRequest(query="流浪地球2"))
    names = [t["function"]["name"] for t in seen_llm[0]["tools"]]
    assert names.index("pansou_search") == names.index("search") + 1
    assert "pansou_search" in seen_llm[0]["messages"][0]["content"]
    # AI 模式下 search 也自动带上 PanSou；pansou_search 换别名深挖；同一关键词第二次被拒绝
    assert seen == ["流浪地球2", "The Wandering Earth 2"]
    assert resp.steps[2].observation == {"error": "这个关键词已经用 PanSou 搜过了"}
    assert resp.matching_count == 6

    # 没配 PanSou：LLM 硬调也只得到「未知工具」
    llm = deepseek_client([[("pansou_search", {"keyword": "x"})],
                           [("finish", {"reason": "无"})]], seen_llm)
    service = make_service(ScriptedTavily({}, default=[]), client=pansou_and_quark(seen))
    resp = await SearchAgent(service, api_key="k", client=llm).run(
        SearchRequest(query="流浪地球2"))
    assert resp.steps[0].observation == {"error": "未知工具 pansou_search"}
    assert "pansou_search" not in [t["function"]["name"] for t in seen_llm[-2]["tools"]]


def test_clean_keyword():
    from app.services.relevance import clean_keyword

    assert clean_keyword("无职转生 第二季 1080P") == "无职转生"
    assert clean_keyword("无职转生～到了异世界就拿出真本事～ 第2季") == "无职转生"
    assert clean_keyword("庆余年 第二季 全36集 夸克网盘") == "庆余年"
    assert clean_keyword("The Wandering Earth 2 2160p") == "The Wandering Earth 2"
    assert clean_keyword("Mission: Impossible") == "Mission: Impossible"
    assert clean_keyword("流浪地球2") == "流浪地球2"
    assert clean_keyword("4K") == "4K"  # 全被清掉时退回原词


async def test_keywords_cleaned_before_pansou():
    seen_llm: list[dict] = []
    llm = deepseek_client([
        [("search", {"queries": ["无职转生 第二季 1080P"], "keyword": "无职转生 第二季 1080P"})],
        [("pansou_search", {"keyword": "无职转生 第三季"})],
        [("finish", {"reason": "够了"})],
    ], seen_llm)
    seen: list = []
    resp = await SearchAgent(pansou_service(seen), api_key="k", client=llm).run(
        SearchRequest(query="流浪地球2"))
    assert seen == ["无职转生"]  # 第二季、第三季清洗后是同一个词，只查一次
    assert resp.steps[0].args["keyword"] == "无职转生 第二季 1080P"  # 原样记录 LLM 给的
    assert resp.steps[1].observation == {"error": "这个关键词已经用 PanSou 搜过了"}
