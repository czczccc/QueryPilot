"""系列电影：候选取 10 条，同一 TMDB collection 的电影合并成一个候选，默认选中搜索词对上的那部。"""

import asyncio

import httpx
from fastapi.testclient import TestClient

from app.models import CollectionInfo, CollectionPart
from app.services.metadata import MediaInfo, MetadataLookup, collection_name, tmdb_collection
from app.services.series import build_candidates, default_part
from tests.test_autosave import Drive, make

BOURNE = [(2501, "谍影重重", "2002-06-14"), (2502, "谍影重重2", "2004-07-23"),
          (2503, "谍影重重3：最后通牒", "2007-08-03"), (49040, "谍影重重4", "2012-08-08"),
          (324668, "谍影重重5", "2016-07-27"), (999, "谍影重重6", None)]


def tmdb_handler(calls: list[str]):
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/search/multi"):
            return httpx.Response(200, json={"results": [
                {"id": 2501, "media_type": "movie", "title": "谍影重重"},
                {"id": 324668, "media_type": "movie", "title": "谍影重重5"},
                {"id": 77, "media_type": "movie", "title": "谍影重重 纪录片"},
            ]})
        if path.startswith("/3/collection/"):
            return httpx.Response(200, json={
                "id": 31562, "name": "谍影重重（系列）", "poster_path": "/c.jpg",
                "parts": [{"id": i, "title": t, "release_date": d or "", "poster_path": f"/{i}.jpg"}
                          for i, t, d in reversed(BOURNE)]})
        mid = int(path.rsplit("/", 1)[1])
        coll = {"id": 31562, "name": "谍影重重（系列）"} if mid != 77 else None
        title = {2501: "谍影重重", 324668: "谍影重重5", 77: "谍影重重 纪录片"}[mid]
        return httpx.Response(200, json={"id": mid, "title": title, "release_date": "2002-06-14",
                                         "belongs_to_collection": coll})

    return handler


def test_collection_parsing():
    calls: list[str] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(tmdb_handler(calls)))
    info = asyncio.run(tmdb_collection("31562", "k", client, today="2026-09-28"))
    assert info.name == "谍影重重" and info.poster.endswith("/c.jpg")
    assert [(p.index, p.id, p.year, p.released) for p in info.parts] == [
        (1, "2501", "2002", True), (2, "2502", "2004", True), (3, "2503", "2007", True),
        (4, "49040", "2012", True), (5, "324668", "2016", True), (6, "999", None, False)]
    assert collection_name("Mission: Impossible Collection") == "Mission: Impossible"
    assert collection_name("碟中谍系列") == "碟中谍" and collection_name("系列") == "系列"


def test_default_part():
    info = CollectionInfo(id="1", name="谍影重重", parts=[
        CollectionPart(index=i, id=str(i), title=t) for i, t in
        [(1, "谍影重重"), (3, "谍影重重3：最后通牒"), (5, "谍影重重5")]])
    assert default_part("谍影重重5", info) == 5
    assert default_part("谍影重重3", info) == 3
    assert default_part("谍影重重", info) == 1
    assert default_part("伯恩", info) == 1


def test_lookup_limit_and_collection_cache():
    calls: list[str] = []
    lookup = MetadataLookup(tmdb_key="k", douban=False,
                            client=httpx.AsyncClient(transport=httpx.MockTransport(
                                tmdb_handler(calls))))
    two = asyncio.run(lookup("谍影重重", None))
    ten = asyncio.run(lookup("谍影重重", None, limit=10))
    assert len(two) == 2 and len(ten) == 3  # 分类只取 2 条；选条目取到 10 条
    assert two[0].collection == {"id": "31562", "name": "谍影重重（系列）", "poster": None}
    asyncio.run(lookup.collection("31562"))
    asyncio.run(lookup.collection("31562"))
    assert calls.count("/3/collection/31562") == 1
    assert asyncio.run(MetadataLookup(douban=False).collection("31562")) is None


