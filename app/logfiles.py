"""日志同时写到文件：按天轮转，总量有上限，重新部署（容器重建）后还在。

LOG_DIR 为空时只输出到控制台（本地开发默认）。Docker 部署时 compose 把 LOG_DIR=/app/logs
挂到数据卷上，查看方法见 .env.example。
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

FILE_NAME = "app.log"


class DailyCappedHandler(TimedRotatingFileHandler):
    """每天零点轮转，只留 `keep_days` 份；当天文件超过 `max_bytes` 时提前轮转
    （同一天的备份会被覆盖），所以磁盘占用最多约 (keep_days+1) × max_bytes。"""

    def __init__(self, path: Path, keep_days: int, max_bytes: int) -> None:
        super().__init__(path, when="midnight", backupCount=keep_days, encoding="utf-8", delay=True)
        self.max_bytes = max_bytes

    def shouldRollover(self, record: logging.LogRecord) -> int:
        if super().shouldRollover(record):
            return 1
        if self.max_bytes <= 0:
            return 0
        try:
            return int(os.path.getsize(self.baseFilename) >= self.max_bytes)
        except OSError:
            return 0


def add_file_handler(log_dir: str, fmt: str, keep_days: int = 14, max_mb: int = 50,
                     root: logging.Logger | None = None) -> Path | None:
    """给根 logger 挂上文件输出；目录写不了时只打警告，服务照常启动。"""
    if not log_dir:
        return None
    path = Path(log_dir) / FILE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = DailyCappedHandler(path, keep_days, max_mb * 1024 * 1024)
    except OSError as e:
        logging.getLogger(__name__).warning("日志目录 %s 不可写（%s），只输出到控制台", log_dir, e)
        return None
    handler.setFormatter(logging.Formatter(fmt))
    (root or logging.getLogger()).addHandler(handler)
    return path
