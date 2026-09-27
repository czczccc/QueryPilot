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


def test_falls_back_to_page_title_without_share_info():
    link = L(name="漫长的季节 夸克网盘资源")
    judge(link, build_target(P(), "漫长的季节"))
    assert link.relevance == "match"


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
