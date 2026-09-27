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
    quark_save_dir_fid: str = "0"  # 转存到的网盘目录 fid，0 为根目录
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
        quark_save_dir_fid=os.getenv("QUARK_SAVE_DIR_FID", "0").strip() or "0",
    )
