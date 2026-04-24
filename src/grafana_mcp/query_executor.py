"""PromQL and LogQL query executor with safety limits."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from grafana_mcp.client import GrafanaClient

logger = logging.getLogger(__name__)

# Default time ranges
DEFAULT_RANGE = timedelta(hours=1)
MAX_RANGE = timedelta(hours=48)


class QueryExecutor:
    """Executes PromQL and LogQL queries with safety limits."""

    def __init__(
        self,
        client: GrafanaClient,
        max_time_range: timedelta = MAX_RANGE,
        query_timeout: int = 30,
    ):
        self.client = client
        self.max_range = max_time_range
        self.query_timeout = query_timeout
        self._datasource_cache: dict[str, dict] = {}

    async def query_prometheus(
        self,
        expr: str,
        datasource_uid: str | None = None,
        range_str: str = "1h",
        step: str = "60s",
    ) -> dict[str, Any]:
        """Execute a PromQL query.

        Args:
            expr: PromQL expression
            datasource_uid: Datasource UID (auto-detects Prometheus if None)
            range_str: Time range (e.g., "1h", "6h", "24h")
            step: Query step interval
        """
        self._validate_query(expr)

        if not datasource_uid:
            datasource_uid = await self._find_datasource("prometheus")

        now = datetime.now(timezone.utc)
        duration = self._parse_duration(range_str)
        if duration > self.max_range:
            duration = self.max_range

        from_ts = int((now - duration).timestamp() * 1000)
        to_ts = int(now.timestamp() * 1000)

        query_body = {
            "refId": "A",
            "expr": expr,
            "range": True,
            "intervalMs": self._parse_step_ms(step),
            "maxDataPoints": 500,
        }

        result = await self.client.query_datasource(
            datasource_uid, query_body, from_ts, to_ts
        )

        return self._format_prometheus_result(result)

    async def query_loki(
        self,
        expr: str,
        datasource_uid: str | None = None,
        range_str: str = "1h",
        limit: int = 100,
    ) -> dict[str, Any]:
        """Execute a LogQL query.

        Args:
            expr: LogQL expression
            datasource_uid: Datasource UID (auto-detects Loki if None)
            range_str: Time range
            limit: Max log lines
        """
        self._validate_query(expr)

        if not datasource_uid:
            datasource_uid = await self._find_datasource("loki")

        now = datetime.now(timezone.utc)
        duration = self._parse_duration(range_str)
        if duration > self.max_range:
            duration = self.max_range

        from_ts = int((now - duration).timestamp() * 1000)
        to_ts = int(now.timestamp() * 1000)

        query_body = {
            "refId": "A",
            "expr": expr,
            "queryType": "range",
            "maxLines": min(limit, 500),
        }

        result = await self.client.query_datasource(
            datasource_uid, query_body, from_ts, to_ts
        )

        return self._format_loki_result(result)

    async def _find_datasource(self, ds_type: str) -> str:
        """Auto-detect a datasource of the given type."""
        if not self._datasource_cache:
            sources = await self.client.list_datasources()
            for s in sources:
                self._datasource_cache[s.get("type", "")] = s

        ds = self._datasource_cache.get(ds_type)
        if not ds:
            raise ValueError(
                f"No {ds_type} datasource found. "
                f"Available: {list(self._datasource_cache.keys())}"
            )
        return ds["uid"]

    def _validate_query(self, expr: str) -> None:
        """Basic query validation to prevent dangerous patterns."""
        if len(expr) > 2000:
            raise ValueError("Query too long (max 2000 chars)")

        blocked = ["<script", "javascript:", "eval(", "exec("]
        lower = expr.lower()
        for pattern in blocked:
            if pattern in lower:
                raise ValueError(f"Blocked query pattern: {pattern}")

    def _parse_duration(self, range_str: str) -> timedelta:
        """Parse duration string like '1h', '30m', '24h', '7d'."""
        match = re.match(r"^(\d+)\s*(m|h|d)$", range_str.strip())
        if not match:
            return DEFAULT_RANGE

        value = int(match.group(1))
        unit = match.group(2)

        if unit == "m":
            return timedelta(minutes=value)
        elif unit == "h":
            return timedelta(hours=value)
        elif unit == "d":
            return timedelta(days=value)
        return DEFAULT_RANGE

    def _parse_step_ms(self, step: str) -> int:
        """Parse step string to milliseconds."""
        match = re.match(r"^(\d+)\s*(s|m|h)$", step.strip())
        if not match:
            return 60_000

        value = int(match.group(1))
        unit = match.group(2)

        if unit == "s":
            return value * 1000
        elif unit == "m":
            return value * 60_000
        elif unit == "h":
            return value * 3_600_000
        return 60_000

    def _format_prometheus_result(self, raw: dict) -> dict[str, Any]:
        """Format Prometheus query result for LLM consumption."""
        results = raw.get("results", {})
        frames = []

        for ref_id, result in results.items():
            for frame in result.get("frames", []):
                schema = frame.get("schema", {})
                data = frame.get("data", {})

                name = schema.get("name", ref_id)
                labels = {}
                for field in schema.get("fields", []):
                    if field.get("labels"):
                        labels = field["labels"]
                        break

                values = data.get("values", [])
                if len(values) >= 2:
                    timestamps = values[0]
                    metrics = values[1]
                    # Return last 10 data points
                    recent = list(zip(timestamps[-10:], metrics[-10:]))
                    frames.append({
                        "metric": name,
                        "labels": labels,
                        "latest_value": metrics[-1] if metrics else None,
                        "data_points": len(metrics),
                        "recent": [{"time": t, "value": v} for t, v in recent],
                    })

        return {"query_type": "prometheus", "series": frames}

    def _format_loki_result(self, raw: dict) -> dict[str, Any]:
        """Format Loki query result for LLM consumption."""
        results = raw.get("results", {})
        log_lines: list[dict[str, Any]] = []

        for ref_id, result in results.items():
            for frame in result.get("frames", []):
                data = frame.get("data", {})
                values = data.get("values", [])
                if len(values) >= 2:
                    timestamps = values[0]
                    lines = values[1]
                    for ts, line in zip(timestamps, lines):
                        log_lines.append({"timestamp": ts, "line": line})

        # Sort by timestamp, newest first
        log_lines.sort(key=lambda x: x["timestamp"], reverse=True)

        return {
            "query_type": "loki",
            "total_lines": len(log_lines),
            "lines": log_lines[:100],  # Cap at 100
        }
