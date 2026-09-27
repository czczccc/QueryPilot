"""QueryPilot FastAPI 入口。

页面、API 与夸克网盘链接搜索编排收敛在单个服务内，
方便本地调试与国内服务器 Docker 部署。
"""

import asyncio
import contextlib
import hmac
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_settings
from app.models import (
    AgentSearchResponse,
    AgentStep,
    FeedbackRequest,
    Notification,
    QuarkSearchResponse,
    SaveRequest,
    SaveResponse,
    SearchRequest,
    SubscribeRequest,
    Subscription,
    UserPrefs,
)
from app.providers.tavily import TavilyProvider
from app.security import RateLimiter, install_request_id_factory, set_request_id
from app.services.agent import SearchAgent
from app.services.cookie_box import CookieBox, session_hash
from app.services.intent import DeepSeekParser
from app.services.memory import LinkStore
from app.services.quark_login import LoginError, QuarkQrLogin, qr_svg
from app.services.quark_save import LoginExpiredError, QuarkSaver, SaveError
from app.services.search import QuarkSearchService, SearchUnavailableError
from app.services.subscriptions import SubscriptionWatcher

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_settings = load_settings()
logging.basicConfig(
    level=_settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s request_id=%(request_id)s",
)
install_request_id_factory()
logger = logging.getLogger(__name__)


def build_default_service() -> QuarkSearchService:
    """按环境变量构建生产服务（密钥来自环境或 .env，绝不落仓库）。"""
    parser = DeepSeekParser(
        api_key=_settings.deepseek_api_key,
        timeout=_settings.request_timeout_seconds,
    )
    provider = TavilyProvider(
        api_key=_settings.tavily_api_key,
        timeout=_settings.request_timeout_seconds,
    )
    store = LinkStore(_settings.memory_db_path) if _settings.memory_db_path else None
    return QuarkSearchService(
        parser=parser,
        tavily=provider,
        use_qkyunso=True,
        use_bing=True,
        timeout=_settings.request_timeout_seconds,
        store=store,
        tg_channels=_settings.tg_channels,
        tg_client=httpx.AsyncClient(
            timeout=10.0, proxy=_settings.tg_proxy, follow_redirects=True
        ) if _settings.tg_proxy else None,
        extra_sites=_settings.extra_sites,
    )


async def _reverify_loop(service: QuarkSearchService, interval_hours: float) -> None:
    """后台定期复验记忆中的旧有效链接；单轮失败只记日志。"""
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            died = await service.reverify_stale(older_than_hours=interval_hours)
            logger.info("记忆复验完成，新增失效 %d 条", died)
        except Exception:
            logger.exception("记忆复验异常")


async def _subscribe_loop(watcher: SubscriptionWatcher, interval_hours: float) -> None:
    """后台定期检查追剧订阅；单轮失败只记日志。"""
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            notes = await watcher.run_once()
            logger.info("订阅检查完成，新通知 %d 条", notes)
        except Exception:
            logger.exception("订阅检查异常")


