"""转存自动分类：规则判断、LLM 判断、目录路径、网盘建目录与失败回退。"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.classify import (
    Category,
    Classifier,
    classify_rules,
    decide_folder,
    safe_name,
)
from app.services.quark_save import QuarkSaver
from tests.test_agent import ScriptedTavily, make_service, quark_client, shares


@pytest.mark.parametrize(("title", "files", "kind", "region", "folder"), [
    ("漫长的季节 (2023) 4K 全12集", [f"漫长的季节.E{i:02d}.2160p.mkv" for i in range(1, 13)],
     "tv", "cn", "/QueryPilot/电视剧/国产剧/漫长的季节 (2023)"),
    ("绝命律师 第六季 美剧", ["Better.Call.Saul.S06E01.mkv", "Better.Call.Saul.S06E02.mkv"],
     "tv", "west", None),
    ("沙丘2 2024 4K HDR", ["Dune.Part.Two.2024.2160p.HDR.mkv"],
     "movie", "other", "/QueryPilot/电影/其他"),
    ("流浪地球2", ["流浪地球2.2160p.mkv"], "movie", "cn", "/QueryPilot/电影/华语"),
    ("请回答1988 韩剧", ["E01.mp4", "E02.mp4", "E03.mp4"], "tv", "jpkr", None),
    ("鬼灭之刃 动漫 全集", ["01.mkv"], "anime", None, None),
    ("地球脉动 BBC 纪录片", ["Planet.Earth.mkv"], "documentary", None, "/QueryPilot/纪录片"),
])
def test_rules(title, files, kind, region, folder):
    cat = classify_rules(title, files)
    assert cat.kind == kind
    if region:
        assert cat.region == region
    if folder:
        assert cat.folder() == folder


def test_folder_names_are_safe():
    assert safe_name('a/b:c*?"<>|d') == "a b c d"
    cat = Category("tv", "cn", "片名/有斜杠", "2020")
    assert cat.folder("我的/根") == "/我的 根/电视剧/国产剧/片名 有斜杠 (2020)"
    assert Category("movie", "west", "x").label() == "欧美电影"


async def test_llm_classifier_and_fallback():
    async def ok(request):
        content = json.dumps({"kind": "tv", "region": "west", "title": "绝命律师", "year": 2015})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    c = Classifier("k", httpx.AsyncClient(transport=httpx.MockTransport(ok)))
    cat = await c("Better Call Saul S01", ["a.mkv"])
    assert (cat.kind, cat.region, cat.title, cat.year, cat.by) == (
        "tv", "west", "绝命律师", "2015", "llm")

    async def bad(request):
        return httpx.Response(500)

    c = Classifier("k", httpx.AsyncClient(transport=httpx.MockTransport(bad)))
    assert (await c("流浪地球2", ["流浪地球2.mkv"])).by == "rules"
    assert (await Classifier()("流浪地球2", ["x.mkv"])).by == "rules"


def drive_with_dirs(existing: dict[str, str], seen: list, mkdir_code: int = 0):
    """夸克假接口：顶层是一个文件夹，里面 12 集；支持按路径查目录与建目录。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "st"}})
        if path.endswith("/sharepage/detail"):
            if request.url.params["pdir_fid"] == "0":
                return httpx.Response(200, json={"code": 0, "data": {
                    "share": {"title": "漫长的季节 2023"},
                    "list": [{"fid": "d1", "share_fid_token": "t1", "file_name": "漫长的季节",
                              "dir": True}]}})
            return httpx.Response(200, json={"code": 0, "data": {"list": [
                {"file_name": f"漫长的季节.E{i:02d}.mkv"} for i in range(1, 13)]}})
        if path.endswith("/file/info/path_list"):
            want = json.loads(request.read())["file_path"][0]
            data = [{"fid": existing[want], "file_path": want}] if want in existing else []
            return httpx.Response(200, json={"code": 0, "data": data})
        if path == "/1/clouddrive/file":
            if mkdir_code:
                return httpx.Response(200, json={"code": mkdir_code})
            return httpx.Response(200, json={"code": 0, "data": {"fid": "newdir"}})
        if path.endswith("/sharepage/save"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t"}})
        if path.endswith("/clouddrive/task"):
            return httpx.Response(200, json={"code": 0, "data": {"status": 2}})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _save_target(seen) -> str:
    req = next(r for r in seen if r.url.path.endswith("/sharepage/save"))
    return json.loads(req.read())["to_pdir_fid"]


async def test_saver_creates_folder_and_saves_there():
    seen: list = []
    s = QuarkSaver("c=1", client=drive_with_dirs({}, seen), classifier=Classifier(),
                   poll_interval=0)
    result = await s.save("abcdef123456")
    assert result.folder == "/QueryPilot/电视剧/国产剧/漫长的季节 (2023)"
    assert result.category == "国产剧"
    mk = next(r for r in seen if r.url.path == "/1/clouddrive/file")
    assert json.loads(mk.read())["dir_path"] == result.folder
    assert _save_target(seen) == "newdir"


async def test_saver_reuses_existing_folder_and_falls_back():
    seen: list = []
    folder = "/QueryPilot/电视剧/国产剧/漫长的季节 (2023)"
    s = QuarkSaver("c=1", client=drive_with_dirs({folder: "have"}, seen),
                   classifier=Classifier(), poll_interval=0)
    await s.save("abcdef123456")
    assert _save_target(seen) == "have"
    assert not any(r.url.path == "/1/clouddrive/file" for r in seen)

    # 建目录失败：退回默认目录，但照样转存
    seen = []
    s = QuarkSaver("c=1", to_pdir_fid="dflt", client=drive_with_dirs({}, seen, mkdir_code=99),
                   classifier=Classifier(), poll_interval=0)
    result = await s.save("abcdef123456")
    assert (result.folder, _save_target(seen)) == (None, "dflt")


async def test_explicit_path_skips_classifier():
    seen: list = []

    async def boom(title, files):
        raise AssertionError("指定路径时不应分类")

    saver = QuarkSaver("c", client=drive_with_dirs({"/我的/剧": "fx"}, seen), poll_interval=0,
                       classifier=boom)
    res = await saver.save("abc", to_path="/我的/剧")
    assert (res.folder, res.category, _save_target(seen)) == ("/我的/剧", None, "fx")


async def test_decide_folder_is_backend_independent():
    place = await decide_folder(Classifier(), "漫长的季节 (2023)",
                                [f"E{i:02d}.mkv" for i in range(1, 13)], "根")
    assert (place.path, place.label, place.basis) == (
        "/根/电视剧/国产剧/漫长的季节 (2023)", "国产剧", "文件名规则（未查到影视资料）")


async def test_no_classifier_keeps_old_behavior():
    seen: list = []
    s = QuarkSaver("c=1", to_pdir_fid="dflt", client=drive_with_dirs({}, seen), poll_interval=0)
    result = await s.save("abcdef123456")
    assert result.folder is None and _save_target(seen) == "dflt"
    assert not any("path_list" in r.url.path for r in seen)


def test_save_api_reports_folder():
    seen: list = []
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)), client=quark_client())
    s = QuarkSaver("c=1", client=drive_with_dirs({}, seen), classifier=Classifier(),
                   poll_interval=0)
    app = create_app(service=service, rate_limit_per_minute=100, saver=s, save_token="t")
    with TestClient(app) as client:
        r = client.post("/api/save", json={"share": "abcdef123456"},
                        headers={"X-Save-Token": "t"}).json()
    assert r["folder"] == "/QueryPilot/电视剧/国产剧/漫长的季节 (2023)"
    assert r["category"] == "国产剧"
    assert "「/QueryPilot/电视剧/国产剧/漫长的季节 (2023)」" in r["message"]
    assert "识别为国产剧" in r["message"]
