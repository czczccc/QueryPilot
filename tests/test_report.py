"""搜索结果反馈：「失效」够票后所有人不再推荐，「不对」只对这部片排除；防刷；导出。"""

import asyncio

from fastapi.testclient import TestClient

from app.models import SearchRequest
from app.services.agent import SearchAgent
from app.services.memory import LinkStore, resource_key
from tests.test_accounts import login
from tests.test_agent import ScriptedTavily, make_service, shares
from tests.test_autosave import CID, Drive, make


async def test_weights_dedupe_and_limit():
    store = LinkStore(":memory:")
    key = resource_key("流浪地球2")
    assert await store.add_report("abcdef1", "dead", "", "流浪地球2", "ip:a") == "recorded"
    assert await store.add_report("abcdef1", "dead", "", "流浪地球2", "ip:a") == "duplicate"
    assert (await store.blocked(key))[0] == set()  # 1 票不够
    await store.add_report("abcdef1", "dead", "", "流浪地球2", "ip:b")
    assert (await store.blocked(key))[0] == {"abcdef1"}
    await store.add_report("abcdef2", "wrong", key, "流浪地球2", "u:uid:1", 2)  # 登录 2 票
    assert (await store.blocked(key))[1] == {"abcdef2"}
    assert (await store.blocked(resource_key("三体")))[1] == set()  # 只对这部片
    store.REPORT_DAILY = 1
    assert await store.add_report("abcdef3", "dead", "", "", "ip:a") == "limited"


async def test_agent_skips_reported():
    tavily = ScriptedTavily({}, default=shares("ok", 6))
    service = make_service(tavily, store=LinkStore(":memory:"))
    s = shares("ok", 6)
    key = resource_key("流浪地球2")
    await service._store.add_report(s[0], "dead", "", "", "u:uid:1", 2)
    await service._store.add_report(s[1], "wrong", key, "流浪地球2", "u:uid:1", 2)
    resp = await SearchAgent(service).run(SearchRequest(query="流浪地球2"))
    by = {lk.share: lk for lk in resp.links}
    assert s[0] not in by or by[s[0]].state == "invalid"
    assert s[1] not in by or by[s[1]].relevance == "mismatch"


def test_report_api_and_export():
    app, store, _, _ = make(Drive(), admin_token="t" * 16)
    with TestClient(app) as client:
        r = client.post("/api/feedback/report", params=CID,
                        json={"share": "abcdef9", "reason": "wrong", "query": "流浪地球 4K"})
        assert r.json() == {"status": "recorded"}
        login(client)
        client.post("/api/feedback/report", params=CID,
                    json={"share": "abcdef9", "reason": "wrong", "query": "流浪地球 4K"})
        assert asyncio.run(store.blocked(resource_key("流浪地球")))[1] == {"abcdef9"}
        assert client.post("/api/feedback/report", params=CID,
                           json={"share": "x", "reason": "dead"}).status_code == 422
        got = client.get("/api/admin/feedback", headers={"X-Admin-Token": "t" * 16}).json()
        [item] = got["items"]
        assert item["query"] == "流浪地球 4K" and item["bad"][0]["weight"] == 3
