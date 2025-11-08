"""Fabric/Power BI Admin client wrapper."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:  # pragma: no cover
    import requests
except ModuleNotFoundError:  # pragma: no cover
    class _RequestsModule:  # type: ignore
        def Session(self) -> None:
            raise RuntimeError("requests is required at runtime")

    requests = _RequestsModule()  # type: ignore
try:  # pragma: no cover - optional at test time
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
except ModuleNotFoundError:  # pragma: no cover
    DefaultAzureCredential = object  # type: ignore

    class SecretClient:  # type: ignore
        def __init__(self, *_, **__):
            raise RuntimeError("azure-keyvault-secrets is required at runtime")

try:  # pragma: no cover
    from msal import ConfidentialClientApplication
except ModuleNotFoundError:  # pragma: no cover
    ConfidentialClientApplication = object  # type: ignore

try:  # pragma: no cover
    from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
except ModuleNotFoundError:  # pragma: no cover
    class Retrying:  # type: ignore
        def __init__(self, *_, **__):
            raise RuntimeError("tenacity is required at runtime")

    def retry_if_exception_type(*_, **__):  # type: ignore
        raise RuntimeError("tenacity is required at runtime")

    def stop_after_attempt(*_, **__):  # type: ignore
        raise RuntimeError("tenacity is required at runtime")

    def wait_exponential_jitter(*_, **__):  # type: ignore
        raise RuntimeError("tenacity is required at runtime")

from .logging_util import log_event, log_retry

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
BASE_URL = "https://api.powerbi.com/v1.0/myorg/admin/activityevents"


class ContinuationExpiredError(Exception):
    """Raised when a continuation URI is no longer valid."""


class RetryableRequestError(Exception):
    """Raised when a retryable HTTP error occurs."""


@dataclass
class ActivityPage:
    events: List[Dict[str, object]]
    continuation: Optional[str]


class PBIClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        keyvault_url: str,
        secret_name: str,
        timeout: int,
        max_retries: int,
        credential: Optional[DefaultAzureCredential] = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()
        self._credential = credential or DefaultAzureCredential()
        self._secret_client = SecretClient(vault_url=keyvault_url, credential=self._credential)
        secret = self._secret_client.get_secret(secret_name)
        self._client = ConfidentialClientApplication(
            client_id=self.client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=secret.value,
        )
        self._retry_counter = 0

    def _get_token(self) -> str:
        result = self._client.acquire_token_silent(scopes=[FABRIC_SCOPE], account=None)
        if not result:
            result = self._client.acquire_token_for_client(scopes=[FABRIC_SCOPE])
        if "access_token" not in result:
            raise RuntimeError(f"Failed to acquire token: {result}")
        return result["access_token"]

    def fetch_page(
        self,
        start: datetime,
        end: datetime,
        continuation_uri: Optional[str],
    ) -> ActivityPage:
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        if continuation_uri:
            url = continuation_uri
            params = None
        else:
            params = {
                "startDateTime": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "endDateTime": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            }
            url = BASE_URL
        response = self._request(url, headers=headers, params=params)
        if response.status_code == 200:
            payload = self._load_json(response)
            continuation = self._extract_continuation(payload, params)
            events = self._extract_events(payload)
            return ActivityPage(events=events, continuation=continuation)
        if continuation_uri and response.status_code in {400, 404, 410}:
            raise ContinuationExpiredError(f"Continuation failed with {response.status_code}")
        response.raise_for_status()
        raise RuntimeError("Unexpected response")

    def _request(
        self,
        url: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, str]],
    ) -> Any:
        retryer = Retrying(
            retry=retry_if_exception_type(RetryableRequestError),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential_jitter(initial=1, max=30),
            reraise=True,
            before_sleep=self._before_sleep,
        )
        for attempt in retryer:
            with attempt:
                response = self._session.get(url, headers=headers, params=params, timeout=self.timeout)
                if response.status_code in {429, 500, 502, 503, 504}:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        log_event("pbi.retry_after", value=retry_after)
                    raise RetryableRequestError(f"HTTP {response.status_code}")
                return response
        raise RuntimeError("Retry loop exhausted")

    @staticmethod
    def _extract_continuation(payload: Any, params: Optional[Dict[str, str]]) -> Optional[str]:
        continuation_uri = payload.get("continuationUri") if isinstance(payload, dict) else None
        if continuation_uri:
            return str(continuation_uri)
        if isinstance(payload, dict):
            token = payload.get("continuationToken")
            if token:
                base_params = {}
                if params:
                    base_params.update({k: v for k, v in params.items() if v is not None})
                base_params["continuationToken"] = str(token)
                return f"{BASE_URL}?{urlencode(base_params)}"
        return None

    @staticmethod
    def _extract_events(payload: Any) -> List[Dict[str, object]]:
        if isinstance(payload, list):
            return [event for event in payload if isinstance(event, dict)]
        if isinstance(payload, dict):
            for key in ("activityEventEntities", "value", "events"):
                maybe = payload.get(key)
                if isinstance(maybe, list):
                    return [event for event in maybe if isinstance(event, dict)]
        return []

    @staticmethod
    def _load_json(response: Any) -> Any:
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Failed to parse JSON from Fabric API") from exc

    def consume_retry_count(self) -> int:
        value = self._retry_counter
        self._retry_counter = 0
        return value

    def _before_sleep(self, retry_state) -> None:
        wait = getattr(retry_state.next_action, "sleep", None)
        self._retry_counter += 1
        log_retry(
            "pbi.request",
            attempt=retry_state.attempt_number,
            wait=wait or 0,
            details={"exception": str(retry_state.outcome.exception())},
        )

