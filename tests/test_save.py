"""一键转存：完整流程、错误码、口令校验、未配置时关闭、cookie 不外泄。"""

import json
import logging

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.quark_save import QuarkSaver, SaveError
from tests.test_agent import ScriptedTavily, make_service, quark_client, shares

COOKIE = "__pus=SECRET-COOKIE-VALUE; __kp=abc"
TOKEN = "let-me-save"


def drive(save_code=0, task_status=(0, 2), token_code=0, seen=None):
    """夸克假接口：记录收到的请求，按参数返回各步结果。"""
    statuses = list(task_status)

    async def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if path.endswith("/sharepage/token"):
            if token_code:
                return httpx.Response(200, json={"code": token_code})
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "st"}})
        if path.endswith("/sharepage/detail"):
            return httpx.Response(200, json={"code": 0, "data": {
                "share": {"status": 1, "title": "流浪地球2"},
                "list": [{"fid": "f1", "share_fid_token": "t1", "file_name": "a.mkv"},
                         {"fid": "f2", "share_fid_token": "t2", "file_name": "b.mkv"}],
            }})
        if path.endswith("/sharepage/save"):
            if save_code:
                return httpx.Response(200, json={"code": save_code, "message": "x"})
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "task9"}})
        if path.endswith("/clouddrive/task"):
            status = statuses.pop(0) if statuses else 0
            return httpx.Response(200, json={"code": 0, "data": {"status": status}})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def saver(**kw) -> QuarkSaver:
    seen = kw.pop("seen", None)
    return QuarkSaver(COOKIE, to_pdir_fid="dir1", client=drive(seen=seen, **kw),
                      poll_interval=0, poll_times=3)


async def test_save_full_flow_sends_cookie_only_to_quark():
    seen: list[httpx.Request] = []
    result = await saver(seen=seen).save("abcdef123456", "ab12")
    assert (result.task_id, result.file_count, result.title, result.done) == (
        "task9", 2, "流浪地球2", True)
    save_req = next(r for r in seen if r.url.path.endswith("/sharepage/save"))
    body = json.loads(save_req.read())
    assert body["fid_list"] == ["f1", "f2"] and body["fid_token_list"] == ["t1", "t2"]
    assert body["to_pdir_fid"] == "dir1" and body["stoken"] == "st"
    assert json.loads(seen[0].read())["passcode"] == "ab12"
    assert all(r.headers["Cookie"] == COOKIE for r in seen)
    assert {r.url.host for r in seen} <= {"pan.quark.cn", "drive-pc.quark.cn"}
    assert "SECRET" not in repr(saver())


async def test_save_pending_task():
    result = await saver(task_status=(0, 0, 0, 0)).save("abcdef123456")
    assert result.done is False


async def test_save_error_codes():
    for kw, text in [
        ({"save_code": 31001}, "登录已过期"),
        ({"save_code": 32003}, "空间不足"),
        ({"save_code": 99999}, "99999"),
        ({"token_code": 41006}, "分享已失效"),
    ]:
        try:
            await saver(**kw).save("abcdef123456")
        except SaveError as e:
            assert text in str(e)
        else:
            raise AssertionError(kw)


async def test_network_error_does_not_log_cookie(caplog):
    async def boom(request):
        raise httpx.ConnectError("down")

    s = QuarkSaver(COOKIE, client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    caplog.set_level(logging.DEBUG)
    try:
        await s.save("abcdef123456")
    except SaveError as e:
        assert "连接夸克失败" in str(e)
    assert "SECRET" not in caplog.text


def _app(saver_obj, token=TOKEN):
    service = make_service(ScriptedTavily({}, default=shares("ok", 6)), client=quark_client())
    return create_app(service=service, rate_limit_per_minute=100, saver=saver_obj,
                      save_token=token)


def test_save_api_requires_token():
    with TestClient(_app(saver())) as client:
        assert client.get("/api/save/status").json()["enabled"] is True
        body = {"share": "abcdef123456", "pwd": "ab12"}
        assert client.post("/api/save", json=body).status_code == 401
        assert client.post("/api/save", json=body,
                           headers={"X-Save-Token": "wrong"}).status_code == 401
        r = client.post("/api/save", json=body, headers={"X-Save-Token": TOKEN})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "message": "已转存《流浪地球2》到你的夸克网盘",
                            "file_count": 2}
        assert "SECRET" not in r.text
        # 参数校验：分享码/提取码格式
        assert client.post("/api/save", json={"share": "../etc"},
                           headers={"X-Save-Token": TOKEN}).status_code == 422


def test_save_api_reports_quark_error():
    with TestClient(_app(saver(save_code=31001))) as client:
        r = client.post("/api/save", json={"share": "abcdef123456"},
                        headers={"X-Save-Token": TOKEN})
        assert r.json()["ok"] is False and "QUARK_COOKIE" in r.json()["message"]


def test_save_disabled_without_cookie_or_token():
    for app in (_app(None), _app(saver(), token="")):
        with TestClient(app) as client:
            assert client.get("/api/save/status").json()["enabled"] is False
            assert client.post("/api/save", json={"share": "abcdef123456"},
                               headers={"X-Save-Token": ""}).status_code == 404
