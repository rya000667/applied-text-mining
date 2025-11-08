"""Event Hubs publishing utilities."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, Iterable, Optional

try:  # pragma: no cover
    from azure.eventhub import EventData, EventHubProducerClient
    from azure.identity import DefaultAzureCredential
except ModuleNotFoundError:  # pragma: no cover
    DefaultAzureCredential = object  # type: ignore

    class EventData:  # type: ignore
        def __init__(self, *_: object, **__: object) -> None:
            raise RuntimeError("azure-eventhub is required at runtime")

    class EventHubProducerClient:  # type: ignore
        def __init__(self, *_, **__):
            raise RuntimeError("azure-eventhub is required at runtime")


@dataclass
class PublishResult:
    events: int
    bytes: int


class EventPublisher:
    def __init__(
        self,
        fully_qualified_namespace: str,
        eventhub_name: str,
        credential: Optional[DefaultAzureCredential] = None,
    ) -> None:
        self._credential = credential or DefaultAzureCredential()
        self._producer = EventHubProducerClient(
            fully_qualified_namespace=fully_qualified_namespace,
            eventhub_name=eventhub_name,
            credential=self._credential,
            enable_idempotent_partitions=True,
        )

    def publish(self, tenant_id: str, events: Iterable[Dict[str, object]]) -> PublishResult:
        total_events = 0
        total_bytes = 0
        ingest_time = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        batch = self._producer.create_batch()
        batch_count = 0
        for event in events:
            body = {
                "tenantId": tenant_id,
                "ingestUtc": ingest_time,
                "event": event,
            }
            payload = json.dumps(body).encode("utf-8")
            event_data = EventData(payload)
            event_id = self._extract_event_id(event)
            if event_id:
                event_data.properties = {"eventId": event_id}
            if not batch.try_add(event_data):
                self._producer.send_batch(batch)
                batch = self._producer.create_batch()
                batch_count = 0
                if not batch.try_add(event_data):
                    raise RuntimeError("Event too large for Event Hubs batch")
            batch_count += 1
            total_events += 1
            total_bytes += len(payload)
        if batch_count > 0:
            self._producer.send_batch(batch)
        return PublishResult(events=total_events, bytes=total_bytes)

    @staticmethod
    def _extract_event_id(event: Dict[str, object]) -> Optional[str]:
        for key in ("Id", "ActivityId", "id", "activityId"):
            value = event.get(key)
            if isinstance(value, str):
                return value
        return None

    def close(self) -> None:
        self._producer.close()

