"""Pydantic 数据契约：请求、响应与领域模型。

第三方供应商响应在适配器边界转换为 `RawSearchResult`，核心层只认识这些模型。
"""

import time
from typing import Literal

from pydantic import BaseModel, Field, computed_field

ResourceType = Literal["game", "movie", "music", "software", "other"]


class SearchRequest(BaseModel):
    """`POST /api/search` 请求体。"""

    query: str = Field(min_length=2, max_length=200)
    refresh: bool = False  # True 时忽略记忆快速返回，强制全网重新搜索
    client_id: str | None = Field(default=None, max_length=64)  # 浏览器标识，用于读取偏好
    session_id: str | None = Field(default=None, max_length=64)  # 追问：上一轮的会话标识


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
    # 今日额度：{ai, reason, message, used, limit, remaining, logged_in}；未开启额度时为空
    quota: dict | None = None


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
    session_id: str = ""  # 用于追问的会话标识
    history: list[str] = Field(default_factory=list)  # 本会话里用户说过的话
    followup: dict | None = None  # 对追问的解释（首轮为空）
    filters: dict = Field(default_factory=dict)  # 当前生效的条件：季数/清晰度/字幕/HDR


class UserPrefs(BaseModel):
    """用户偏好（按浏览器标识保存在记忆库）。"""

    min_resolution: Literal["2160p", "1080p", "720p"] | None = None  # 默认最低清晰度
    prefer_subtitle: bool = False
    prefer_hdr: bool = False


class FeedbackRequest(BaseModel):
    """用户对某条链接的反馈（目前只有「复制」）。"""

    share: str = Field(min_length=6, max_length=32, pattern=r"^[0-9a-zA-Z]+$")


class ReportRequest(BaseModel):
    """搜索结果反馈：wrong 不是要找的这部（只对这部片排除），dead 链接失效（所有人都不再推荐）。"""

    share: str = Field(min_length=6, max_length=32, pattern=r"^[0-9a-zA-Z]+$")
    reason: Literal["wrong", "dead"]
    query: str = Field(default="", max_length=200)


MediaType = Literal["movie", "tv"]
Resolution = Literal["2160p", "1080p", "720p", "SD"]
RESOLUTION_RANK = {"SD": 1, "720p": 2, "1080p": 3, "2160p": 4}


class AirEpisode(BaseModel):
    """一集的播出信息（TMDB）。"""

    episode: int
    air_date: str | None = None  # YYYY-MM-DD；TMDB 还没定的为 None
    name: str | None = None


class SubscribeRequest(BaseModel):
    """订阅一部电影 / 一季剧集。

    推荐先用 `GET /api/media/search` 选中条目，把它的 media/tmdb_id/douban_id/season 等带上；
    只给 resource 时后端自己识别（TMDB → 豆瓣），识别不到就按关键词订阅。
    """

    client_id: str = Field(min_length=8, max_length=64)
    query: str = Field(min_length=2, max_length=200)  # 定期检查时用的搜索词
    resource: str = Field(min_length=1, max_length=100)  # 资源名（记忆库主键来源）
    # 订阅同时打开自动转存（需要登录）；没搜到资源也能订阅，等有资源时自动存
    auto_save: bool = False
    media: MediaType | None = None
    season: int | None = Field(default=None, ge=1, le=100)
    year: str | None = Field(default=None, max_length=4)
    tmdb_id: str | None = Field(default=None, max_length=20)
    douban_id: str | None = Field(default=None, max_length=20)
    poster: str | None = Field(default=None, max_length=500)
    total_episodes: int | None = Field(default=None, ge=1, le=5000)  # 手动指定总集数
    start_episode: int = Field(default=1, ge=1, le=5000)
    resolution: Resolution | None = None  # 清晰度要求（不低于）
    include: str | None = Field(default=None, max_length=100)  # 分享名须包含（空格分隔，全部满足）
    exclude: str | None = Field(default=None, max_length=100)  # 分享名含任一即排除
    # 洗版（需同时开自动转存）：已存的集清晰度没达到 upgrade_to 前，出现更高清的就再存一份新版本
    upgrade: bool = False
    upgrade_to: Resolution | None = None  # 洗版目标，默认 2160p
    # 单独订阅系列电影里的某一部：候选里的系列信息带上，卡片显示「系列 · 第 N 部」
    collection_id: str | None = Field(default=None, max_length=20)
    collection_name: str | None = Field(default=None, max_length=100)
    collection_index: int | None = Field(default=None, ge=1, le=200)


