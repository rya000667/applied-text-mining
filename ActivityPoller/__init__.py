"""Timer-triggered Azure Function entry point."""
from __future__ import annotations

import os
import signal
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

try:  # pragma: no cover - imported for runtime only
    import azure.functions as func
except ModuleNotFoundError:  # pragma: no cover - allow tests without Azure SDK
    from typing import Any

    func = Any  # type: ignore

from .logging_util import RunMetrics, configure_logging, log_event, log_metrics, log_shutdown_request
from .pbi_client import ContinuationExpiredError, PBIClient
from .publisher import EventPublisher
from .state_store import StateStore, clamp_start, end_of_day, midnight_utc, next_day

STOP_EVENT = Event()


def _handle_sigterm(signum, frame) -> None:  # type: ignore[override]
    log_event("signal.received", signal=signum)
    STOP_EVENT.set()


try:
    signal.signal(signal.SIGTERM, _handle_sigterm)
except ValueError:
    pass


def main(mytimer: func.TimerRequest) -> None:
    configure_logging()
    env = _Env()
    log_event("function.start", schedule=env.timer_schedule)

    store = StateStore(env.storage_account_url, env.state_container)
    client = PBIClient(
        tenant_id=env.tenant_id,
        client_id=env.client_id,
        keyvault_url=env.keyvault_url,
        secret_name=env.keyvault_secret_name,
        timeout=env.request_timeout,
        max_retries=env.max_retries,
    )
    publisher = EventPublisher(env.eventhub_fqdn, env.eventhub_name)

    try:
        _run_once(store, client, publisher, env)
    finally:
        publisher.close()
        log_event("function.end")


class _Env:
    def __init__(self) -> None:
        self.tenant_id = _require("TENANT_ID")
        self.client_id = _require("CLIENT_ID")
        self.keyvault_url = _require("KEYVAULT_URL")
        self.keyvault_secret_name = _require("KEYVAULT_SECRET_NAME")
        self.storage_account_url = _require("STORAGE_ACCOUNT_URL")
        self.state_container = _require("STATE_CONTAINER")
        self.eventhub_fqdn = _require("EVENTHUB_FQDN")
        self.eventhub_name = _require("EVENTHUB_NAME")
        self.timer_schedule = _require("TIMER_SCHEDULE")
        self.overlap_seconds = int(os.environ.get("OVERLAP_SECONDS", "60"))
        self.request_timeout = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30"))
        self.max_retries = int(os.environ.get("MAX_RETRIES", "5"))


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} must be set")
    return value


def _run_once(store: StateStore, client: PBIClient, publisher: EventPublisher, env: _Env) -> None:
    now = datetime.now(UTC)
    state = store.load(env.tenant_id)
    metrics = RunMetrics()

    if state.etag is None and state.last_end is None and state.resume_from is None:
        start = now - timedelta(days=28)
        new_state = replace(
            state,
            resume_from=midnight_utc(start),
            continuation_uri=None,
            last_end=None,
        )
        state = store.save(env.tenant_id, new_state, match_etag=None, previous_state=None)

    if state.resume_from is None:
        start_candidate = clamp_start(now, state.last_end, env.overlap_seconds)
        previous = state
        new_state = replace(
            state,
            resume_from=midnight_utc(start_candidate),
            continuation_uri=None,
        )
        state = store.save(env.tenant_id, new_state, match_etag=previous.etag, previous_state=previous)

    processing_now = now
    while state.resume_from and state.resume_from < processing_now:
        if STOP_EVENT.is_set():
            log_shutdown_request(["resume_loop"])
            break
        day_start = state.resume_from
        day_end = min(end_of_day(day_start), processing_now)
        continuation = state.continuation_uri
        try:
            page = client.fetch_page(day_start, day_end, continuation)
        except ContinuationExpiredError:
            previous = state
            new_state = replace(state, continuation_uri=None)
            state = store.save(env.tenant_id, new_state, match_etag=previous.etag, previous_state=previous)
            continue

        publish_result = publisher.publish(env.tenant_id, page.events)
        metrics.pages += 1
        metrics.events += publish_result.events
        metrics.bytes += publish_result.bytes

        if page.continuation:
            previous = state
            new_state = replace(state, continuation_uri=page.continuation)
            state = store.save(env.tenant_id, new_state, match_etag=previous.etag, previous_state=previous)
        else:
            previous = state
            new_state = replace(state, resume_from=next_day(day_start), continuation_uri=None)
            state = store.save(env.tenant_id, new_state, match_etag=previous.etag, previous_state=previous)

    if not STOP_EVENT.is_set() and (state.resume_from is None or state.resume_from >= processing_now) and state.continuation_uri is None:
        previous = state
        new_state = replace(
            state,
            last_end=datetime.now(UTC),
            resume_from=None,
            continuation_uri=None,
        )
        state = store.save(env.tenant_id, new_state, match_etag=previous.etag, previous_state=previous)

    metrics.retries += client.consume_retry_count()
    log_metrics(metrics)

