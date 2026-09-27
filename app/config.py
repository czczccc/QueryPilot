"""运行配置：从环境变量读取，密钥绝不落代码或仓库。"""

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # dotenv 未安装时直接读环境变量
    pass


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str = field(default="", repr=False)  # 也可用 LLM_API_KEY
    llm_base_url: str = ""  # 兼容 OpenAI 格式的第三方服务地址，空为 DeepSeek 官方
    llm_model: str = ""  # 模型名，空为 deepseek-v4-flash
    llm_stream: str = "auto"  # auto：非流式 400 时自动改流式；true / false 强制
    tavily_api_key: str = ""
    app_env: str = "development"
    log_level: str = "INFO"
    request_timeout_seconds: float = 8.0
    max_results: int = 12
    memory_db_path: str = "data/querypilot.db"  # 空字符串关闭记忆
    reverify_interval_hours: float = 6.0  # 后台复验周期，0 关闭
    tg_channels: tuple[str, ...] = ()  # Telegram 公开频道用户名
    tg_proxy: str = ""  # 访问 t.me 的代理，如 http://127.0.0.1:7890
    extra_sites: tuple[str, ...] = ()  # 资源站搜索 URL 模板，{q} 为关键词
    subscribe_interval_hours: float = 12.0  # 追剧订阅检查周期，0 关闭
    notify_webhook: str = ""  # 订阅有更新时额外推送到这个 webhook（可选）
    # 一键转存：夸克登录 cookie 与接口口令只放本地 .env / 环境变量，不入库、不回显
    quark_cookie: str = field(default="", repr=False)
    save_token: str = field(default="", repr=False)
    quark_save_dir_fid: str = "0"
    save_classify: bool = True  # 转存时自动分类到 {根目录}/电影|电视剧/地区/片名；关闭则存到 QUARK_SAVE_DIR_FID
    save_root_dir: str = "QueryPilot"  # 自动分类的网盘根目录名
    tmdb_api_key: str = field(default="", repr=False)  # TMDB v3 key 或 v4 读访问令牌
    tmdb_api_base: str = "https://api.themoviedb.org/3"  # 国内可换成反代地址
    douban_lookup: bool = True  # 分类时是否查豆瓣
    cookie_secret: str = field(default="", repr=False)  # 扫码登录凭证的加密密钥，空则自动生成密钥文件
    quark_login: bool = True  # 是否开放夸克扫码登录（多人各自转存到自己的网盘）  # 转存到的网盘目录 fid，0 为根目录
    # 防滥用（0 表示不限制）
    rate_limit_per_minute: int = 10  # 同一 IP 每分钟请求数
    ip_daily_searches: int = 100  # 同一 IP 每天搜索总次数
    anon_daily_ai: int = 3  # 未登录每天 AI 搜索次数，用完降级为规则模式
    user_daily_ai: int = 30  # 登录用户每天 AI 搜索次数
    site_daily_tokens: int = 1_000_000  # 全站每天 LLM token 预算，到顶全站降级
    search_cache_minutes: float = 60.0  # 同一句搜索多久内直接复用结果
    trust_proxy: bool = False  # 部署在 Nginx 等反代后面时开启，按 X-Forwarded-For 取真实 IP
    extra: dict = field(default_factory=dict)


def load_settings() -> Settings:
    def _int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    def _float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except ValueError:
            return default

    def _list(name: str) -> tuple[str, ...]:
        return tuple(x.strip() for x in os.getenv(name, "").split(",") if x.strip())

    return Settings(
        deepseek_api_key=(os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")).strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_stream=os.getenv("LLM_STREAM", "auto").strip().lower() or "auto",
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        request_timeout_seconds=_float("REQUEST_TIMEOUT_SECONDS", 8.0),
        max_results=_int("MAX_RESULTS", 12),
        memory_db_path=os.getenv("MEMORY_DB_PATH", "data/querypilot.db"),
        reverify_interval_hours=_float("REVERIFY_INTERVAL_HOURS", 6.0),
        tg_channels=_list("TG_CHANNELS"),
        tg_proxy=os.getenv("TG_PROXY", ""),
        extra_sites=_list("EXTRA_SITES"),
        subscribe_interval_hours=_float("SUBSCRIBE_INTERVAL_HOURS", 12.0),
        notify_webhook=os.getenv("NOTIFY_WEBHOOK", ""),
        quark_cookie=os.getenv("QUARK_COOKIE", "").strip(),
        save_token=os.getenv("SAVE_TOKEN", "").strip(),
        cookie_secret=os.getenv("COOKIE_SECRET", "").strip(),
        quark_login=os.getenv("QUARK_LOGIN", "true").strip().lower() not in ("0", "false", "no"),
        save_classify=os.getenv("SAVE_CLASSIFY", "true").strip().lower() not in ("0", "false", "no"),
        tmdb_api_key=os.getenv("TMDB_API_KEY", "").strip(),
        tmdb_api_base=os.getenv("TMDB_API_BASE", "").strip() or "https://api.themoviedb.org/3",
        douban_lookup=os.getenv("DOUBAN_LOOKUP", "true").strip().lower() not in ("0", "false", "no"),
        save_root_dir=os.getenv("SAVE_ROOT_DIR", "QueryPilot").strip() or "QueryPilot",
        quark_save_dir_fid=os.getenv("QUARK_SAVE_DIR_FID", "0").strip() or "0",
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 10),
        ip_daily_searches=_int("IP_DAILY_SEARCHES", 100),
        anon_daily_ai=_int("ANON_DAILY_AI_SEARCHES", 3),
        user_daily_ai=_int("USER_DAILY_AI_SEARCHES", 30),
        site_daily_tokens=_int("SITE_DAILY_TOKEN_BUDGET", 1_000_000),
        search_cache_minutes=_float("SEARCH_CACHE_MINUTES", 60.0),
        trust_proxy=os.getenv("TRUST_PROXY", "false").strip().lower() in ("1", "true", "yes"),
    )
