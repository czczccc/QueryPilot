"""成组订阅统一挑资源：谍影重重 1~3 部只搜到 1、2 部和一个 1-5 合集时，第 3 部从合集里取；
都缺时一个合集补齐全部；剧集全季包优先；AI 挑选并给理由，额度用完按规则挑。"""

import asyncio
import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from app.models import QualityInfo, QuarkLink
from app.services import llm
from app.services.memory import LinkStore
from app.services.organize import pick_files
from app.services.packs import PackChooser, covered, parts_in, rule_pick
from app.services.subscriptions import SubscriptionWatcher
from tests.test_accounts import login
from tests.test_autosave import CID, OWNER, Drive, make

C = OWNER
TITLES = {1: "谍影重重", 2: "谍影重重2", 3: "谍影重重3：最后通牒"}


def link(share: str, title: str, files=(), res: str = "1080p", n: int = 1) -> QuarkLink:
    return QuarkLink(name=title, share=share, source="t", time="", state="valid",
                     share_title=title, files_preview=list(files), relevance="match",
                     quality=QualityInfo(resolution=res, video_count=n, score=10 * n))


BOURNE_LINKS = [
    link("one000001", "谍影重重 2002 1080p"),
    link("two000002", "谍影重重2 4K", res="2160p"),
    link("pack00005", "谍影重重1-5合集 1080P", ["谍影重重1", "谍影重重2", "谍影重重3"], n=5),
    link("other0001", "碟中谍 1-6 合集", n=6),
]


class FakeAgent:
    def __init__(self, links: list[QuarkLink]):
        self.links = links
        self.queries: list[str] = []

    async def run(self, req, fresh_hours=None):
        self.queries.append(req.query)
        return SimpleNamespace(links=[lk.model_copy(deep=True) for lk in self.links],
                               required_resolution=None)


def watcher_with(links: list[QuarkLink]):
    store = LinkStore(":memory:")
    agent = FakeAgent(links)
    watcher = SubscriptionWatcher(agent, store)
    calls: list[tuple] = []

    async def saver(owner, sub, lk, wanted=None, note=None):
        calls.append((sub.collection_index or sub.season, lk.share, wanted, note))
        await store.log_auto_save(sub.id, lk.share, True, 1, None, "存了")
        if wanted:
            sub.saved_episodes = sorted(set(sub.saved_episodes) | wanted)
        return [("auto_saved", f"存了《{sub.resource}》", lk.share)]

    watcher.auto_saver = saver
    return store, agent, watcher, calls


async def bourne(store: LinkStore, n: int = 3):
    subs = []
    for i in range(1, n + 1):
        subs.append(await store.add_subscription(
            C, TITLES[i], TITLES[i], media="movie", tmdb_id=str(2500 + i), year=str(2000 + i),
            collection_id="31562", collection_name="谍影重重", collection_index=i, series=True,
            release_date=f"{2000 + i}-06-01", auto_save=True))
    return subs


def test_parts_and_coverage():
    assert parts_in("谍影重重1-5合集 1080P", "谍影重重", TITLES) == {1, 2, 3, 4, 5}
    assert parts_in("谍影重重 2002 1080p", "谍影重重", TITLES) == {1}
    assert parts_in("谍影重重2 4K", "谍影重重", TITLES) == {2}
    assert parts_in("最后通牒 BluRay", "谍影重重", TITLES) == {3}
    assert parts_in("【谍影重重系列】全5部", "谍影重重", TITLES) == {1, 2, 3, 4, 5}
    assert parts_in("谍影重重三部曲", "谍影重重", TITLES) == {1, 2, 3}
    assert covered(BOURNE_LINKS[3], "谍影重重", False, TITLES) == set()  # 不是这个系列
    tv = link("s", "无职转生 S01-S03 全季 1080p", n=60)
    assert covered(tv, "无职转生", True) == {1, 2, 3}
    assert covered(link("x", "别的剧 S01-S03"), "无职转生", True) == set()