def create_app(
    service: QuarkSearchService | None = None,
    rate_limit_per_minute: int | None = None,
    reverify_interval_hours: float | None = None,
    agent: SearchAgent | None = None,
    subscribe_interval_hours: float | None = None,
    saver: QuarkSaver | None = None,
    save_token: str | None = None,
    qr_login: QuarkQrLogin | None = None,
    cookie_box: CookieBox | None = None,
    quark_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """创建应用；传入 service / agent 便于测试注入假实现。"""
    resolved = service or build_default_service()
    resolved_agent = agent or SearchAgent(
        resolved,
        api_key=_settings.deepseek_api_key if service is None else "",
    )
    limiter = RateLimiter(
        rate=rate_limit_per_minute if rate_limit_per_minute is not None else 10
    )

    interval = (
        reverify_interval_hours
        if reverify_interval_hours is not None
        else (_settings.reverify_interval_hours if service is None else 0)
    )

    watcher = (
        SubscriptionWatcher(
            resolved_agent,
            resolved.store,
            webhook=_settings.notify_webhook if service is None else "",
        )
        if resolved.store is not None
        else None
    )
    sub_interval = (
        subscribe_interval_hours
        if subscribe_interval_hours is not None
        else (_settings.subscribe_interval_hours if service is None else 0)
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks = []
        if interval > 0:
            tasks.append(asyncio.create_task(_reverify_loop(resolved, interval)))
        if watcher is not None and sub_interval > 0:
            tasks.append(asyncio.create_task(_subscribe_loop(watcher, sub_interval)))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        lifespan=lifespan,
        title="QueryPilot",
        description="AI 搜索与链接验证引擎：自然语言输入，多引擎聚合检索，严格验证结果可用性。",
        version="0.10.0",
    )
    app.state.search_service = resolved
    app.state.agent = resolved_agent
    app.state.watcher = watcher
    # 一键转存：cookie 与口令都配置了才开启（测试可注入）
    if saver is None and service is None and _settings.quark_cookie and _settings.save_token:
        saver = QuarkSaver(_settings.quark_cookie, _settings.quark_save_dir_fid)
    resolved_token = save_token if save_token is not None else (
        _settings.save_token if service is None else ""
    )
    app.state.saver = saver if resolved_token else None
    # 扫码登录（多人各自转存到自己的网盘）：凭证按浏览器会话 AES 加密存进记忆库
    if service is None and _settings.quark_login and resolved.store is not None:
        qr_login = qr_login or QuarkQrLogin()
        cookie_box = cookie_box or CookieBox.load(
            _settings.cookie_secret, Path(_settings.memory_db_path).parent / ".cookie_secret"
        )
    login_enabled = qr_login is not None and cookie_box is not None and resolved.store is not None
    quark_http = quark_client or httpx.AsyncClient(timeout=10.0)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        """request_id 贯穿：日志、响应头 X-Request-ID、耗时记录。"""
        request_id = uuid.uuid4().hex
        set_request_id(request_id)
        start = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "http method=%s path=%s status=%s duration_ms=%d",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    def rate_limit_dep(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请稍后重试"},
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        """服务端渲染的搜索页面。"""
        return templates.TemplateResponse(request, "index.html")

    @app.get("/health")
    async def health() -> dict:
        """健康检查：只证明应用进程可响应，不探测外部 API。"""
        return {"status": "ok", "version": "0.10.0"}

    @app.get("/api/memory/stats")
    async def memory_stats() -> dict:
        """记忆库统计：有效/失效链接数与累计搜索次数。"""
        store = app.state.search_service.store
        if store is None:
            return {"enabled": False}
        return {"enabled": True, **(await store.stats())}

    def _store_or_404():
        store = app.state.search_service.store
        if store is None:
            raise HTTPException(status_code=404, detail="记忆功能未开启")
        return store

    @app.get("/api/prefs", response_model=UserPrefs)
    async def get_prefs(client_id: str = Query(min_length=8, max_length=64)) -> UserPrefs:
        """读取某个浏览器的偏好（未设置时返回默认值）。"""
        return await _store_or_404().get_prefs(client_id)

    @app.put("/api/prefs", response_model=UserPrefs)
    async def put_prefs(
        prefs: UserPrefs,
        client_id: str = Query(min_length=8, max_length=64),
        _: None = Depends(rate_limit_dep),
    ) -> UserPrefs:
        await _store_or_404().set_prefs(client_id, prefs)
        return prefs

    @app.post("/api/feedback")
    async def feedback(req: FeedbackRequest) -> dict:
        """用户复制了某条链接：记入记忆，作为排序信号。"""
        recorded = await _store_or_404().record_copy(req.share)
        return {"recorded": recorded}

    ClientId = Query(min_length=8, max_length=64)

    @app.post("/api/subscriptions", response_model=Subscription)
    async def subscribe(
        req: SubscribeRequest, _: None = Depends(rate_limit_dep)
    ) -> Subscription:
        """订阅：之后定期重搜，有新集数或更高清版本时产生通知。"""
        store = _store_or_404()
        baseline = await app.state.watcher.baseline(req.resource)
        sub = await store.add_subscription(req.client_id, req.query, req.resource, baseline)
        if sub is None:
            raise HTTPException(status_code=409, detail="订阅数已达上限（20 个）")
        return sub

    @app.get("/api/subscriptions", response_model=list[Subscription])
    async def list_subscriptions(client_id: str = ClientId) -> list[Subscription]:
        return [sub for _, sub in await _store_or_404().list_subscriptions(client_id)]

    @app.delete("/api/subscriptions/{sub_id}")
    async def unsubscribe(sub_id: int, client_id: str = ClientId) -> dict:
        if not await _store_or_404().delete_subscription(client_id, sub_id):
            raise HTTPException(status_code=404, detail="订阅不存在")
        return {"deleted": True}

    @app.get("/api/notifications", response_model=list[Notification])
    async def notifications(client_id: str = ClientId) -> list[Notification]:
        return await _store_or_404().notifications(client_id)

    @app.post("/api/notifications/read")
    async def notifications_read(client_id: str = ClientId) -> dict:
        await _store_or_404().mark_read(client_id)
        return {"ok": True}

    SESSION_COOKIE = "qp_quark"

    async def _user_cookie(request: Request) -> tuple[str, str, str | None] | None:
        """当前浏览器扫码登录过的夸克账号：(会话哈希, cookie, 昵称)。"""
        token = request.cookies.get(SESSION_COOKIE)
        if not login_enabled or not token:
            return None
        sh = session_hash(token)
        row = await resolved.store.get_account(sh)
        if row is None:
            return None
        cookie = cookie_box.decrypt(row[0], sh.encode())
        if cookie is None:  # 密钥换了：当作未登录
            await resolved.store.delete_account(sh)
            return None
        return sh, cookie, row[1]

    @app.get("/api/save/status")
    async def save_status(request: Request) -> dict:
        """前端据此决定转存按钮怎么走（不透露任何配置内容）。

        - `login`：可以扫码登录自己的夸克；`logged_in` / `nickname`：当前浏览器是否已登录；
        - `token_mode`：部署者在 .env 配置了自己的 cookie，凭口令转存到部署者网盘。
        """
        user = await _user_cookie(request)
        token_mode = app.state.saver is not None
        return {
            "enabled": token_mode or login_enabled,
            "login": login_enabled,
            "logged_in": user is not None,
            "nickname": user[2] if user else None,
            "token_mode": token_mode,
        }

    @app.post("/api/quark/login")
    async def quark_login_start(_: None = Depends(rate_limit_dep)) -> dict:
        """开始扫码登录：返回二维码（SVG 与原始内容），前端轮询状态。"""
        if not login_enabled:
            raise HTTPException(status_code=404, detail="扫码登录未开启")
        try:
            login_id, content = await qr_login.start()
        except LoginError as e:
            raise HTTPException(status_code=502, detail=str(e))
        return {"login_id": login_id, "qr_url": content, "qr_svg": qr_svg(content),
                "expires_in": 300}

    @app.get("/api/quark/login/{login_id}")
    async def quark_login_poll(login_id: str, request: Request, response: Response) -> dict:
        """轮询扫码状态；成功时把凭证加密入库，并给浏览器下发 HttpOnly 会话。"""
        if not login_enabled:
            raise HTTPException(status_code=404, detail="扫码登录未开启")
        try:
            result = await qr_login.poll(login_id[:64])
        except LoginError as e:
            return {"status": "error", "message": str(e)}
        if result.status != "success":
            return {"status": result.status}
        old = await _user_cookie(request)
        if old:
            await resolved.store.delete_account(old[0])
        token = secrets.token_urlsafe(32)
        sh = session_hash(token)
        await resolved.store.put_account(
            sh, cookie_box.encrypt(result.cookie or "", sh.encode()), result.nickname
        )
        response.set_cookie(
            SESSION_COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax",
            secure=request.url.scheme == "https",
        )
        return {"status": "success", "nickname": result.nickname}

    @app.post("/api/quark/logout")
    async def quark_logout(request: Request, response: Response) -> dict:
        """退出：删除服务器上保存的凭证并清掉浏览器会话。"""
        user = await _user_cookie(request)
        if user:
            await resolved.store.delete_account(user[0])
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.post("/api/save", response_model=SaveResponse)
    async def save_to_drive(
        req: SaveRequest,
        request: Request,
        response: Response,
        x_save_token: str = Header(default=""),
        _: None = Depends(rate_limit_dep),
    ) -> SaveResponse:
        """转存到夸克网盘：扫码登录过的存到自己的网盘；否则凭口令存到部署者的网盘。"""
        user = await _user_cookie(request)
        if user is not None:
            saver_obj = QuarkSaver(user[1], _settings.quark_save_dir_fid if service is None
                                   else "0", client=quark_http)
        elif app.state.saver is not None and x_save_token:
            if not hmac.compare_digest(x_save_token.encode(), resolved_token.encode()):
                raise HTTPException(status_code=401, detail="转存口令不正确")
            saver_obj = app.state.saver
        elif login_enabled:
            raise HTTPException(status_code=401, detail="请先扫码登录夸克")
        elif app.state.saver is not None:
            raise HTTPException(status_code=401, detail="转存口令不正确")
        else:
            raise HTTPException(status_code=404, detail="一键转存未开启")
        try:
            result = await saver_obj.save(req.share, req.pwd)
        except LoginExpiredError as e:
            if user is None:
                return SaveResponse(ok=False, message=str(e))
            await resolved.store.delete_account(user[0])
            response.delete_cookie(SESSION_COOKIE)
            return SaveResponse(ok=False, message="夸克登录已过期，请重新扫码登录")
        except SaveError as e:
            return SaveResponse(ok=False, message=str(e))
        name = f"《{result.title}》" if result.title else f"{result.file_count} 个文件"
        message = f"已转存{name}到你的夸克网盘" if result.done else f"已提交转存{name}，夸克正在后台处理"
        return SaveResponse(ok=True, message=message, file_count=result.file_count)

    @app.post("/api/search", response_model=QuarkSearchResponse)
    async def api_search(
        req: SearchRequest,
        _: None = Depends(rate_limit_dep),
    ) -> QuarkSearchResponse:
        try:
            return await app.state.search_service.search(req)
        except SearchUnavailableError:
            raise HTTPException(status_code=503, detail="所有搜索源暂不可用，请稍后重试")

    @app.post("/api/agent/search", response_model=AgentSearchResponse)
    async def api_agent_search(
        req: SearchRequest,
        _: None = Depends(rate_limit_dep),
    ) -> AgentSearchResponse:
        """agent 搜索：多轮「规划 → 搜索/验证 → 观察」，响应附带每一步轨迹。"""
        try:
            return await app.state.agent.run(req)
        except SearchUnavailableError:
            raise HTTPException(status_code=503, detail="所有搜索源暂不可用，请稍后重试")

    @app.get("/api/agent/stream")
    async def api_agent_stream(
        query: str,
        refresh: bool = False,
        client_id: str | None = None,
        session_id: str | None = None,
        _: None = Depends(rate_limit_dep),
    ) -> StreamingResponse:
        """SSE 流式 agent 搜索：每完成一步推送 `step` 事件，最后推送 `result`。"""
        try:
            req = SearchRequest(
                query=query, refresh=refresh, client_id=client_id, session_id=session_id
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="输入长度需为 2–200 个字符")

        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

        async def emit(step: AgentStep) -> None:
            await queue.put(("step", step.model_dump_json()))

        async def run() -> None:
            try:
                result = await app.state.agent.run(req, emit=emit)
                await queue.put(("result", result.model_dump_json()))
            except SearchUnavailableError:
                await queue.put(("error", '{"detail":"所有搜索源暂不可用，请稍后重试"}'))
            except Exception:
                logger.exception("agent 流式搜索异常")
                await queue.put(("error", '{"detail":"服务器内部错误，请稍后重试"}'))
            finally:
                await queue.put(None)

        async def events() -> AsyncIterator[str]:
            task = asyncio.create_task(run())
            try:
                while (item := await queue.get()) is not None:
                    yield f"event: {item[0]}\ndata: {item[1]}\n\n"
            finally:
                if not task.done():  # 客户端断开：取消后台搜索
                    task.cancel()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
