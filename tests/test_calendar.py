"""追剧日历：TMDB 每集播出日期、每集状态（已存 / 有资源 / 播了没资源 / 未播）、日历接口。"""

import asyncio
from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient

from app.models import AirEpisode, Subscription
from app.services import calendar as cal
from app.services.metadata import MetadataLookup, tmdb_season
from tests.test_accounts import login
from tests.test_agent import shares
from tests.test_autosave import BODY, CID, Drive, make
from tests.test_subscribe_v2 import TV, FakeLookup
from tests.test_subscriptions import eps, setup

C = "c" * 8
D = date(2026, 9, 27)


def day(offset: int) -> str:
    return (D + timedelta(days=offset)).isoformat()


def test_tmdb_season_parsing():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"episodes": [
            {"episode_number": 1, "air_date": "2026-09-01", "name": "第1集"},
            {"episode_number": 2, "air_date": "", "name": None},
            {"episode_number": "x"},
        ]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    got = asyncio.run(tmdb_season("42", 2, "k", client))
    assert got == [AirEpisode(episode=1, air_date="2026-09-01", name="第1集"),
                   AirEpisode(episode=2)]
    assert seen[0][0] == "/3/tv/42/season/2" and seen[0][1]["api_key"] == "k"
    assert asyncio.run(MetadataLookup(tmdb_key="", douban=False).schedule("42", 1)) == []


def test_episode_status():
    sub = Subscription(id=1, query="q", resource="流浪地球", created=0, media="tv",
                       start_episode=2, saved_episodes=[2], best_episodes=3, schedule=[
                           AirEpisode(episode=i, air_date=day(i - 4)) for i in range(1, 7)
                       ] + [AirEpisode(episode=7)])
    got = cal.build_calendar([sub], D - timedelta(days=7), D + timedelta(days=30), D)
    # 第 1 集在起始集之前不列；没有日期的第 7 集不列
    assert [(e["episode"], e["status"]) for e in got] == [
        (2, "saved"), (3, "available"), (4, "no_resource"), (5, "upcoming"), (6, "upcoming")]
    movie = Subscription(id=2, query="q", resource="m", created=0, media="movie",
                         schedule=[AirEpisode(episode=1, air_date=day(0))])
    assert cal.build_calendar([movie], D, D, D) == []


class CalendarLookup(FakeLookup):
    def __init__(self, infos, schedule):
        super().__init__(infos)
        self.eps = schedule
        self.schedule_calls: list[tuple] = []

    async def schedule(self, tmdb_id, season):
        self.schedule_calls.append((tmdb_id, season))
        return self.eps


async def test_watcher_refreshes_schedule_and_total():
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(2)}, s)
    watcher.lookup = CalendarLookup([TV], [AirEpisode(episode=i, air_date=day(i))
                                           for i in range(1, 11)])
    tv = await store.add_subscription(C, "流浪地球", "流浪地球", media="tv", season=2,
                                      tmdb_id="42")
    await watcher.check(C, tv)
    [(_, now)] = await store.list_subscriptions(C)
    assert watcher.lookup.schedule_calls == [("42", 2)]
    assert len(now.schedule) == 10 and now.total_episodes == 10  # 日历里的集数比 TMDB 季信息多
    assert "schedule" not in now.model_dump()  # 订阅接口不带日历


def test_calendar_api():
    today = cal.today()
    lookup = CalendarLookup([TV], [
        AirEpisode(episode=1, air_date=(today - timedelta(days=3)).isoformat(), name="开播"),
        AirEpisode(episode=2, air_date=(today + timedelta(days=4)).isoformat()),
        AirEpisode(episode=3, air_date=(today + timedelta(days=60)).isoformat()),
    ])
    app, _, _, _ = make(Drive(), media_lookup=lookup)
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json={**BODY, "query": "流浪地球 第二季"}).json()
        got = client.get("/api/calendar", params=CID).json()
        assert got["today"] == today.isoformat()
        assert [(e["episode"], e["status"], e["subscription_id"]) for e in got["episodes"]] == [
            (1, "no_resource", sub["id"]), (2, "upcoming", sub["id"])]
        assert got["episodes"][0]["name"] == "开播" and got["episodes"][0]["season"] == 2
        # 已经存下日历：再查不重复请求 TMDB
        client.get("/api/calendar", params=CID)
        assert lookup.schedule_calls == [("42", 2)]
        far = client.get("/api/calendar", params={
            **CID, "start": today.isoformat(),
            "end": (today + timedelta(days=90)).isoformat()}).json()
        assert [e["episode"] for e in far["episodes"]] == [2, 3]
        assert client.get("/api/calendar", params={
            **CID, "subscription_id": 999}).json()["episodes"] == []
        bad = client.get("/api/calendar", params={
            **CID, "start": "2026-01-01", "end": "2026-12-31"})
        assert bad.status_code == 400
