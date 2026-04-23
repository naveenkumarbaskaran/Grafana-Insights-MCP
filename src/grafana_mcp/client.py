"""Grafana HTTP API client with auth and retry."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3


class GrafanaClient:
    """Async client for Grafana HTTP API."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.user = user
        self.password = password
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Accept": "application/json"}
            auth = None

            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            elif self.user and self.password:
                auth = httpx.BasicAuth(self.user, self.password)

            self._client = httpx.AsyncClient(
                auth=auth,
                headers=headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    # ── Dashboards ──────────────────────────────────────────

    async def search_dashboards(
        self,
        query: str | None = None,
        tag: str | None = None,
        folder_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search dashboards."""
        params: dict[str, Any] = {"limit": limit, "type": "dash-db"}
        if query:
            params["query"] = query
        if tag:
            params["tag"] = tag
        if folder_id is not None:
            params["folderIds"] = folder_id
        return await self._get("/api/search", params=params)

    async def get_dashboard(self, uid: str) -> dict[str, Any]:
        """Get dashboard by UID."""
        return await self._get(f"/api/dashboards/uid/{uid}")

    async def list_folders(self) -> list[dict[str, Any]]:
        """List dashboard folders."""
        return await self._get("/api/folders")

    # ── Alerts ──────────────────────────────────────────────

    async def list_alert_rules(self) -> list[dict[str, Any]]:
        """List all Grafana-managed alert rules."""
        return await self._get("/api/v1/provisioning/alert-rules")

    async def get_alert_instances(self) -> dict[str, Any]:
        """Get current alert instances (Alertmanager)."""
        return await self._get("/api/alertmanager/grafana/api/v2/alerts")

    async def get_alert_groups(self) -> dict[str, Any]:
        """Get alert groups from Alertmanager."""
        return await self._get(
            "/api/alertmanager/grafana/api/v2/alerts/groups"
        )

    async def create_silence(
        self,
        matchers: list[dict[str, str]],
        starts_at: str,
        ends_at: str,
        comment: str = "",
        created_by: str = "grafana-mcp",
    ) -> dict[str, Any]:
        """Create a silence in Alertmanager."""
        body = {
            "matchers": matchers,
            "startsAt": starts_at,
            "endsAt": ends_at,
            "comment": comment,
            "createdBy": created_by,
        }
        return await self._post(
            "/api/alertmanager/grafana/api/v2/silences", json=body
        )

    # ── Data Query ──────────────────────────────────────────

    async def query_datasource(
        self,
        datasource_uid: str,
        query_body: dict[str, Any],
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> dict[str, Any]:
        """Execute a query against a datasource via the unified query API."""
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        body = {
            "queries": [
                {**query_body, "datasource": {"uid": datasource_uid}}
            ],
            "from": str(from_ts or (now - 3600000)),
            "to": str(to_ts or now),
        }
        return await self._post("/api/ds/query", json=body)

    async def list_datasources(self) -> list[dict[str, Any]]:
        """List all configured datasources."""
        return await self._get("/api/datasources")

    async def check_datasource_health(
        self, datasource_uid: str
    ) -> dict[str, Any]:
        """Check datasource health/connectivity."""
        return await self._get(
            f"/api/datasources/uid/{datasource_uid}/health"
        )

    # ── Annotations ─────────────────────────────────────────

    async def list_annotations(
        self,
        from_ts: int | None = None,
        to_ts: int | None = None,
        tags: list[str] | None = None,
        dashboard_uid: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List annotations in a time range."""
        params: dict[str, Any] = {"limit": limit}
        if from_ts:
            params["from"] = from_ts
        if to_ts:
            params["to"] = to_ts
        if tags:
            params["tags"] = ",".join(tags)
        if dashboard_uid:
            # Need dashboard ID, not UID — resolve first
            params["dashboardUID"] = dashboard_uid
        return await self._get("/api/annotations", params=params)

    async def create_annotation(
        self,
        text: str,
        tags: list[str] | None = None,
        dashboard_uid: str | None = None,
        panel_id: int | None = None,
        time_ms: int | None = None,
    ) -> dict[str, Any]:
        """Create an annotation."""
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        body: dict[str, Any] = {
            "text": text,
            "time": time_ms or now,
            "tags": tags or [],
        }
        if dashboard_uid:
            body["dashboardUID"] = dashboard_uid
        if panel_id:
            body["panelId"] = panel_id
        return await self._post("/api/annotations", json=body)

    # ── Org ─────────────────────────────────────────────────

    async def get_org(self) -> dict[str, Any]:
        """Get current org info."""
        return await self._get("/api/org")

    async def get_health(self) -> dict[str, Any]:
        """Get Grafana instance health."""
        return await self._get("/api/health")

    # ── HTTP Helpers ────────────────────────────────────────

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Any:
        client = await self._get_client()
        url = f"{self.base_url}{path}"

        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500 and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise
            except httpx.TransportError:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise
        raise RuntimeError(f"GET {path} failed after retries")

    async def _post(self, path: str, json: Any = None) -> Any:
        client = await self._get_client()
        resp = await client.post(f"{self.base_url}{path}", json=json)
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