class SeasonsSubscribeRequest(SubscribeRequest):
    """一次订阅一部剧的全部季：和单季订阅一样的请求体（必须带 tmdb_id），再加 auto_join。"""

    tmdb_id: str = Field(min_length=1, max_length=20)
    auto_join: bool = False  # 以后出新季自动加入


class NoticeReadRequest(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=500)  # 不传或空：全部已读


class CollectionSubscribeRequest(BaseModel):
    """订阅整个系列（TMDB collection）：每部建一个电影订阅，共用这组规则；整个系列只占 1 个名额。"""

    client_id: str = Field(min_length=8, max_length=64)
    collection_id: str = Field(min_length=1, max_length=20)
    auto_join: bool = False  # 以后出新作自动加入
    auto_save: bool = False
    resolution: Resolution | None = None
    include: str | None = Field(default=None, max_length=100)
    exclude: str | None = Field(default=None, max_length=100)
    upgrade: bool = False
    upgrade_to: Resolution | None = None


class SeriesSubscription(BaseModel):
    """一个整个系列的订阅（系列级设置）。"""

    collection_id: str
    name: str
    poster: str | None = None
    auto_join: bool = False
    created: float
    subscriptions: int = 0  # 系列里还在订阅中的部数（完成的移入订阅历史）


class SeriesUpdate(BaseModel):
    auto_join: bool


def unreleased(release_date: str | None, series: bool) -> bool:
    """电影还没上映：上映日期在今天之后；系列里没定档（TMDB 没给日期）的也算。"""
    if release_date:
        return release_date > time.strftime("%Y-%m-%d")
    return series


