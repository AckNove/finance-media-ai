"""日志配置。第一阶段只输出到标准流，后续可扩展文件与结构化日志。"""

from __future__ import annotations

import logging
import sys

DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configure_logging(level: str = "INFO", fmt: str = DEFAULT_FORMAT) -> None:
    """配置根 logger；重复调用是安全的（force=True 覆盖既有 handler）。"""
    resolved = getattr(logging, str(level).upper(), logging.INFO)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(level=resolved, handlers=[handler], force=True)
    # 降低第三方库噪声
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
