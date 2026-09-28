"""QueryPilot FastAPI 入口。

页面、API 与夸克网盘链接搜索编排收敛在单个服务内，
方便本地调试与国内服务器 Docker 部署。
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.admin import admin_router
from app.config import load_settings
from app.models import (
    AgentSearchResponse,
    AgentStep,
    FeedbackRequest,
    MediaCandidate,
    Notification,
    OrganizeRequest,
    QuarkLink,
    QuarkSearchResponse,
    SaveRequest,
    SaveResponse,
    SearchRequest,
    SubscribeRequest,
    Subscription,
    SubscriptionHistory,
    SubscriptionUpdate,
    UserPrefs,
)
from app.providers.tavily import TavilyProvider
from app.security import RateLimiter, install_request_id_factory, set_request_id
from app.services import calendar as cal
from app.services import llm
from app.services.agent import SearchAgent
from app.services.classify import Category, Classifier, episode_no, safe_name
from app.services.cookie_box import CookieBox, session_hash
from app.services.intent import DeepSeekParser
from app.services.memory import LinkStore, resource_key
from app.services.metadata import MetadataLookup
from app.services.organize import TidyPlan, file_resolution, kind_of, plan_tidy
from app.services.quality import RESOLUTION_RANK
from app.services.quark_login import LoginError, QuarkQrLogin, qr_svg
from app.services.quark_save import LoginExpiredError, QuarkSaver, SaveError, Tidy
from app.services.relevance import seasons_in
from app.services.search import QuarkSearchService, SearchUnavailableError
from app.services.series import build_candidates
from app.services.subscriptions import RES_TEXT, SubscriptionWatcher, strip_season
from app.services.trending import Trending
from app.services.usage import SYSTEM, QuotaConfig, QuotaGuard, UsageStore

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_settings = load_settings()
logging.basicConfig(
    level=_settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s request_id=%(request_id)s",
)
install_request_id_factory()
logger = logging.getLogger(__name__)


@dataclass
class Who:
    """当前请求是谁：额度身份、IP 身份、是否登录，以及站长设置的单独额度 / 封禁说明。"""

    subject: str
    ip_subject: str
    logged_in: bool
    nickname: str | None = None
    ai_limit: int | None = None
    banned: str | None = None


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
        pansou_url=_settings.pansou_url,
        pansou_token=_settings.pansou_token,
        pansou_timeout=_settings.pansou_timeout,
        pansou_src=_settings.pansou_src,
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


async def _subscribe_loop(
    watcher: SubscriptionWatcher, interval_hours: float, quota: QuotaGuard | None = None
) -> None:
    """后台定期检查追剧订阅；单轮失败只记日志。LLM 用量记到 system，也受全站预算约束。"""
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            allowed = True
            if quota is not None:
                allowed = (await quota.check(SYSTEM, SYSTEM, True)).reason != "site_budget"
            with llm.scope(allowed=allowed) as meter:
                notes = await watcher.run_once()
            if quota is not None:
                await quota.record(SYSTEM, SYSTEM, meter, searched=False)
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
    classifier=None,
    quota: QuotaGuard | None = None,
    invite_required: bool | None = None,
    invite_codes: tuple[str, ...] | None = None,
    admin_token: str | None = None,
    media_lookup: MetadataLookup | None = None,
    trending: Trending | None = None,
    search_on_subscribe: bool | None = None,
) -> FastAPI:
    """创建应用；传入 service / agent 便于测试注入假实现。"""
    resolved = service or build_default_service()
    resolved_agent = agent or SearchAgent(
        resolved,
        api_key=_settings.deepseek_api_key if service is None else "",
        cache_minutes=_settings.search_cache_minutes if service is None else 0,
    )
    per_minute = (
        rate_limit_per_minute if rate_limit_per_minute is not None
        else _settings.rate_limit_per_minute
    )
    limiter = RateLimiter(rate=per_minute or 10**9)
    # 额度与用量：测试注入的 service 默认不开，生产按 .env
    if quota is None and service is None:
        quota = QuotaGuard(
            UsageStore(_settings.memory_db_path or ":memory:"),
            QuotaConfig(
                anon_daily_ai=_settings.anon_daily_ai,
                user_daily_ai=_settings.user_daily_ai,
                site_daily_tokens=_settings.site_daily_tokens,
                ip_daily_searches=_settings.ip_daily_searches,
                anon_daily_searches=_settings.anon_daily_searches if _settings.quark_login else 0,
            ),
        )
    trust_proxy = _settings.trust_proxy if service is None else False

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
            tasks.append(asyncio.create_task(_subscribe_loop(watcher, sub_interval, quota)))
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
    app.state.quota = quota
    # 一键转存：cookie 与口令都配置了才开启（测试可注入）
    # 转存自动分类：有 LLM key 用 LLM 判断，否则规则；SAVE_CLASSIFY=false 关闭
    # 影视条目识别（TMDB / 豆瓣）：订阅选条目、刷新总集数、转存分类共用
    if media_lookup is None and service is None:
        media_lookup = MetadataLookup(
            tmdb_key=_settings.tmdb_api_key, tmdb_base=_settings.tmdb_api_base,
            douban=_settings.douban_lookup,
        )
    if trending is None:
        trending = Trending(
            tmdb_key=_settings.tmdb_api_key if service is None else "",
            tmdb_base=_settings.tmdb_api_base,
            douban=_settings.douban_lookup and service is None,
        )
    if watcher is not None:
        watcher.lookup = media_lookup
    if agent is None:
        resolved_agent.lookup = media_lookup
    if search_on_subscribe is None:
        search_on_subscribe = service is None
    if classifier is None and service is None and _settings.save_classify:
        classifier = Classifier(api_key=_settings.deepseek_api_key, lookup=media_lookup)
    root_dir = _settings.save_root_dir if service is None else "QueryPilot"
    if saver is None and service is None and _settings.quark_cookie and _settings.save_token:
        saver = QuarkSaver(_settings.quark_cookie, _settings.quark_save_dir_fid,
                           classifier=classifier, root_dir=root_dir)
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
    # 账号表与额度表放在一起（未开额度时用内存库，主要给测试）
    users = quota.store if quota is not None else UsageStore(":memory:")
    need_invite = (
        invite_required if invite_required is not None
        else (_settings.invite_required if service is None else False)
    )
    env_invites = set(
        invite_codes if invite_codes is not None
        else (_settings.invite_codes if service is None else ())
    )
    pending_invites: dict[str, str] = {}  # login_id → 用户填的邀请码（扫码成功后才校验）
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

    def client_ip(request: Request) -> str:
        """真实客户端 IP：TRUST_PROXY 开启时取 X-Forwarded-For 的第一个地址。"""
        if trust_proxy:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            if forwarded:
                return forwarded[:64]
        return request.client.host if request.client else "unknown"

    def rate_limit_dep(request: Request) -> None:
        if not limiter.allow(client_ip(request)):
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

    async def _owner(request: Request, client_id: str) -> str:
        """订阅与通知的归属：登录用户按账号（换浏览器也在），否则按浏览器标识。"""
        user = await _user_cookie(request)
        return f"u:{user[3]}" if user else client_id

    @app.get("/api/trending")
    async def trending_media() -> dict:
        """首页热门影视（搜索框占位和「试试」）：TMDB 本周热门 → 豆瓣热门 → 内置列表，缓存 6 小时。

        返回 {"source": "tmdb"|"douban"|"default", "items": [{title, year, media, poster}]}。"""
        return await trending()

    @app.get("/api/media/search", response_model=list[MediaCandidate])
    async def media_search(
        q: str = Query(min_length=1, max_length=100), year: str | None = Query(None, max_length=4),
        _: None = Depends(rate_limit_dep),
    ) -> list[MediaCandidate]:
        """订阅前选影视条目（TMDB 配了 key 才查，豆瓣无需 key），每个来源最多 10 条；
        同一系列的电影合并成一个 kind=collection 的候选（带全部作品）。都查不到返回空列表。"""
        if media_lookup is None:
            return []
        name = strip_season(q.strip())
        infos = await media_lookup(name, year, limit=10)
        return await build_candidates(name, infos, getattr(media_lookup, "collection", None))

    async def _identify(req: SubscribeRequest) -> dict:
        """订阅的条目信息：前端选好的优先，缺的用 TMDB / 豆瓣补；都没有就按关键词订阅。"""
        f: dict = {k: getattr(req, k) for k in (
            "media", "season", "year", "tmdb_id", "douban_id", "poster", "start_episode",
            "resolution", "include", "exclude", "upgrade_to") if getattr(req, k) is not None}
        if req.upgrade:
            f["upgrade"] = True
        if f.get("season") is None:
            seasons = seasons_in(f"{req.resource} {req.query}")
            if len(seasons) == 1:
                f["season"] = min(seasons)
        if media_lookup is not None:
            name = strip_season(req.resource.strip())
            infos = [i for i in await media_lookup(name, req.year) if i.media]
            ids = {req.tmdb_id, req.douban_id} - {None}
            if ids:
                match = next((i for i in infos if i.id in ids), None)
            else:  # 没选条目时只认片名对得上的，避免认错成别的片
                key = resource_key(name)
                match = next((i for i in infos if req.media in (None, i.media) and key in {
                    resource_key(strip_season(t)) for t in (i.title, i.original_title or "")
                }), None)
            if match is not None:
                f.setdefault("media", match.media)
                f.setdefault("year", match.year)
                f.setdefault("poster", match.poster)
                f.setdefault(f"{match.source}_id", match.id)
                if match.media == "tv":
                    f.setdefault("season", 1)
                    total = match.episodes.get(f["season"])
                    if total:
                        f["total_episodes"] = total
                    if match.season_years.get(f["season"]):
                        f["season_year"] = match.season_years[f["season"]]
        if f.get("media") == "tv":
            f.setdefault("season", 1)
        else:
            f.pop("season", None)
        if req.total_episodes:
            f["total_episodes"], f["manual_total"] = req.total_episodes, True
        return f

    def _spawn_check(owner: str, sub: Subscription) -> None:
        if _cooldown_ok(sub.id):
            task = asyncio.create_task(_sync_check(owner, sub))
            _bg_tasks.add(task)
            task.add_done_callback(_bg_tasks.discard)

    @app.post("/api/subscriptions", response_model=Subscription)
    async def subscribe(
        req: SubscribeRequest, request: Request, _: None = Depends(rate_limit_dep)
    ) -> Subscription:
        """订阅一部电影 / 一季剧集：之后定期重搜，有资源、新集、更高清时通知；
        开了自动转存就把网盘缺的集存进去，集齐后订阅完成、移入订阅历史。开放扫码登录时需要先登录。"""
        store = _store_or_404()
        user = await _user_cookie(request)
        if login_enabled and user is None:
            raise HTTPException(status_code=401, detail="订阅追剧需要先扫码登录夸克")
        if req.auto_save and user is None:
            raise HTTPException(status_code=401, detail="自动转存需要先扫码登录夸克")
        owner = f"u:{user[3]}" if user else req.client_id
        fields = await _identify(req)
        resource = req.resource.strip()
        if fields.get("season", 1) > 1 and not seasons_in(resource):
            resource = f"{resource} 第{fields['season']}季"
        baseline = await app.state.watcher.baseline(resource)
        sub = await store.add_subscription(
            owner, req.query, resource, baseline, auto_save=req.auto_save, **fields
        )
        if sub is None:
            raise HTTPException(status_code=409, detail="订阅数已达上限（20 个）")
        sub = await store.edit_subscription(owner, sub.id, state="new") or sub
        if req.auto_save or search_on_subscribe:
            _spawn_check(owner, sub)  # 立即在后台搜一次：已有资源就马上通知 / 转存
        return sub

    @app.get("/api/subscriptions", response_model=list[Subscription])
    async def list_subscriptions(
        request: Request, client_id: str = ClientId,
        media: str | None = Query(None, pattern="^(movie|tv)$"),
    ) -> list[Subscription]:
        owner = await _owner(request, client_id)
        subs = [sub for _, sub in await _store_or_404().list_subscriptions(owner)]
        return [x for x in subs if media is None or x.media == media]

    @app.patch("/api/subscriptions/{sub_id}", response_model=Subscription)
    async def edit_subscription(
        sub_id: int, req: SubscriptionUpdate, request: Request, client_id: str = ClientId,
    ) -> Subscription:
        """改订阅：自动转存开关、暂停 / 恢复、总集数与起始集、清晰度要求、包含 / 排除词。"""
        store = _store_or_404()
        user = await _user_cookie(request)
        if req.auto_save and user is None:
            raise HTTPException(status_code=401, detail="自动转存需要先扫码登录夸克")
        owner = await _owner(request, client_id)
        fields: dict = {}
        if req.auto_save is not None:
            fields["auto_save"] = req.auto_save
        if req.total_episodes is not None:
            fields["total_episodes"], fields["manual_total"] = req.total_episodes, True
        if req.start_episode is not None:
            fields["start_episode"] = req.start_episode
        for k in ("resolution", "include", "exclude", "upgrade_to"):
            v = getattr(req, k)
            if v is not None:
                fields[k] = v.strip() or None
        if req.upgrade is not None:
            fields["upgrade"] = req.upgrade
        state = None if req.paused is None else ("paused" if req.paused else "active")
        sub = await store.edit_subscription(owner, sub_id, state=state, **fields)
        if sub is None:
            raise HTTPException(status_code=404, detail="订阅不存在")
        if sub.state != "paused" and (req.auto_save or req.paused is False or req.upgrade):
            # 打开自动转存 / 恢复订阅时立即在后台检查一次，把网盘里缺的集补齐
            _spawn_check(owner, sub)
        return sub

    @app.post("/api/subscriptions/{sub_id}/complete", response_model=SubscriptionHistory)
    async def complete_subscription(
        sub_id: int, request: Request, client_id: str = ClientId
    ) -> SubscriptionHistory:
        """手动完成订阅（如电影已经满意了）：移入订阅历史，可重新订阅。"""
        store = _store_or_404()
        owner = await _owner(request, client_id)
        sub = await store.get_subscription(owner, sub_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="订阅不存在")
        hid = await store.archive_subscription(owner, sub, "手动完成")
        got = await store.get_history(owner, hid)
        assert got is not None
        return got[0]

    @app.get("/api/calendar")
    async def episode_calendar(
        request: Request, client_id: str = ClientId,
        start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        subscription_id: int | None = None,
    ) -> dict:
        """追剧日历：订阅的剧集在 [start, end]（默认前 7 天到后 30 天）播出的集和状态。

        状态：saved 已存 / available 有资源未存 / no_resource 已播出但还没资源 / upcoming 未播出。
        播出日期来自 TMDB（需要 TMDB_API_KEY；只用豆瓣识别的剧没有日历）。"""
        store = _store_or_404()
        owner = await _owner(request, client_id)
        subs = [x for _, x in await store.list_subscriptions(owner)
                if subscription_id in (None, x.id)]
        now = cal.today()
        try:
            lo = date.fromisoformat(start) if start else now - timedelta(days=7)
            hi = date.fromisoformat(end) if end else now + timedelta(days=30)
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from None
        if hi < lo or (hi - lo).days > 120:
            raise HTTPException(status_code=400, detail="日期范围需在 120 天以内")
        # 还没有日历的剧（刚订阅、还没检查过）：现查一次
        fetch = getattr(media_lookup, "schedule", None)
        todo = [x for x in subs if x.media == "tv" and x.tmdb_id and not x.schedule][:20]
        if fetch is not None and todo:
            got = await asyncio.gather(*(fetch(x.tmdb_id, x.season or 1) for x in todo))
            for x, eps in zip(todo, got, strict=True):
                if eps:
                    x.schedule = eps
                    await store.set_schedule(x.id, eps)
        return {"today": now.isoformat(), "start": lo.isoformat(), "end": hi.isoformat(),
                "episodes": cal.build_calendar(subs, lo, hi, now)}

    @app.get("/api/subscriptions/history", response_model=list[SubscriptionHistory])
    async def subscription_history(
        request: Request, client_id: str = ClientId
    ) -> list[SubscriptionHistory]:
        """已完成的订阅（最近完成的在前）。"""
        return await _store_or_404().subscription_history(await _owner(request, client_id))

    @app.delete("/api/subscriptions/history/{hid}")
    async def delete_history(hid: int, request: Request, client_id: str = ClientId) -> dict:
        owner = await _owner(request, client_id)
        if not await _store_or_404().delete_history(owner, hid):
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"deleted": True}

    @app.post("/api/subscriptions/history/{hid}/resubscribe", response_model=Subscription)
    async def resubscribe(
        hid: int, request: Request, client_id: str = ClientId,
        _: None = Depends(rate_limit_dep),
    ) -> Subscription:
        """按历史里的设置重新订阅（如出了新一季想再追，可以之后再改季 / 集数）。"""
        store = _store_or_404()
        user = await _user_cookie(request)
        if login_enabled and user is None:
            raise HTTPException(status_code=401, detail="订阅追剧需要先扫码登录夸克")
        owner = await _owner(request, client_id)
        got = await store.get_history(owner, hid)
        if got is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        hist, data = got
        fields = {k: v for k, v in data.items() if k in SubscriptionUpdate.model_fields
                  or k in ("media", "season", "year", "tmdb_id", "douban_id", "poster",
                           "total_episodes", "start_episode", "auto_save")}
        fields.pop("paused", None)
        if user is None:
            fields["auto_save"] = False
        sub = await store.add_subscription(
            owner, hist.query, hist.resource, await app.state.watcher.baseline(hist.resource),
            **{k: v for k, v in fields.items() if v is not None},
        )
        if sub is None:
            raise HTTPException(status_code=409, detail="订阅数已达上限（20 个）")
        await store.delete_history(owner, hid)
        sub = await store.edit_subscription(owner, sub.id, state="new") or sub
        if sub.auto_save or search_on_subscribe:
            _spawn_check(owner, sub)
        return sub

    _bg_tasks: set[asyncio.Task] = set()
    _last_sync: dict[int, float] = {}
    SYNC_COOLDOWN = 120.0  # 同一订阅两次「立即检查」至少间隔这么多秒

    def _cooldown_ok(sub_id: int) -> bool:
        now = time.monotonic()
        if now - _last_sync.get(sub_id, -SYNC_COOLDOWN) < SYNC_COOLDOWN:
            return False
        _last_sync[sub_id] = now
        while len(_last_sync) > 5000:
            _last_sync.pop(next(iter(_last_sync)))
        return True

    async def _sync_check(owner: str, sub: Subscription) -> list[tuple]:
        """立即检查并补齐：重搜 → 通知 → 自动转存缺的集。LLM 用量记到订阅者名下。"""
        allowed = True
        if quota is not None:
            allowed = (await quota.check(SYSTEM, SYSTEM, True)).reason != "site_budget"
        try:
            with llm.scope(allowed=allowed) as meter:
                notes = await app.state.watcher.check(owner, sub, sync_save=True)
        except Exception as exc:
            logger.exception("订阅立即检查失败 id=%s", sub.id)
            await app.state.watcher.failed(sub, exc)
            return []
        if quota is not None and owner.startswith("u:"):
            await quota.record(f"user:{owner[2:]}", SYSTEM, meter, searched=False)
        return notes

    @app.post("/api/subscriptions/{sub_id}/check")
    async def check_now(
        sub_id: int, request: Request, client_id: str = ClientId,
        _: None = Depends(rate_limit_dep),
    ) -> dict:
        """立即检查这个订阅（会重新搜索，耗时几十秒）；打开了自动转存时顺带补齐网盘缺的集。

        返回这次产生的通知；同一订阅 2 分钟内只能触发一次。
        """
        store = _store_or_404()
        if login_enabled and await _user_cookie(request) is None:
            raise HTTPException(status_code=401, detail="请先扫码登录夸克")
        owner = await _owner(request, client_id)
        sub = next((x for _, x in await store.list_subscriptions(owner) if x.id == sub_id), None)
        if sub is None:
            raise HTTPException(status_code=404, detail="订阅不存在")
        if not _cooldown_ok(sub.id):
            raise HTTPException(status_code=429, detail="刚检查过，请稍后再试")
        notes = await _sync_check(owner, sub)
        return {"notifications": [{"kind": k, "message": m, "share": sh} for k, m, sh in notes]}

    @app.get("/api/subscriptions/{sub_id}/saves")
    async def auto_save_log(sub_id: int, request: Request, client_id: str = ClientId) -> list[dict]:
        """这个订阅最近的自动转存记录。"""
        return await _store_or_404().auto_save_log(await _owner(request, client_id), sub_id)

    async def _account_saver(owner: str) -> tuple[QuarkSaver, str] | None:
        """订阅者扫码登录的凭证 → (转存器, 会话哈希)；没有或解不开返回 None。"""
        account = await resolved.store.latest_account(owner[2:])
        cookie = cookie_box.decrypt(account[1], account[0].encode()) if account else None
        if cookie is None:
            return None
        return QuarkSaver(cookie, "0", client=quark_http, classifier=classifier,
                          root_dir=root_dir), account[0]

    def _is_movie(sub: Subscription, link: QuarkLink | None = None) -> bool:
        if sub.media:
            return sub.media == "movie"
        return bool(link and link.quality and link.quality.video_count <= 1)

    async def _sub_folder(owner: str, sub: Subscription, movie: bool) -> str:
        """订阅的目标目录：第一次按条目（不是分享标题）算出来后锁定，之后永不重新分类。"""
        if sub.folder:
            return sub.folder
        title = strip_season(sub.resource)
        cat = None
        if classifier is not None:
            try:
                cat = await classifier(f"{title} {sub.year or ''}".strip(), [])
            except (httpx.HTTPError, ValueError, KeyError):  # 分类失败就用默认目录
                logger.warning("订阅目录分类失败 id=%s", sub.id)
        if cat is None:
            cat = Category(kind="movie" if movie else "tv", region="other", title=title)
        cat.title, cat.year = title, sub.year or cat.year
        if movie:
            cat.kind = "movie"
        elif cat.kind == "movie":
            cat.kind = "tv"
        name = safe_name(f"{title} ({cat.year})" if cat.year else title)
        path = cat.folder(root_dir)
        if not path.endswith("/" + name):
            path += "/" + name
        if not movie:
            path += f"/Season {sub.season or 1:02d}"
        sub.folder = path
        await resolved.store.edit_subscription(owner, sub.id, folder=path)
        return path

    async def _legacy_episodes(
        saver: QuarkSaver, sub: Subscription, folders: list[str]
    ) -> set[int]:
        """以前存到别的目录（分类没锁定时）的集，也算已存，避免目录一变就重复存。"""
        headers = saver.drive_headers()
        found: set[int] = set()
        for folder in folders:
            fid = await saver.find_dir(folder, headers)
            if fid is None:
                continue
            for e in await saver.list_tree(fid, folder, headers):
                if not e["dir"] and kind_of(e["file_name"]) in ("video", "archive"):
                    ep = episode_no(e["file_name"], sub.season, any_ext=True)
                    if ep is not None:
                        found.add(ep)
        return found

    async def auto_save(
        owner: str, sub: Subscription, link: QuarkLink, wanted: set[int] | None = None
    ) -> list[tuple]:
        """订阅检查发现新集时调用：用订阅者扫码登录的凭证只转存网盘里还没有的集。

        存进订阅锁定的目录；每集只存一个最好的版本、展平分享里的嵌套文件夹、不存压缩包，
        存完改成「片名 S01E06」。`wanted`：只要这些集号（订阅范围内缺的）。
        """
        store = resolved.store
        if not (login_enabled and owner.startswith("u:")):
            return []
        name = f"《{sub.resource}》"

        async def pause(status: str, message: str) -> list[tuple]:
            if sub.auto_save_status == status:  # 已经提醒过，不重复打扰
                return []
            sub.auto_save_status = status
            await store.set_auto_save_status(sub.id, status)
            await store.log_auto_save(sub.id, link.share, False, 0, None, message)
            return [("auto_save_paused", message, link.share)]

        expired = f"{name}有更新，但你的夸克登录已失效，自动转存已暂停，重新扫码登录后自动恢复"
        got = await _account_saver(owner)
        if got is None:
            return await pause("login_expired", expired)
        saver, sh = got
        movie = _is_movie(sub, link)
        allowed = True
        if quota is not None:
            allowed = (await quota.check(SYSTEM, SYSTEM, True)).reason != "site_budget"
        try:
            with llm.scope(allowed=allowed) as meter:
                first = sub.folder is None
                folder = await _sub_folder(owner, sub, movie)
                if first and not movie:
                    old = [f for f in await store.saved_folders(sub.id) if f != folder]
                    sub.saved_episodes = sorted(
                        set(sub.saved_episodes) | await _legacy_episodes(saver, sub, old))
                saved = set(sub.saved_episodes)
                if movie:  # 电影「之前已存」= 以前自动转存成功过
                    saved = {0} if await store.has_auto_saved(sub.id) else set()
                better = sub.upgradable(movie)  # 洗版：还能升级的已存集

                def keep(file_name: str) -> bool:
                    if movie:
                        return True
                    ep = episode_no(file_name, sub.season, any_ext=True)
                    return (ep not in saved or ep in better) and (wanted is None or ep in wanted)

                default_res = link.quality.resolution if link.quality else None
                tidy = Tidy(strip_season(sub.resource), movie, sub.season, sub.year,
                            better=better, default_res=default_res,
                            offset=0 if movie else await _season_offset(sub),
                            total=sub.total_episodes)
                result = await saver.save(link.share, link.pwd, to_path=folder, only_new=True,
                                          keep=keep, tidy=tidy)
        except LoginExpiredError:
            await store.delete_account(sh)
            return await pause("login_expired", expired)
        except SaveError as e:
            message = f"{name}自动转存失败：{e}"
            await store.log_auto_save(sub.id, link.share, False, 0, None, message)
            return [("auto_save_failed", message, link.share)]
        finally:
            if quota is not None:
                await quota.record(f"user:{owner[2:]}", SYSTEM, meter, searched=False)
        if sub.auto_save_status:
            sub.auto_save_status = None
            await store.set_auto_save_status(sub.id, None)
        have = {episode_no(n, sub.season, any_ext=True) for n in result.present
                if kind_of(n) in ("video", "archive")} - {None}
        sub.saved_episodes = sorted(set(sub.saved_episodes) | have)
        upgraded = _record_versions(sub, movie, result.saved, default_res, saved)
        where = f"「{result.folder}」" if result.folder else "你的夸克网盘"
        if upgraded:
            label = RES_TEXT.get(upgraded[1], upgraded[1])
            which = "" if movie else "第 " + "、".join(map(str, upgraded[0])) + " 集"
            message = (f"{name}{which}换成了更高清的 {label} 版本，存在{where}；旧版本还在，"
                       f"可以在「整理」里确认删除")
            await store.log_auto_save(sub.id, link.share, True, result.file_count,
                                      result.folder, message)
            return [("upgraded", message, link.share)]
        if result.file_count == 0:
            message = f"{name}的新内容网盘里都已经有了，没有重复转存"
        else:
            skipped = f"，跳过已有的 {result.skipped} 个" if result.skipped else ""
            message = f"已自动转存{name}的 {result.file_count} 个新文件到{where}{skipped}"
        await store.log_auto_save(sub.id, link.share, True, result.file_count, result.folder,
                                  message)
        return [("auto_saved", message, link.share)] if result.file_count else []

    async def _season_offset(sub: Subscription) -> int:
        """前面各季的总集数（TMDB），给按绝对集号编的合集换算本季集号；查不到为 0。"""
        if media_lookup is None or not sub.season or sub.season <= 1 or not sub.tmdb_id:
            return 0
        infos = await media_lookup(strip_season(sub.resource), sub.year)
        info = next((i for i in infos if i.id == sub.tmdb_id and i.source == "tmdb"), None)
        if info is None or any(s not in info.episodes for s in range(1, sub.season)):
            return 0
        return sum(info.episodes[s] for s in range(1, sub.season))

    def _record_versions(
        sub: Subscription, movie: bool, names: list[str], default_res: str | None,
        before: set[int],
    ) -> tuple[list[int], str] | None:
        """记下这次存的各集清晰度；有集是洗版换的（之前就有）时返回 (集号, 新清晰度)。"""
        upgraded: list[int] = []
        best = ""
        for n in names:
            if kind_of(n) not in ("video", "archive"):
                continue
            ep = 0 if movie else episode_no(n, sub.season, any_ext=True)
            res = file_resolution(n, default_res)
            if ep is None or res is None:
                continue
            old = sub.versions.get(ep)
            if RESOLUTION_RANK[res] <= RESOLUTION_RANK.get(old or "", 0):
                continue
            sub.versions[ep] = res
            if ep in before:
                upgraded.append(ep)
            best = max(best, res, key=lambda r: RESOLUTION_RANK.get(r, 0))
        return (sorted(upgraded), best) if upgraded else None

    async def _tidy_plan(owner: str, sub: Subscription) -> tuple[QuarkSaver, dict, TidyPlan]:
        got = await _account_saver(owner)
        if got is None:
            raise HTTPException(status_code=401, detail="夸克登录已失效，请重新扫码登录")
        saver = got[0]
        headers = saver.drive_headers()
        movie = _is_movie(sub)
        target = await _sub_folder(owner, sub, movie)
        root = "/" + (safe_name(root_dir) or "QueryPilot")
        # 只扫本站存过的目录（都在 QueryPilot 根目录下），不碰网盘里别的东西
        folders = [f for f in dict.fromkeys([target, *await resolved.store.saved_folders(sub.id)])
                   if f == root or f.startswith(root + "/")]
        entries: list[dict] = []
        try:
            for folder in folders:
                fid = await saver.find_dir(folder, headers)
                if fid is not None:
                    entries += await saver.list_tree(fid, folder, headers)
        except LoginExpiredError:
            raise HTTPException(status_code=401, detail="夸克登录已失效，请重新扫码登录") from None
        except (SaveError, httpx.HTTPError, ValueError):
            raise HTTPException(status_code=502, detail="读取网盘目录失败，请稍后重试") from None
        seen: set[str] = set()
        entries = [e for e in entries if not (e["fid"] in seen or seen.add(e["fid"]))]
        plan = plan_tidy(entries, target, strip_season(sub.resource), movie, sub.season,
                         sub.year)
        return saver, headers, plan

    async def _owned_sub(request: Request, client_id: str, sub_id: int) -> tuple[str, Subscription]:
        if login_enabled and await _user_cookie(request) is None:
            raise HTTPException(status_code=401, detail="请先扫码登录夸克")
        owner = await _owner(request, client_id)
        sub = await _store_or_404().get_subscription(owner, sub_id)
        if sub is None or not owner.startswith("u:"):
            raise HTTPException(status_code=404, detail="订阅不存在")
        return owner, sub

    @app.get("/api/subscriptions/{sub_id}/organize")
    async def organize_preview(
        sub_id: int, request: Request, client_id: str = ClientId,
        _: None = Depends(rate_limit_dep),
    ) -> dict:
        """整理预览：扫这个订阅存过的目录，列出将移动 / 重命名的文件和建议删除的文件。

        不做任何改动。建议删除的（重复版本、压缩包、空文件夹）要用户在界面上勾选确认。
        """
        owner, sub = await _owned_sub(request, client_id, sub_id)
        _, _, plan = await _tidy_plan(owner, sub)
        return plan.as_dict()

    @app.post("/api/subscriptions/{sub_id}/organize")
    async def organize_apply(
        sub_id: int, req: OrganizeRequest, request: Request, client_id: str = ClientId,
        _: None = Depends(rate_limit_dep),
    ) -> dict:
        """执行整理：按服务器重新算出的计划移动到锁定目录并重命名；
        只删除 `delete_fids` 里列出、且确实在「建议删除」里的文件（进夸克回收站，可恢复）。"""
        owner, sub = await _owned_sub(request, client_id, sub_id)
        saver, headers, plan = await _tidy_plan(owner, sub)
        done = {"moved": 0, "renamed": 0, "deleted": 0, "errors": []}
        try:
            target_fid = await saver.ensure_dir(plan.target, headers)
            to_move = [m["fid"] for m in plan.moves if m["from"] != plan.target]
            if to_move:
                await saver.move(to_move, target_fid, headers)
                done["moved"] = len(to_move)
            for m in plan.moves:
                if m["to_name"] != m["name"]:
                    try:
                        await saver.rename(m["fid"], m["to_name"], headers)
                        done["renamed"] += 1
                    except SaveError as e:
                        done["errors"].append(f"{m['name']}：{e}")
            allowed = {d["fid"] for d in plan.deletes}
            chosen = [f for f in dict.fromkeys(req.delete_fids) if f in allowed]
            if chosen:
                await saver.delete(chosen, headers)
                done["deleted"] = len(chosen)
        except LoginExpiredError:
            raise HTTPException(status_code=401, detail="夸克登录已失效，请重新扫码登录") from None
        except (SaveError, httpx.HTTPError, ValueError) as e:
            done["errors"].append(str(e) if isinstance(e, SaveError) else "连接夸克失败")
        eps = {episode_no(m["to_name"], sub.season, any_ext=True) for m in plan.moves} - {None}
        if eps:
            sub.saved_episodes = sorted(set(sub.saved_episodes) | eps)
            await resolved.store.update_subscription(owner, sub, [])
        return {**done, "target": plan.target}

    if watcher is not None:
        watcher.auto_saver = auto_save

    @app.delete("/api/subscriptions/{sub_id}")
    async def unsubscribe(request: Request, sub_id: int, client_id: str = ClientId) -> dict:
        if not await _store_or_404().delete_subscription(await _owner(request, client_id), sub_id):
            raise HTTPException(status_code=404, detail="订阅不存在")
        return {"deleted": True}

    @app.get("/api/notifications", response_model=list[Notification])
    async def notifications(request: Request, client_id: str = ClientId) -> list[Notification]:
        return await _store_or_404().notifications(await _owner(request, client_id))

    @app.post("/api/notifications/read")
    async def notifications_read(request: Request, client_id: str = ClientId) -> dict:
        await _store_or_404().mark_read(await _owner(request, client_id))
        return {"ok": True}

    SESSION_COOKIE = "qp_quark"

    async def _user_cookie(request: Request) -> tuple[str, str, str | None, str] | None:
        """当前浏览器扫码登录过的夸克账号：(会话哈希, cookie, 昵称, 账号 id)。"""
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
        return sh, cookie, row[1], row[2] or f"session:{sh[:16]}"

    async def _identity(request: Request) -> Who:
        """登录用户按账号，匿名按 IP（client_id 可随意伪造，不作依据）；顺带查封禁与单独额度。"""
        ip = client_ip(request)
        who = Who(f"ip:{ip}", f"ip:{ip}", False)
        ban = await users.get_ip_ban(ip)
        if ban is not None:
            who.banned = f"该网络已被停用：{ban}" if ban else "该网络已被停用"
        user = await _user_cookie(request)
        if user is not None:
            who.subject, who.logged_in, who.nickname = f"user:{user[3]}", True, user[2]
            row = await users.get_user(user[3])
            if row is not None:
                who.ai_limit = row["ai_limit"]
                if row["banned"]:
                    reason = row["ban_reason"]
                    who.banned = f"该账号已被停用：{reason}" if reason else "该账号已被停用"
        return who

    async def _check(who: Who):
        return await quota.check(who.subject, who.ip_subject, who.logged_in, who.ai_limit)

    async def _quota_start(request: Request):
        """搜索前检查封禁与额度：封禁 403、IP 当天超限 429、未登录免费次数用完 401。"""
        who = await _identity(request)
        if who.banned:
            raise HTTPException(status_code=403, detail=who.banned)
        if quota is None:
            return None
        subject, ip_subject = who.subject, who.ip_subject
        decision = await _check(who)
        if decision.blocked:
            raise HTTPException(status_code=429, detail=decision.message())
        if decision.login_required:
            raise HTTPException(status_code=401, detail=decision.message())
        return subject, ip_subject, decision

    async def _quota_end(ctx, meter: llm.Meter, result=None) -> None:
        if ctx is None:
            return
        await quota.record(ctx[0], ctx[1], meter, decision=ctx[2])
        if result is not None:
            result.quota = ctx[2].to_dict()

    app.include_router(admin_router(
        users,
        admin_token if admin_token is not None else (
            _settings.admin_token if service is None else ""
        ),
        quota,
        rate_limit_dep,
    ))

    @app.get("/api/quota")
    async def quota_status(request: Request) -> dict:
        """当前访客今天的 AI 搜索额度（前端显示剩余次数）。"""
        if quota is None:
            return {"enabled": False}
        decision = await _check(await _identity(request))
        return {"enabled": True, **decision.to_dict()}

    @app.get("/api/me")
    async def me(request: Request) -> dict:
        """当前访客：是否登录、昵称、今天的额度，以及登录/邀请制开关（前端据此显示引导）。"""
        who = await _identity(request)
        info = {
            "login": login_enabled,
            "invite_required": login_enabled and need_invite,
            "logged_in": who.logged_in,
            "nickname": who.nickname,
            "banned": who.banned,  # 被停用时是给用户看的说明，否则为 null
            "quota": None,
        }
        if quota is not None:
            info["quota"] = (await _check(who)).to_dict()
        return info

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
    async def quark_login_start(
        invite_code: str = Body(default="", embed=True, max_length=64),
        _: None = Depends(rate_limit_dep),
    ) -> dict:
        """开始扫码登录：返回二维码（SVG 与原始内容），前端轮询状态。

        开启邀请制时，新账号要在这里带上邀请码（老账号不用）；扫码成功后才校验。
        """
        if not login_enabled:
            raise HTTPException(status_code=404, detail="扫码登录未开启")
        try:
            login_id, content = await qr_login.start()
        except LoginError as e:
            raise HTTPException(status_code=502, detail=str(e))
        if invite_code.strip():
            pending_invites[login_id] = invite_code.strip()
            while len(pending_invites) > 500:
                pending_invites.pop(next(iter(pending_invites)))
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
            if result.status == "expired":
                pending_invites.pop(login_id[:64], None)
            return {"status": result.status}
        code = pending_invites.pop(login_id[:64], "")
        user_id = result.user_id or (
            "nick:" + hashlib.sha256(result.nickname.encode()).hexdigest()[:24]
            if result.nickname else None
        )
        known = await users.get_user(user_id) if user_id else None
        if known is None and need_invite:
            ok = bool(code) and (code in env_invites or await users.use_invite(code))
            if not ok:  # 丢弃这次拿到的凭证，不建会话
                return {"status": "invite_required",
                        "message": "邀请码无效或已用完" if code else "新用户需要邀请码"}
        if known is not None and known.get("banned"):
            return {"status": "banned", "message": "该账号已被停用"}
        old = await _user_cookie(request)
        if old:
            await resolved.store.delete_account(old[0])
        token = secrets.token_urlsafe(32)
        sh = session_hash(token)
        user_id = user_id or f"session:{sh[:16]}"
        await resolved.store.put_account(
            sh, cookie_box.encrypt(result.cookie or "", sh.encode()), result.nickname, user_id
        )
        await users.touch_user(user_id, result.nickname, code or None)
        await resolved.store.clear_auto_save_status(f"u:{user_id}", "login_expired")
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
            saver_obj = QuarkSaver(
                user[1], _settings.quark_save_dir_fid if service is None else "0",
                client=quark_http, classifier=classifier, root_dir=root_dir,
            )
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
        who = await _identity(request)
        if who.banned:
            raise HTTPException(status_code=403, detail=who.banned)
        ctx = None
        if quota is not None:
            ctx = (who.subject, who.ip_subject, await _check(who))
        allowed = ctx is None or ctx[2].reason != "site_budget"
        try:
            with llm.scope(allowed=allowed) as meter:
                result = await saver_obj.save(req.share, req.pwd)
        except LoginExpiredError as e:
            if user is None:
                return SaveResponse(ok=False, message=str(e))
            await resolved.store.delete_account(user[0])
            response.delete_cookie(SESSION_COOKIE)
            return SaveResponse(ok=False, message="夸克登录已过期，请重新扫码登录")
        except SaveError as e:
            return SaveResponse(ok=False, message=str(e))
        finally:
            if ctx is not None:
                await quota.record(ctx[0], ctx[1], meter, searched=False)
        name = f"《{result.title}》" if result.title else f"{result.file_count} 个文件"
        where = f"「{result.folder}」" if result.folder else "你的夸克网盘"
        if result.done:
            message = f"已转存{name}到{where}"
        else:
            message = f"已提交转存{name}到{where}，夸克正在后台处理"
        if result.category:
            message += f"（识别为{result.category}，依据：{result.basis}）"
        return SaveResponse(ok=True, message=message, file_count=result.file_count,
                            folder=result.folder, category=result.category)

    @app.post("/api/search", response_model=QuarkSearchResponse)
    async def api_search(
        req: SearchRequest,
        request: Request,
        _: None = Depends(rate_limit_dep),
    ) -> QuarkSearchResponse:
        ctx = await _quota_start(request)
        try:
            with llm.scope(allowed=ctx is None or ctx[2].ai) as meter:
                result = await app.state.search_service.search(req)
            await _quota_end(ctx, meter, result)
            return result
        except SearchUnavailableError:
            raise HTTPException(status_code=503, detail="所有搜索源暂不可用，请稍后重试")

    @app.post("/api/agent/search", response_model=AgentSearchResponse)
    async def api_agent_search(
        req: SearchRequest,
        request: Request,
        _: None = Depends(rate_limit_dep),
    ) -> AgentSearchResponse:
        """agent 搜索：多轮「规划 → 搜索/验证 → 观察」，响应附带每一步轨迹。"""
        ctx = await _quota_start(request)
        try:
            with llm.scope(allowed=ctx is None or ctx[2].ai) as meter:
                result = await app.state.agent.run(req)
            await _quota_end(ctx, meter, result)
            return result
        except SearchUnavailableError:
            raise HTTPException(status_code=503, detail="所有搜索源暂不可用，请稍后重试")

    @app.get("/api/agent/stream")
    async def api_agent_stream(
        request: Request,
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

        ctx = await _quota_start(request)
        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

        async def emit(step: AgentStep) -> None:
            await queue.put(("step", step.model_dump_json()))

        async def run() -> None:
            try:
                if ctx is not None and ctx[2].reason:  # 先告诉前端本次降级了
                    await queue.put(("quota", json.dumps(ctx[2].to_dict(), ensure_ascii=False)))
                with llm.scope(allowed=ctx is None or ctx[2].ai) as meter:
                    result = await app.state.agent.run(req, emit=emit)
                await _quota_end(ctx, meter, result)
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
