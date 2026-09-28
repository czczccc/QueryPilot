"""相关性判定、偏好与反馈测试。"""

import json
import socket
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import ParsedResource, QualityInfo, QuarkLink, SearchRequest, UserPrefs
from app.services.agent import SearchAgent
from app.services.memory import LinkStore
from app.services.relevance import build_target, judge, llm_judge, seasons_in, years_in
from app.services.search import sort_links
from tests.test_agent import ScriptedTavily, make_service, quark_client, shares


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


def P(resource="漫长的季节", **kw) -> ParsedResource:
    return ParsedResource(resource=resource, search_suggestions=[resource], **kw)


def L(share="abc1234567", title=None, files=(), name="资源页", **kw) -> QuarkLink:
    return QuarkLink(name=name, share=share, source="x", time="未知", state="valid",
                     share_title=title, files_preview=list(files), **kw)


# ---------------- 年份 / 季数解析 ----------------

def test_years_and_seasons():
    assert years_in("流浪地球2.2023.2160p") == {2023}
    assert years_in("1080p 2160p x265") == set()
    assert seasons_in("绝命律师 第二季 全10集") == {2}
    assert seasons_in("Better.Call.Saul.S03E01.mkv") == {3}
    assert seasons_in("第1-3季 合集") == {1, 2, 3}
    assert seasons_in("S01-S06 全集") == {1, 2, 3, 4, 5, 6}
    assert seasons_in("第十二季") == {12}
    assert seasons_in("漫长的季节") == set()


# ---------------- 规则判定 ----------------

def test_match_by_title_and_files():
    target = build_target(P(), "漫长的季节")
    link = L(title="漫长的季节 4K 全12集")
    judge(link, target)
    assert link.relevance == "match"

    link = L(title=None, files=["The.Long.Season.E01.mkv"])
    judge(link, build_target(P(english_name="The Long Season"), "漫长的季节"))
    assert link.relevance == "match"


def test_year_mismatch_from_douban():
    target = build_target(P("沙丘"), "沙丘", douban_year="2021")
    old = L(title="沙丘 Dune 1984 蓝光")
    judge(old, target)
    assert (old.relevance, old.relevance_note) == ("mismatch", "年份不符：1984")
    new = L(title="沙丘 2021 4K")
    judge(new, target)
    assert new.relevance == "match"


def test_season_mismatch_and_range_ok():
    target = build_target(P("绝命律师"), "绝命律师 第三季")
    assert target.season == 3
    wrong = L(title="绝命律师 第一季")
    judge(wrong, target)
    assert wrong.relevance == "mismatch"
    pack = L(title="绝命律师 S01-S06 全集")
    judge(pack, target)
    assert pack.relevance == "match"


def test_uncertain_when_no_name():
    link = L(title="4K 电影合集 更新中")
    judge(link, build_target(P(), "漫长的季节"))
    assert link.relevance == "uncertain"


def test_page_title_only_is_not_enough():
    """只有搜索页标题（可能是一页上百个链接的聚合页）：最多算待核对。"""
    link = L(name="漫长的季节 夸克网盘资源")
    judge(link, build_target(P(), "漫长的季节"))
    assert link.relevance == "uncertain"


