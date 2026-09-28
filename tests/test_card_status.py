"""订阅卡片：显示这一季的开播年份；检查失败时记下原因，不再一直「首次搜索中」。"""

import asyncio
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from app.models import AirEpisode
from app.services.metadata import search_tmdb
from app.services.subscriptions import check_error_text
from tests.test_accounts import login
from tests.test_agent import shares
from tests.test_autosave import BODY, Drive, make
from tests.test_calendar import CalendarLookup
from tests.test_subscribe_v2 import TV
from tests.test_subscriptions import eps, setup

C = "c" * 8


def test_tmdb_season_years():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search/multi"):
            return httpx.Response(200, json={"results": [
                {"id": 7, "media_type": "tv", "name": "无职转生", "first_air_date": "2021-01-11"}]})
        return httpx.Response(200, json={
            "id": 7, "name": "无职转生", "first_air_date": "2021-01-11", "seasons": [
                {"season_number": 0, "episode_count": 3, "air_date": "2021-03-01"},
                {"season_number": 1, "episode_count": 23, "air_date": "2021-01-11"},
                {"season_number": 2, "episode_count": 25, "air_date": "2023-07-03"},
                {"season_number": 3, "episode_count": 14, "air_date": None},
            ]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    [info] = asyncio.run(search_tmdb("无职转生", None, "k", client))
    assert info.year == "2021" and info.season_years == {1: "2021", 2: "2023"}


def test_subscribe_records_season_year():
    tv = replace(TV, season_years={1: "2023", 2: "2025"})
    app, _, _, _ = make(Drive(), media_lookup=CalendarLookup([tv], []))
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json={**BODY, "query": "流浪地球 第二季"}).json()
        # 目录、相关性仍按剧集年份；卡片用这一季的年份
        assert sub["season"] == 2 and sub["year"] == "2023" and sub["season_year"] == "2025"
        assert sub["last_error"] is None


async def test_refresh_fills_season_year_from_schedule():
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(2)}, s)
    watcher.lookup = CalendarLookup([TV], [AirEpisode(episode=1, air_date="2026-04-05"),
                                           AirEpisode(episode=2, air_date="2026-04-12")])
    sub = await store.add_subscription(C, "流浪地球", "流浪地球", media="tv", season=2,
                                       year="2023", tmdb_id="42")
    await watcher.check(C, sub)
    [(_, now)] = await store.list_subscriptions(C)
    assert now.season_year == "2026" and now.year == "2023"


class Broken:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def run(self, *_, **__):
        raise self.exc


async def test_failed_check_records_error_then_clears():
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(2)}, s)
    agent = watcher._agent
    sub = await store.add_subscription(C, "流浪地球", "流浪地球", state="new")
    watcher._agent = Broken(httpx.ConnectError("boom http://secret-host"))
    await watcher.run_once()
    [(_, now)] = await store.list_subscriptions(C)
    assert now.last_error == "搜索服务暂时不可用，稍后会自动重试"
    assert now.last_checked is not None and now.state != "new"

    watcher._agent = agent
    await watcher.run_once()
    [(_, now)] = await store.list_subscriptions(C)
    assert now.last_error is None and sub.id == now.id


def test_error_text_hides_details():
    assert check_error_text(httpx.ReadTimeout("x")) == "搜索服务响应超时，稍后会自动重试"
    assert "key" not in check_error_text(ValueError("api key abc"))
