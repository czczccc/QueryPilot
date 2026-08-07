"""QueryPilot FastAPI 入口。

页面、API 与搜索编排收敛在单个服务内，方便本地调试与国内服务器 Docker 部署。
"""

from fastapi import FastAPI, HTTPException

from app.config import load_settings
from app.models import SearchRequest, SearchResponse
from app.providers.tavily import TavilyProvider
from app.services.intent import DeepSeekParser
from app.services.search import SearchService, SearchUnavailableError


def build_default_service() -> SearchService:
    """按环境变量构建生产服务（密钥来自环境或 .env，绝不落仓库）。"""
    settings = load_settings()
    parser = DeepSeekParser(
        api_key=settings.deepseek_api_key,
        timeout=settings.request_timeout_seconds,
    )
    provider = TavilyProvider(
        api_key=settings.tavily_api_key,
        timeout=settings.request_timeout_seconds,
    )
    return SearchService(
        parser=parser,
        providers=[provider],
        max_results=settings.max_results,
    )


def create_app(service: SearchService | None = None) -> FastAPI:
    """创建应用；传入 service 便于测试注入假实现。"""
    resolved = service or build_default_service()
    app = FastAPI(
        title="QueryPilot",
        description="可解释的 AI 聚合搜索：自然语言输入，多查询改写、搜索、去重排序并解释匹配原因。",
        version="0.1.0",
    )
    app.state.search_service = resolved

    @app.on_event("startup")
    async def _startup() -> None:
        # 保持 state 一致；测试注入的 service 不被覆盖
        app.state.search_service = resolved

    @app.get("/health")
    async def health() -> dict:
        """健康检查：只证明应用进程可响应，不探测外部 API。"""
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/api/search", response_model=SearchResponse)
    async def api_search(req: SearchRequest) -> SearchResponse:
        try:
            return await app.state.search_service.search(req)
        except SearchUnavailableError:
            raise HTTPException(status_code=503, detail="所有搜索源暂不可用，请稍后重试")

    return app


app = create_app()