async def test_llm_judge_resolves_uncertain():
    links = [L("u1", title="合集A"), L("u2", title="合集B"), L("m1", title="漫长的季节")]
    target = build_target(P(), "漫长的季节")
    for lk in links:
        judge(lk, target)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        shares_sent = json.loads(body["messages"][1]["content"])["shares"]
        assert [s["title"] for s in shares_sent] == ["合集A", "合集B"]
        content = json.dumps({"results": {"0": True, "1": False}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    judged = await llm_judge(links, P(), target, "k", client)
    assert judged == 2
    assert [lk.relevance for lk in links] == ["match", "mismatch", "match"]


async def test_llm_judge_failure_keeps_uncertain():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "不是JSON"}}]})

    links = [L(title="合集A")]
    judge(links[0], build_target(P(), "漫长的季节"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await llm_judge(links, P(), build_target(P(), "x"), "k", client) == 0
    assert links[0].relevance == "uncertain"


# ---------------- 排序 ----------------

def test_sort_relevance_prefs_and_copies():
    q = QualityInfo(resolution="1080p", score=30)
    q_sub = QualityInfo(resolution="1080p", score=30, has_subtitle=True)
    wrong = L("wrong00001", quality=QualityInfo(resolution="2160p", score=50), relevance="mismatch")
    plain = L("plain00001", quality=q, relevance="match")
    sub = L("subti00001", quality=q_sub, relevance="match")
    copied = L("copie00001", quality=q, relevance="match", copy_count=3)
    links = [wrong, plain, sub, copied]

    sort_links(links, UserPrefs(prefer_subtitle=True))
    assert [lk.share for lk in links] == ["subti00001", "copie00001", "plain00001", "wrong00001"]


# ---------------- 记忆：迁移、偏好、反馈 ----------------

async def test_store_migrates_old_schema(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE links (share TEXT PRIMARY KEY, pwd TEXT, name TEXT NOT NULL,
            source TEXT NOT NULL, time TEXT NOT NULL, conf TEXT NOT NULL, state TEXT NOT NULL,
            http INTEGER, quality TEXT, first_seen REAL NOT NULL, last_checked REAL NOT NULL,
            fail_count INTEGER NOT NULL DEFAULT 0);
        INSERT INTO links VALUES ('old0000001', NULL, '旧', 'x', '未知', '中', 'valid',
            200, NULL, 0, 0, 0);
    """)
    conn.commit()
    conn.close()

    store = LinkStore(db)
    await store.save("k", [L("new0000001", title="漫长的季节", files=["a.mkv"])])
    recalled = {lk.share: lk for lk in await store.recall("k")}
    assert recalled["new0000001"].share_title == "漫长的季节"
    assert recalled["new0000001"].files_preview == ["a.mkv"]
    assert await store.record_copy("old0000001") is True
    assert await store.record_copy("nope000001") is False


async def test_prefs_roundtrip_and_copy_count():
    store = LinkStore(":memory:")
    assert await store.get_prefs("client-abc") == UserPrefs()
    await store.set_prefs("client-abc", UserPrefs(min_resolution="2160p", prefer_hdr=True))
    assert (await store.get_prefs("client-abc")).min_resolution == "2160p"

    await store.save("k", [L("share00001")])
    await store.record_copy("share00001")
    await store.record_copy("share00001")
    assert (await store.recall("k"))[0].copy_count == 2


# ---------------- agent 集成 ----------------

async def test_agent_uses_pref_resolution_and_skips_wrong_titles():
    store = LinkStore(":memory:")
    await store.set_prefs("client-abc", UserPrefs(min_resolution="2160p"))
    uhd = shares("uhd", 6)
    wrong = shares("old", 3)
    files = {s: "流浪地球2.2160p.mkv" for s in uhd}
    files |= {s: "流浪地球.2019.2160p.mkv" for s in wrong}
    tavily = ScriptedTavily({}, default=[*wrong, *uhd])
    svc = make_service(tavily, client=quark_client(files), store=store)
    resp = await SearchAgent(svc).run(SearchRequest(query="流浪地球2 2023", client_id="client-abc"))

    assert resp.required_resolution == "2160p"
    assert resp.matching_count == 6
    by_share = {lk.share: lk for lk in resp.links}
    assert all(by_share[s].relevance == "mismatch" for s in wrong)
    assert all(by_share[s].relevance == "match" for s in uhd)
    # 片名/年份不符的排在最后
    assert {lk.share for lk in resp.links[-3:]} == set(wrong)
    verify_step = next(s for s in resp.steps if s.tool == "verify")
    assert verify_step.observation["wrong_title"] == 3


# ---------------- API ----------------

def _client(store: LinkStore | None):
    svc = make_service(ScriptedTavily({}), store=store)
    return TestClient(create_app(service=svc))


def test_prefs_and_feedback_api():
    store = LinkStore(":memory:")
    client = _client(store)
    assert client.get("/api/prefs", params={"client_id": "client-xyz"}).json() == {
        "min_resolution": None, "prefer_subtitle": False, "prefer_hdr": False,
    }
    put = client.put("/api/prefs", params={"client_id": "client-xyz"},
                     json={"min_resolution": "1080p", "prefer_subtitle": True})
    assert put.status_code == 200
    assert client.get("/api/prefs", params={"client_id": "client-xyz"}).json()[
        "min_resolution"] == "1080p"
    assert client.put("/api/prefs", params={"client_id": "client-xyz"},
                      json={"min_resolution": "8K"}).status_code == 422
    assert client.get("/api/prefs", params={"client_id": "x"}).status_code == 422

    assert client.post("/api/feedback", json={"share": "nothere01"}).json() == {"recorded": False}
    assert client.post("/api/feedback", json={"share": "bad share!"}).status_code == 422


def test_prefs_api_404_without_memory():
    client = _client(store=None)
    assert client.get("/api/prefs", params={"client_id": "client-xyz"}).status_code == 404


# ---------------- 聚合页 / 演员名干扰（用户反馈的两个例子） ----------------

AGG = "datashare_data/share_detail.md at main"


def test_aggregator_page_links_judged_by_their_own_share_title():
    """搜「鬼吹灯」：GitHub 聚合页一页上百个链接，每条都带页面标题；
    以夸克分享自己的标题为准，《魔力歌先生》《还珠格格》不能算相关。"""
    from app.services.intent import rule_based_parsed

    target = build_target(rule_based_parsed("鬼吹灯"), "鬼吹灯")
    wrong1 = L(name=AGG, title="[国产综艺]《魔力歌先生》（2026年）-毒舌电影最抖音",
               files=["S01.2026.2160p.60fps.WEB-DL.h265.10bit.AAC"])
    wrong2 = L(name=AGG, title="[国产剧]《还珠格格》（共4季）（1998-2011年）-毒舌电影最抖音",
               files=["还珠格格1 1080P 国语中字 无台标"])
    right = L(name=AGG, title="[国产剧]《鬼吹灯之精绝古城》（2016）4K",
              files=["鬼吹灯之精绝古城.E01.2160p.mkv"])
    for lk in (wrong1, wrong2, right):
        judge(lk, target)
    assert (wrong1.relevance, wrong1.relevance_note) == ("mismatch", "分享是《魔力歌先生》")
    assert wrong2.relevance == "mismatch"
    assert right.relevance == "match"
    # 只有聚合页标题、没读到分享内容：最多待核对
    page_only = L(name="鬼吹灯 夸克网盘资源合集")
    judge(page_only, target)
    assert page_only.relevance == "uncertain"


def test_actor_name_in_query_does_not_decide_relevance():
    """搜「靳东 精英律师」：片名是「精英律师」，只含演员名的其它剧不算相关。"""
    from app.services.intent import rule_based_parsed

    for q in ("靳东 精英律师", "靳东的精英律师"):
        target = build_target(rule_based_parsed(q), q)
        other = L(title="《伪装者》靳东 胡歌 全48集", files=["伪装者.E01.1080p.mp4"])
        actor_only = L(title="靳东主演 电视剧合集", files=["外交风云.E01.1080p.mp4"])
        right = L(title="《精英律师》全40集 4K", files=["精英律师.E01.2160p.mp4"])
        plain = L(title="精英律师 全40集", files=["E01.mp4"])
        for lk in (other, actor_only, right, plain):
            judge(lk, target)
        assert other.relevance == "mismatch", q
        assert actor_only.relevance == "uncertain", q  # 不算相关，留给 AI 核对
        assert right.relevance == "match" and plain.relevance == "match", q


def test_uncertain_links_never_trigger_subscription_or_auto_save():
    from app.services.subscriptions import snapshot

    link = L(title="靳东主演 电视剧合集", files=["外交风云.E01.1080p.mp4"],
             quality=QualityInfo(resolution="1080p", score=30, video_count=40))
    link.relevance = "uncertain"
    assert snapshot([link])[0] == 0
    link.relevance = "match"
    assert snapshot([link])[0] == 40


def test_aliases_from_tmdb_douban():
    from app.services.metadata import MediaInfo
    from app.services.relevance import add_aliases

    target = build_target(P("漫长的季节"), "漫长的季节")
    add_aliases(target, [
        MediaInfo(source="tmdb", title="漫长的季节", original_title="The Long Season"),
        MediaInfo(source="tmdb", title="繁花", original_title="Blossoms Shanghai"),  # 不相干
    ])
    assert "thelongseason" in target.names and "blossomsshanghai" not in target.names
    link = L(title="The.Long.Season.S01.2160p", files=["E01.mkv"])
    judge(link, target)
    assert link.relevance == "match"


async def test_subscription_rechecks_entry_and_asks_about_unsure():
    """订阅按自己的条目再判：年份不符的不算；只有拿不准的时发一次「请核对」，不自动转存。"""
    from tests.test_subscriptions import eps, setup

    s = shares("ep", 2)
    files = {s[0]: ["流浪地球2.2019.E01.1080p.mkv", "流浪地球2.2019.E02.1080p.mkv"],
             s[1]: eps(3)}
    store, _, watcher = setup(files, s)
    saved = []

    async def saver(client_id, sub, link, wanted=None):
        saved.append(link.share)
        return []

    watcher.auto_saver = saver
    sub = await store.add_subscription("c" * 8, "流浪地球2", "流浪地球2", year="2023",
                                       media="tv", season=1, auto_save=True)
    notes = await watcher.check("c" * 8, sub)
    # 2019 年的那个被判年份不符；另一个确认相关
    assert [k for k, _, _ in notes] == ["found"] and s[1] in notes[0][1]
    assert saved == [s[1]]

    unsure_files = {s[0]: ["合集.E01.mkv", "合集.E02.mkv"]}
    store2, _, watcher2 = setup(unsure_files, [s[0]])
    watcher2.auto_saver = saver
    saved.clear()
    sub2 = await store2.add_subscription("c" * 8, "流浪地球2", "流浪地球2", auto_save=True)
    notes = await watcher2.check("c" * 8, sub2)
    assert [k for k, _, _ in notes] == ["maybe"] and saved == []
    [(_, again)] = await store2.list_subscriptions("c" * 8)
    assert await watcher2.check("c" * 8, again) == []  # 同一个只提醒一次


# ---------------- 线上实测（无职转生）：波浪号、游戏、多季合集、后几季年份 ----------------

MUSHOKU = "无职转生～到了异世界就拿出真本事～"


def _sub_target(season: int, year: str = "2021"):
    from app.models import Subscription
    from app.services.subscriptions import SubscriptionWatcher

    sub = Subscription(id=1, query=MUSHOKU, resource=f"{MUSHOKU} 第{season}季", created=0,
                       media="tv", season=season, year=year)
    return SubscriptionWatcher._target(sub)


def test_fullwidth_tilde_and_multi_season_pack():
    pack = "无职转生 ~到了异世界就拿出真本事 S01-S02季全集 4K超清2160P收藏版 内封简日双语字幕"
    for season, want in ((1, "match"), (2, "match"), (3, "mismatch")):
        lk = L(title=pack, files=["S01E01.mkv"])
        judge(lk, _sub_target(season))
        assert lk.relevance == want, (season, lk.relevance_note)


def test_games_are_not_videos():
    game = L(title="W 无职转生到了异世界就拿出真本事 TENOKE中文版", files=["setup.exe"])
    judge(game, _sub_target(2))
    assert game.relevance == "mismatch" and "游戏" in game.relevance_note
    only_exe = L(title="无职转生 到了异世界就拿出真本事", files=["Mushoku.exe", "data.pak"])
    judge(only_exe, _sub_target(1))
    assert only_exe.relevance == "mismatch"
    # 片名里带「游戏」的影视不受影响
    squid = L(title="鱿鱼游戏 第二季 2024 1080P", files=["鱿鱼游戏.S02E01.mkv"])
    judge(squid, build_target(P("鱿鱼游戏"), "鱿鱼游戏"))
    assert squid.relevance == "match"


def test_later_seasons_keep_later_years():
    s2 = L(title=f"{MUSHOKU} 第二季 2023 1080P", files=["S02E01.mkv"])
    judge(s2, _sub_target(2))
    assert s2.relevance == "match"
    old = L(title=f"{MUSHOKU} 第二季 2012", files=["S02E01.mkv"])
    judge(old, _sub_target(2))
    assert old.relevance == "mismatch"
    # 电影还是按 ±1 年判
    movie = L(title="流浪地球2 2019", files=["a.mkv"])
    judge(movie, build_target(P("流浪地球2"), "流浪地球2", "2023"))
    assert movie.relevance == "mismatch"


def test_per_share_source_title_counts_when_share_page_has_no_name():
    from app.models import ParsedResource, QuarkLink
    from app.services.relevance import build_target, judge
    target = build_target(ParsedResource(resource="我不是大师", search_suggestions=["我不是大师"]),
                          "我不是大师")

    def mk(source, name):
        return QuarkLink(name=name, share="s", source=source, time="", state="valid",
                         share_title="更新中", files_preview=["01.mp4", "02.mp4"])

    pansou = mk("PanSou·tg", "我不是大师 (2026) 更新至10集 4K")
    judge(pansou, target)
    assert pansou.relevance == "match"
    only_keyword = mk("PanSou", "我不是大师")  # PanSou 没标题时用搜索词代替：不算
    judge(only_keyword, target)
    assert only_keyword.relevance == "uncertain"
    aggregate = mk("https://example.com/list", "我不是大师 等 100 部资源合集")
    judge(aggregate, target)
    assert aggregate.relevance == "uncertain"
