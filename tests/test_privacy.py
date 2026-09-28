"""隐私说明 + 删除我的数据：删掉订阅、历史、通知、偏好、夸克凭证和账号记录，并退出登录。"""

import asyncio

from fastapi.testclient import TestClient

from app.services.usage import today
from tests.test_accounts import login
from tests.test_autosave import BODY, CID, Drive, make

OTHER = {"client_id": "browser-0002"}


def test_privacy_notice():
    app, _, _, _ = make(Drive())
    with TestClient(app) as client:
        got = client.get("/api/privacy").json()
    titles = [s["title"] for s in got["sections"]]
    assert "夸克登录凭证" in titles and "删除我的数据" in titles and got["updated"]


def test_delete_my_data():
    app, store, _, _ = make(Drive())
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=BODY).json()
        client.post(f"/api/subscriptions/{sub['id']}/complete", params=CID)
        client.post("/api/subscriptions", json={**BODY, "query": "三体", "resource": "三体"})
        client.put("/api/prefs", params=CID, json={"resolution": "1080p"})
        # 别人（另一个浏览器、没登录）的订阅不受影响
        asyncio.run(store.add_subscription(OTHER["client_id"], "繁花", "繁花"))

        before = client.get("/api/me/data", params=CID).json()
        assert before["logged_in"] is True
        counts = before["counts"]
        assert counts["subscriptions"] == 1 and counts["history"] == 1
        assert counts["quark_logins"] == 1

        assert client.delete("/api/me/data", params=CID).status_code == 400  # 没确认
        got = client.delete("/api/me/data", params={**CID, "confirm": "DELETE"})
        assert got.status_code == 200
        deleted = got.json()["deleted"]
        assert deleted["subscriptions"] == 1 and deleted["history"] == 1
        assert deleted["quark_logins"] == 1 and deleted["account"] >= 1
        assert "qp_quark" in got.headers.get("set-cookie", "")

        # 已退出：服务器上凭证没了，数据清空
        client.cookies.clear()
        after = client.get("/api/me/data", params=CID).json()
        assert after["logged_in"] is False
        assert asyncio.run(store.personal_data(["u:uid:1", CID["client_id"]], "uid:1")) == {
            k: 0 for k in after["counts"]}
        assert len(asyncio.run(store.list_subscriptions(OTHER["client_id"]))) == 1


async def test_forget_user_keeps_today_and_bans():
    from app.services.usage import UsageStore

    users = UsageStore(":memory:")
    await users.touch_user("a", "甲")
    await users.touch_user("b", "乙")
    await users.execute("UPDATE users SET banned = 1 WHERE user_id = 'b'")
    await users.execute("INSERT INTO usage_daily (day, subject, searches) VALUES "
                        "('2026-01-01', 'user:a', 3), (?, 'user:a', 5)", (today(),))
    await users.forget_user("a", today())
    await users.forget_user("b", today())
    assert await users.get_user("a") is None
    assert (await users.get_user("b"))["banned"] == 1
    rows = await users.query("SELECT day FROM usage_daily WHERE subject = 'user:a'")
    assert [r["day"] for r in rows] == [today()]
