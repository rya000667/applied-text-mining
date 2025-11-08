"""Structured logging utilities for the Activity Poller."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


@dataclass
class RunMetrics:
    """Holds counters emitted at the end of a polling run."""

    pages: int = 0
    events: int = 0
    bytes: int = 0
    retries: int = 0
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "pages": self.pages,
            "events": self.events,
            "bytes": self.bytes,
            "retries": self.retries,
        }
        payload.update(self.extras)
        return payload


def configure_logging() -> None:
    """Configure structured JSON logging suitable for Azure Functions."""

    logging.basicConfig(level=LOG_LEVEL, format="%(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(f"Type {type(value)!r} is not JSON serialisable")


def log_event(message: str, **fields: Any) -> None:
    record = {"utc": datetime.now(timezone.utc).isoformat(), "message": message}
    if fields:
        record.update(fields)
    logging.info(json.dumps(record, default=_json_default))


def log_checkpoint_update(old_state: Dict[str, Any], new_state: Dict[str, Any]) -> None:
    log_event("checkpoint.update", old=old_state, new=new_state)


def log_retry(event: str, attempt: int, wait: float, details: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {"event": event, "attempt": attempt, "wait": wait}
    if details:
        payload.update(details)
    log_event("retry", **payload)


def log_metrics(metrics: RunMetrics) -> None:
    log_event("run.metrics", **metrics.as_dict())


def log_shutdown_request(pending_pages: Iterable[str]) -> None:
    log_event("shutdown.request", pending=list(pending_pages))