async def test_bourne_part_three_comes_from_the_pack():
    """Jay 的场景：订阅了 1~3 部，1、2 部已经各自存好，只搜到 1、2 部单独的分享和一个 1-5 合集。"""
    store, agent, watcher, calls = watcher_with(BOURNE_LINKS)
    one, two, three = await bourne(store)
    await store.log_auto_save(one.id, "one000001", True, 1, None, "存了")
    await store.log_auto_save(two.id, "two000002", True, 1, None, "存了")

    notes = await watcher.check(C, three)
    assert agent.queries[0] == "谍影重重"  # 整组按系列名搜一次
    assert [(c[0], c[1]) for c in calls] == [(3, "pack00005")]  # 只给第 3 部存，从合集里取
    assert "第 3 部" in calls[0][3] and "按规则挑选" in calls[0][3]
    assert notes[0] == ("auto_saved", "存了《谍影重重3：最后通牒》", "pack00005")

    # 3 小时内同一组不再统一挑；第 3 部已经存过，单独检查也不会再存
    await watcher.check(C, three)
    assert len(calls) == 1


async def test_one_pack_fills_every_missing_part():
    store, _, watcher, calls = watcher_with(BOURNE_LINKS)
    one, two, three = await bourne(store)
    await watcher.check(C, one)
    assert [(c[0], c[1]) for c in calls] == [(1, "pack00005"), (2, "pack00005"),
                                             (3, "pack00005")]
    assert "一个资源就补齐了缺的第 1、2、3 部" in calls[0][3]
    # 其他两部的通知直接写进了库
    kinds = {(n.subscription_id, n.kind) for n in await store.notifications(C)}
    assert {(two.id, "auto_saved"), (three.id, "auto_saved")} <= kinds
    for sub in (two, three):
        await watcher.check(C, sub)
    assert len(calls) == 3


async def test_tv_season_pack_is_preferred():
    links = [link("s3only001", "无职转生 第3季 1080p", n=2),
             link("allseason", "无职转生 S01-S03 全季 1080p", n=6)]
    store, _, watcher, calls = watcher_with(links)
    subs = [await store.add_subscription(
        C, f"无职转生 第{s}季", f"无职转生 第{s}季", media="tv", season=s, total_episodes=2,
        tmdb_id="94664", collection_id="tv:94664", collection_name="无职转生", series=True,
        release_date="2021-01-10", auto_save=True) for s in (1, 2, 3)]
    await watcher.check(C, subs[0])
    assert [(c[0], c[1], c[2]) for c in calls][:3] == [
        (1, "allseason", {1, 2}), (2, "allseason", {1, 2}), (3, "allseason", {1, 2})]


async def test_group_skipped_without_auto_save_or_collection():
    store, agent, watcher, calls = watcher_with(BOURNE_LINKS)
    plain = await store.add_subscription(C, "谍影重重", "谍影重重", media="movie", auto_save=True)
    await watcher.check(C, plain)
    assert agent.queries == ["谍影重重"] and calls[0][3] is None  # 普通订阅：原来的逻辑


def test_pick_only_this_part_from_pack():
    files = [{"fid": str(i), "file_name": n, "_path": p} for i, (n, p) in enumerate([
        ("谍影重重.2002.1080p.mkv", "/谍影重重1-5合集/谍影重重1"),
        ("谍影重重2.2004.1080p.mkv", "/谍影重重1-5合集/谍影重重2"),
        ("The.Bourne.Ultimatum.2007.1080p.mkv", "/谍影重重1-5合集/谍影重重3：最后通牒"),
        ("谍影重重5.2016.2160p.mkv", "/谍影重重1-5合集"),
    ])]
    chosen, _ = pick_files(files, True, part=3, series="谍影重重", title="谍影重重3：最后通牒",
                           year="2007", pack=True)
    assert [f["fid"] for f in chosen] == ["2"]
    # 认不出第几部：按年份认；还认不出且是挑中的合集就不存
    english = [{"fid": "a", "file_name": "Bourne.Identity.2002.mkv", "_path": "/x"},
               {"fid": "b", "file_name": "Bourne.Ultimatum.2007.mkv", "_path": "/x"}]
    assert [f["fid"] for f in pick_files(english, True, part=3, series="谍影重重",
                                         year="2007", pack=True)[0]] == ["b"]
    assert pick_files(english, True, part=3, series="谍影重重", year="2010", pack=True)[0] == []
    # 不是合集（同一部的两个版本）：照旧挑最好的
    versions = [{"fid": "a", "file_name": "谍影重重3.1080p.mkv"},
                {"fid": "b", "file_name": "谍影重重3.2160p.mkv"}]
    assert [f["fid"] for f in pick_files(versions, True, part=3, series="谍影重重")[0]] == ["b"]


