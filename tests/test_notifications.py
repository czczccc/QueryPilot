"""通知：单条已读 / 全部已读 / 删除，默认只返回未读；「可能相关」同一订阅只留最新一条；存齐或完成后自动收起。"""

import asyncio

from fastapi.testclient import TestClient

from app.services.memory import LinkStore
from tests.test_accounts import login
from tests.test_autosave import BODY, CID, Drive, make

C = "c" * 8


async def test_maybe_keeps_latest_and_fulfilled_season_tidies():
    store = LinkStore(":memory:")
    sub = await store.add_subscription(C, "流浪地球", "流浪地球", media="tv", season=1,
                                       total_episodes=2)
    await store.update_subscription(C, sub, [("maybe", "可能 1", "s1")])
    await store.update_subscription(C, sub, [("maybe", "可能 2", "s2"), ("found", "有了", "s3")])
    unread = await store.notifications(C, include_read=False)
    assert sorted(n.message for n in unread) == ["可能 2", "有了"]
    assert await store.notified_shares(sub.id) == {"s1", "s2", "s3"}  # 收起的也不会重复提醒

    sub.saved_episodes = [1, 2]  # 这一季存齐：之前的通知都收起，这次的留着
    await asyncio.sleep(0.01)
    await store.update_subscription(C, sub, [("auto_saved", "存好了", "s3")])
    assert [n.message for n in await store.notifications(C, include_read=False)] == ["存好了"]


async def test_archive_hides_all_but_completed():
    store = LinkStore(":memory:")
    sub = await store.add_subscription(C, "流浪地球", "流浪地球", media="movie")
    await store.update_subscription(C, sub, [("found", "有了", "s1"), ("completed", "完成", None)])
    await store.archive_subscription(C, sub, "已存")
    assert [n.kind for n in await store.notifications(C, include_read=False)] == ["completed"]


def test_notification_api():
    app, store, _, _ = make(Drive())
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=BODY).json()
        [(owner, obj)] = asyncio.run(store.list_subscriptions())
        asyncio.run(store.update_subscription(owner, obj, [
            ("found", "甲", "s1"), ("episodes", "乙", "s2"), ("quality", "丙", "s3")]))
        got = client.get("/api/notifications", params=CID).json()
        assert len(got) == 3 and all(not n["read"] for n in got)
        a, b, c = sorted(got, key=lambda n: n["id"])

        assert client.post(f"/api/notifications/{a['id']}/read", params=CID).json() == {"ok": True}
        assert client.delete(f"/api/notifications/{b['id']}", params=CID).json() == {
            "deleted": True}
        assert [n["id"] for n in client.get("/api/notifications", params=CID).json()] == [c["id"]]
        every = client.get("/api/notifications", params={**CID, "include_read": True}).json()
        assert sorted(n["id"] for n in every) == [a["id"], c["id"]]  # 删掉的不再出现

        assert client.post("/api/notifications/read", params=CID).json() == {"ok": True}
        assert client.get("/api/notifications", params=CID).json() == []
        assert client.delete("/api/notifications/99999", params=CID).status_code == 404
        assert sub["id"] == obj.id
