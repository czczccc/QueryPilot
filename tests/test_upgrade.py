"""洗版：已存的集清晰度没到目标前，出现更高清的就再存一份新版本（旧版本不删，整理时确认删除）。"""

import asyncio

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Subscription
from app.services.agent import SearchAgent
from app.services.cookie_box import CookieBox
from app.services.memory import LinkStore
from app.services.organize import plan_tidy, versioned_name
from tests.test_accounts import FakeLogin, login
from tests.test_agent import ScriptedTavily, _public_dns, make_service, shares  # noqa: F401
from tests.test_organize import OWNER, Drive, F, FixedClassifier
from tests.test_subscriptions import episodes_client

CID = {"client_id": "browser-0001"}
TARGET = "/QueryPilot/电视剧/国产剧/流浪地球2 (2025)/Season 01"


def names(res: str) -> list[str]:
    return [f"流浪地球2.E{i:02d}.{res}.mkv" for i in range(1, 4)]


def test_upgradable_episodes():
    sub = Subscription(id=1, query="q", resource="r", created=0, media="tv",
                       total_episodes=3, saved_episodes=[1, 2, 3],
                       versions={1: "2160p", 2: "1080p"})
    assert sub.upgradable(False) == {} and sub.upgrade_done is None  # 没开洗版
    sub.upgrade = True
    assert sub.upgradable(False) == {2: 3, 3: 0}  # 认不出清晰度的按最低算
    assert sub.upgrade_done is False
    sub.upgrade_to = "1080p"
    assert sub.upgradable(False) == {3: 0}
    sub.versions[3] = "1080p"
    assert sub.upgrade_done is True
    movie = Subscription(id=2, query="q", resource="r", created=0, media="movie",
                         upgrade=True, versions={0: "1080p"})
    assert movie.upgradable(True) == {0: 3} and movie.upgrade_done is False


def test_plan_keeps_upgraded_version_and_suggests_old_one():
    entries = [
        {"fid": "old", "file_name": "流浪地球2 S01E01.mkv", "folder": TARGET, "size": 1},
        {"fid": "new", "file_name": "流浪地球2 S01E01 2160p.mkv", "folder": TARGET, "size": 2},
    ]
    plan = plan_tidy(entries, TARGET, "流浪地球2", False, 1, "2025")
    assert plan.moves == []  # 带清晰度的新版本名已经是标准名
    assert [(d["fid"], d["reason"]) for d in plan.deletes] == [("old", "重复版本，已保留更好的")]
    assert versioned_name("流浪地球2 S01E01.mkv", "2160p") == "流浪地球2 S01E01 2160p.mkv"


def test_tv_upgrade_saves_better_version_then_completes():
    drive = Drive()
    drive.share = {"0": [F(n) for n in names("1080p")]}
    store = LinkStore(":memory:")
    s = shares("ep", 1)
    verify = {s[0]: names("1080p")}  # 搜索验证时看到的分享文件
    service = make_service(ScriptedTavily({}, default=s), client=episodes_client(verify),
                           store=store)
    app = create_app(
        service=service, agent=SearchAgent(service), rate_limit_per_minute=1000,
        qr_login=FakeLogin(), cookie_box=CookieBox.from_secret("k"),
        quark_client=httpx.AsyncClient(transport=httpx.MockTransport(drive.handler)),
        classifier=FixedClassifier(),
    )
    body = {**CID, "query": "流浪地球2", "resource": "流浪地球2", "media": "tv",
            "year": "2025", "total_episodes": 3, "upgrade": True}
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=body).json()
        assert sub["upgrade"] is True and sub["upgrade_to"] is None
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))

        async def check():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            return await app.state.watcher.check(owner, obj, sync_save=True)

        # 第一次：存 1080p，记下各集清晰度；还没到 4K，订阅不完成
        notes = asyncio.run(check())
        assert "auto_saved" in [k for k, _, _ in notes]
        [(_, now)] = asyncio.run(store.list_subscriptions(OWNER))
        assert now.versions == {1: "1080p", 2: "1080p", 3: "1080p"}
        assert now.lack_episodes == [] and now.upgrade_done is False

        # 再查一次、没有更高清的：不重复存
        asyncio.run(check())
        assert len(drive.saves) == 1

        # 出现 4K：三集都再存一份，新版本带清晰度命名，旧文件不删；达到目标后订阅完成
        verify[s[0]] = names("2160p")
        drive.share = {"0": [F(n) for n in names("2160p")]}
        notes = asyncio.run(check())
        kinds = [k for k, _, _ in notes]
        assert "upgraded" in kinds and "completed" in kinds
        assert len(drive.saves) == 2 and drive.saves[1]["fid_list"] == names("2160p")
        assert drive.deletes == []
        folder = drive.files[drive.dirs[TARGET]]
        assert sorted(f["file_name"] for f in folder) == sorted([
            *(f"流浪地球2 S01E{i:02d} 2160p.mkv" for i in range(1, 4)),
            *(f"流浪地球2 S01E{i:02d}.mkv" for i in range(1, 4))])
        assert asyncio.run(store.list_subscriptions(OWNER)) == []
        [hist] = client.get("/api/subscriptions/history", params=CID).json()
        assert hist["reason"] == "已集齐 3 集，全部达到 4K"


