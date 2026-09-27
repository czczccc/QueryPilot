"""防 token 滥用：LLM 用量计量与降级开关、每日额度、IP 上限、全站预算、结果缓存。"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SearchRequest
from app.services import llm
from app.services.agent import SearchAgent
from app.services.usage import SITE, QuotaConfig, QuotaGuard, UsageStore, today
from tests.test_agent import ScriptedTavily, _public_dns, make_service, shares  # noqa: F401
from tests.test_llm_config import stream_only_client


def json_client(usage: dict | None):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = {"choices": [{"message": {"role": "assistant", "content": "好"}}]}
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


PAYLOAD = {"model": "m", "messages": [{"role": "user", "content": "你好" * 50}]}


async def test_meter_uses_reported_usage_or_estimates():
    with llm.scope() as meter:
        await llm.chat(json_client({"total_tokens": 42}), PAYLOAD, "k", 5)
        await llm.chat(json_client({"total_tokens": 8}), PAYLOAD, "k", 5)
    assert (meter.calls, meter.tokens, meter.estimated) == (2, 50, False)

    with llm.scope() as meter:
        await llm.chat(json_client(None), PAYLOAD, "k", 5)
    assert meter.calls == 1 and meter.tokens > 50 and meter.estimated


async def test_disabled_scope_blocks_llm_calls():
    with llm.scope(allowed=False) as meter:
        assert not llm.enabled()
        with pytest.raises(llm.LLMDisabled):
            await llm.chat(json_client(None), PAYLOAD, "k", 5)
    assert meter.calls == 0
    assert llm.enabled()


async def test_degraded_agent_uses_rules_without_llm():
    seen: list[dict] = []
    agent = SearchAgent(make_service(ScriptedTavily({}, default=shares("ok", 6))),
                        api_key="k", client=stream_only_client(seen))
    with llm.scope(allowed=False) as meter:
        resp = await agent.run(SearchRequest(query="流浪地球2"))
    assert resp.planner == "rules" and seen == [] and meter.calls == 0
    with llm.scope() as meter:
        resp = await agent.run(SearchRequest(query="流浪地球2", refresh=True))
    assert resp.planner == "llm" and meter.calls > 0 and meter.tokens > 0


def guard(**cfg) -> QuotaGuard:
    return QuotaGuard(UsageStore(":memory:"), QuotaConfig(**cfg))


async def test_quota_user_limit_site_budget_and_ip_cap():
    g = guard(anon_daily_ai=2, user_daily_ai=5, site_daily_tokens=1000, ip_daily_searches=4)
    ai = llm.Meter(calls=2, tokens=100)
    for _ in range(2):
        d = await g.check("ip:1", "ip:1", False)
        assert d.ai
        await g.record("ip:1", "ip:1", ai, decision=d)
    assert d.to_dict()["remaining"] == 0
    d = await g.check("ip:1", "ip:1", False)
    assert (d.ai, d.reason) == (False, "user_quota")
    assert "登录" in d.message()
    # 降级后的规则搜索不算 AI 次数，但算 IP 搜索次数
    await g.record("ip:1", "ip:1", llm.Meter(), decision=d)
    assert d.used == 2
    # 登录用户单独计数，但同一 IP 的搜索总数一起算
    u = await g.check("user:a", "ip:1", True)
    assert u.ai and u.limit == 5
    await g.record("user:a", "ip:1", ai)
    blocked = await g.check("user:a", "ip:1", True)
    assert blocked.blocked and not blocked.ai
    # 全站 token 到预算：所有人降级
    await g.store.add(today(), [(SITE, {"tokens": 1000})])
    d = await g.check("user:b", "ip:2", True)
    assert (d.ai, d.reason) == (False, "site_budget")
    assert (await g.store.get(today(), SITE))["llm_calls"] == 6


async def test_agent_cache_reuses_result_without_llm_or_search():
    tavily = ScriptedTavily({}, default=shares("ok", 6))
    agent = SearchAgent(make_service(tavily), cache_minutes=60)
    first = await agent.run(SearchRequest(query="流浪地球2"))
    assert first.matching_count == 6
    searches = len(tavily.queries)
    emitted = []

    async def emit(step):
        emitted.append(step.tool)

    with llm.scope() as meter:
        again = await agent.run(SearchRequest(query=" 流浪地球2 "), emit=emit)
    assert (len(tavily.queries), meter.calls) == (searches, 0)
    assert [s.tool for s in again.steps] == emitted == ["cache"]
    assert again.session_id != first.session_id
    assert [lk.share for lk in again.links] == [lk.share for lk in first.links]
    # 缓存的结果照样能追问；强制刷新不走缓存
    follow = await agent.run(SearchRequest(query="要4K的", session_id=again.session_id))
    assert follow.steps[0].tool == "interpret_followup"
    fresh = await agent.run(SearchRequest(query="流浪地球2", refresh=True))
    assert fresh.steps[0].tool != "cache"


def quota_app(**cfg):
    seen: list[dict] = []
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)))
    agent = SearchAgent(service, api_key="k", client=stream_only_client(seen))
    app = create_app(service=service, agent=agent, rate_limit_per_minute=100, quota=guard(**cfg))
    return TestClient(app), seen


def test_api_degrades_after_quota_and_reports_it():
    client, seen = quota_app(anon_daily_ai=1, ip_daily_searches=3)
    assert client.get("/api/quota").json()["remaining"] == 1
    first = client.post("/api/agent/search", json={"query": "流浪地球2"}).json()
    assert first["planner"] == "llm"
    assert first["quota"]["ai"] and first["quota"]["remaining"] == 0
    n = len(seen)
    second = client.post("/api/agent/search", json={"query": "流浪地球2", "refresh": True}).json()
    assert second["planner"] == "rules" and len(seen) == n
    assert second["quota"]["reason"] == "user_quota"
    # SSE：先推送 quota 事件说明降级
    with client.stream("GET", "/api/agent/stream", params={"query": "沙丘2"}) as r:
        body = "".join(r.iter_text())
    kinds = [blk.split("\n")[0].removeprefix("event: ") for blk in body.strip().split("\n\n")]
    assert kinds[0] == "quota" and kinds[-1] == "result"
    result = json.loads(body.strip().split("\n\n")[-1].split("\n")[1].removeprefix("data: "))
    assert result["quota"]["reason"] == "user_quota"
    # 第 4 次：IP 当天搜索数到顶
    r = client.post("/api/agent/search", json={"query": "流浪地球2"})
    assert r.status_code == 429 and "明天" in r.json()["detail"]


def test_quota_off_by_default_in_tests():
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)))
    client = TestClient(create_app(service=service))
    assert client.get("/api/quota").json() == {"enabled": False}
    assert client.post("/api/agent/search", json={"query": "流浪地球2"}).json()["quota"] is None
