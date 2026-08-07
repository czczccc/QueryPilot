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

    return Settings(
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        request_timeout_seconds=_float("REQUEST_TIMEOUT_SECONDS", 8.0),
        max_results=_int("MAX_RESULTS", 12),
    )