def test_edit_upgrade_settings():
    drive = Drive()
    store = LinkStore(":memory:")
    service = make_service(ScriptedTavily({}, default=shares("ep", 1)),
                           client=episodes_client({}), store=store)
    app = create_app(
        service=service, agent=SearchAgent(service), rate_limit_per_minute=1000,
        qr_login=FakeLogin(), cookie_box=CookieBox.from_secret("k"),
        quark_client=httpx.AsyncClient(transport=httpx.MockTransport(drive.handler)),
    )
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json={
            **CID, "query": "流浪地球2", "resource": "流浪地球2", "media": "movie"}).json()
        assert sub["upgrade"] is False and sub["upgrade_done"] is None
        got = client.patch(f"/api/subscriptions/{sub['id']}", params=CID,
                           json={"upgrade": True, "upgrade_to": "1080p"}).json()
        assert got["upgrade"] is True and got["upgrade_to"] == "1080p"
        assert got["upgrade_done"] is False and got["versions"] == {}
        got = client.patch(f"/api/subscriptions/{sub['id']}", params=CID,
                           json={"upgrade_to": ""}).json()
        assert got["upgrade_to"] is None


def test_movie_upgrade_then_completes():
    drive = Drive()
    drive.share = {"0": [F("流浪地球2.2023.1080p.mkv")]}
    store = LinkStore(":memory:")
    s = shares("ep", 1)
    verify = {s[0]: ["流浪地球2.2023.1080p.mkv"]}
    service = make_service(ScriptedTavily({}, default=s), client=episodes_client(verify),
                           store=store)
    app = create_app(
        service=service, agent=SearchAgent(service), rate_limit_per_minute=1000,
        qr_login=FakeLogin(), cookie_box=CookieBox.from_secret("k"),
        quark_client=httpx.AsyncClient(transport=httpx.MockTransport(drive.handler)),
        classifier=FixedClassifier(),
    )
    body = {**CID, "query": "流浪地球2", "resource": "流浪地球2", "media": "movie",
            "year": "2023", "upgrade": True}
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=body).json()
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))

        async def check():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            return await app.state.watcher.check(owner, obj)

        asyncio.run(check())
        [(_, now)] = asyncio.run(store.list_subscriptions(OWNER))
        assert now.versions == {0: "1080p"} and len(drive.saves) == 1
        asyncio.run(check())  # 没有更高清的：不重复存
        assert len(drive.saves) == 1

        verify[s[0]] = ["流浪地球2.2023.2160p.mkv"]
        drive.share = {"0": [F("流浪地球2.2023.2160p.mkv")]}
        kinds = [k for k, _, _ in asyncio.run(check())]
        assert "upgraded" in kinds and "completed" in kinds and len(drive.saves) == 2
        [hist] = client.get("/api/subscriptions/history", params=CID).json()
        assert hist["reason"] == "已洗版到 4K"
