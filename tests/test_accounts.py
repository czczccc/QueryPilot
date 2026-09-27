"""账号分级：扫码登录即账号、未登录限次、订阅需登录、邀请码。"""

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.cookie_box import CookieBox
from app.services.memory import LinkStore
from app.services.quark_login import LoginResult
from app.services.usage import QuotaConfig, QuotaGuard, UsageStore
from tests.test_agent import (  # noqa: F401
    ScriptedTavily,
    _public_dns,
    make_service,
    quark_client,
    shares,
)
from tests.test_quark_login import QuarkAuth

CID = "browser-0001"


class FakeLogin:
    """扫码立即成功；每次 start 前可以指定这次是哪个夸克账号。"""

    def __init__(self):
        self.next = ("小明", "uid:1")

    async def start(self):
        return "login-1", "https://su.quark.cn/4_eMHBJ?token=t"

    async def poll(self, login_id):
        nickname, uid = self.next
        return LoginResult("success", f"__pus={uid}", nickname, uid)


def make_app(**kw):
    store = LinkStore(":memory:")
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)),
                           client=quark_client(), store=store)
    guard = QuotaGuard(UsageStore(":memory:"), QuotaConfig(
        anon_daily_searches=kw.pop("anon", 2), ip_daily_searches=0, anon_daily_ai=0,
        user_daily_ai=0, site_daily_tokens=0))
    login = FakeLogin()
    app = create_app(service=service, rate_limit_per_minute=1000, qr_login=login,
                     cookie_box=CookieBox.from_secret("k"), quota=guard, **kw)
    return app, login, guard


def login(client, code: str = "") -> dict:
    lid = client.post("/api/quark/login", json={"invite_code": code}).json()["login_id"]
    return client.get(f"/api/quark/login/{lid}").json()


def search(client):
    return client.post("/api/agent/search", json={"query": "流浪地球2", "client_id": CID})


def test_account_user_id_from_quark_account_info():
    class WithUid(QuarkAuth):
        async def handler(self, request):
            resp = await super().handler(request)
            if request.url.path == "/account/info":
                return httpx.Response(200, json={"data": {"nickname": "小明", "uid": 42}},
                                      headers=resp.headers.multi_items())
            return resp

    import asyncio

    async def run():
        q = WithUid(waits=0).login()
        lid, _ = await q.start()
        return await q.poll(lid)

    result = asyncio.run(run())
    assert (result.status, result.nickname, result.user_id) == ("success", "小明", "uid:42")


def test_anonymous_limited_then_login_continues():
    app, _, _ = make_app(anon=2)
    with TestClient(app) as client:
        me = client.get("/api/me").json()
        assert me["login"] and not me["logged_in"] and not me["invite_required"]
        assert me["quota"]["searches_remaining"] == 2
        assert search(client).status_code == 200
        r = search(client)
        assert r.json()["quota"]["searches_remaining"] == 0
        r = search(client)
        assert r.status_code == 401 and "登录" in r.json()["detail"]

        assert login(client)["status"] == "success"
        r = search(client)
        assert r.status_code == 200
        assert r.json()["quota"]["logged_in"] and r.json()["quota"]["searches_remaining"] is None
        me = client.get("/api/me").json()
        assert (me["logged_in"], me["nickname"]) == (True, "小明")


def test_subscriptions_need_login_and_follow_account():
    app, login_svc, _ = make_app()
    body = {"client_id": CID, "query": "流浪地球2 4K", "resource": "流浪地球2"}
    with TestClient(app) as a, TestClient(app) as b:
        assert a.post("/api/subscriptions", json=body).status_code == 401
        login(a)
        assert a.post("/api/subscriptions", json=body).status_code == 200
        # 另一个浏览器登录同一个夸克账号，看得到同一份订阅
        login(b)
        other = {"client_id": "browser-0002"}
        assert [s["resource"] for s in b.get("/api/subscriptions", params=other).json()] == [
            "流浪地球2"]
        # 换成别的账号就看不到
        login_svc.next = ("小红", "uid:2")
        login(b)
        assert b.get("/api/subscriptions", params=other).json() == []


def test_invite_required_for_new_accounts_only():
    app, login_svc, guard = make_app(invite_required=True, invite_codes=("hello",))
    with TestClient(app) as client:
        assert client.get("/api/me").json()["invite_required"] is True
        r = login(client)
        assert r["status"] == "invite_required" and "qp_quark" not in client.cookies
        assert login(client, "wrong")["message"] == "邀请码无效或已用完"
        assert login(client, "hello")["status"] == "success"
        # 老用户再登录不需要邀请码
        client.post("/api/quark/logout")
        assert login(client)["status"] == "success"

        # 库里的一次性邀请码
        guard.store._conn.execute(
            "INSERT INTO invites (code, max_uses, uses, created) VALUES ('once', 1, 0, 0)")
        login_svc.next = ("小红", "uid:2")
        assert login(client, "once")["status"] == "success"
        login_svc.next = ("小刚", "uid:3")
        assert login(client, "once")["status"] == "invite_required"
