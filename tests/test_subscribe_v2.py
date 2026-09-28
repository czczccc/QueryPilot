"""订阅 v2：以影视条目为订阅对象、按季与集数范围、缺集、状态、完成归档与重新订阅。"""

import asyncio

from fastapi.testclient import TestClient

from app.models import Subscription
from app.services.metadata import MediaInfo
from app.services.subscriptions import passes_filters, strip_season
from tests.test_accounts import login
from tests.test_agent import shares
from tests.test_autosave import BODY, CID, OWNER, Drive, make
from tests.test_subscriptions import eps, setup

C = "c" * 8


class FakeLookup:
    def __init__(self, infos: list[MediaInfo]):
        self.infos = infos
        self.calls: list[tuple] = []

    async def __call__(self, name, year, fresh=False, limit=2):
        self.calls.append((name, year, fresh))
        return self.infos


TV = MediaInfo(source="tmdb", title="流浪地球", year="2023", media="tv", id="42",
               poster="https://image.tmdb.org/t/p/w342/a.jpg", episodes={1: 3, 2: 8})


def test_lack_episodes_and_state():
    sub = Subscription(id=1, query="q", resource="r", created=0, media="tv", season=1,
                       total_episodes=5, start_episode=2, saved_episodes=[1, 2, 4])
    assert sub.wanted == [2, 3, 4, 5] and sub.lack_episodes == [3, 5]
    assert Subscription(id=1, query="q", resource="r", created=0).lack_episodes is None
    assert strip_season("漫长的季节 第2季") == "漫长的季节"


def test_filters():
    from app.models import QuarkLink
    lk = QuarkLink(name="流浪地球 4K 国语", share="abc123", source="t", time="",
                   files_preview=["E01.mkv"])
    assert passes_filters(lk, "4k", None) and not passes_filters(lk, "1080p", None)
    assert not passes_filters(lk, None, "国语 粤语") and passes_filters(lk, None, "粤语")


async def test_tv_completes_when_all_saved_and_moves_to_history():
    s = shares("ep", 1)
    files = {s[0]: eps(2)}
    store, _, watcher = setup(files, s)

    async def saver(client_id, sub, link, wanted=None):
        have = set(range(1, link.quality.video_count + 1))
        got = sorted(have & wanted) if wanted is not None else sorted(have)
        sub.saved_episodes = sorted(set(sub.saved_episodes) | set(got))
        return [("auto_saved", f"存了 {got}", link.share)] if got else []

    watcher.auto_saver = saver
    sub = await store.add_subscription(C, "流浪地球2", "流浪地球2", (0, 0, None),
                                       media="tv", season=1, total_episodes=3, auto_save=True)
    assert sub.state == "active" and sub.lack_episodes == [1, 2, 3]
    notes = await watcher.check(C, sub)
    assert [k for k, _, _ in notes] == ["found", "auto_saved"]
    [(_, now)] = await store.list_subscriptions(C)
    assert now.saved_episodes == [1, 2] and now.lack_episodes == [3]

    files[s[0]] = eps(3)
    notes = await watcher.check(C, now)
    assert [k for k, _, _ in notes] == ["episodes", "auto_saved", "completed"]
    assert "已集齐 3 集" in notes[-1][1]
    assert await store.list_subscriptions(C) == []
    [hist] = await store.subscription_history(C)
    assert hist.reason == "已集齐 3 集" and hist.saved_count == 3 and hist.total_episodes == 3


async def test_tv_without_auto_save_completes_when_full_season_available():
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(3)}, s)
    sub = await store.add_subscription(C, "流浪地球2", "流浪地球2", (0, 0, None),
                                       media="tv", season=1, total_episodes=3)
    notes = await watcher.check(C, sub)
    assert [k for k, _, _ in notes] == ["found", "completed"]
    assert (await store.subscription_history(C))[0].reason == "全 3 集资源已出齐"


