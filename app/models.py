"""Pydantic 数据契约：请求、响应与领域模型。

第三方供应商响应在适配器边界转换为 `RawSearchResult`，核心层只认识这些模型。
"""

from typing import Literal

from pydantic import BaseModel, Field

ResourceType = Literal["game", "movie", "music", "software", "other"]


class SearchRequest(BaseModel):
    """`POST /api/search` 请求体。"""

    query: str = Field(min_length=2, max_length=200)
    refresh: bool = False  # True 时忽略记忆快速返回，强制全网重新搜索
    client_id: str | None = Field(default=None, max_length=64)  # 浏览器标识，用于读取偏好


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


class QualityInfo(BaseModel):
    """从分享文件列表推断出的资源质量（规则识别，见 services/quality.py）。"""

    resolution: Literal["2160p", "1080p", "720p", "SD"] | None = None
    resolution_guessed: bool = False  # True 表示由单文件体积推断，而非文件名
    hdr: bool = False
    codec: str | None = None
    source: str | None = None  # REMUX / BluRay / WEB-DL / HDTV
    low_quality: bool = False  # 枪版/TC/CAM
    video_count: int = 0
    size_bytes: int | None = None
    has_subtitle: bool = False
    score: int = 0


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
    quality: QualityInfo | None = None  # 仅验证有效时填充
    share_title: str | None = None  # 夸克分享页上的标题（验证时获得）
    files_preview: list[str] = Field(default_factory=list)  # 分享内前几个文件/文件夹名
    relevance: Literal["match", "uncertain", "mismatch"] = "uncertain"  # 是否是要找的那部
    relevance_note: str | None = None  # 判定依据，如「年份不符：2019」
    copy_count: int = 0  # 被用户复制的次数（反馈信号）
    from_memory: bool = False  # 来自记忆库（之前搜索验证过）
    last_checked: float | None = None  # 最近一次验证的 Unix 时间戳（记忆库链接）


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
    memory_hits: int = 0  # 从记忆库复用的链接数
    skipped_invalid: int = 0  # 记忆中近期已失效、直接跳过验证的链接数
    served_from_memory: bool = False  # 记忆足够，跳过了全网搜索


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


class AgentStep(BaseModel):
    """agent 的一步：调用了哪个工具、参数、观察结果摘要。"""

    step: int
    tool: str
    args: dict = Field(default_factory=dict)
    observation: dict = Field(default_factory=dict)
    thought: str | None = None  # LLM 给出的简短理由（规则规划时为空）
    planner: Literal["llm", "rules"] = "rules"
    duration_ms: int = 0


class AgentSearchResponse(QuarkSearchResponse):
    """agent 搜索响应：在普通响应基础上附带每一步的轨迹。"""

    steps: list[AgentStep] = Field(default_factory=list)
    planner: Literal["llm", "rules"] = "rules"
    stop_reason: str = ""
    required_resolution: str | None = None
    matching_count: int = 0  # 满足清晰度要求的有效链接数


class UserPrefs(BaseModel):
    """用户偏好（按浏览器标识保存在记忆库）。"""

    min_resolution: Literal["2160p", "1080p", "720p"] | None = None  # 默认最低清晰度
    prefer_subtitle: bool = False
    prefer_hdr: bool = False


class FeedbackRequest(BaseModel):
    """用户对某条链接的反馈（目前只有「复制」）。"""

    share: str = Field(min_length=6, max_length=32, pattern=r"^[0-9a-zA-Z]+$")
