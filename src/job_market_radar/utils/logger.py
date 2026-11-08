"""Logging setup shared by all pipeline components."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAMESPACE = "job_market_radar"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILE_NAME = "job_market_radar.log"

_HANDLER_MARKER = "_job_market_radar_handler"


def _make_console_stream_safe() -> None:
    """Avoid UnicodeEncodeError on legacy Windows consoles (e.g. cp1256).

    Characters the console cannot display (such as the "ã" in "São Paulo")
    are escaped instead of crashing the process. The log file is always UTF-8.
    """
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(errors="backslashreplace")


def setup_logging(log_dir: Path | None = None, level: str = "INFO") -> logging.Logger:
    """Configure the package logger with a console handler and an optional file handler.

    Safe to call multiple times: previously installed handlers are replaced,
    never duplicated.

    Args:
        log_dir: Directory for a rotating UTF-8 log file. ``None`` disables file logging.
        level: Logging level name.

    Returns:
        The configured package-level logger.
    """
    logger = logging.getLogger(LOGGER_NAMESPACE)
    logger.setLevel(level.upper())
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    _make_console_stream_safe()
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARKER, True)
    logger.addHandler(console_handler)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _HANDLER_MARKER, True)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the package namespace (e.g. ``ingestion.csv``)."""
    if name.startswith(LOGGER_NAMESPACE):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{name}")
