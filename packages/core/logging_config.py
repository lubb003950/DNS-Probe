"""
统一日志配置：按天轮转，保留 30 天，同时输出到文件和控制台。
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "data" / "logs"


def setup_logging(service: str = "app", level: int = logging.INFO) -> None:
    """
    配置日志处理器。

    Args:
        service: 日志文件前缀，如 "api" / "agent"。
        level:   日志级别，默认 INFO。
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{service}.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    logging.basicConfig(level=level, handlers=[file_handler, console_handler], force=True)

    # 抑制 SQLAlchemy 和 uvicorn 的 DEBUG 噪音
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
