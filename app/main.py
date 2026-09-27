"""QueryPilot FastAPI 入口。

页面、API 与夸克网盘链接搜索编排收敛在单个服务内，
方便本地调试与国内服务器 Docker 部署。
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
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
    SearchRequest,
    SubscribeRequest,
    Subscription,
    UserPrefs,
)
from app.providers.tavily import TavilyProvider
from app.security import RateLimiter, install_request_id_factory, set_request_id
from app.services.agent import SearchAgent
from app.services.intent import DeepSeekParser
from app.services.memory import LinkStore
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
        version="0.9.0",
    )
    app.state.search_service = resolved
    app.state.agent = resolved_agent
    app.state.watcher = watcher
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
        return {"status": "ok", "version": "0.9.0"}

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