class Subscription(BaseModel):
    id: int
    query: str
    resource: str
    created: float
    last_checked: float | None = None
    last_error: str | None = None  # 上次检查失败的原因（给用户看的）；成功检查后清空
    best_episodes: int = 0  # 目前见过的最多集数（有效且相关的链接里）
    best_score: int = 0  # 目前见过的最高质量分
    best_resolution: str | None = None
    auto_save: bool = False  # 发现新集时自动转存到自己的夸克网盘（需要扫码登录）
    # 自动转存暂停的原因：login_expired（夸克登录失效，重新扫码后自动恢复）/ no_login；正常为 None
    auto_save_status: str | None = None
    # ---- v2：以影视条目为订阅对象（借鉴 MoviePilot 的设计思路）----
    # new 新建（等第一次搜索）/ active 订阅中 / pending 待定（没识别出条目或总集数，能搜不能自动完成）
    # / paused 暂停（不检查）
    state: Literal["new", "active", "pending", "paused"] = "active"
    media: MediaType | None = None
    season: int | None = None
    year: str | None = None  # 片子（剧集首播）年份：网盘目录名、相关性都按它
    season_year: str | None = None  # 这一季开播年份（TMDB）：卡片展示用，没有时显示 year
    tmdb_id: str | None = None
    douban_id: str | None = None
    poster: str | None = None
    total_episodes: int | None = None  # 这一季总集数（TMDB/豆瓣，或手动设定）
    start_episode: int = 1
    manual_total: bool = False  # 手动改过总集数：之后不再被元数据自动覆盖
    resolution: Resolution | None = None
    include: str | None = None
    exclude: str | None = None
    saved_episodes: list[int] = Field(default_factory=list)  # 网盘里已有的集（按转存时的目录清点）
    # 第一次自动转存时确定并锁定的网盘目录（按条目：类型/地区/片名 (年份)/Season 01），之后不再重新分类
    folder: str | None = None
    upgrade: bool = False
    upgrade_to: Resolution | None = None
    # 已存各集的清晰度（集号 → 2160p/1080p/720p/SD；电影用 0）；认不出清晰度的集不在里面
    versions: dict[int, str] = Field(default_factory=dict)
    # 系列电影：属于哪个系列、第几部；series=True 表示是「订阅整个系列」建的（不单独占名额）
    collection_id: str | None = None
    collection_name: str | None = None
    collection_index: int | None = None
    series: bool = False
    release_date: str | None = None  # 电影上映日期（系列里还没上映的：待定，上映后才开始搜）
    # 这一季的播出日历（TMDB，每天随元数据刷新）；不在订阅接口里返回，见 /api/calendar
    schedule: list[AirEpisode] = Field(default_factory=list, exclude=True)

    @property
    def wanted(self) -> list[int]:
        """订阅范围内的集号；不知道总集数时为空。"""
        if self.media != "tv" or not self.total_episodes:
            return []
        return list(range(self.start_episode, self.total_episodes + 1))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lack_episodes(self) -> list[int] | None:
        """范围内网盘还缺的集；不知道总集数时为 None。"""
        if not self.wanted:
            return None
        have = set(self.saved_episodes)
        return [e for e in self.wanted if e not in have]

    def upgradable(self, movie: bool) -> dict[int, int]:
        """洗版时还能升级的集：{集号: 当前清晰度等级}（电影用 0）；没开洗版为空。

        只看已经存进网盘的集；清晰度认不出的按最低（0）算。"""
        if not self.upgrade:
            return {}
        target = RESOLUTION_RANK[self.upgrade_to or "2160p"]
        if movie:
            eps = [0]
        else:
            eps = [e for e in self.saved_episodes if not self.wanted or e in self.wanted]
        ranks = {e: RESOLUTION_RANK.get(self.versions.get(e) or "", 0) for e in eps}
        return {e: r for e, r in ranks.items() if r < target}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def show_id(self) -> str | None:
        """整部剧一起订阅时的剧 id（TMDB），前端按它把各季折叠成一组；否则为 None。"""
        cid = self.collection_id or ""
        return cid[3:] if self.series and cid.startswith("tv:") else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_seasons(self) -> bool:
        """是否是「全部季」一起订阅建的。"""
        return self.show_id is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def upgrade_done(self) -> bool | None:
        """洗版是否已完成（范围内已存的都达到目标清晰度）；没开洗版为 None。"""
        if not self.upgrade:
            return None
        if self.media == "movie":
            return 0 in self.versions and not self.upgradable(True)
        return bool(self.saved_episodes) and not self.upgradable(False)


class SubscriptionUpdate(BaseModel):
    """修改订阅：只改给出的字段。"""

    auto_save: bool | None = None
    paused: bool | None = None
    total_episodes: int | None = Field(default=None, ge=1, le=5000)
    start_episode: int | None = Field(default=None, ge=1, le=5000)
    resolution: Resolution | Literal[""] | None = None  # "" 表示清除
    include: str | None = Field(default=None, max_length=100)
    exclude: str | None = Field(default=None, max_length=100)
    upgrade: bool | None = None
    upgrade_to: Resolution | Literal[""] | None = None  # "" 表示恢复默认（2160p）


AutoSaveRequest = SubscriptionUpdate  # 兼容旧名


class OrganizeRequest(BaseModel):
    """执行整理：移动 / 重命名按服务器重新算出的计划做；删除只删这里列出、且在计划的建议删除里的。"""

    delete_fids: list[str] = Field(default_factory=list, max_length=500)


class SubscriptionHistory(BaseModel):
    """已完成（或手动结束）的订阅，可重新订阅。"""

    id: int
    query: str
    resource: str
    media: MediaType | None = None
    season: int | None = None
    year: str | None = None
    tmdb_id: str | None = None
    douban_id: str | None = None
    poster: str | None = None
    total_episodes: int | None = None
    saved_count: int = 0
    created: float
    completed: float
    reason: str  # 如「已集齐 12 集」「手动完成」


class CollectionPart(BaseModel):
    """系列电影里的一部（TMDB collection 的 parts，按上映日期排好），或剧集的一季。"""

    index: int  # 第几部，从 1 开始；剧集是季号
    id: str  # 这一部的 TMDB 电影 id；剧集是 s<季号>
    title: str
    original_title: str | None = None
    year: str | None = None
    release_date: str | None = None  # YYYY-MM-DD；没有表示还没定档
    poster: str | None = None
    released: bool = False  # 已上映
    episodes: int | None = None  # 剧集：这一季的总集数


