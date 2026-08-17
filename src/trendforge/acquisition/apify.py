from __future__ import annotations

from typing import Any

import httpx

from trendforge.acquisition.errors import AcquisitionError, MissingTokenError
from trendforge.acquisition.provider import ActorRunResult
from trendforge.config import get_settings

DEFAULT_BASE_URL = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
PAGE_SIZE = 100


def extract_cost(data: dict[str, Any] | None) -> tuple[float | None, str | None]:
    """Return (amount, currency) only when Apify reports a numeric cost. Never invent."""
    if not isinstance(data, dict):
        return None, None
    for key in ("usageTotalUsd", "usageUsd"):
        val = data.get(key)
        if isinstance(val, (int, float)):
            return float(val), "USD"
    usage = data.get("usage")
    if isinstance(usage, dict):
        for key in ("USD", "usd", "totalUsd"):
            val = usage.get(key)
            if isinstance(val, (int, float)):
                return float(val), "USD"
    return None, None


class ApifyClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ):
        settings = get_settings()
        self.token = (token if token is not None else settings.apify_api_token).strip()
        self.base_url = (base_url or settings.apify_api_base_url or DEFAULT_BASE_URL).rstrip("/")
        self._client = client

    def _client_ctx(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=httpx.Timeout(30.0, read=120.0))

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise MissingTokenError(
                "APIFY_API_TOKEN is not set. Add it to .env to run Apify acquisition. "
                "YouTube discovery continues without it."
            )
        params: dict[str, Any] = {"token": self.token}
        if extra:
            params.update(extra)
        return params

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        owns = self._client is None
        client = self._client_ctx()
        try:
            resp = client.request(method, f"{self.base_url}/{path.lstrip('/')}", **kwargs)
        finally:
            if owns:
                client.close()
        if resp.status_code in {402, 429}:
            raise AcquisitionError(f"Apify quota/limit ({resp.status_code}): {resp.text[:500]}")
        if resp.status_code == 401:
            raise AcquisitionError("Apify API token was rejected.")
        if resp.status_code >= 400:
            raise AcquisitionError(f"Apify API {resp.status_code}: {resp.text[:500]}")
        return resp

    def fetch_dataset_items(
        self,
        dataset_id: str,
        *,
        max_items: int = 100,
        page_size: int = PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        offset = 0
        remaining = max(0, int(max_items))
        while remaining > 0:
            limit = min(page_size, remaining)
            resp = self._request(
                "GET",
                f"datasets/{dataset_id}/items",
                params=self._params({"clean": 1, "limit": limit, "offset": offset}),
            )
            payload = resp.json()
            if not isinstance(payload, list):
                break
            page = [item for item in payload if isinstance(item, dict)]
            if not page:
                break
            collected.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
            remaining = max_items - len(collected)
        return collected[:max_items]

    def run_actor_result(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        *,
        timeout_secs: int = 180,
        max_total_charge_usd: float = 0.5,
        max_items: int | None = 40,
    ) -> ActorRunResult:
        actor = actor_id.replace("/", "~")
        params: dict[str, Any] = {
            "waitForFinish": min(60, max(1, timeout_secs)),
            "timeout": timeout_secs,
            "maxTotalChargeUsd": max_total_charge_usd,
        }
        if max_items is not None:
            params["maxItems"] = max_items
        resp = self._request(
            "POST",
            f"acts/{actor}/runs",
            params=self._params(params),
            json=run_input,
        )
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise AcquisitionError("Apify run response missing data")
        status = str(data.get("status") or "")
        run_id = data.get("id")
        waited = 0
        while status not in TERMINAL and run_id and waited < timeout_secs:
            poll = self._request(
                "GET",
                f"actor-runs/{run_id}",
                params=self._params({"waitForFinish": 15}),
            )
            body = poll.json()
            data = body.get("data") if isinstance(body, dict) else data
            status = str((data or {}).get("status") or "")
            waited += 15
        cost, currency = extract_cost(data if isinstance(data, dict) else None)
        dataset_id = (data or {}).get("defaultDatasetId") if isinstance(data, dict) else None
        if status != "SUCCEEDED":
            return ActorRunResult(
                actor_id=actor_id,
                items=[],
                run_id=str(run_id) if run_id else None,
                dataset_id=str(dataset_id) if dataset_id else None,
                status=status or "unknown",
                actual_cost=cost,
                currency=currency,
                raw_run=data if isinstance(data, dict) else {},
            )
        items: list[dict[str, Any]] = []
        if dataset_id:
            items = self.fetch_dataset_items(
                str(dataset_id),
                max_items=max_items or 100,
            )
        return ActorRunResult(
            actor_id=actor_id,
            items=items,
            run_id=str(run_id) if run_id else None,
            dataset_id=str(dataset_id) if dataset_id else None,
            status="SUCCEEDED",
            actual_cost=cost,
            currency=currency,
            raw_run=data if isinstance(data, dict) else {},
        )

    def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        *,
        timeout_secs: int = 180,
        max_total_charge_usd: float = 0.5,
        max_items: int | None = 40,
    ) -> list[dict[str, Any]]:
        result = self.run_actor_result(
            actor_id,
            run_input,
            timeout_secs=timeout_secs,
            max_total_charge_usd=max_total_charge_usd,
            max_items=max_items,
        )
        if result.status != "SUCCEEDED":
            raise AcquisitionError(
                f"Apify actor {actor_id} ended with status {result.status or 'unknown'}"
            )
        return result.items
