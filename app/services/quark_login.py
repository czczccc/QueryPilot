"""夸克扫码登录：页面显示二维码，用户用夸克 App 扫码确认，后端拿到登录 cookie。

为什么不能「跳转到夸克登录页再读 cookie」：cookie 属于 quark.cn 域，
浏览器不允许我们的网站读取别家的 cookie。扫码登录是在服务器上完成整个登录，
cookie 直接落在服务器这边。

流程（逆向自夸克网页版登录页，未在真实环境验证，接口变动时需调整）：
1. `getTokenForQrcodeLogin` 取一次性 token，拼成二维码内容；
2. 轮询 `getServiceTicketByQrcodeToken`：等待扫码 → 返回 service_ticket；
3. 带 ticket 访问 `pan.quark.cn/account/info`，响应的 Set-Cookie 就是登录态。
"""

import logging
import secrets
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field

import httpx

from app.services.quark import UA

logger = logging.getLogger(__name__)

CLIENT_ID = "532"
TOKEN_URL = "https://uop.quark.cn/cas/ajax/getTokenForQrcodeLogin"
TICKET_URL = "https://uop.quark.cn/cas/ajax/getServiceTicketByQrcodeToken"
ACCOUNT_URL = "https://pan.quark.cn/account/info"
QR_BASE = "https://su.quark.cn/4_eMHBJ"

STATUS_OK = 2000000
STATUS_WAITING = 50004001
STATUS_EXPIRED = 50004002
LOGIN_TTL = 300.0  # 二维码 5 分钟有效


class LoginError(Exception):
    """可直接展示给用户的登录错误（不含凭证）。"""


@dataclass
class PendingLogin:
    token: str
    created: float = field(default_factory=time.monotonic)


@dataclass
class LoginResult:
    status: str  # waiting / expired / success
    cookie: str | None = None
    nickname: str | None = None
    user_id: str | None = None  # 夸克账号的稳定标识（取不到时为 None，由调用方兜底）


def qr_content(token: str) -> str:
    params = {
        "token": token,
        "client_id": CLIENT_ID,
        "ssb": "weblogin",
        "uc_param_str": "",
        "uc_biz_str": "S:custom|OPT:SAREA@0|OPT:IMMERSIVE@1|OPT:BACK_BTN_STYLE@0",
    }
    return f"{QR_BASE}?{urllib.parse.urlencode(params)}"


def qr_svg(content: str) -> str | None:
    """生成二维码 SVG（segno 未安装时返回 None，前端可自行用 qr_url 生成）。"""
    try:
        import segno
    except ImportError:
        return None
    return segno.make(content, error="m").svg_inline(scale=5, border=2, omitsize=True)


class QuarkQrLogin:
    """管理进行中的扫码登录（内存，最多 200 个，过期自动清理）。"""

    def __init__(
        self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 10.0
    ) -> None:
        # 每次请求用独立的短命客户端：cookie 罐不在用户之间共享（登录不频繁，开销可忽略）
        self._transport = transport
        self._timeout = timeout
        self._pending: dict[str, PendingLogin] = {}

    async def _get(self, url: str, params: dict) -> httpx.Response:
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            return await client.get(url, params=params, headers=self._headers())

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": UA, "Referer": "https://pan.quark.cn/"}

    def _gc(self) -> None:
        now = time.monotonic()
        for lid in [k for k, v in self._pending.items() if now - v.created > LOGIN_TTL]:
            self._pending.pop(lid, None)
        while len(self._pending) > 200:
            self._pending.pop(next(iter(self._pending)))

    async def start(self) -> tuple[str, str]:
        """开始一次扫码登录，返回 (login_id, 二维码内容)。"""
        self._gc()
        try:
            resp = await self._get(
                TOKEN_URL,
                {"client_id": CLIENT_ID, "v": "1.2", "request_id": str(uuid.uuid4())},
            )
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            raise LoginError("连接夸克失败，请稍后重试") from None
        token = ((body.get("data") or {}).get("members") or {}).get("token")
        if body.get("status") != STATUS_OK or not token:
            logger.warning("夸克扫码 token 获取失败 status=%s", body.get("status"))
            raise LoginError("获取夸克登录二维码失败")
        login_id = secrets.token_urlsafe(16)
        self._pending[login_id] = PendingLogin(token)
        return login_id, qr_content(token)

    async def poll(self, login_id: str) -> LoginResult:
        """查询扫码状态；成功时返回登录 cookie（调用方负责加密保存，之后本对象不再持有）。"""
        pending = self._pending.get(login_id)
        if pending is None or time.monotonic() - pending.created > LOGIN_TTL:
            self._pending.pop(login_id, None)
            return LoginResult("expired")
        try:
            resp = await self._get(
                TICKET_URL,
                {"client_id": CLIENT_ID, "v": "1.2", "token": pending.token,
                 "request_id": str(uuid.uuid4())},
            )
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return LoginResult("waiting")  # 网络抖动：下次轮询再试
        status = body.get("status")
        if status == STATUS_WAITING:
            return LoginResult("waiting")
        ticket = ((body.get("data") or {}).get("members") or {}).get("service_ticket")
        if status != STATUS_OK or not ticket:
            self._pending.pop(login_id, None)
            return LoginResult("expired")

        self._pending.pop(login_id, None)
        cookie, nickname, user_id = await self._exchange(ticket)
        return LoginResult("success", cookie, nickname, user_id)

    async def _exchange(self, ticket: str) -> tuple[str, str | None, str | None]:
        """用 service_ticket 换登录 cookie（响应 Set-Cookie 里的全部 cookie）。"""
        try:
            resp = await self._get(ACCOUNT_URL, {"st": ticket, "lw": "scan"})
        except httpx.HTTPError:
            raise LoginError("登录确认失败，请重新扫码") from None
        jar = httpx.Cookies()
        jar.extract_cookies(resp)
        pairs = {c.name: c.value for c in jar.jar}
        if not pairs:
            raise LoginError("夸克没有返回登录凭证，请重新扫码")
        nickname, user_id = None, None
        try:
            data = resp.json().get("data") or {}
            nickname = data.get("nickname") if isinstance(data.get("nickname"), str) else None
            # 账号信息里的用户标识（字段名来自网页版，未公开文档；取不到就交给调用方兜底）
            for key in ("uid", "user_id", "userId", "member_id"):
                if isinstance(data.get(key), str | int) and str(data[key]).strip():
                    user_id = f"{key}:{data[key]}"
                    break
        except (ValueError, AttributeError):
            pass
        return "; ".join(f"{k}={v}" for k, v in pairs.items()), nickname, user_id