class CollectionInfo(BaseModel):
    id: str  # TMDB collection id；整部剧是 tv:<TMDB 剧集 id>
    name: str  # 去掉「（系列）」等后缀，如「谍影重重」；整部剧是剧名
    poster: str | None = None
    parts: list[CollectionPart] = Field(default_factory=list)
    media: MediaType = "movie"  # tv：整部剧的各季
    tmdb_id: str | None = None  # 整部剧的 TMDB id
    year: str | None = None  # 整部剧的首播年


class MediaCandidate(BaseModel):
    """订阅前让用户选的影视条目。系列电影合并成一个 kind=collection 的候选，用下拉框选第几部。"""

    source: str  # tmdb / douban
    id: str | None = None  # 条目 id；系列候选是 collection id
    title: str
    original_title: str | None = None
    year: str | None = None
    media: MediaType | None = None  # 系列候选为 movie
    kind: Literal["movie", "tv", "collection"] | None = None  # 豆瓣认不出类型时为 None
    poster: str | None = None
    seasons: int | None = None
    episodes: dict[int, int] = Field(default_factory=dict)  # 季 → 总集数
    collection: CollectionInfo | None = None  # 只有 kind=collection 时有
    default_part: int | None = None  # 默认选中第几部（从 1 开始）


# 通知 kind → (卡片类型 type, 一句话说明)
NOTICE_TYPES: dict[str, tuple[str, str]] = {
    "found": ("found", "找到资源了"),
    "episodes": ("new_episodes", "有新集了"),
    "quality": ("better_quality", "有更高清的版本"),
    "maybe": ("maybe", "找到可能相关的资源，请自己核对"),
    "auto_saved": ("saved", "已自动转存到你的网盘"),
    "upgraded": ("upgraded", "已转存更高清的版本"),
    "auto_save_failed": ("save_failed", "自动转存失败"),
    "auto_save_paused": ("save_paused", "自动转存已暂停，需要重新扫码登录"),
    "completed": ("completed", "订阅已完成"),
    "series_new": ("series_new", "出了新的一部 / 一季，已自动订阅"),
}


class Notification(BaseModel):
    """订阅通知。`message` 是原来的整段文字（兼容旧前端）；卡片用后面的结构化字段。"""

    id: int
    subscription_id: int
    resource: str
    # episodes 新集 / found 有资源了 / quality 更高清 / completed 订阅完成 / maybe 可能相关 /
    # auto_saved / auto_save_failed / auto_save_paused 自动转存结果 / upgraded 洗版 / series_new
    kind: str
    message: str
    share: str | None = None  # 带来更新的那条链接（分享码）
    ts: float
    read: bool = False
    # ---- 卡片用的结构化字段 ----
    type: str = ""  # found / new_episodes / better_quality / maybe / saved / upgraded /
    # save_failed / save_paused / completed / series_new（见 NOTICE_TYPES）
    summary: str = ""  # 一句话中文说明，如「找到可能相关的资源，请自己核对」
    subscription_name: str = ""  # 订阅名（去掉「第N季」）
    season: int | None = None  # 剧集第几季
    part: int | None = None  # 系列电影第几部
    resource_title: str | None = None  # 分享的标题（资源名）
    url: str | None = None  # 分享链接
    created_at: str = ""  # ISO 时间（北京时间，带 +08:00）


class SaveRequest(BaseModel):
    """一键转存某条分享链接到部署者的夸克网盘。"""

    share: str = Field(min_length=6, max_length=32, pattern=r"^[0-9a-zA-Z]+$")
    pwd: str | None = Field(default=None, pattern=r"^[0-9a-zA-Z]{4}$")


class SaveResponse(BaseModel):
    ok: bool
    message: str
    file_count: int = 0
    folder: str | None = None  # 自动分类后存入的网盘目录
    category: str | None = None  # 识别出的类别，如「国产剧」「欧美电影」
