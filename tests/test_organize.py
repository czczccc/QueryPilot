"""订阅转存的整理：目录锁定、按集去重、每集一个版本、不存压缩包、展平嵌套文件夹、
标准命名，以及「整理已有目录」的预览与确认执行（用户反馈《我不是大师》存乱的例子）。"""

import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.agent import SearchAgent
from app.services.classify import Category
from app.services.cookie_box import CookieBox
from app.services.memory import LinkStore
from app.services.organize import pick_files, plan_tidy, standard_name
from tests.test_accounts import FakeLogin, login
from tests.test_agent import ScriptedTavily, _public_dns, make_service, shares  # noqa: F401
from tests.test_subscriptions import episodes_client

OWNER = "u:uid:1"
CID = {"client_id": "browser-0001"}
# 用户截图里的分享：顶层文件夹里还套了一层「我丨不是大师」，同一集有 mkv / mp4 / zip 多个版本
SHARE_FILES = [
    *(f"{i:02d}-4K.高码率.zip" for i in range(1, 6)),
    "06-4K.高码率.mkv", "06-4K.高码率.zip",
    "09-4K.高码率.mkv", "09.mp4", "10-4K.高码率.mkv", "10.mp4", "说明.txt",
]
SIZES = {"09.mp4": 396 << 20, "10.mp4": 466 << 20}


def F(name, size=None):
    return {"file_name": name, "size": size or SIZES.get(name, 1 << 30), "fid": name,
            "share_fid_token": "t" + name}


def test_pick_one_version_per_episode_and_skip_archives():
    chosen, _ = pick_files([F(n) for n in SHARE_FILES], movie=False, season=1)
    assert [f["file_name"] for f in chosen] == [
        "06-4K.高码率.mkv", "09-4K.高码率.mkv", "10-4K.高码率.mkv"]
    # 一个视频都没有时才存压缩包（每集一个）
    only_zip, _ = pick_files([F("01.zip"), F("02.rar"), F("a.txt")], movie=False)
    assert [f["file_name"] for f in only_zip] == ["01.zip", "02.rar"]
    movie, _ = pick_files([F("片.1080p.mkv", 2 << 30), F("片.2160p.mkv", 9 << 30),
                           F("片.zip")], movie=True)
    assert [f["file_name"] for f in movie] == ["片.2160p.mkv"]
    subs, _ = pick_files([F("E01.1080p.mkv"), F("E01.chs.srt"), F("E02.srt")], movie=False)
    assert [f["file_name"] for f in subs] == ["E01.1080p.mkv", "E01.chs.srt"]
    assert standard_name("我不是大师", ".mkv", 1, 6) == "我不是大师 S01E06.mkv"
    assert standard_name("流浪地球2", ".mkv", year="2023") == "流浪地球2 (2023).mkv"