async def test_paused_and_pending_and_meta_refresh():
    s = shares("ep", 1)
    store, tavily, watcher = setup({s[0]: eps(2)}, s)
    sub = await store.add_subscription(C, "流浪地球2", "流浪地球2")
    assert sub.state == "pending"  # 没识别出条目：能搜不能自动完成
    paused = await store.edit_subscription(C, sub.id, state="paused")
    assert paused.state == "paused"
    assert await watcher.check(C, paused) == [] and tavily.queries == []
    assert await watcher.run_once() == 0 and tavily.queries == []

    lookup = FakeLookup([TV])
    watcher.lookup = lookup
    tv = await store.add_subscription(C, "流浪地球", "流浪地球", media="tv", season=2,
                                      tmdb_id="42", total_episodes=5)
    await watcher.check(C, tv)
    assert lookup.calls == [("流浪地球", None, True)]
    [(_, now)] = [x for x in await store.list_subscriptions(C) if x[1].id == tv.id]
    assert now.total_episodes == 8  # TMDB 第 2 季 8 集
    await watcher.check(C, now)
    assert len(lookup.calls) == 1  # 一天只刷新一次

    manual = await store.edit_subscription(C, tv.id, total_episodes=4, manual_total=True)
    assert manual.total_episodes == 4 and manual.manual_total


def test_api_identify_edit_complete_resubscribe():
    app, _, _, _ = make(Drive())
    with TestClient(app) as client:  # 没有条目识别：条目搜索返回空列表
        assert client.get("/api/media/search", params={"q": "流浪地球"}).json() == []
    lookup = FakeLookup([TV])
    app, _, _, _ = make(Drive(), media_lookup=lookup)
    with TestClient(app) as client:
        [cand] = client.get("/api/media/search", params={"q": "流浪地球 第二季"}).json()
        assert cand["id"] == "42" and cand["episodes"] == {"1": 3, "2": 8}
        login(client)
        sub = client.post("/api/subscriptions", json={
            **BODY, "query": "流浪地球 第二季", "resolution": "1080p", "exclude": "枪版"}).json()
        assert (sub["media"], sub["season"], sub["total_episodes"], sub["tmdb_id"]) == (
            "tv", 2, 8, "42")
        assert sub["resource"] == "流浪地球 第2季" and sub["state"] == "new"
        assert sub["lack_episodes"] == list(range(1, 9)) and sub["exclude"] == "枪版"

        r = client.patch(f"/api/subscriptions/{sub['id']}", params=CID, json={
            "total_episodes": 10, "start_episode": 3, "paused": True, "resolution": ""})
        got = r.json()
        assert (got["total_episodes"], got["manual_total"], got["start_episode"]) == (10, True, 3)
        assert got["state"] == "paused" and got["resolution"] is None
        assert client.get("/api/subscriptions", params={**CID, "media": "movie"}).json() == []

        h = client.post(f"/api/subscriptions/{sub['id']}/complete", params=CID).json()
        assert h["reason"] == "手动完成" and h["season"] == 2
        assert client.get("/api/subscriptions", params=CID).json() == []
        [hist] = client.get("/api/subscriptions/history", params=CID).json()
        again = client.post(f"/api/subscriptions/history/{hist['id']}/resubscribe",
                            params=CID).json()
        assert (again["season"], again["total_episodes"], again["exclude"]) == (2, 10, "枪版")
        assert client.get("/api/subscriptions/history", params=CID).json() == []
        assert client.delete("/api/subscriptions/history/999", params=CID).status_code == 404


def test_auto_save_only_wanted_episodes_and_counts_drive():
    """订阅范围 2~3 集，网盘已有第 1 集：只存 2、3 集，转存后按目录清点，集齐即完成。"""
    drive = Drive(episodes=3)
    app, store, _, _ = make(drive)
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json={
            **BODY, "media": "tv", "total_episodes": 3, "start_episode": 2}).json()
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))

        async def check():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            return await app.state.watcher.check(owner, obj)

        notes = asyncio.run(check())
        assert [k for k, _, _ in notes] == ["found", "auto_saved", "completed"]
        assert drive.saved[0]["fid_list"] == ["f2", "f3"]
        [hist] = client.get("/api/subscriptions/history", params=CID).json()
        assert hist["saved_count"] == 3 and hist["reason"] == "已集齐 2 集"
