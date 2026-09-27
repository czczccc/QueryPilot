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
    deepseek_api_key: str = ""
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
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
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
    )
