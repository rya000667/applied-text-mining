from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from ActivityPoller.__init__ import _run_once
from ActivityPoller.pbi_client import ActivityPage, ContinuationExpiredError
from ActivityPoller.publisher import PublishResult
from ActivityPoller.state_store import CheckpointState, midnight_utc


class FakeStateStore:
    def __init__(self, state: CheckpointState) -> None:
        self.state = state
        self._etag_counter = 0 if state.etag is None else 1

    def load(self, tenant_id: str) -> CheckpointState:
        return replace(self.state)

    def save(self, tenant_id: str, state: CheckpointState, match_etag, previous_state=None) -> CheckpointState:
        if previous_state is not None and previous_state.etag != match_etag:
            raise AssertionError("ETag mismatch in test double")
        self._etag_counter += 1
        new_state = replace(state, etag=f"etag-{self._etag_counter}")
        self.state = new_state
        return replace(new_state)


class FakePublisher:
    def __init__(self) -> None:
        self.sent = []

    def publish(self, tenant_id: str, events):
        events = list(events)
        if events:
            self.sent.append((tenant_id, events))
        total_bytes = sum(len(str(e)) for e in events)
        return PublishResult(events=len(events), bytes=total_bytes)


class FakeClient:
    def __init__(self, pages):
        self._pages = list(pages)
        self._retry_count = 0
        self.calls = []

    def fetch_page(self, start, end, continuation_uri):
        self.calls.append((start, end, continuation_uri))
        if not self._pages:
            return ActivityPage(events=[], continuation=None)
        entry = self._pages[0]
        try:
            if callable(entry):
                result = entry(start, end, continuation_uri)
            else:
                events, continuation = entry
                result = ActivityPage(events=events, continuation=continuation)
        except ContinuationExpiredError:
            raise
        else:
            self._pages.pop(0)
            return result

    def consume_retry_count(self) -> int:
        value = self._retry_count
        self._retry_count = 0
        return value


def make_env(overlap: int = 60):
    return SimpleNamespace(tenant_id="tenant", overlap_seconds=overlap)


def test_resume_processes_continuations_until_day_completes(monkeypatch):
    start_day = midnight_utc(datetime.now(UTC) - timedelta(days=1))
    initial_state = CheckpointState(last_end=None, resume_from=start_day, continuation_uri=None, etag="etag-0")
    store = FakeStateStore(initial_state)
    publisher = FakePublisher()
    client = FakeClient([
        ([{"Id": "1"}], "https://cont"),
        ([{"Id": "2"}], None),
        ([], None),
    ])

    _run_once(store, client, publisher, make_env())

    assert store.state.resume_from is None
    assert store.state.continuation_uri is None
    assert store.state.last_end is not None
    assert len(publisher.sent) == 2
    assert publisher.sent[0][1][0]["Id"] == "1"
    assert publisher.sent[1][1][0]["Id"] == "2"
    assert len(client.calls) == 3


def test_expired_continuation_replays_day(monkeypatch):
    start_day = midnight_utc(datetime.now(UTC) - timedelta(days=1))
    initial_state = CheckpointState(
        last_end=None,
        resume_from=start_day,
        continuation_uri="https://stale",
        etag="etag-0",
    )
    store = FakeStateStore(initial_state)
    publisher = FakePublisher()

    def first_call(start, end, continuation_uri):
        if continuation_uri:
            raise ContinuationExpiredError("stale")
        return ActivityPage(events=[{"Id": "x"}], continuation=None)

    client = FakeClient([first_call, ([], None)])

    _run_once(store, client, publisher, make_env())

    assert store.state.resume_from is None
    assert store.state.continuation_uri is None
    assert store.state.last_end is not None
    assert len(publisher.sent) == 1
    assert publisher.sent[0][1][0]["Id"] == "x"
    assert client.calls[0][2] == "https://stale"
    assert len(client.calls) == 3