async def test_ai_pick_with_reason_and_rule_fallback(monkeypatch):
    cands = [(BOURNE_LINKS[1], {2}), (BOURNE_LINKS[2], {1, 2, 3, 4, 5})]
    sent: list[dict] = []

    async def chat(client, payload, key, timeout):
        sent.append(json.loads(payload["messages"][1]["content"]))
        return {"content": json.dumps({"pick": "1", "reason": "合集一次补齐第 2、3 部"})}

    monkeypatch.setattr(llm, "chat", chat)
    chooser = PackChooser("k")
    got = await chooser(cands, {2, 3}, "谍影重重", False)
    assert got.link.share == "pack00005" and got.parts == {2, 3} and got.by == "ai"
    assert got.reason == "合集一次补齐第 2、3 部（AI 挑选）"
    assert sent[0]["missing"] == [2, 3] and sent[0]["candidates"][1]["covers"] == [1, 2, 3, 4, 5]

    with llm.scope(allowed=False):  # 额度用完：按规则挑，结果一样
        got = await chooser(cands, {2, 3}, "谍影重重", False)
    assert got.link.share == "pack00005" and got.by == "rule"
    assert len(sent) == 1

    async def bad(client, payload, key, timeout):
        return {"content": json.dumps({"pick": "9", "reason": "x"})}

    monkeypatch.setattr(llm, "chat", bad)
    assert (await chooser(cands, {2, 3}, "谍影重重", False)).by == "rule"  # 越界退回规则


def test_rule_prefers_more_missing_then_resolution():
    a = link("a", "谍影重重3 4K", res="2160p")
    b = link("b", "谍影重重1-5合集", n=5)
    assert rule_pick([(a, {3}), (b, {1, 2, 3, 4, 5})], {3}, False).link.share == "a"
    assert rule_pick([(a, {3}), (b, {1, 2, 3, 4, 5})], {2, 3}, False).link.share == "b"
    assert rule_pick([(a, {3})], {1}, False) is None


class PackDrive(Drive):
    """分享是「谍影重重1-5合集/」文件夹，里面 1~3 部各一个视频；目标目录是空的。"""

    def __init__(self):
        super().__init__()
        self.have = []
        self.names = {"f1": "谍影重重.2002.1080p.mkv", "f2": "谍影重重2.2004.1080p.mkv",
                      "f3": "谍影重重3.最后通牒.2007.1080p.mkv"}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/detail"):
            if request.url.params["pdir_fid"] == "0":
                return httpx.Response(200, json={"code": 0, "data": {
                    "share": {"title": "谍影重重1-5合集"},
                    "list": [{"fid": "pack", "share_fid_token": "tp",
                              "file_name": "谍影重重1-5合集", "dir": True}]}})
            return httpx.Response(200, json={"code": 0, "data": {"list": [
                {"fid": f"f{i}", "share_fid_token": f"t{i}", "file_name": n}
                for i, n in [(1, "谍影重重.2002.1080p.mkv"), (2, "谍影重重2.2004.1080p.mkv"),
                             (3, "谍影重重3.最后通牒.2007.1080p.mkv")]]}})
        return await super().handler(request)


def test_auto_save_takes_only_this_part_from_pack():
    drive = PackDrive()
    app, store, _, _ = make(drive)
    with TestClient(app) as client:
        login(client)
        [_, _, three] = asyncio.run(bourne(store))
        notes = asyncio.run(app.state.watcher.auto_saver(
            OWNER, three, BOURNE_LINKS[2], None, note="一个资源就补齐了缺的第 3 部（按规则挑选）"))
        [req] = drive.saved
        assert req["fid_list"] == ["f3"]  # 只存第 3 部
        assert "来自合集「谍影重重1-5合集」" in notes[0][1] and "按规则挑选" in notes[0][1]
        [log] = client.get(f"/api/subscriptions/{three.id}/saves", params=CID).json()
        assert "第 3 部" in log["message"] and "谍影重重 系列" in log["folder"]
