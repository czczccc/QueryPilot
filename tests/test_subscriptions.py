"""追剧订阅：基线、新集数/更高清通知、webhook 推送、API 与每人上限。"""

import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.agent import SearchAgent
from app.services.memory import LinkStore
from app.services.subscriptions import SubscriptionWatcher
from tests.test_agent import ScriptedTavily, make_service, quark_client, shares

CID = "client-0001"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


def episodes_client(files: dict[str, list[str]]):
    """每个分享码对应一组文件名（多集）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        if request.url.path.endswith("/sharepage/detail"):
            names = files.get(request.url.params["pwd_id"], ["流浪地球2.E01.1080p.mkv"])
            return httpx.Response(200, json={"code": 0, "data": {
                "share": {"status": 1},
                "list": [{"file_name": n, "size": 1 << 30} for n in names],
            }})
        return httpx.Response(200, text="")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def eps(n: int, res: str = "1080p") -> list[str]:
    return [f"流浪地球2.E{i:02d}.{res}.mkv" for i in range(1, n + 1)]


def setup(files, tavily_shares, webhook_client=None, webhook=""):
    store = LinkStore(":memory:")
    tavily = ScriptedTavily({}, default=tavily_shares)
    service = make_service(tavily, client=episodes_client(files), store=store)
    agent = SearchAgent(service)
    watcher = SubscriptionWatcher(agent, store, webhook=webhook, client=webhook_client)
    return store, tavily, watcher


async def test_new_episodes_and_better_quality_notify():
    s = shares("ep", 5)
    files = {x: eps(3) for x in s}
    store, _, watcher = setup(files, s)
    sub = await store.add_subscription(CID, "流浪地球2", "流浪地球2", (3, 0, None))

    # 没有变化：不通知（但第一次拿到的质量分会作为起点）
    assert await watcher.check(CID, sub) == []
    assert sub.best_score > 0

    files[s[0]] = eps(5)  # 更新到 5 集
    files[s[1]] = eps(3, "2160p")  # 出现 4K
    notes = await watcher.check(CID, sub)
    kinds = {k: (m, sh) for k, m, sh in notes}
    assert "更新到 5 集" in kinds["episodes"][0] and kinds["episodes"][1] == s[0]
    assert "4K" in kinds["quality"][0] and kinds["quality"][1] == s[1]

    saved = (await store.list_subscriptions(CID))[0][1]
    assert (saved.best_episodes, saved.best_resolution) == (5, "2160p")
    assert saved.last_checked is not None
    got = await store.notifications(CID)
    assert len(got) == 2 and not any(n.read for n in got)
    await store.mark_read(CID)
    assert all(n.read for n in await store.notifications(CID))


async def test_dead_and_mismatched_links_do_not_count():
    s = shares("ok", 5)
    files = {x: eps(2) for x in s}
    files["other000"] = [f"三体.E{i:02d}.mkv" for i in range(1, 30)]  # 片名不符
    store, _, watcher = setup(files, [*s, "dead0001", "other000"])
    sub = await store.add_subscription(CID, "流浪地球2", "流浪地球2", (2, 0, None))
    notes = await watcher.check(CID, sub)
    assert [k for k, _, _ in notes] == []


async def test_webhook_push_and_run_once_survives_errors():
    posted: list[dict] = []

    async def hook(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.read()))
        return httpx.Response(200)

    s = shares("ep", 5)
    store, _, watcher = setup({x: eps(4) for x in s}, s,
                              webhook_client=httpx.AsyncClient(
                                  transport=httpx.MockTransport(hook)),
                              webhook="https://hook.example/x")
    await store.add_subscription(CID, "流浪地球2", "流浪地球2", (1, 0, None))
    await store.add_subscription(CID, "x", "坏订阅", (0, 0, None))

    async def boom(req, **kw):
        if req.query == "x":
            raise RuntimeError("boom")
        return await real(req, **kw)

    real = watcher._agent.run
    watcher._agent.run = boom
    assert await watcher.run_once() == 1
    assert "更新到 4 集" in posted[0]["text"]


async def test_baseline_from_memory_and_limit():
    s = shares("ep", 5)
    store, _, watcher = setup({x: eps(6) for x in s}, s)
    await watcher._agent.run(__import__("app.models").models.SearchRequest(query="流浪地球2"))
    assert (await watcher.baseline("流浪地球2"))[0] == 6

    for i in range(3):
        assert await store.add_subscription(CID, f"剧{i}", f"剧{i}", limit=3)
    assert await store.add_subscription(CID, "剧9", "剧9", limit=3) is None
    # 已订阅的再订阅不算新增
    assert await store.add_subscription(CID, "剧0 4K", "剧0", limit=3) is not None


def test_subscription_api():
    store = LinkStore(":memory:")
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)),
                           client=quark_client(), store=store)
    app = create_app(service=service, rate_limit_per_minute=100)
    with TestClient(app) as client:
        r = client.post("/api/subscriptions",
                        json={"client_id": CID, "query": "流浪地球2 4K", "resource": "流浪地球2"})
        assert r.status_code == 200
        sub_id = r.json()["id"]
        listed = client.get("/api/subscriptions", params={"client_id": CID}).json()
        assert [s["resource"] for s in listed] == ["流浪地球2"]
        assert client.get("/api/subscriptions", params={"client_id": "other-client"}).json() == []
        assert client.get("/api/notifications", params={"client_id": CID}).json() == []
        # 别人删不掉
        assert client.delete(f"/api/subscriptions/{sub_id}",
                             params={"client_id": "other-client"}).status_code == 404
        assert client.delete(f"/api/subscriptions/{sub_id}",
                             params={"client_id": CID}).status_code == 200
        assert client.post("/api/notifications/read",
                           params={"client_id": CID}).json() == {"ok": True}


def test_subscription_api_without_memory():
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)), client=quark_client())
    app = create_app(service=service, rate_limit_per_minute=100)
    with TestClient(app) as client:
        r = client.get("/api/subscriptions", params={"client_id": CID})
        assert r.status_code == 404
