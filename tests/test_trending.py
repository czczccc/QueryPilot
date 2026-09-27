"""首页热门影视：TMDB 本周热门优先，没 key 或失败用豆瓣，都不行用内置列表；缓存 6 小时。"""

import asyncio

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import trending as tr
from app.services.agent import SearchAgent
from tests.test_agent import ScriptedTavily, make_service

TMDB = {"results": [
    {"media_type": "tv", "name": "繁花", "first_air_date": "2023-12-27", "poster_path": "/a.jpg"},
    {"media_type": "movie", "title": "沙丘2", "release_date": "2024-03-08"},
    {"media_type": "person", "name": "某演员"},
    {"media_type": "movie", "title": "成人片", "adult": True},
    {"media_type": "tv", "name": "繁花"},
]}
DOUBAN = {
    "tv": {"subjects": [{"title": "我的阿勒泰", "cover": "https://img/1.jpg"},
                        {"title": "繁花"}, {"title": "三体"}]},
    "movie": {"subjects": [{"title": "热辣滚烫", "cover": "https://img/2.jpg"}]},
}


def client(seen: list, tmdb_status: int = 200, douban_status: int = 200):
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "api.themoviedb.org":
            assert request.url.path == "/3/trending/all/week"
            assert request.url.params["language"] == "zh-CN"
            return httpx.Response(tmdb_status, json=TMDB)
        if request.url.host == "movie.douban.com":
            return httpx.Response(douban_status, json=DOUBAN[request.url.params["type"]])
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_tmdb_first_then_cached():
    seen: list = []
    t = tr.Trending(tmdb_key="k", client=client(seen))
    got = asyncio.run(t())
    assert got["source"] == "tmdb"
    assert got["items"] == [
        {"title": "繁花", "year": "2023", "media": "tv",
         "poster": "https://image.tmdb.org/t/p/w342/a.jpg"},
        {"title": "沙丘2", "year": "2024", "media": "movie", "poster": None},
    ]
    asyncio.run(t())
    assert seen == ["api.themoviedb.org"]  # 6 小时内用缓存


def test_douban_when_no_key_or_tmdb_fails():
    seen: list = []
    got = asyncio.run(tr.Trending(tmdb_key="k", client=client(seen, tmdb_status=500))())
    assert got["source"] == "douban"
    assert [(i["title"], i["media"]) for i in got["items"]] == [
        ("我的阿勒泰", "tv"), ("热辣滚烫", "movie"), ("繁花", "tv"), ("三体", "tv")]
    assert got["items"][0]["poster"] == "https://img/1.jpg" and got["items"][0]["year"] is None
    got = asyncio.run(tr.Trending(client=client([]))())
    assert got["source"] == "douban"


def test_default_when_all_fail():
    t = tr.Trending(tmdb_key="k", client=client([], tmdb_status=500, douban_status=403))
    got = asyncio.run(t())
    assert got["source"] == "default" and len(got["items"]) >= 10
    assert asyncio.run(tr.Trending(douban=False)())["source"] == "default"  # 不联网


def test_api_needs_no_login():
    seen: list = []
    service = make_service(ScriptedTavily({}, default=[]))
    app = create_app(service=service, agent=SearchAgent(service),
                     trending=tr.Trending(tmdb_key="k", client=client(seen)))
    with TestClient(app) as c:
        r = c.get("/api/trending")
        assert r.status_code == 200 and r.json()["source"] == "tmdb"
    service = make_service(ScriptedTavily({}, default=[]))
    with TestClient(create_app(service=service, agent=SearchAgent(service))) as c:
        assert c.get("/api/trending").json()["source"] == "default"  # 测试环境不外连
