"""订阅新集自动转存：开关、只存网盘里没有的集、登录失效暂停与恢复。"""

import json

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.agent import SearchAgent
from app.services.classify import Classifier, episode_key
from app.services.cookie_box import CookieBox
from app.services.memory import LinkStore
from tests.test_accounts import FakeLogin, login
from tests.test_agent import ScriptedTavily, _public_dns, make_service, shares  # noqa: F401
from tests.test_subscriptions import episodes_client, eps

OWNER = "u:uid:1"


class Drive:
    """假夸克网盘：分享里是「流浪地球2/」文件夹 + n 集；目标目录里已有第 1 集。"""

    def __init__(self, episodes: int = 3):
        self.episodes = episodes
        self.expired = False
        self.saved: list[dict] = []
        self.have = ["流浪地球2.E01.2160p.mkv"]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sharepage/token"):
            if self.expired:
                return httpx.Response(200, json={"code": 31001, "message": "login"})
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "st"}})
        if path.endswith("/sharepage/detail"):
            if request.url.params["pdir_fid"] == "0":
                return httpx.Response(200, json={"code": 0, "data": {
                    "share": {"title": "流浪地球 全集"},
                    "list": [{"fid": "top", "share_fid_token": "tt", "file_name": "流浪地球",
                              "dir": True}]}})
            return httpx.Response(200, json={"code": 0, "data": {"list": [
                {"fid": f"f{i}", "share_fid_token": f"t{i}",
                 "file_name": f"流浪地球.E{i:02d}.1080p.mkv"}
                for i in range(1, self.episodes + 1)]}})
        if path.endswith("/file/info/path_list"):
            return httpx.Response(200, json={"code": 0, "data": [{"fid": "dir1"}]})
        if path.endswith("/file/sort"):
            assert request.url.params["pdir_fid"] == "dir1"
            return httpx.Response(200, json={"code": 0, "data": {"list": [
                {"file_name": n, "fid": n, "dir": False} for n in self.have]}})
        if path.endswith("/sharepage/save"):
            self.saved.append(json.loads(request.read()))
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t"}})
        if path.endswith("/clouddrive/task"):
            return httpx.Response(200, json={"code": 0, "data": {"status": 2}})
        return httpx.Response(404)


def make(drive: Drive, episodes: int = 3):
    store = LinkStore(":memory:")
    s = shares("ep", 1)
    tavily = ScriptedTavily({}, default=s)
    files = {x: eps(episodes) for x in s}
    service = make_service(tavily, client=episodes_client(files), store=store)
    app = create_app(
        service=service, agent=SearchAgent(service), rate_limit_per_minute=1000,
        qr_login=FakeLogin(), cookie_box=CookieBox.from_secret("k"),
        quark_client=httpx.AsyncClient(transport=httpx.MockTransport(drive.handler)),
        classifier=Classifier(),
    )
    return app, store, files, s


BODY = {"client_id": "browser-0001", "query": "流浪地球", "resource": "流浪地球"}
CID = {"client_id": "browser-0001"}


def test_episode_key():
    assert episode_key("流浪地球.E05.1080p.mkv") == "1x5"
    assert episode_key("Show.S02E03.mkv") == "2x3"
    assert episode_key("第12集.mp4") == "1x12"
    assert episode_key("流浪地球2.2160p.mkv") is None
    assert episode_key("E05.srt") is None


def test_auto_save_only_new_episodes_then_pause_and_resume():
    drive = Drive(episodes=3)
    app, store, files, s = make(drive)
    with TestClient(app) as client:
        sub = client.post("/api/subscriptions", json={**BODY, "client_id": "x" * 8})
        assert sub.status_code == 401  # 未登录不能订阅
        login(client)
        sub = client.post("/api/subscriptions", json=BODY).json()
        assert sub["auto_save"] is False
        r = client.patch(f"/api/subscriptions/{sub['id']}", params=CID, json={"auto_save": True})
        assert r.json()["auto_save"] is True

        async def check():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            return await app.state.watcher.check(owner, obj)

        import asyncio
        notes = asyncio.run(check())
        kinds = [k for k, _, _ in notes]
        assert kinds == ["episodes", "auto_saved"]
        assert "2 个新文件" in notes[1][1] and "跳过已有的 1 个" in notes[1][1]
        [req] = drive.saved
        # 展开分享里的文件夹，只存第 2、3 集，直接存进分类目录
        assert (req["fid_list"], req["pdir_fid"], req["to_pdir_fid"]) == (
            ["f2", "f3"], "top", "dir1")
        log = client.get(f"/api/subscriptions/{sub['id']}/saves", params=CID).json()
        assert log[0]["ok"] and log[0]["file_count"] == 2

        # 网盘里都有了：不再提交转存
        drive.have = [f"流浪地球.E{i:02d}.mkv" for i in range(1, 6)]
        for x in s:
            files[x] = eps(5)
        drive.episodes = 5
        notes = asyncio.run(check())
        assert [k for k, _, _ in notes] == ["episodes"] and len(drive.saved) == 1

        # 夸克登录失效：暂停并只提醒一次
        drive.expired = True
        for x in s:
            files[x] = eps(6)
        notes = asyncio.run(check())
        assert [k for k, _, _ in notes] == ["episodes", "auto_save_paused"]
        assert "重新扫码" in notes[1][1]
        listed = client.get("/api/subscriptions", params=CID).json()
        assert listed == [] or listed[0]["auto_save_status"] == "login_expired"
        for x in s:
            files[x] = eps(7)
        notes = asyncio.run(check())
        assert [k for k, _, _ in notes] == ["episodes"]

        # 重新扫码登录后恢复
        drive.expired = False
        login(client)
        [sub_now] = client.get("/api/subscriptions", params=CID).json()
        assert sub_now["auto_save"] and sub_now["auto_save_status"] is None


def test_auto_save_needs_login_and_off_by_default():
    drive = Drive()
    app, store, _, _ = make(drive)
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=BODY).json()
        client.post("/api/quark/logout")
        r = client.patch(f"/api/subscriptions/{sub['id']}", params=CID, json={"auto_save": True})
        assert r.status_code == 401

        async def check():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            return await app.state.watcher.check(owner, obj)

        import asyncio
        notes = asyncio.run(check())
        assert [k for k, _, _ in notes] == ["episodes"] and drive.saved == []
