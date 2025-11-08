"""Checkpoint state management backed by Azure Blob Storage."""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional

try:  # pragma: no cover - optional for tests
    from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient, BlobServiceClient, ContentSettings
except ModuleNotFoundError:  # pragma: no cover
    DefaultAzureCredential = object  # type: ignore

    class ResourceExistsError(Exception):
        pass

    class ResourceNotFoundError(Exception):
        pass

    class ResourceModifiedError(Exception):
        pass

    class BlobClient:  # type: ignore
        pass

    class BlobServiceClient:  # type: ignore
        def __init__(self, *_, **__):
            raise RuntimeError("azure-storage-blob is required at runtime")

    class ContentSettings:  # type: ignore
        def __init__(self, *_, **__):
            pass

from .logging_util import log_checkpoint_update, log_event

STATE_CONTENT_SETTINGS = ContentSettings(content_type="application/json")


@dataclass
class CheckpointState:
    last_end: Optional[datetime]
    resume_from: Optional[datetime]
    continuation_uri: Optional[str]
    etag: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lastEnd": isoformat_or_none(self.last_end),
            "resumeFrom": isoformat_or_none(self.resume_from),
            "continuationUri": self.continuation_uri,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any], etag: Optional[str]) -> "CheckpointState":
        return cls(
            last_end=parse_utc(payload.get("lastEnd")),
            resume_from=parse_utc(payload.get("resumeFrom")),
            continuation_uri=payload.get("continuationUri"),
            etag=etag,
        )


class StateStore:
    def __init__(self, account_url: str, container: str, credential: Optional[DefaultAzureCredential] = None) -> None:
        self._credential = credential or DefaultAzureCredential()
        service = BlobServiceClient(account_url=account_url, credential=self._credential)
        self._container = service.get_container_client(container)
        try:
            self._container.create_container()
        except ResourceExistsError:
            pass

    def blob_path(self, tenant_id: str) -> str:
        return f"state/fabric/{tenant_id}.json"

    def load(self, tenant_id: str) -> CheckpointState:
        blob = self._blob_client(tenant_id)
        try:
            downloader = blob.download_blob()
        except ResourceNotFoundError:
            return CheckpointState(last_end=None, resume_from=None, continuation_uri=None, etag=None)
        payload = json.loads(downloader.readall())
        return CheckpointState.from_dict(payload, downloader.properties.get("etag"))

    def save(
        self,
        tenant_id: str,
        state: CheckpointState,
        match_etag: Optional[str],
        previous_state: Optional[CheckpointState] = None,
    ) -> CheckpointState:
        blob = self._blob_client(tenant_id)
        data = json.dumps(state.to_dict()).encode("utf-8")
        old_state = previous_state.to_dict() if previous_state else {}
        try:
            result = blob.upload_blob(
                data,
                overwrite=True,
                content_settings=STATE_CONTENT_SETTINGS,
                if_match=match_etag,
            )
        except ResourceModifiedError as exc:
            raise ETagMismatchError() from exc
        log_checkpoint_update(old_state, state.to_dict())
        state.etag = result.get("etag")
        return state

    def upsert_with_retry(self, tenant_id: str, transform) -> CheckpointState:
        max_attempts = 5
        attempt = 0
        while True:
            attempt += 1
            current = self.load(tenant_id)
            new_state, match = transform(current)
            try:
                return self.save(tenant_id, new_state, match)
            except ETagMismatchError:
                if attempt >= max_attempts:
                    raise
                delay = 0.2 + random.random() * 0.3
                log_event("checkpoint.etag_conflict", attempt=attempt, wait=delay)
                time.sleep(delay)

    def _blob_client(self, tenant_id: str) -> BlobClient:
        return self._container.get_blob_client(self.blob_path(tenant_id))


class ETagMismatchError(Exception):
    """Raised when optimistic concurrency fails."""


def isoformat_or_none(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(UTC).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def parse_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clamp_start(now: datetime, last_end: Optional[datetime], overlap: int) -> datetime:
    window_floor = now - timedelta(days=28)
    if last_end is None:
        return window_floor
    candidate = last_end - timedelta(seconds=overlap)
    if candidate < window_floor:
        return window_floor
    return candidate


def midnight_utc(value: datetime) -> datetime:
    return datetime(year=value.year, month=value.month, day=value.day, tzinfo=UTC)


def next_day(value: datetime) -> datetime:
    return midnight_utc(value) + timedelta(days=1)


def end_of_day(value: datetime) -> datetime:
    return next_day(value)

