"""防止用户的夸克号因为操作太密被风控：每个账号的写操作排队、间隔加随机抖动，每天转存次数有上限。"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

CN_OFFSET = 8 * 3600


def cn_day(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime((now or time.time()) + CN_OFFSET))


def seconds_to_cn_midnight(now: float | None = None) -> int:
    now = now or time.time()
    return int(86400 - (now + CN_OFFSET) % 86400) + 1


class Pacer:
    """按夸克账号节流。

    - `wait(key)`：同一账号的写操作（打开分享、转存、建目录、移动、改名、删除）一个接一个做，
      两次之间至少隔 `gap` 秒再加 0~`jitter` 秒随机，不像脚本那样整齐连发。
    - `take_save(key)`：记一次转存；当天（北京时间）超过 `daily_saves` 次时不记，返回距离明天
      0 点的秒数（给 Retry-After 用）；没超返回 None。0 表示不限。
    """

    def __init__(
        self, gap: float = 1.5, jitter: float = 2.0, daily_saves: int = 100,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gap, self.jitter, self.daily_saves = gap, jitter, daily_saves
        self._sleep, self._clock = sleep, clock
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._saves: dict[str, tuple[str, int]] = {}

    async def wait(self, key: str) -> None:
        if not key or (self.gap <= 0 and self.jitter <= 0):
            return
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            last = self._last.get(key)
            if last is not None:
                delay = last + self.gap + random.uniform(0, self.jitter) - self._clock()
                if delay > 0:
                    await self._sleep(delay)
            self._last[key] = self._clock()
        if len(self._last) > 5000:  # 只留最近活跃的账号
            for k in list(self._last)[:1000]:
                self._last.pop(k, None)
                if (lk := self._locks.get(k)) is not None and not lk.locked():
                    self._locks.pop(k, None)

    def saves_left(self, key: str) -> int | None:
        if self.daily_saves <= 0:
            return None
        day, n = self._saves.get(key, ("", 0))
        return self.daily_saves - (n if day == cn_day() else 0)

    def take_save(self, key: str) -> int | None:
        if self.daily_saves <= 0 or not key:
            return None
        today = cn_day()
        day, n = self._saves.get(key, (today, 0))
        n = n if day == today else 0
        if n >= self.daily_saves:
            return seconds_to_cn_midnight()
        self._saves[key] = (today, n + 1)
        if len(self._saves) > 20000:
            self._saves = {k: v for k, v in self._saves.items() if v[0] == today}
        return None


def stagger(base: float) -> float:
    """后台批量检查两个订阅之间的等待：base 的 0.5~1.5 倍。"""
    return random.uniform(0.5, 1.5) * base if base > 0 else 0.0
