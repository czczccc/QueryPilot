"""站长后台：口令保护、用量统计、封禁账号 / IP、单独额度、邀请码。"""

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.cookie_box import CookieBox
from app.services.memory import LinkStore
from app.services.usage import QuotaConfig, QuotaGuard, UsageStore
from tests.test_accounts import FakeLogin, login, search
from tests.test_agent import (  # noqa: F401
    ScriptedTavily,
    _public_dns,
    make_service,
    quark_client,
    shares,
)

ADMIN = {"X-Admin-Token": "admin-secret"}


def make_app(**kw):
    store = LinkStore(":memory:")
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)),
                           client=quark_client(), store=store)
    guard = QuotaGuard(UsageStore(":memory:"), QuotaConfig(
        anon_daily_searches=0, ip_daily_searches=0, anon_daily_ai=3, user_daily_ai=5,
        site_daily_tokens=0))
    return create_app(service=service, rate_limit_per_minute=1000, qr_login=FakeLogin(),
                      cookie_box=CookieBox.from_secret("k"), quota=guard, **kw)


def test_admin_disabled_without_token():
    with TestClient(make_app()) as client:
        assert client.get("/api/admin/overview", headers=ADMIN).status_code == 404


def test_admin_requires_token():
    with TestClient(make_app(admin_token="admin-secret")) as client:
        assert client.get("/api/admin/overview").status_code == 401
        assert client.get("/api/admin/overview",
                          headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_admin_flow():
    app = make_app(admin_token="admin-secret")
    with TestClient(app) as admin, TestClient(app) as user:
        search(user)  # 匿名一次
        login(user)
        search(user)
        search(user)

        ov = admin.get("/api/admin/overview", headers=ADMIN).json()
        assert ov["today"]["searches"] == 3 and ov["users"] == 1
        assert ov["limits"]["user_daily_ai"] == 5
        subjects = {r["subject"]: r for r in ov["top"]}
        assert subjects["user:uid:1"]["searches"] == 2
        assert subjects["user:uid:1"]["nickname"] == "小明"
        assert subjects["ip:testclient"]["searches"] == 3

        users = admin.get("/api/admin/users", headers=ADMIN).json()
        assert [(u["user_id"], u["today_searches"], u["total_searches"]) for u in users] == [
            ("uid:1", 2, 2)]
        assert admin.get("/api/admin/users", params={"q": "不存在"}, headers=ADMIN).json() == []

        # 单独额度
        r = admin.put("/api/admin/users/uid:1/limit", json={"ai_limit": 100}, headers=ADMIN)
        assert r.json()["ai_limit"] == 100
        assert user.get("/api/me").json()["quota"]["limit"] == 100
        assert admin.put("/api/admin/users/nobody/limit", json={"ai_limit": 1},
                         headers=ADMIN).status_code == 404

        # 封禁账号：搜索、转存都 403，再登录也不行；解封后恢复
        admin.post("/api/admin/users/uid:1/ban", json={"reason": "刷接口"}, headers=ADMIN)
        r = search(user)
        assert r.status_code == 403 and "刷接口" in r.json()["detail"]
        assert user.get("/api/me").json()["banned"]
        assert user.post("/api/save", json={"share": "abcdef123456"}).status_code == 403
        user.post("/api/quark/logout")
        assert login(user)["status"] == "banned"
        admin.post("/api/admin/users/uid:1/unban", headers=ADMIN)
        assert login(user)["status"] == "success"
        assert search(user).status_code == 200

        # 封 IP
        admin.post("/api/admin/bans", json={"ip": "testclient", "reason": "脚本"}, headers=ADMIN)
        assert [b["ip"] for b in admin.get("/api/admin/bans", headers=ADMIN).json()] == [
            "testclient"]
        assert search(user).status_code == 403
        assert admin.delete("/api/admin/bans/testclient", headers=ADMIN).json() == {
            "deleted": True}
        assert search(user).status_code == 200


def test_admin_invites():
    app = make_app(admin_token="admin-secret", invite_required=True)
    with TestClient(app) as admin, TestClient(app) as user:
        codes = admin.post("/api/admin/invites", json={"count": 2, "max_uses": 1, "note": "朋友"},
                           headers=ADMIN).json()
        assert len(codes) == 2
        assert login(user)["status"] == "invite_required"
        assert login(user, codes[0]["code"])["status"] == "success"
        listed = {i["code"]: i["uses"] for i in admin.get("/api/admin/invites",
                                                          headers=ADMIN).json()}
        assert listed[codes[0]["code"]] == 1 and listed[codes[1]["code"]] == 0
        assert admin.delete(f"/api/admin/invites/{codes[1]['code']}",
                            headers=ADMIN).json() == {"deleted": True}
        assert admin.post("/api/admin/invites", headers=ADMIN).status_code == 200
        assert search(user).json()["quota"]["logged_in"] is True
