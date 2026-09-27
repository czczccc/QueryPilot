"""agent 循环测试：规则规划、LLM 规划（假 DeepSeek）、LLM 失败降级、记忆、API/SSE。"""

import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import ParsedResource, QualityInfo, QuarkLink, RawSearchResult, SearchRequest
from app.services.agent import TARGET_MATCHES, SearchAgent
from app.services.intent import ParseOutcome
from app.services.memory import LinkStore, resource_key
from app.services.search import QuarkSearchService


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


PARSED = ParsedResource(
    resource="流浪地球2",
    aliases=["流浪地球 2"],
    english_name="The Wandering Earth 2",
    search_suggestions=["流浪地球2 夸克网盘", "流浪地球2 4K"],
)


class FakeParser:
    def __init__(self, parsed: ParsedResource = PARSED):
        self.parsed = parsed

    async def parse(self, query: str) -> ParseOutcome:
        return ParseOutcome(parsed=self.parsed, fallback_used=False)


class ScriptedTavily:
    """按查询词返回不同的分享链接：`plan` 把查询词映射到分享码列表。"""

    name = "tavily"

    def __init__(self, plan: dict[str, list[str]], default: list[str] | None = None):
        self.plan = plan
        self.default = default or []
        self.queries: list[str] = []

    async def search(self, query: str, limit: int) -> list[RawSearchResult]:
        self.queries.append(query)
        shares = self.plan.get(query, self.default)
        return [
            RawSearchResult(title="流浪地球2", url=f"https://pan.quark.cn/s/{s}",
                            snippet="", provider="tavily")
            for s in shares
        ]


