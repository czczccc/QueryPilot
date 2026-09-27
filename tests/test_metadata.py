"""影视资料查询（TMDB / 豆瓣）与基于资料的分类。"""

import json

import httpx

from app.services.classify import Classifier, classify_meta
from app.services.metadata import MediaInfo, MetadataLookup, search_douban, search_tmdb


def tmdb_handler(seen: list):
    async def handler(request: httpx.Request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/search/multi"):
            return httpx.Response(200, json={"results": [
                {"media_type": "person", "id": 9},
                {"media_type": "tv", "id": 1, "name": "旧版", "first_air_date": "1999-01-01"},
                {"media_type": "tv", "id": 2, "name": "漫长的季节", "first_air_date": "2023-04-22"},
            ]})
        if path.endswith("/tv/2"):
            return httpx.Response(200, json={
                "name": "漫长的季节", "original_name": "漫长的季节", "first_air_date": "2023-04-22",
                "genres": [{"name": "剧情"}, {"name": "犯罪"}], "origin_country": ["CN"],
                "original_language": "zh", "number_of_seasons": 1,
            })
        return httpx.Response(404)
    return handler


async def test_tmdb_v3_key_and_year_order():
    seen: list = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(tmdb_handler(seen)))
    found = await search_tmdb("漫长的季节", "2023", "v3key", client, limit=1)
    assert len(found) == 1
    info = found[0]
    assert (info.title, info.year, info.media, info.countries, info.seasons) == (
        "漫长的季节", "2023", "tv", ["CN"], 1)
    assert seen[0].url.params["api_key"] == "v3key"
    assert "authorization" not in seen[0].headers
    assert seen[0].url.params["language"] == "zh-CN"


async def test_tmdb_v4_token_uses_bearer_and_detail_failure_keeps_hit():
    seen: list = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(tmdb_handler(seen)))
    found = await search_tmdb("x", None, "eyJtoken", client, base="https://proxy.example/3/")
    assert seen[0].headers["authorization"] == "Bearer eyJtoken"
    assert "api_key" not in seen[0].url.params
    assert seen[0].url.host == "proxy.example"
    # tv/1 详情 404 时退回搜索结果本身
    assert [f.title for f in found] == ["旧版", "漫长的季节"]


async def test_douban_suggest_and_detail():
    async def handler(request: httpx.Request):
        if "subject_suggest" in request.url.path:
            return httpx.Response(200, json=[
                {"id": "35588177", "title": "漫长的季节", "year": "2023", "episode": "12"}])
        if request.url.path.endswith("/tv/35588177"):
            assert request.headers["referer"].startswith("https://m.douban.com")
            return httpx.Response(200, json={
                "countries": ["中国大陆"], "genres": ["剧情", "悬疑"], "is_tv": True,
                "year": "2023"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    [info] = await search_douban("漫长的季节", "2023", client)
    assert (info.source, info.media, info.countries, info.genres) == (
        "douban", "tv", ["中国大陆"], ["剧情", "悬疑"])


async def test_lookup_tolerates_failure_and_caches():
    calls = {"n": 0}

    async def handler(request: httpx.Request):
        calls["n"] += 1
        if "douban" in request.url.host:
            return httpx.Response(403)
        return await tmdb_handler([])(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    lookup = MetadataLookup("k", douban=True, client=client)
    found = await lookup("漫长的季节", "2023")
    assert found and all(f.source == "tmdb" for f in found)
    n = calls["n"]
    assert await lookup("漫长的季节", "2023") == found
    assert calls["n"] == n  # 命中缓存
    assert not MetadataLookup("", douban=False).enabled


def test_classify_meta_mapping():
    def cat(**kw):
        return classify_meta(MediaInfo(source="tmdb", title="t", **kw))

    assert (cat(media="tv", countries=["CN"]).kind, cat(media="tv", countries=["CN"]).region) == (
        "tv", "cn")
    assert cat(media="movie", countries=["US"]).label() == "欧美电影"
    assert cat(media="tv", countries=["KR"]).label() == "日韩剧"
    assert cat(media="tv", genres=["动画"], countries=["JP"]).kind == "anime"
    assert cat(media="movie", genres=["Documentary"]).kind == "documentary"
    assert cat(media="tv", genres=["真人秀"]).kind == "variety"
    assert cat(media="movie", language="ja").region == "jpkr"
    assert cat(media="movie", countries=["IN"]).region == "other"
    assert cat(media="tv", countries=["CN"]).basis() == "TMDB"


class FakeLookup:
    def __init__(self, metas):
        self.metas = metas
        self.asked = []

    async def __call__(self, name, year):
        self.asked.append((name, year))
        return self.metas


METAS = [
    MediaInfo(source="tmdb", title="漫长的季节", year="2023", media="tv", countries=["CN"]),
    MediaInfo(source="douban", title="漫长的季节", year="2023", media="tv", countries=["中国大陆"]),
]


async def test_classifier_passes_metadata_to_llm():
    got = {}

    async def handler(request: httpx.Request):
        body = json.loads(request.content)
        got["payload"] = json.loads(body["messages"][1]["content"])
        content = json.dumps({"kind": "tv", "region": "cn", "title": "漫长的季节", "year": "2023"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    lookup = FakeLookup(METAS)
    c = Classifier("k", httpx.AsyncClient(transport=httpx.MockTransport(handler)), lookup)
    cat = await c("漫长的季节 (2023) 4K 全12集", ["E01.mkv"])
    assert lookup.asked == [("漫长的季节", "2023")]
    assert [m["source"] for m in got["payload"]["candidates"]] == ["tmdb", "douban"]
    assert cat.folder() == "/QueryPilot/电视剧/国产剧/漫长的季节 (2023)"
    assert cat.basis() == "TMDB + 豆瓣 + LLM"


async def test_classifier_without_llm_uses_metadata_then_rules():
    cat = await Classifier(lookup=FakeLookup(METAS))("漫长的季节", ["a.mkv"])
    assert (cat.by, cat.label(), cat.basis()) == ("tmdb", "国产剧", "TMDB")

    cat = await Classifier(lookup=FakeLookup([]))("流浪地球2", ["流浪地球2.mkv"])
    assert cat.basis() == "文件名规则（未查到影视资料）"

    async def bad(request):
        return httpx.Response(500)

    # LLM 挂了但有资料 → 用资料映射，不落到规则
    c = Classifier("k", httpx.AsyncClient(transport=httpx.MockTransport(bad)), FakeLookup(METAS))
    assert (await c("漫长的季节", ["a.mkv"])).by == "tmdb"
