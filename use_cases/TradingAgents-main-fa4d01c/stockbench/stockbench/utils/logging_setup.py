from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from loguru import logger


# Global variables: track whether logging has been initialized
_logging_initialized = False
_log_path = None


def _to_logging_level(level_str: str) -> int:
    """Convert string level to standard library logging level value."""
    try:
        return getattr(logging, str(level_str).upper())
    except Exception:
        return logging.INFO


class InterceptHandler(logging.Handler):
    """Bridge standard library logging to loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except Exception:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_json_logging(config: Optional[Dict[str, Any]] = None, base_dir: Optional[str] = None) -> str:
    """Setup unified logging (JSON file + console), configurable levels, and bridge standard library logging.

    Args:
        config: Full configuration dictionary; reads logging.console_level, logging.file_level, logging.intercept_std_logging
        base_dir: Log base directory; if not specified, reads environment variable STOCKBENCH_LOG_DIR or uses default path

    Returns:
        str: Main log file path
    """
    global _logging_initialized, _log_path

    # If already initialized, return the previous path directly
    if _logging_initialized and _log_path:
        return _log_path

    # Read log levels from configuration
    log_cfg = (config.get("logging", {}) if isinstance(config, dict) else {}) or {}
    console_level = str(log_cfg.get("console_level", "INFO")).upper()
    file_level = str(log_cfg.get("file_level", "INFO")).upper()
    intercept_std_logging = bool(log_cfg.get("intercept_std_logging", True))

    # Support environment variable configuration for log path
    if base_dir is None:
        base_dir = os.environ.get(
            "STOCKBENCH_LOG_DIR", os.path.join(os.getcwd(), "logs", "stockbench")
        )

    os.makedirs(base_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(base_dir, f"{date_str}.log")

    # Remove default handlers to avoid duplication
    logger.remove()

    # Add main log file handler (JSON)
    logger.add(
        log_path,
        serialize=True,
        enqueue=True,
        backtrace=False,
        diagnose=False,
        rotation="00:00",
        level=file_level,
    )

    # Add console output (non-JSON format, convenient for debugging)
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=console_level,
    )

    # Intercept standard library logging to loguru (optional)
    if intercept_std_logging:
        try:
            logging.basicConfig(
                handlers=[InterceptHandler()],
                level=_to_logging_level(console_level),
                force=True,
            )
        except TypeError:
            # Compatible with environments that don't support force parameter
            logging.root.handlers = [InterceptHandler()]
            logging.root.setLevel(_to_logging_level(console_level))

        # Elevate third-party library log levels to avoid leaking sensitive information (e.g., httpx prints full URLs and query parameters at INFO level)
        for name in ("httpx", "httpcore", "urllib3"):
            try:
                logging.getLogger(name).setLevel(logging.WARNING)
            except Exception:
                pass

    # Mark as initialized
    _logging_initialized = True
    _log_path = log_path

    return log_path


class Metrics:
    def __init__(self) -> None:
        self.counters: Dict[str, int] = {}
        self.gauges: Dict[str, float] = {}
        self.timings_ms: Dict[str, float] = {}

    def incr(self, key: str, value: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + int(value)

    def gauge(self, key: str, value: float) -> None:
        self.gauges[key] = float(value)

    def timing(self, key: str, ms: float) -> None:
        self.timings_ms[key] = float(ms)

    def flush(self, extra: Dict[str, Any] | None = None) -> None:
        payload = {
            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "metrics",
            "counters": self.counters,
            "gauges": self.gauges,
            "timings_ms": self.timings_ms,
        }
        if extra:
            payload.update(extra)
        logger.bind(component="metrics").info(payload)
 