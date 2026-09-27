"""对话式追问：规则解释、在上一轮结果上筛选、条件变化后补搜、LLM 解释、会话过期。"""

import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SearchRequest
from app.services.agent import SearchAgent
from app.services.conversation import ConversationStore, interpret_rules
from tests.test_agent import PARSED, ScriptedTavily, make_service, quark_client, shares


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


def test_interpret_rules():
    r = interpret_rules("要第二季")
    assert (r.mode, r.season) == ("refine", 2)
    r = interpret_rules("只要中字的 4K")
    assert (r.subtitle, r.resolution) == (True, "2160p")
    assert interpret_rules("有 HDR 的吗").hdr is True
    assert interpret_rules("再找找").more is True
    assert interpret_rules("沙丘2").mode == "new"


def test_conversation_store_expiry_and_lru():
    from app.services.conversation import Conversation
    from app.services.relevance import RelevanceTarget

    store = ConversationStore(ttl=0.0)
    conv = Conversation(id="a", parsed=PARSED, target=RelevanceTarget([], None, None),
                        required=None)
    store.put(conv)
    assert store.get("a") is None  # 立即过期

    store = ConversationStore(max_size=2)
    for i in "abc":
        store.put(Conversation(id=i, parsed=PARSED,
                               target=RelevanceTarget([], None, None), required=None))
    assert store.get("a") is None and store.get("c") is not None


# 6 条有效：3 条带字幕 4K，3 条无字幕 1080p
SUB = shares("sub", 3)
PLAIN = shares("pln", 3)
FILES = {s: "流浪地球2.2160p.中字.mkv" for s in SUB} | {s: "流浪地球2.1080p.mkv" for s in PLAIN}


async def _first_round(agent):
    return await agent.run(SearchRequest(query="流浪地球2"))


async def test_followup_filters_previous_results_without_search():
    tavily = ScriptedTavily({}, default=[*SUB, *PLAIN, *shares("more", 5)])
    agent = SearchAgent(make_service(tavily, client=quark_client(FILES)))
    first = await _first_round(agent)
    assert first.session_id and first.history == ["流浪地球2"]
    calls = len(tavily.queries)

    # 上一轮只有 3 条带字幕，不够 5 条 → 在上一轮基础上补搜
    follow = await agent.run(SearchRequest(query="只要中字的", session_id=first.session_id))
    assert follow.steps[0].tool == "interpret_followup"
    assert follow.followup["subtitle"] is True
    assert follow.filters["subtitle"] is True
    assert follow.session_id == first.session_id
    assert follow.history == ["流浪地球2", "只要中字的"]
    assert all(lk.quality.has_subtitle for lk in follow.links if lk.state == "valid")
    assert any("中字" in q for q in tavily.queries[calls:])


async def test_followup_filter_only_when_enough():
    subs = shares("sub", 6)
    files = {s: "流浪地球2.2160p.中字.mkv" for s in subs}
    tavily = ScriptedTavily({}, default=[*subs, *PLAIN])
    agent = SearchAgent(make_service(tavily, client=quark_client(files)))
    first = await _first_round(agent)
    calls = len(tavily.queries)

    follow = await agent.run(SearchRequest(query="4K 的", session_id=first.session_id))
    assert [s.tool for s in follow.steps] == ["interpret_followup"]
    assert follow.stop_reason == "在上一轮结果里筛选即可满足"
    assert len(tavily.queries) == calls  # 没有再搜索
    assert follow.required_resolution == "2160p"
    assert follow.matching_count == 6


async def test_followup_season_change_marks_old_season_mismatch():
    s1 = shares("sa", 3)
    s2 = shares("sb", 5)
    files = {s: "流浪地球2.第1季.E01.mkv" for s in s1} | {s: "流浪地球2.第2季.E01.mkv" for s in s2}
    tavily = ScriptedTavily({}, default=s1)
    agent = SearchAgent(make_service(tavily, client=quark_client(files)))
    first = await _first_round(agent)
    tavily.default = s2

    follow = await agent.run(SearchRequest(query="要第二季", session_id=first.session_id))
    assert follow.filters["season"] == 2
    by = {lk.share: lk for lk in follow.links}
    assert all(by[s].relevance == "mismatch" for s in s1)
    assert follow.matching_count == 5
    assert any("第2季" in q for q in tavily.queries)


async def test_unrelated_followup_starts_new_search():
    tavily = ScriptedTavily({}, default=shares("ok", 6))
    agent = SearchAgent(make_service(tavily))
    first = await _first_round(agent)
    follow = await agent.run(SearchRequest(query="沙丘2", session_id=first.session_id))
    assert follow.followup["mode"] == "new"
    assert follow.session_id != first.session_id
    assert follow.history == ["沙丘2"]


async def test_unknown_session_is_a_fresh_search():
    agent = SearchAgent(make_service(ScriptedTavily({}, default=shares("ok", 6))))
    resp = await agent.run(SearchRequest(query="流浪地球2", session_id="nope"))
    assert resp.followup is None
    assert resp.steps[0].tool == "recall_memory"


async def test_llm_interprets_followup():
    seen: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        seen.append(body)
        if "tools" in body:  # agent 规划：直接结束
            call = {"id": "c1", "type": "function",
                    "function": {"name": "finish", "arguments": '{"reason":"ok"}'}}
            return httpx.Response(200, json={"choices": [{"message": {
                "role": "assistant", "content": "", "tool_calls": [call]}}]})
        content = json.dumps({"mode": "refine", "season": None, "resolution": "2160p",
                              "subtitle": False, "hdr": True, "more": False})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    llm = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    agent = SearchAgent(make_service(ScriptedTavily({})), api_key="k", client=llm)
    first = await agent.run(SearchRequest(query="流浪地球2"))
    follow = await agent.run(
        SearchRequest(query="有没有画质更好的，最好杜比", session_id=first.session_id))
    assert follow.followup["by"] == "llm"
    assert follow.filters == {"season": None, "resolution": "2160p", "subtitle": False,
                              "hdr": True}


def test_stream_followup_roundtrip():
    tavily = ScriptedTavily({}, default=[*SUB, *PLAIN])
    client = TestClient(create_app(service=make_service(tavily, client=quark_client(FILES))))

    def run(params):
        with client.stream("GET", "/api/agent/stream", params=params) as r:
            body = "".join(r.iter_text())
        last = body.strip().split("\n\n")[-1].split("\n")
        assert last[0] == "event: result"
        return json.loads(last[1].removeprefix("data: "))

    first = run({"query": "流浪地球2"})
    follow = run({"query": "只要4K", "session_id": first["session_id"]})
    assert follow["history"] == ["流浪地球2", "只要4K"]
    assert follow["filters"]["resolution"] == "2160p"