class Drive:
    """假夸克：分享（带嵌套文件夹）+ 网盘目录树，记录转存 / 移动 / 重命名 / 删除。"""

    def __init__(self):
        self.share = {"0": [{"fid": "top", "file_name": "我不是大师", "dir": True,
                             "share_fid_token": "tt"}],
                      "top": [{"fid": "nest", "file_name": "我丨不是大师", "dir": True,
                               "share_fid_token": "tn"}],
                      "nest": [F(n) for n in SHARE_FILES]}
        self.dirs: dict[str, str] = {}  # 路径 → fid
        self.files: dict[str, list[dict]] = {}  # 目录 fid → 文件
        self.saves: list[dict] = []
        self.renames: list[tuple[str, str]] = []
        self.moves: list[tuple[list, str]] = []
        self.deletes: list[list] = []
        self.paths: list[str] = []

    def mkdir(self, path: str) -> str:
        if path not in self.dirs:
            fid = f"d{len(self.dirs)}"
            self.dirs[path] = fid
            self.files[fid] = []
            parent = path.rsplit("/", 1)[0]
            if parent in self.dirs:
                self.files[self.dirs[parent]].append(
                    {"fid": fid, "file_name": path.rsplit("/", 1)[1], "dir": True})
        return self.dirs[path]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path, p = request.url.path, request.url.params
        ok = {"code": 0, "data": {"task_id": "t"}}
        if path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "st"}})
        if path.endswith("/sharepage/detail"):
            items = self.share.get(p["pdir_fid"], [])
            return httpx.Response(200, json={"code": 0, "data": {
                "share": {"title": "叫我小弟 全集"}, "list": items}})
        if path.endswith("/file/info/path_list"):
            want = json.loads(request.read())["file_path"][0]
            self.paths.append(want)
            fid = self.dirs.get(want)
            return httpx.Response(200, json={"code": 0, "data": [{"fid": fid}] if fid else []})
        if path.endswith("/clouddrive/file") and request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {
                "fid": self.mkdir(json.loads(request.read())["dir_path"])}})
        if path.endswith("/file/sort"):
            return httpx.Response(200, json={"code": 0, "data": {
                "list": self.files.get(p["pdir_fid"], [])}})
        if path.endswith("/sharepage/save"):
            body = json.loads(request.read())
            self.saves.append(body)
            by_fid = {f["fid"]: f for items in self.share.values() for f in items}
            for fid in body["fid_list"]:
                self.files[body["to_pdir_fid"]].append({**by_fid[fid], "fid": "n" + fid})
            return httpx.Response(200, json=ok)
        if path.endswith("/clouddrive/task"):
            return httpx.Response(200, json={"code": 0, "data": {"status": 2}})
        if path.endswith("/file/rename"):
            body = json.loads(request.read())
            self.renames.append((body["fid"], body["file_name"]))
            for items in self.files.values():
                for f in items:
                    if f["fid"] == body["fid"]:
                        f["file_name"] = body["file_name"]
            return httpx.Response(200, json={"code": 0, "data": {}})
        if path.endswith("/file/move"):
            body = json.loads(request.read())
            self.moves.append((body["filelist"], body["to_pdir_fid"]))
            for items in self.files.values():
                for f in list(items):
                    if f["fid"] in body["filelist"]:
                        items.remove(f)
                        self.files[body["to_pdir_fid"]].append(f)
            return httpx.Response(200, json=ok)
        if path.endswith("/file/delete"):
            body = json.loads(request.read())
            self.deletes.append(body["filelist"])
            return httpx.Response(200, json=ok)
        return httpx.Response(404)


class FixedClassifier:
    """模拟 LLM 每次分类结果都不一样（用户截图里三次存到三个目录）。"""

    def __init__(self):
        self.calls = 0

    async def __call__(self, title, files):
        self.calls += 1
        kind = "movie" if self.calls == 1 else "tv"
        return Category(kind=kind, region="cn", title="叫我小弟", by="llm")


def make(drive: Drive):
    store = LinkStore(":memory:")
    s = shares("ep", 1)
    service = make_service(ScriptedTavily({}, default=s),
                           client=episodes_client({s[0]: [f"流浪地球2.E{i:02d}.mkv"
                                                          for i in range(1, 11)]}),
                           store=store)
    app = create_app(
        service=service, agent=SearchAgent(service), rate_limit_per_minute=1000,
        qr_login=FakeLogin(), cookie_box=CookieBox.from_secret("k"),
        quark_client=httpx.AsyncClient(transport=httpx.MockTransport(drive.handler)),
        classifier=FixedClassifier(),
    )
    return app, store, s


