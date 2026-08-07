"""工程防护：进程内限流、深度抓取 SSRF 防护、日志 request_id 贯穿。

均为轻量实现，不引入额外依赖，便于阅读与面试讲解。
"""

import asyncio
import ipaddress
import logging
import socket
import time
from collections import deque
from contextvars import ContextVar
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# ---------------- 日志 request_id 贯穿 ----------------

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


_old_record_factory = None


def install_request_id_factory() -> None:
    """全局 LogRecord 工厂：每条日志创建时注入当前 request_id。

    注意：Python 3.12 的 `Logger.filter` 不再把 filters 传播到父级 logger，
    挂在 root 上的 filter 对子 logger 无效，因此改用 LogRecord 工厂方案。
    """
    global _old_record_factory
    if _old_record_factory is None:
        _old_record_factory = logging.getLogRecordFactory()

        def _factory(*args, **kwargs) -> logging.LogRecord:
            record = _old_record_factory(*args, **kwargs)
            record.request_id = _request_id_var.get()
            return record

        logging.setLogRecordFactory(_factory)


# ---------------- 进程内限流（每 IP 滑动窗口） ----------------

class RateLimiter:
    """内存滑动窗口限流：每 key（默认客户端 IP）在窗口内最多 rate 次请求。"""

    def __init__(self, rate: int, window: float = 60.0) -> None:
        self._rate = rate
        self._window = window
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._rate:
            return False
        hits.append(now)
        self._gc(now)
        return True

    def _gc(self, now: float) -> None:
        # 简单清理：键过多时移除已过期条目，避免无限增长
        if len(self._hits) > 10000:
            expired = [k for k, q in self._hits.items() if not q or now - q[-1] > self._window]
            for k in expired:
                del self._hits[k]


# ---------------- 深度抓取 SSRF 防护 ----------------

def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析的视为不安全
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def is_safe_fetch_url(url: str) -> bool:
    """深度抓取目标校验：仅允许 http/https 且解析后指向公网 IP。

    注意：这是基础防护（仍受 DNS rebinding 理论限制），生产环境建议配合
    URL scheme 白名单与出站代理。IPv6 环回/私有段同样被拦截。
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        # socket.getaddrinfo 是阻塞的，放到线程池避免卡事件循环（跨平台）
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except OSError:
        return False
    for info in infos:
        if _is_blocked_ip(info[4][0]):
            return False
    return bool(infos)
