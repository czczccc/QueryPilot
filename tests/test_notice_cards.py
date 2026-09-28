"""通知卡片的结构化字段、按 ids 标已读、POST /api/subscriptions/seasons 与 show_id 分组。"""

import asyncio

from fastapi.testclient import TestClient

from app.models import QualityInfo, QuarkLink
from app.services.memory import LinkStore
from tests.test_accounts import login
from tests.test_autosave import CID, Drive, make
from tests.test_series_subscribe import SeriesLookup
from tests.test_tv_seasons import NAME, show

C = "c" * 8


async def test_structured_fields():
    store = LinkStore(":memory:")
    await store.save("无职转生", [QuarkLink(
        name="无职转生 第三季 1080P", share="abc123", source="pansou", time="", state="valid",
        share_title="【动漫】无职转生Ⅲ", quality=QualityInfo())])
    sub = await store.add_subscription(C, "无职转生", "无职转生 第3季", media="tv", season=3)
    await store.update_subscription(C, sub, [("maybe", "《无职转生 第3季》找到一个……", "abc123")])
    movie = await store.add_subscription(C, "谍影重重3", "谍影重重3", media="movie",
                                         collection_id="9", collection_index=3, series=True)
    await store.update_subscription(C, movie, [("auto_saved", "已转存", None)])
    notes = {n.kind: n for n in await store.notifications(C)}
    n = notes["maybe"]
    assert (n.type, n.summary, n.subscription_name, n.season, n.part) == (
        "maybe", "找到可能相关的资源，请自己核对", "无职转生", 3, None)
    assert n.resource_title == "【动漫】无职转生Ⅲ" and n.url == "https://pan.quark.cn/s/abc123"
    assert n.created_at.endswith("+08:00") and n.message.startswith("《无职转生")
    saved = notes["auto_saved"]
    assert (saved.type, saved.part, saved.season, saved.url) == ("saved", 3, None, None)


def test_read_by_ids_and_seasons_endpoint():
    app, store, _, _ = make(Drive(), media_lookup=SeriesLookup(show(2)))
    with TestClient(app) as client:
        login(client)
        got = client.post("/api/subscriptions/seasons", json={
            **CID, "query": NAME, "resource": NAME, "media": "tv", "tmdb_id": "94664",
            "auto_join": True, "resolution": "1080p"}).json()
        assert [s["season"] for s in got] == [1, 2]
        assert all(s["show_id"] == "94664" and s["all_seasons"] is True for s in got)
        [coll] = client.get("/api/subscriptions/collections", params=CID).json()
        assert coll["collection_id"] == "tv:94664" and coll["auto_join"] is True
        single = client.post("/api/subscriptions", json={
            **CID, "query": "三体", "resource": "三体", "media": "tv"}).json()
        assert single["show_id"] is None and single["all_seasons"] is False

        [(owner, a), (_, b), *_] = asyncio.run(store.list_subscriptions())
        asyncio.run(store.update_subscription(owner, a, [("found", "甲", "s1")]))
        asyncio.run(store.update_subscription(owner, b, [("found", "乙", "s2")]))
        ids = [n["id"] for n in client.get("/api/notifications", params=CID).json()]
        assert len(ids) == 2
        client.post("/api/notifications/read", params=CID, json={"ids": [ids[0]]})
        assert [n["id"] for n in client.get("/api/notifications", params=CID).json()] == ids[1:]
        client.post("/api/notifications/read", params=CID)  # 空 body：全部已读
        assert client.get("/api/notifications", params=CID).json() == []
