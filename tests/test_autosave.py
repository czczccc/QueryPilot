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
        # 直接改库打开开关（不走 PATCH，避免它触发的后台立即检查干扰这里的顺序）
        import asyncio
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))

        async def check():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            return await app.state.watcher.check(owner, obj)

        import asyncio
        notes = asyncio.run(check())
        kinds = [k for k, _, _ in notes]
        assert kinds == ["found", "auto_saved"]
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
        assert [k for k, _, _ in notes] == ["found"] and drive.saved == []


def test_turning_on_or_check_now_fills_existing_episodes():
    """网盘是空的：打开开关或点「立即检查」就把现有的集补齐，不必等到出新集。"""
    import time

    drive = Drive(episodes=3)
    drive.have = []
    app, store, _, _ = make(drive)
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=BODY).json()
        # 先让订阅基线等于当前集数：之后的检查不会产生「新集」通知
        import asyncio

        async def baseline():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            await app.state.watcher.check(owner, obj)

        asyncio.run(baseline())
        assert drive.saved == []

        r = client.patch(f"/api/subscriptions/{sub['id']}", params=CID, json={"auto_save": True})
        assert r.json()["auto_save"] is True
        # 打开开关时后台立即检查一次；这次检查受 2 分钟冷却保护
        for _ in range(100):
            if drive.saved:
                break
            time.sleep(0.02)
        [req] = drive.saved
        assert req["fid_list"] == ["f1", "f2", "f3"]
        assert client.post(f"/api/subscriptions/{sub['id']}/check",
                           params=CID).status_code == 429


def test_check_now_saves_synchronously():
    drive = Drive(episodes=3)
    drive.have = ["流浪地球.E01.mkv"]
    app, store, _, _ = make(drive)
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=BODY).json()
        import asyncio
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))
        r = client.post(f"/api/subscriptions/{sub['id']}/check", params=CID)
        kinds = [n["kind"] for n in r.json()["notifications"]]
        assert kinds == ["found", "auto_saved"]
        assert drive.saved[0]["fid_list"] == ["f2", "f3"]
        assert client.post("/api/subscriptions/999/check", params=CID).status_code == 404
        client.post("/api/quark/logout")
        assert client.post(f"/api/subscriptions/{sub['id']}/check",
                           params=CID).status_code == 401


async def test_subscribe_without_results_then_saved_when_found():
    """订阅时没搜到：之后出现资源就通知并自动转存（剧集）。"""
    from tests.test_subscriptions import setup

    s = shares("ep", 1)
    files: dict[str, list[str]] = {}
    store, tavily, watcher = setup(files, [])
    saved: list[str] = []

    async def saver(client_id, sub, link):
        saved.append(link.share)
        return [("auto_saved", "已转存", link.share)]

    watcher.auto_saver = saver
    sub = await store.add_subscription("c" * 8, "流浪地球2", "流浪地球2", (0, 0, None))
    sub.auto_save = True
    assert await watcher.check("c" * 8, sub) == [] and saved == []

    tavily.default = s
    files[s[0]] = eps(2)
    notes = await watcher.check("c" * 8, sub)
    assert [k for k, _, _ in notes] == ["found", "auto_saved"]
    assert "目前 2 集" in notes[0][1] and saved == [s[0]]


async def test_movie_saved_once_when_resolution_met_then_only_notify():
    """电影：清晰度不满足先不存；第一次满足时存一次；之后更高清只提醒不重复存。"""
    from tests.test_subscriptions import setup

    s = shares("mv", 1)
    files = {s[0]: ["流浪地球2.720p.mkv"]}
    store, _, watcher = setup(files, s)
    saved: list[str] = []

    async def saver(client_id, sub, link):
        saved.append(link.share)
        await store.log_auto_save(sub.id, link.share, True, 1, None, "已转存")
        return [("auto_saved", "已转存", link.share)]

    watcher.auto_saver = saver
    sub = await store.add_subscription("c" * 8, "流浪地球2 1080p", "流浪地球2", (0, 0, None))
    sub.auto_save = True
    notes = await watcher.check("c" * 8, sub)
    assert [k for k, _, _ in notes] == ["found"] and "目前" not in notes[0][1]
    assert saved == []  # 720p 不满足 1080p 要求

    files[s[0]] = ["流浪地球2.1080p.mkv"]
    notes = await watcher.check("c" * 8, sub)
    assert "auto_saved" in [k for k, _, _ in notes] and saved == [s[0]]

    files[s[0]] = ["流浪地球2.2160p.mkv"]
    notes = await watcher.check("c" * 8, sub)
    assert [k for k, _, _ in notes] == ["quality"] and saved == [s[0]]


def test_subscribe_with_auto_save():
    drive = Drive(episodes=3)
    drive.have = []
    app, _, _, _ = make(drive)
    with TestClient(app) as client:
        assert client.post("/api/subscriptions",
                           json={**BODY, "auto_save": True}).status_code == 401
        login(client)
        sub = client.post("/api/subscriptions", json={**BODY, "auto_save": True}).json()
        assert sub["auto_save"] is True
        import time
        for _ in range(100):
            if drive.saved:
                break
            time.sleep(0.02)
        assert drive.saved[0]["fid_list"] == ["f1", "f2", "f3"]