def quark_client(files: dict[str, str] | None = None, default: str = "流浪地球2.1080p.mkv"):
    """夸克验证假接口：`files` 指定某分享码的文件名；以 dead 开头的分享码判定失效。"""
    files = files or {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/token"):
            sid = json.loads(request.read())["pwd_id"]
            if sid.startswith("dead"):
                return httpx.Response(404, json={"code": 41006})
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        if request.url.path.endswith("/sharepage/detail"):
            name = files.get(request.url.params["pwd_id"], default)
            return httpx.Response(200, json={
                "code": 0, "data": {"share": {"status": 1}, "list": [{"file_name": name}]},
            })
        return httpx.Response(200, text="")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def shares(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i:08d}" for i in range(n)]


def make_service(tavily, client=None, store=None, parsed=PARSED):
    return QuarkSearchService(parser=FakeParser(parsed), tavily=tavily, use_qkyunso=False,
                              use_bing=False, client=client or quark_client(), store=store)


# ---------------- 规则规划 ----------------

async def test_rules_stop_once_target_met():
    tavily = ScriptedTavily({}, default=shares("ok", 6))
    agent = SearchAgent(make_service(tavily))
    resp = await agent.run(SearchRequest(query="流浪地球2"))
    assert [s.tool for s in resp.steps] == ["recall_memory", "search", "verify"]
    assert resp.planner == "rules"
    assert resp.matching_count == 6
    assert resp.stop_reason == "已找到足够满足要求的有效链接"
    assert all(link.state == "valid" for link in resp.links)


async def test_rules_alternate_search_when_not_enough():
    primary = shares("aa", 2)
    alt = shares("bb", 4)
    tavily = ScriptedTavily({q: primary for q in PARSED.search_suggestions}, default=alt)
    agent = SearchAgent(make_service(tavily))
    resp = await agent.run(SearchRequest(query="流浪地球2"))
    tools = [s.tool for s in resp.steps]
    assert tools == ["recall_memory", "search", "verify", "search", "verify"]
    second = resp.steps[3].args
    assert any("The Wandering Earth 2" in q for q in second["queries"])
    assert not set(second["queries"]) & set(PARSED.search_suggestions)
    assert resp.matching_count == 6


async def test_required_resolution_filters_matches():
    parsed = PARSED.model_copy(update={"quality": "4K"})
    uhd = shares("uhd", 2)
    files = {s: "流浪地球2.2160p.mkv" for s in uhd}
    tavily = ScriptedTavily({}, default=[*shares("fhd", 5), *uhd])
    agent = SearchAgent(make_service(tavily, client=quark_client(files), parsed=parsed))
    resp = await agent.run(SearchRequest(query="流浪地球2 4K"))
    assert resp.required_resolution == "2160p"
    assert resp.matching_count == 2
    # 满足 4K 的排在前面
    assert {link.share for link in resp.links[:2]} == set(uhd)
    # 不满足目标 → 用完补搜后结束
    assert resp.stop_reason == "可用的搜索手段已用完"


async def test_dead_links_not_counted():
    tavily = ScriptedTavily({}, default=[*shares("dead", 3), *shares("ok", 5)])
    resp = await SearchAgent(make_service(tavily)).run(SearchRequest(query="流浪地球2"))
    assert resp.matching_count == 5
    assert sum(link.state == "invalid" for link in resp.links) == 3


# ---------------- 记忆 ----------------

async def test_memory_satisfies_without_search():
    store = LinkStore(":memory:")
    remembered = [
        QuarkLink(name="流浪地球2", share=s, source="x", time="未知", state="valid",
                  share_title="流浪地球2 1080p",
                  quality=QualityInfo(resolution="1080p", score=30))
        for s in shares("mem", TARGET_MATCHES)
    ]
    await store.save(resource_key("流浪地球2"), remembered)
    tavily = ScriptedTavily({}, default=shares("ok", 6))
    resp = await SearchAgent(make_service(tavily, store=store)).run(
        SearchRequest(query="流浪地球2"))
    assert [s.tool for s in resp.steps] == ["recall_memory"]
    assert resp.metrics.served_from_memory is True
    assert tavily.queries == []

    refreshed = await SearchAgent(make_service(tavily, store=store)).run(
        SearchRequest(query="流浪地球2", refresh=True))
    assert "search" in [s.tool for s in refreshed.steps]
    assert tavily.queries


# ---------------- LLM 规划 ----------------

def deepseek_client(script: list[list[tuple[str, dict]]], seen: list[dict]):
    """假 DeepSeek：每次请求按脚本返回一组工具调用，并记录收到的 messages。"""
    calls = iter(script)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        seen.append(body)
        batch = next(calls)
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant",
            "content": "先查记忆再搜",
            "tool_calls": [
                {"id": f"call_{len(seen)}_{i}", "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
                for i, (name, args) in enumerate(batch)
            ],
        }}]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_llm_planner_drives_tools():
    seen: list[dict] = []
    llm = deepseek_client([
        [("recall_memory", {})],
        [("search", {"queries": ["流浪地球2 4K 夸克"], "keyword": "流浪地球2"})],
        [("verify", {"limit": 10}), ("finish", {"reason": "够了"})],
    ], seen)
    tavily = ScriptedTavily({}, default=shares("ok", 3))
    agent = SearchAgent(make_service(tavily), api_key="k", client=llm)
    resp = await agent.run(SearchRequest(query="流浪地球2"))

    assert resp.planner == "llm"
    assert [s.tool for s in resp.steps] == ["recall_memory", "search", "verify", "finish"]
    assert tavily.queries == ["流浪地球2 4K 夸克"]
    assert resp.stop_reason == "够了"
    assert resp.steps[0].thought == "先查记忆再搜"
    # 第三次请求带上了前两步的工具结果，且 tool_call_id 对应
    tool_msgs = [m for m in seen[2]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_1_0", "call_2_0"]
    assert "state" in json.loads(tool_msgs[-1]["content"])
    assert seen[0]["tools"] and seen[0]["tool_choice"] == "required"


async def test_llm_failure_falls_back_to_rules():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    llm = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tavily = ScriptedTavily({}, default=shares("ok", 6))
    resp = await SearchAgent(make_service(tavily), api_key="k", client=llm).run(
        SearchRequest(query="流浪地球2"))
    assert resp.planner == "rules"
    assert resp.matching_count == 6


async def test_step_budget_enforced():
    seen: list[dict] = []
    # LLM 一直搜索、从不结束
    llm = deepseek_client([[("search", {"queries": [f"q{i}"]})] for i in range(20)], seen)
    tavily = ScriptedTavily({}, default=[])
    resp = await SearchAgent(make_service(tavily), api_key="k", client=llm).run(
        SearchRequest(query="流浪地球2"))
    assert len(resp.steps) == 8
    assert resp.stop_reason == "达到步数上限"


# ---------------- API ----------------

def _app_client():
    tavily = ScriptedTavily({}, default=shares("ok", 6))
    svc = make_service(tavily)
    return TestClient(create_app(service=svc))


def test_agent_search_endpoint():
    resp = _app_client().post("/api/agent/search", json={"query": "流浪地球2"})
    assert resp.status_code == 200
    data = resp.json()
    assert [s["tool"] for s in data["steps"]] == ["recall_memory", "search", "verify"]
    assert data["matching_count"] == 6


def test_agent_stream_endpoint():
    with _app_client().stream("GET", "/api/agent/stream", params={"query": "流浪地球2"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [blk.split("\n") for blk in body.strip().split("\n\n")]
    kinds = [e[0].removeprefix("event: ") for e in events]
    assert kinds == ["step", "step", "step", "result"]
    result = json.loads(events[-1][1].removeprefix("data: "))
    assert result["matching_count"] == 6


def test_agent_stream_validates_query():
    assert _app_client().get("/api/agent/stream", params={"query": "x"}).status_code == 422
