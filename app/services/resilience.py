"""对外请求的重试和熔断。

- `RetryTransport`：遇到 429 / 502 / 503 / 504、连不上时指数退避重试（带抖动，尊重 Retry-After，
  次数有上限）。读超时只对 GET 重试：POST（比如转存）超时可能其实已经执行了，重发会重复。
- `CircuitBreaker`：某个搜索源连续失败 N 次就暂停它几分钟，期间直接跳过，不拖慢整次搜索、
  也不继续撞封禁；暂停期满放一次请求试探，成功就恢复。
"""

import asyncio
import logging
import random
import time

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 502, 503, 504})


class RetryTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None, retries: int = 2,
                 base: float = 0.5, cap: float = 8.0) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self.retries = retries
        self.base = base
        self.cap = cap

    def _delay(self, attempt: int, resp: httpx.Response | None) -> float:
        if resp is not None:
            after = resp.headers.get("retry-after", "")
            if after.isdigit():
                return min(float(after), self.cap)
        return min(self.cap, self.base * 2 ** attempt) * random.uniform(0.8, 1.2)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        idempotent = request.method in ("GET", "HEAD", "OPTIONS")
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                resp = await self._inner.handle_async_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                if last:
                    raise
                why, resp = type(e).__name__, None
            except httpx.TimeoutException as e:
                if last or not idempotent:
                    raise
                why, resp = type(e).__name__, None
            else:
                if resp.status_code not in RETRY_STATUS or last:
                    return resp
                why = f"HTTP {resp.status_code}"
                await resp.aclose()
            delay = self._delay(attempt, resp)
            logger.info("请求 %s %s %s，%.1fs 后重试（第 %d 次）",
                        request.method, request.url.host, why, delay, attempt + 1)
            await asyncio.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def aclose(self) -> None:
        await self._inner.aclose()


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown: float = 300.0) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def allow(self, name: str) -> bool:
        return time.monotonic() >= self._open_until.get(name, 0.0)

    def record(self, name: str, ok: bool) -> bool:
        """记一次结果；返回这次是否刚刚触发熔断。"""
        if ok:
            self._fails.pop(name, None)
            self._open_until.pop(name, None)
            return False
        n = self._fails.get(name, 0) + 1
        self._fails[name] = n
        if n >= self.threshold:
            self._open_until[name] = time.monotonic() + self.cooldown
            self._fails[name] = 0
            logger.warning("搜索源 %s 连续失败 %d 次，暂停 %.0f 秒", name, n, self.cooldown)
            return True
        return False