def test_subscription_saves_are_locked_deduped_flattened_and_renamed():
    drive = Drive()
    app, store, s = make(drive)
    # 测试里的搜索解析固定出「流浪地球2」；网盘这边的分享仍是用户截图里的样子
    body = {**CID, "query": "流浪地球2", "resource": "流浪地球2", "media": "tv",
            "year": "2025"}
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=body).json()
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))

        async def check():
            [(owner, obj)] = await store.list_subscriptions(OWNER)
            return await app.state.watcher.check(owner, obj, sync_save=True)

        notes = asyncio.run(check())
        assert "auto_saved" in [k for k, _, _ in notes]
        target = "/QueryPilot/电视剧/国产剧/流浪地球2 (2025)/Season 01"
        [(_, now)] = asyncio.run(store.list_subscriptions(OWNER))
        assert now.folder == target  # 标准片名，不是分享标题「叫我小弟」；剧集不会被分到电影
        # 展平嵌套文件夹、每集一个版本、不存压缩包
        [save] = drive.saves
        assert save["pdir_fid"] == "nest" and save["to_pdir_fid"] == drive.dirs[target]
        assert save["fid_list"] == ["06-4K.高码率.mkv", "09-4K.高码率.mkv", "10-4K.高码率.mkv"]
        assert sorted(n for _, n in drive.renames) == [
            "流浪地球2 S01E06.mkv", "流浪地球2 S01E09.mkv", "流浪地球2 S01E10.mkv"]
        assert now.saved_episodes == [6, 9, 10]

        # 再查两次：目录锁定、不再重新分类，也不重复转存
        asyncio.run(check())
        asyncio.run(check())
        assert len(drive.saves) == 1


def test_organize_preview_and_confirmed_apply():
    """以前存乱的目录：预览列出移动 / 重命名 / 建议删除；只删用户勾选的。"""
    drive = Drive()
    app, store, _ = make(drive)
    old = "/QueryPilot/电视剧/国产剧/叫我小弟"
    old_fid = drive.mkdir("/QueryPilot")
    drive.mkdir("/QueryPilot/电视剧")
    drive.mkdir("/QueryPilot/电视剧/国产剧")
    old_fid = drive.mkdir(old)
    nest = drive.mkdir(old + "/我丨不是大师")
    drive.files[old_fid] += [{"fid": f"o{i}", "file_name": n, "size": 1 << 30}
                             for i, n in enumerate(["01-4K.高码率.zip", "06-4K.高码率.mkv",
                                                    "06-4K.高码率.zip"])]
    drive.files[nest] += [{"fid": "n9a", "file_name": "09-4K.高码率.mkv", "size": 1 << 31},
                          {"fid": "n9b", "file_name": "09.mp4", "size": 396 << 20}]
    body = {**CID, "query": "我不是大师", "resource": "我不是大师", "media": "tv",
            "year": "2025"}
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json=body).json()
        asyncio.run(store.log_auto_save(sub["id"], "x", True, 3, old, "旧的"))
        plan = client.get(f"/api/subscriptions/{sub['id']}/organize", params=CID).json()
        assert plan["target"] == "/QueryPilot/电视剧/国产剧/我不是大师 (2025)/Season 01"
        assert {(m["name"], m["to_name"]) for m in plan["moves"]} == {
            ("06-4K.高码率.mkv", "我不是大师 S01E06.mkv"),
            ("09-4K.高码率.mkv", "我不是大师 S01E09.mkv")}
        reasons = {d["name"]: d["reason"] for d in plan["deletes"]}
        assert reasons["09.mp4"] == "重复版本，已保留更好的"
        assert reasons["06-4K.高码率.zip"] == "压缩包，已有视频版本"
        assert reasons["我丨不是大师"] == "整理后是空文件夹"
        assert "01-4K.高码率.zip" in reasons  # 有视频的剧，压缩包也建议删
        assert drive.moves == [] and drive.deletes == []  # 预览不做任何改动

        # 用户只勾了 09.mp4，外加一个不在建议里的 fid（必须被忽略）
        r = client.post(f"/api/subscriptions/{sub['id']}/organize", params=CID,
                        json={"delete_fids": ["n9b", "not-in-plan"]}).json()
        assert r["moved"] == 2 and r["renamed"] == 2 and r["deleted"] == 1
        assert drive.deletes == [["n9b"]]
        [(_, now)] = asyncio.run(store.list_subscriptions(OWNER))
        assert now.saved_episodes == [6, 9]


def test_plan_ignores_unknown_files():
    entries = [{"fid": "a", "file_name": "花絮.mkv", "folder": "/T", "dir": False},
               {"fid": "b", "file_name": "E01.mkv", "folder": "/T", "dir": False}]
    plan = plan_tidy(entries, "/T", "剧", movie=False)
    assert [u["name"] for u in plan.untouched] == ["花絮.mkv"]
    assert [m["to_name"] for m in plan.moves] == ["剧 S01E01.mkv"]
