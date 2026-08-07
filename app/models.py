"""Pydantic 数据契约：请求、响应与领域模型。

第三方供应商响应在适配器边界转换为 `RawSearchResult`，核心层只认识这些模型。
"""

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

ResourceType = Literal["game", "movie", "music", "software", "other"]


class SearchRequest(BaseModel):
    """`POST /api/search` 请求体。"""

    query: str = Field(min_length=2, max_length=200)


class SearchIntent(BaseModel):
    """DeepSeek 解析出的结构化意图（失败时由规则降级生成）。"""

    resource_type: ResourceType
    keywords: list[str] = Field(min_length=1, max_length=8)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    query_variants: list[str] = Field(min_length=1, max_length=3)


class RawSearchResult(BaseModel):
    """适配器输出的统一原始结果（未去重、未评分）。"""

    title: str
    url: str
    snippet: str = ""
    provider: str
    relevance: float | None = None  # 供应商相关性；缺失时评分用中性值


class SearchResult(BaseModel):
    """聚合排序后的最终结果。"""

    title: str
    url: HttpUrl
    snippet: str
    sources: list[str]
    score: int = Field(ge=0, le=100)
    reason: str


class ProviderStatus(BaseModel):
    """单个搜索提供方的执行状态，不暴露内部堆栈或密钥。"""

    name: str
    status: Literal["ok", "error", "skipped"] = "ok"
    result_count: int = 0
    duration_ms: int = 0
    error_type: str | None = None


class SearchMetrics(BaseModel):
    """整条请求的流水线指标。"""

    duration_ms: int
    raw_result_count: int
    deduplicated_result_count: int
    fallback_used: bool = False


class SearchResponse(BaseModel):
    """`POST /api/search` 响应体。"""

    request_id: str
    intent: SearchIntent
    results: list[SearchResult] = Field(default_factory=list)
    providers: list[ProviderStatus] = Field(default_factory=list)
    metrics: SearchMetrics
