"""QueryPilot FastAPI 入口。

页面、API 与搜索编排收敛在单个服务内，方便本地调试与国内服务器 Docker 部署。
"""

from fastapi import FastAPI

app = FastAPI(
    title="QueryPilot",
    description="可解释的 AI 聚合搜索：自然语言输入，多查询改写、搜索、去重排序并解释匹配原因。",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    """健康检查：只证明应用进程可响应，不探测外部 API。"""
    return {"status": "ok", "version": "0.1.0"}
