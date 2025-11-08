import pytest
from unittest.mock import Mock

from ActivityPoller.state_store import (
    CheckpointState,
    ETagMismatchError,
    ResourceModifiedError,
    StateStore,
)


def test_save_updates_etag_and_uses_match(monkeypatch):
    store = StateStore.__new__(StateStore)
    blob = Mock()
    blob.upload_blob.return_value = {"etag": "etag-new"}
    monkeypatch.setattr(store, "_blob_client", lambda tenant_id: blob)

    previous = CheckpointState(last_end=None, resume_from=None, continuation_uri=None, etag="etag-old")
    state = CheckpointState(last_end=None, resume_from=None, continuation_uri=None, etag="etag-old")

    result = store.save("tenant", state, match_etag="etag-old", previous_state=previous)

    assert result.etag == "etag-new"
    _, kwargs = blob.upload_blob.call_args
    assert kwargs["if_match"] == "etag-old"


def test_save_raises_on_etag_conflict(monkeypatch):
    store = StateStore.__new__(StateStore)
    blob = Mock()
    blob.upload_blob.side_effect = ResourceModifiedError("conflict")
    monkeypatch.setattr(store, "_blob_client", lambda tenant_id: blob)

    state = CheckpointState(last_end=None, resume_from=None, continuation_uri=None, etag="etag-old")

    with pytest.raises(ETagMismatchError):
        store.save("tenant", state, match_etag="etag-old", previous_state=None)

