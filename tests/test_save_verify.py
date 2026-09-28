"""转存核对：夸克报完成但目标目录里没文件、分享里没有要的集，都不能记成成功；网络错误写出真实原因。"""

import asyncio

import httpx
from fastapi.testclient import TestClient

from app.services.quark_save import http_error_text
from tests.test_accounts import login
from tests.test_autosave import BODY, CID, OWNER, Drive, make


class GhostDrive(Drive):
    """任务报完成，但文件没有出现在目标目录。"""

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/save"):
            self.saved.append({})
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "t"}})
        return await super().handler(request)


def run_check(app, store):
    async def go():
        [(owner, obj)] = await store.list_subscriptions(OWNER)
        return await app.state.watcher.check(owner, obj)
    return asyncio.run(go())


def test_empty_target_is_not_success():
    app, store, _, _ = make(GhostDrive())
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json={**BODY, "media": "tv",
                                                      "total_episodes": 3}).json()
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))
        notes = run_check(app, store)
        failed = [m for k, m, _ in notes if k == "auto_save_failed"]
        assert failed and "目标目录里没有出现文件" in failed[0]
        [(_, now)] = asyncio.run(store.list_subscriptions(OWNER))
        assert 2 not in now.saved_episodes and 3 not in now.saved_episodes
        logs = client.get(f"/api/subscriptions/{sub['id']}/saves", params=CID).json()
        assert logs and not logs[0]["ok"]


def test_nothing_matched_is_not_success():
    drive = Drive(episodes=3)
    drive.have = []
    app, store, _, _ = make(drive)
    with TestClient(app) as client:
        login(client)
        sub = client.post("/api/subscriptions", json={**BODY, "media": "tv",
                                                      "total_episodes": 12}).json()
        asyncio.run(store.set_auto_save(OWNER, sub["id"], True))
        [(owner, obj)] = asyncio.run(store.list_subscriptions(OWNER))
        from app.models import QuarkLink
        link = QuarkLink(name="流浪地球", share="ep00000000", source="t", time="")
        # 分享里只有 1~3 集，要的是第 9 集：一个都挑不出来，不能报成功
        notes = asyncio.run(app.state.watcher.auto_saver(owner, obj, link, {9}))
        assert notes and notes[0][0] == "auto_save_failed" and "没能在分享" in notes[0][1]


def test_http_error_text():
    req = httpx.Request("GET", "https://x")
    assert "超时" in http_error_text(httpx.ReadTimeout("t", request=req))
    resp = httpx.Response(429, request=req)
    assert "429" in http_error_text(httpx.HTTPStatusError("x", request=req, response=resp))
