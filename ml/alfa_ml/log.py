"""structlog JSON logging with named ML events (see docs/ml_monitoring.md)."""

import logging
from typing import Any

import structlog

_configured = False


def configure(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
    )
    _configured = True


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