def test_build_candidates_merges_series_and_dedupes_douban():
    coll = {"id": "9", "name": "谍影重重（系列）", "poster": None}
    infos = [
        MediaInfo(source="tmdb", title="谍影重重 纪录片", year="2010", media="movie", id="77"),
        MediaInfo(source="tmdb", title="谍影重重", year="2002", media="movie", id="2501",
                  collection=coll),
        MediaInfo(source="tmdb", title="谍影重重5", year="2016", media="movie", id="324668",
                  collection=coll),
        MediaInfo(source="douban", title="谍影重重5", year="2016", media="movie", id="d5"),
        MediaInfo(source="douban", title="谍影重重之别的", year="2020", media="movie", id="d9"),
        MediaInfo(source="douban", title="不相干", year="2020", media=None, id="dx"),
    ]
    parts = [CollectionPart(index=i, id=str(i), title=t, year=y) for i, t, y in
             [(1, "谍影重重", "2002"), (2, "谍影重重2", "2004"), (5, "谍影重重5", "2016")]]
    fetched: list[str] = []

    async def fetch(cid):
        fetched.append(cid)
        return CollectionInfo(id=cid, name="谍影重重", parts=parts)

    got = asyncio.run(build_candidates("谍影重重5", infos, fetch))
    assert fetched == ["9"]
    assert [(c.kind, c.title, c.id) for c in got] == [
        ("collection", "谍影重重", "9"), ("movie", "谍影重重 纪录片", "77"),
        ("movie", "谍影重重之别的", "d9")]
    assert got[0].default_part == 5 and got[0].media == "movie" and got[0].year == "2002"
    assert len(got[0].collection.parts) == 3

    # 没有 TMDB key（拿不到系列）：照旧一部一个候选
    plain = asyncio.run(build_candidates("谍影重重", infos[:3], None))
    assert [c.kind for c in plain] == ["movie"] * 3


def test_media_search_api_returns_series():
    calls: list[str] = []
    lookup = MetadataLookup(tmdb_key="k", douban=False,
                            client=httpx.AsyncClient(transport=httpx.MockTransport(
                                tmdb_handler(calls))))
    app, _, _, _ = make(Drive(), media_lookup=lookup)
    with TestClient(app) as client:
        got = client.get("/api/media/search", params={"q": "谍影重重5"}).json()
    assert [c["kind"] for c in got] == ["collection", "movie"]
    series = got[0]
    assert series["title"] == "谍影重重" and series["default_part"] == 5
    assert [p["title"] for p in series["collection"]["parts"]][:3] == [
        "谍影重重", "谍影重重2", "谍影重重3：最后通牒"]
    assert got[1]["collection"] is None and got[1]["default_part"] is None


def test_douban_short_titles_and_tmdb_parts_without_collection_are_merged():
    coll = {"id": "9", "name": "碟中谍系列", "poster": None}
    parts = [CollectionPart(index=i, id=str(i), title=t, year=y) for i, t, y in
             [(4, "碟中谍4：幽灵协议", "2011"), (7, "碟中谍7：致命清算（上）", "2023"),
              (8, "碟中谍8：最终清算", "2025")]]
    infos = [
        MediaInfo(source="tmdb", title="碟中谍4：幽灵协议", year="2011", media="movie", id="4",
                  collection=coll),
        MediaInfo(source="tmdb", title="碟中谍8：最终清算", year="2025", media="movie", id="8"),
        MediaInfo(source="douban", title="碟中谍4", year="2011", media="movie", id="d4"),
        MediaInfo(source="douban", title="碟中谍7：致命清算", year="2023", media="movie", id="d7"),
        MediaInfo(source="douban", title="碟中谍4", year="2099", media="movie", id="dx"),
    ]

    async def fetch(cid):
        return CollectionInfo(id=cid, name="碟中谍", parts=parts)

    got = asyncio.run(build_candidates("碟中谍", infos, fetch))
    assert [(c.kind, c.id) for c in got] == [("collection", "9"), ("movie", "dx")]
