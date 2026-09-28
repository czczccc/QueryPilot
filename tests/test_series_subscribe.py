"""订阅整个系列：每部一个电影订阅，整个系列只占 1 个名额；没上映的待定；新作自动加入（默认关）。"""

import time

from fastapi.testclient import TestClient

from app.models import CollectionInfo, CollectionPart
from app.services.memory import LinkStore
from tests.test_accounts import login
from tests.test_agent import shares
from tests.test_autosave import CID, Drive, make
from tests.test_card_status import Broken
from tests.test_subscribe_v2 import FakeLookup
from tests.test_subscriptions import eps, setup

C = "c" * 8


def parts(n: int, dated: int | None = None) -> list[CollectionPart]:
    """前 `dated` 部有上映日期（默认全有），之后的没定档。"""
    dated = n if dated is None else dated
    return [CollectionPart(index=i, id=str(100 + i), title=f"谍影重重{i}", year=str(2000 + i),
                           release_date=f"{2000 + i}-06-01" if i <= dated else None,
                           released=i <= dated)
            for i in range(1, n + 1)]


class SeriesLookup(FakeLookup):
    def __init__(self, info: CollectionInfo):
        super().__init__([])
        self.info = info
        self.fetches: list[tuple[str, bool]] = []

    async def collection(self, cid, fresh=False):
        self.fetches.append((cid, fresh))
        return self.info if cid == self.info.id else None


def series(n: int, dated: int | None = None) -> CollectionInfo:
    return CollectionInfo(id="31562", name="谍影重重", poster="https://p/c.jpg",
                          parts=parts(n, dated))


def test_subscribe_whole_series_api():
    lookup = SeriesLookup(series(3, dated=2))
    app, _, _, _ = make(Drive(), media_lookup=lookup)
    with TestClient(app) as client:
        login(client)
        # 已经单独订阅了第 2 部：整个系列时跳过它
        single = client.post("/api/subscriptions", json={
            **CID, "query": "谍影重重2", "resource": "谍影重重2", "media": "movie",
            "tmdb_id": "102", "collection_id": "31562", "collection_name": "谍影重重",
            "collection_index": 2}).json()
        assert single["collection_index"] == 2 and single["series"] is False

        got = client.post("/api/subscriptions/collection", json={
            **CID, "collection_id": "31562", "resolution": "1080p"}).json()
        assert [(s["collection_index"], s["state"]) for s in got] == [(1, "new"), (3, "pending")]
        first = got[0]
        assert first["series"] is True and first["media"] == "movie"
        assert first["tmdb_id"] == "101" and first["resolution"] == "1080p"
        assert first["collection_name"] == "谍影重重" and first["poster"] == "https://p/c.jpg"
        assert got[1]["release_date"] is None

        [coll] = client.get("/api/subscriptions/collections", params=CID).json()
        assert coll["auto_join"] is False and coll["subscriptions"] == 2

        edited = client.patch("/api/subscriptions/collection/31562", params=CID,
                              json={"auto_join": True}).json()
        assert edited["auto_join"] is True

        missing = client.post("/api/subscriptions/collection", json={
            **CID, "collection_id": "999"})
        assert missing.status_code == 404

        assert client.delete("/api/subscriptions/collection/31562", params=CID).json() == {
            "deleted": True}
        left = client.get("/api/subscriptions", params=CID).json()
        assert [s["resource"] for s in left] == ["谍影重重2"]  # 单独订阅的不受影响
        assert client.get("/api/subscriptions/collections", params=CID).json() == []


async def test_series_counts_as_one_slot():
    store = LinkStore(":memory:")
    for i in range(19):
        assert await store.add_subscription(C, f"片{i}", f"片{i}") is not None
    info = series(5)
    assert await store.add_collection(C, info.id, info.name, None, False, {}, [])
    for p in info.parts:  # 系列里的各部不再占名额
        assert await store.add_subscription(C, p.title, p.title, series=True,
                                            collection_id=info.id) is not None
    assert await store.add_subscription(C, "片x", "片x") is None  # 20 个满了
    assert not await store.add_collection(C, "2", "碟中谍", None, False, {}, [])
    assert await store.add_collection(C, info.id, info.name, None, True, {}, [])  # 已有的可以改

    # 系列里的都完成了、没开新作自动加入：系列不再占名额
    await store.set_collection(C, info.id, auto_join=False)
    for _, sub in await store.list_subscriptions(C):
        if sub.series:
            await store.archive_subscription(C, sub, "手动完成")
    assert await store.list_collections(C) == []
    assert await store.add_subscription(C, "片x", "片x") is not None


async def test_unreleased_part_is_not_searched():
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(1)}, s)
    watcher._agent = Broken(AssertionError("没上映不该搜索"))
    info = series(2, dated=1)
    sub = await watcher.add_part(C, info, info.parts[1], {})
    assert sub.state == "pending"
    assert await watcher.check(C, sub) == []
    [(_, now)] = await store.list_subscriptions(C)
    assert now.last_checked is not None and now.state == "pending"


async def test_auto_join_new_parts_and_date_refresh():
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(1)}, s)
    old = series(2, dated=1)
    watcher.lookup = SeriesLookup(old)
    assert await store.add_collection(C, old.id, old.name, None, True,
                                      {"auto_save": False, "resolution": "2160p"},
                                      [p.id for p in old.parts])
    for p in old.parts:
        await watcher.add_part(C, old, p, {"resolution": "2160p"})

    # 刚订阅时已经查过：一天内不再查
    assert await watcher.join_new_parts() == 0 and watcher.lookup.fetches == []
    # 第二天：TMDB 上第 2 部定档了，又出了第 3 部
    await store.set_collection(C, old.id, checked=time.time() - 2 * 86400)
    watcher.lookup.info = series(3)
    assert await watcher.join_new_parts() == 1
    subs = {x.collection_index: x for _, x in await store.list_subscriptions(C)}
    assert subs[2].release_date == "2002-06-01" and subs[2].state == "active"  # 上映了，开始搜
    assert subs[3].resolution == "2160p" and subs[3].series is True
    [note] = await store.notifications(C)
    assert note.kind == "series_new" and note.message == "《谍影重重》系列新增《谍影重重3》，已为你订阅"
    [coll] = await store.list_collections(C)
    assert coll["known"] == ["101", "102", "103"]

    # 一天内不重复查；删掉的那部不会被再加回来
    await store.delete_subscription(C, subs[3].id)
    assert await watcher.join_new_parts() == 0
    await store.set_collection(C, old.id, checked=time.time() - 2 * 86400)
    assert await watcher.join_new_parts() == 0
    assert len(await store.list_subscriptions(C)) == 2
    assert watcher.lookup.fetches == [("31562", True), ("31562", True)]
