"""剧集一次订阅全部季：复用「订阅整个系列」，collection_id 为 tv:<TMDB id>；整部剧占 1 个名额，
没开播的季待定，「以后出新季自动加入」默认关。"""

import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from app.models import CollectionInfo, CollectionPart
from app.services.memory import LinkStore
from app.services.metadata import tmdb_tv_seasons
from tests.test_accounts import login
from tests.test_agent import shares
from tests.test_autosave import CID, Drive, make
from tests.test_series_subscribe import SeriesLookup
from tests.test_subscriptions import eps, setup

C = "c" * 8
NAME = "无职转生～到了异世界就拿出真本事～"


def show(n: int, aired: int | None = None) -> CollectionInfo:
    aired = n if aired is None else aired
    return CollectionInfo(
        id="tv:94664", name=NAME, poster="https://p/t.jpg", media="tv", tmdb_id="94664",
        year="2021", parts=[CollectionPart(
            index=i, id=f"s{i}", title=NAME if i == 1 else f"{NAME} 第{i}季",
            year=str(2020 + i) if i <= aired else None,
            release_date=f"{2020 + i}-07-01" if i <= aired else None,
            released=i <= aired, episodes=20 + i) for i in range(1, n + 1)])


def test_tmdb_tv_seasons_parsing():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/tv/94664"
        return httpx.Response(200, json={
            "id": 94664, "name": NAME, "first_air_date": "2021-01-11", "poster_path": "/a.jpg",
            "seasons": [
                {"season_number": 0, "episode_count": 3, "air_date": "2021-03-01"},
                {"season_number": 2, "episode_count": 25, "air_date": "2023-07-03"},
                {"season_number": 1, "episode_count": 23, "air_date": "2021-01-11"},
                {"season_number": 3, "episode_count": 0, "air_date": None},
            ]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    info = asyncio.run(tmdb_tv_seasons("94664", "k", client, today="2026-09-28"))
    assert info.id == "tv:94664" and info.media == "tv" and info.year == "2021"
    assert [(p.index, p.id, p.title, p.episodes, p.released) for p in info.parts] == [
        (1, "s1", NAME, 23, True), (2, "s2", f"{NAME} 第2季", 25, True),
        (3, "s3", f"{NAME} 第3季", None, False)]


def test_subscribe_all_seasons_api():
    app, _, _, _ = make(Drive(), media_lookup=SeriesLookup(show(3, aired=2)))
    with TestClient(app) as client:
        login(client)
        # 第 2 季已经单独订阅过：跳过
        client.post("/api/subscriptions", json={
            **CID, "query": NAME, "resource": NAME, "media": "tv", "season": 2,
            "tmdb_id": "94664"})
        got = client.post("/api/subscriptions/collection", json={
            **CID, "collection_id": "tv:94664", "resolution": "1080p"}).json()
        assert [(s["season"], s["state"]) for s in got] == [(1, "new"), (3, "pending")]
        first = got[0]
        assert first["media"] == "tv" and first["tmdb_id"] == "94664" and first["series"] is True
        assert first["year"] == "2021" and first["season_year"] == "2021"
        assert first["total_episodes"] == 21 and first["resolution"] == "1080p"
        assert first["collection_id"] == "tv:94664" and first["collection_index"] == 1
        assert got[1]["resource"] == f"{NAME} 第3季"
        [coll] = client.get("/api/subscriptions/collections", params=CID).json()
        assert coll["name"] == NAME and coll["subscriptions"] == 2 and coll["auto_join"] is False


async def test_whole_show_is_one_slot_and_new_season_joins():
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(1)}, s)
    old = show(2)
    watcher.lookup = SeriesLookup(old)
    for i in range(19):
        await store.add_subscription(C, f"片{i}", f"片{i}")
    assert await store.add_collection(C, old.id, old.name, None, True, {},
                                      [p.id for p in old.parts])
    for p in old.parts:
        assert await watcher.add_part(C, old, p, {}) is not None
    assert await store.add_subscription(C, "片x", "片x") is None  # 19 + 整部剧 1 = 20

    watcher.lookup.info = show(3, aired=2)  # 宣布了第 3 季，还没开播
    await store.set_collection(C, old.id, checked=time.time() - 2 * 86400)
    assert await watcher.join_new_parts() == 1
    new = [x for _, x in await store.list_subscriptions(C) if x.season == 3]
    assert new[0].state == "pending" and new[0].total_episodes == 23
    [note] = await store.notifications(C)
    assert note.message == f"《{NAME}》出了第 3 季，已为你订阅"


async def test_history_season_is_skipped():
    store = LinkStore(":memory:")
    sub = await store.add_subscription(C, NAME, NAME, media="tv", season=1, tmdb_id="94664")
    await store.archive_subscription(C, sub, "已集齐")
    assert await store.history_tmdb_ids(C) == {"94664:1"}
