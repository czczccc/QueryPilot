"""后台任务加固：同一订阅不并发检查、退出时不再开始新检查、重新部署后尽快补查。"""

import asyncio
import time

from app.services.memory import LinkStore
from app.services.subscriptions import SubscriptionWatcher


class SlowAgent:
    def __init__(self):
        self.calls = 0
        self.gate = asyncio.Event()

    async def run(self, *a, **k):
        self.calls += 1
        await self.gate.wait()
        raise RuntimeError("stop here")


async def _sub(store):
    return await store.add_subscription("c1", "流浪地球2", "流浪地球2", (0, 0, None))


async def test_same_subscription_not_checked_twice():
    store = LinkStore(":memory:")
    agent = SlowAgent()
    watcher = SubscriptionWatcher(agent, store)
    sub = await _sub(store)
    first = asyncio.create_task(watcher.check("c1", sub))
    await asyncio.sleep(0.05)
    assert watcher.busy(sub.id)
    assert await watcher.check("c1", sub) == []  # 第二次直接跳过
    agent.gate.set()
    await asyncio.gather(first, return_exceptions=True)
    assert agent.calls <= 1 and not watcher.busy(sub.id)


async def test_stopping_skips_new_checks():
    store = LinkStore(":memory:")
    agent = SlowAgent()
    watcher = SubscriptionWatcher(agent, store)
    sub = await _sub(store)
    watcher.stopping = True
    assert await watcher.check("c1", sub) == []
    assert await watcher.run_once() == 0
    assert agent.calls == 0


async def test_due_in_runs_soon_after_long_gap():
    store = LinkStore(":memory:")
    watcher = SubscriptionWatcher(SlowAgent(), store)
    assert await watcher.due_in(6) == 6 * 3600  # 没有订阅：照常等一个周期
    sub = await _sub(store)
    assert await watcher.due_in(6) == 300  # 从没检查过：5 分钟后开始
    sub.last_check = {"at": time.time() - 3600}  # 1 小时前查过：再等约 5 小时

    async def listed(*_):
        return [("c1", sub)]

    store.list_subscriptions = listed
    assert 4.9 * 3600 < await watcher.due_in(6) <= 5 * 3600
