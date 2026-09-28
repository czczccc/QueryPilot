"""防夸克风控：同一账号写操作排队 + 随机间隔、每天转存次数上限（429 + Retry-After）、批量检查错开。"""

import asyncio

from fastapi.testclient import TestClient

from app.services import subscriptions as subs_mod
from app.services.pacing import Pacer, seconds_to_cn_midnight
from tests.test_accounts import login
from tests.test_agent import shares
from tests.test_autosave import BODY, CID, Drive, make
from tests.test_subscriptions import eps, setup


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(round(s, 3))
        self.now += s


async def test_writes_are_spaced_per_account():
    t = FakeTime()
    pacer = Pacer(gap=2.0, jitter=0.0, daily_saves=0, sleep=t.sleep, clock=t.clock)
    await pacer.wait("u:a")  # 第一次不等
    await pacer.wait("u:b")  # 别的账号不受影响
    t.now += 0.5
    await pacer.wait("u:a")  # 距上次 0.5 秒：再等 1.5 秒
    await pacer.wait("u:a")
    assert t.sleeps == [1.5, 2.0]

    jittery = Pacer(gap=1.0, jitter=3.0, daily_saves=0, sleep=t.sleep, clock=t.clock)
    t.sleeps.clear()
    for _ in range(20):
        await jittery.wait("u:a")
    assert all(1.0 <= s <= 4.0 for s in t.sleeps[1:])
    assert len(set(t.sleeps[1:])) > 1  # 间隔带随机，不是整齐的
    await Pacer(0, 0, 0).wait("u:a")  # 关闭时直接返回


def test_daily_save_limit():
    pacer = Pacer(0, 0, daily_saves=2)
    assert pacer.take_save("u:a") is None and pacer.take_save("u:a") is None
    retry = pacer.take_save("u:a")
    assert retry is not None and 0 < retry <= 86401 and pacer.saves_left("u:a") == 0
    assert pacer.take_save("u:b") is None
    assert Pacer(0, 0, 0).take_save("u:a") is None  # 0 = 不限
    assert 0 < seconds_to_cn_midnight() <= 86401


def test_save_api_returns_429_with_retry_after():
    drive = Drive(episodes=3)
    app, _, _, s = make(drive, pacer=Pacer(0, 0, daily_saves=1))
    with TestClient(app) as client:
        login(client)
        first = client.post("/api/save", json={"share": s[0]})
        assert first.status_code == 200 and first.json()["ok"] is True
        second = client.post("/api/save", json={"share": s[0]})
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) > 0
        assert "今天转存次数已达上限（1 次）" in second.json()["detail"]
        assert len(drive.saved) == 1


def test_auto_save_waits_for_tomorrow_when_limit_hit():
    drive = Drive(episodes=3)
    pacer = Pacer(0, 0, daily_saves=1)
    app, store, _, s = make(drive, pacer=pacer)
    with TestClient(app) as client:
        login(client)
        client.post("/api/save", json={"share": s[0]})  # 手动转存用掉了今天的次数
        sub = client.post("/api/subscriptions", json=BODY).json()
        asyncio.run(store.set_auto_save("u:uid:1", sub["id"], True))
        r = client.post(f"/api/subscriptions/{sub['id']}/check", params=CID)
        kinds = [n["kind"] for n in r.json()["notifications"]]
        assert "auto_saved" not in kinds and "auto_save_failed" not in kinds
        [log] = client.get(f"/api/subscriptions/{sub['id']}/saves", params=CID).json()
        assert "转存次数已用完" in log["message"]
        [(_, now)] = asyncio.run(store.list_subscriptions("u:uid:1"))
        assert now.auto_save_status is None  # 不暂停订阅
        again = client.post(f"/api/subscriptions/{sub['id']}/check", params=CID)
        assert again.status_code == 429 and int(again.headers["Retry-After"]) > 0


async def test_batch_check_is_staggered(monkeypatch):
    s = shares("ep", 1)
    store, _, watcher = setup({s[0]: eps(1)}, s)
    for name in ("流浪地球", "三体", "繁花"):
        await store.add_subscription("c" * 8, name, name)
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(sec):
        waits.append(sec)
        await real_sleep(0)

    monkeypatch.setattr(subs_mod.asyncio, "sleep", fake_sleep)
    watcher.stagger = 10.0
    await watcher.run_once()
    assert len(waits) == 2 and all(5.0 <= w <= 15.0 for w in waits)
