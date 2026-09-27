"""夸克扫码登录：二维码、轮询、凭证加密入库、按会话转存、过期清理、退出。"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.cookie_box import CookieBox, session_hash
from app.services.memory import LinkStore
from app.services.quark_login import QuarkQrLogin, qr_content
from tests.test_agent import ScriptedTavily, make_service, quark_client, shares
from tests.test_save import drive

USER_COOKIE = "__pus=USER-SECRET; __puus=abc"


class QuarkAuth:
    """假的夸克登录服务：前两次轮询等待，第三次返回 ticket。"""

    def __init__(self, waits: int = 2, status: int = 2000000):
        self.waits = waits
        self.status = status

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("getTokenForQrcodeLogin"):
            return httpx.Response(200, json={"status": 2000000,
                                             "data": {"members": {"token": "qr-token"}}})
        if path.endswith("getServiceTicketByQrcodeToken"):
            assert request.url.params["token"] == "qr-token"
            if self.waits > 0:
                self.waits -= 1
                return httpx.Response(200, json={"status": 50004001})
            if self.status != 2000000:
                return httpx.Response(200, json={"status": self.status})
            return httpx.Response(200, json={"status": 2000000,
                                             "data": {"members": {"service_ticket": "st-1"}}})
        if path == "/account/info":
            assert request.url.params["st"] == "st-1"
            return httpx.Response(
                200, json={"data": {"nickname": "小明"}},
                headers=[("set-cookie", "__pus=USER-SECRET; Path=/; Domain=.quark.cn"),
                         ("set-cookie", "__puus=abc; Path=/; Domain=.quark.cn")],
            )
        return httpx.Response(404)

    def login(self) -> QuarkQrLogin:
        return QuarkQrLogin(transport=httpx.MockTransport(self.handler))


def test_qr_content_contains_token():
    assert "token=qr-token" in qr_content("qr-token")
    assert qr_content("x").startswith("https://su.quark.cn/")


async def test_poll_flow_returns_cookie():
    login = QuarkAuth().login()
    login_id, content = await login.start()
    assert "qr-token" in content
    assert (await login.poll(login_id)).status == "waiting"
    assert (await login.poll(login_id)).status == "waiting"
    result = await login.poll(login_id)
    assert result.status == "success" and result.nickname == "小明"
    assert dict(p.split("=") for p in result.cookie.split("; ")) == {
        "__pus": "USER-SECRET", "__puus": "abc"}
    assert (await login.poll(login_id)).status == "expired"  # 用过即失效
    assert (await login.poll("unknown")).status == "expired"


async def test_poll_expired_qr():
    login = QuarkAuth(waits=0, status=50004002).login()
    login_id, _ = await login.start()
    assert (await login.poll(login_id)).status == "expired"


def test_cookie_box_roundtrip_and_key_file(tmp_path):
    box = CookieBox.load("", tmp_path / ".cookie_secret")
    blob = box.encrypt("secret", b"aad")
    assert b"secret" not in blob
    assert box.decrypt(blob, b"aad") == "secret"
    assert box.decrypt(blob, b"other") is None  # 密文绑定会话，不能挪用
    again = CookieBox.load("", tmp_path / ".cookie_secret")  # 读回同一把密钥
    assert again.decrypt(blob, b"aad") == "secret"
    assert oct((tmp_path / ".cookie_secret").stat().st_mode & 0o777) == "0o600"
    assert CookieBox.from_secret("x").decrypt(blob, b"aad") is None


@pytest.fixture
def app_env():
    store = LinkStore(":memory:")
    seen: list[httpx.Request] = []
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)),
                           client=quark_client(), store=store)
    app = create_app(
        service=service, rate_limit_per_minute=100,
        qr_login=QuarkAuth(waits=0).login(), cookie_box=CookieBox.from_secret("k"),
        quark_client=drive(seen=seen),
    )
    return app, store, seen


def _login(client) -> dict:
    start = client.post("/api/quark/login").json()
    assert start["qr_svg"].startswith("<svg") and "qr-token" in start["qr_url"]
    return client.get(f"/api/quark/login/{start['login_id']}").json()


def test_login_then_save_to_own_drive(app_env):
    app, store, seen = app_env
    with TestClient(app) as client:
        status = client.get("/api/save/status").json()
        assert status["login"] is True and status["logged_in"] is False
        # 未登录不能转存
        assert client.post("/api/save", json={"share": "abcdef123456"}).status_code == 401

        done = _login(client)
        assert done == {"status": "success", "nickname": "小明"}
        token = client.cookies.get("qp_quark")
        assert token and "USER-SECRET" not in token

        # 数据库里只有密文，按会话哈希存
        row = store._conn.execute("SELECT * FROM quark_accounts").fetchone()
        assert row["session_hash"] == session_hash(token)
        assert b"USER-SECRET" not in bytes(row["cookie_enc"])

        status = client.get("/api/save/status").json()
        assert (status["logged_in"], status["nickname"]) == (True, "小明")
        assert "USER-SECRET" not in json.dumps(status)

        r = client.post("/api/save", json={"share": "abcdef123456"})
        assert r.json()["ok"] is True
        assert all(req.headers["Cookie"] == USER_COOKIE for req in seen)

        assert client.post("/api/quark/logout").json() == {"ok": True}
        assert store._conn.execute("SELECT COUNT(*) FROM quark_accounts").fetchone()[0] == 0


def test_other_browser_cannot_use_session(app_env):
    app, _, _ = app_env
    with TestClient(app) as a, TestClient(app) as b:
        _login(a)
        b.cookies.set("qp_quark", "forged")
        assert b.get("/api/save/status").json()["logged_in"] is False
        assert b.post("/api/save", json={"share": "abcdef123456"}).status_code == 401


def test_expired_user_cookie_is_cleared():
    store = LinkStore(":memory:")
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)),
                           client=quark_client(), store=store)
    app = create_app(
        service=service, rate_limit_per_minute=100,
        qr_login=QuarkAuth(waits=0).login(), cookie_box=CookieBox.from_secret("k"),
        quark_client=drive(save_code=31001),
    )
    with TestClient(app) as client:
        _login(client)
        r = client.post("/api/save", json={"share": "abcdef123456"})
        assert r.json() == {"ok": False, "message": "夸克登录已过期，请重新扫码登录",
                            "file_count": 0}
        assert client.get("/api/save/status").json()["logged_in"] is False


def test_login_disabled_without_memory():
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)), client=quark_client())
    app = create_app(service=service, rate_limit_per_minute=100,
                     qr_login=QuarkAuth().login(), cookie_box=CookieBox.from_secret("k"))
    with TestClient(app) as client:
        assert client.post("/api/quark/login").status_code == 404
        assert client.get("/api/save/status").json()["login"] is False
