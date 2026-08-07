"""Pydantic 数据契约：请求、响应与领域模型。

第三方供应商响应在适配器边界转换为 `RawSearchResult`，核心层只认识这些模型。
"""

from typing import Literal

from pydantic import BaseModel, Field

ResourceType = Literal["game", "movie", "music", "software", "other"]


class SearchRequest(BaseModel):
    """`POST /api/search` 请求体。"""

    query: str = Field(min_length=2, max_length=200)


class RawSearchResult(BaseModel):
    """适配器输出的统一原始结果（未去重、未评分）。"""

    title: str
    url: str
    snippet: str = ""
    provider: str
    relevance: float | None = None  # 供应商相关性；缺失时评分用中性值


class ParsedResource(BaseModel):
    """DeepSeek 解析出的影视资源信息（失败时由规则降级生成）。"""

    resource: str
    quality: str | None = None
    preference: str | None = None
    aliases: list[str] = Field(default_factory=list, max_length=8)
    english_name: str | None = None
    search_suggestions: list[str] = Field(min_length=1, max_length=6)


class QuarkLink(BaseModel):
    """一条夸克网盘分享链接（含提取码与可达性验证）。"""

    name: str
    share: str
    pwd: str | None = None
    source: str
    time: str
    conf: str = "中"  # 置信度：高/中/低
    http: int | None = None  # 壳页状态码
    state: str = "unknown"  # 严格验证状态：valid / invalid / unknown


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


class DoubanMeta(BaseModel):
    """豆瓣链接识别出的影视资源元信息。"""

    subject_id: str
    url: str
    title: str
    year: str | None = None
    kind: str | None = None


class QuarkSearchResponse(BaseModel):
    """`POST /api/search` 响应体（网盘链接搜索）。"""

    request_id: str
    query: str
    parsed: ParsedResource
    links: list[QuarkLink] = Field(default_factory=list)
    providers: list[ProviderStatus] = Field(default_factory=list)
    metrics: SearchMetrics
    douban: DoubanMeta | None = None  # 输入为豆瓣链接时填充
