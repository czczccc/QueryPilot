"""QueryPilot FastAPI 入口。

页面、API 与夸克网盘链接搜索编排收敛在单个服务内，
方便本地调试与国内服务器 Docker 部署。
"""

import logging
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import load_settings
from app.models import QuarkSearchResponse, SearchRequest
from app.providers.tavily import TavilyProvider
from app.security import RateLimiter, install_request_id_factory, set_request_id
from app.services.intent import DeepSeekParser
from app.services.search import QuarkSearchService, SearchUnavailableError

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
    return QuarkSearchService(
        parser=parser,
        tavily=provider,
        use_qkyunso=True,
        use_bing=True,
        timeout=_settings.request_timeout_seconds,
    )


def create_app(
    service: QuarkSearchService | None = None,
    rate_limit_per_minute: int | None = None,
) -> FastAPI:
    """创建应用；传入 service 便于测试注入假实现。"""
    resolved = service or build_default_service()
    limiter = RateLimiter(
        rate=rate_limit_per_minute if rate_limit_per_minute is not None else 10
    )

    app = FastAPI(
        title="QueryPilot",
        description="AI 搜索与链接验证引擎：自然语言输入，多引擎聚合检索，严格验证结果可用性。",
        version="0.3.0",
    )
    app.state.search_service = resolved
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
        return {"status": "ok", "version": "0.3.0"}

    @app.post("/api/search", response_model=QuarkSearchResponse)
    async def api_search(
        req: SearchRequest,
        _: None = Depends(rate_limit_dep),
    ) -> QuarkSearchResponse:
        try:
            return await app.state.search_service.search(req)
        except SearchUnavailableError:
            raise HTTPException(status_code=503, detail="所有搜索源暂不可用，请稍后重试")

    return app


app = create_app()
