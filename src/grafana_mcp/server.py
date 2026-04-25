"""MCP server wrapper for Grafana tools."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from grafana_mcp.client import GrafanaClient
from grafana_mcp.query_executor import QueryExecutor

logger = logging.getLogger(__name__)


class GrafanaMCPServer:
    """MCP server that exposes Grafana read (and optional write) tools."""

    def __init__(
        self,
        client: GrafanaClient,
        allow_writes: bool = False,
        allowed_folders: list[str] | None = None,
        excluded_folders: list[str] | None = None,
        max_time_range: str = "48h",
        query_timeout: int = 30,
    ):
        self.client = client
        self.allow_writes = allow_writes
        self.allowed_folders = allowed_folders
        self.excluded_folders = excluded_folders or []
        self.query_executor = QueryExecutor(
            client,
            max_time_range=self._parse_td(max_time_range),
            query_timeout=query_timeout,
        )

    def get_tools(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions."""
        tools = [
            {
                "name": "list_dashboards",
                "description": "Search or list Grafana dashboards. Returns title, UID, folder, tags.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search text"},
                        "tag": {"type": "string", "description": "Filter by tag"},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            },
            {
                "name": "get_dashboard",
                "description": "Get a dashboard's panels, queries, and variables by UID",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Dashboard UID"},
                    },
                    "required": ["uid"],
                },
            },
            {
                "name": "list_alerts",
                "description": "List alert rules with their current state (firing, pending, normal)",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_firing_alerts",
                "description": "Get only currently firing or pending alerts",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "query_prometheus",
                "description": "Execute a PromQL query against Prometheus datasource",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "expr": {"type": "string", "description": "PromQL expression"},
                        "range": {"type": "string", "default": "1h", "description": "Time range (e.g., '1h', '6h', '24h')"},
                        "step": {"type": "string", "default": "60s", "description": "Query step interval"},
                        "datasource_uid": {"type": "string", "description": "Datasource UID (auto-detected if omitted)"},
                    },
                    "required": ["expr"],
                },
            },
            {
                "name": "query_loki",
                "description": "Execute a LogQL query against Loki datasource",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "expr": {"type": "string", "description": "LogQL expression"},
                        "range": {"type": "string", "default": "1h"},
                        "limit": {"type": "integer", "default": 100, "description": "Max log lines"},
                        "datasource_uid": {"type": "string"},
                    },
                    "required": ["expr"],
                },
            },
            {
                "name": "list_datasources",
                "description": "List all configured datasources with type, URL, and health",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_datasource_health",
                "description": "Check connectivity of a specific datasource",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "datasource_uid": {"type": "string"},
                    },
                    "required": ["datasource_uid"],
                },
            },
            {
                "name": "list_annotations",
                "description": "Get annotations (deploy markers, incidents) in a time range",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "range": {"type": "string", "default": "24h", "description": "Lookback period"},
                        "tags": {"type": "string", "description": "Comma-separated tags to filter"},
                        "dashboard_uid": {"type": "string"},
                    },
                },
            },
            {
                "name": "get_org_info",
                "description": "Get Grafana organization info and instance health",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

        if self.allow_writes:
            tools.extend([
                {
                    "name": "silence_alert",
                    "description": "Create a silence for a specific alert",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "alertname": {"type": "string", "description": "Alert name matcher"},
                            "duration": {"type": "string", "default": "1h", "description": "Silence duration (e.g., '1h', '4h')"},
                            "comment": {"type": "string", "description": "Reason for silence"},
                        },
                        "required": ["alertname"],
                    },
                },
                {
                    "name": "create_annotation",
                    "description": "Add an annotation to a dashboard (deploy marker, incident, etc.)",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Annotation text"},
                            "tags": {"type": "string", "description": "Comma-separated tags"},
                            "dashboard_uid": {"type": "string"},
                        },
                        "required": ["text"],
                    },
                },
            ])

        return tools

    async def handle_tool_call(
        self, tool_name: str, args: dict[str, Any]
    ) -> Any:
        """Route and execute a tool call."""
        try:
            handler = getattr(self, f"_handle_{tool_name}", None)
            if handler:
                return await handler(args)
            return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.exception("Tool call failed: %s", tool_name)
            return {"error": f"{type(e).__name__}: {e}"}

    async def _handle_list_dashboards(self, args: dict) -> list[dict]:
        dashboards = await self.client.search_dashboards(
            query=args.get("query"),
            tag=args.get("tag"),
            limit=args.get("limit", 20),
        )
        result = []
        for d in dashboards:
            folder = d.get("folderTitle", "General")
            if self.allowed_folders and folder not in self.allowed_folders:
                continue
            if folder in self.excluded_folders:
                continue
            result.append({
                "uid": d.get("uid"),
                "title": d.get("title"),
                "folder": folder,
                "tags": d.get("tags", []),
                "url": d.get("url"),
            })
        return result

    async def _handle_get_dashboard(self, args: dict) -> dict:
        data = await self.client.get_dashboard(args["uid"])
        dashboard = data.get("dashboard", {})
        panels = []
        for p in dashboard.get("panels", []):
            panels.append({
                "id": p.get("id"),
                "title": p.get("title"),
                "type": p.get("type"),
                "datasource": p.get("datasource"),
            })
        return {
            "uid": dashboard.get("uid"),
            "title": dashboard.get("title"),
            "tags": dashboard.get("tags", []),
            "panels": panels,
            "variables": [
                {"name": v.get("name"), "type": v.get("type")}
                for v in dashboard.get("templating", {}).get("list", [])
            ],
        }

    async def _handle_list_alerts(self, args: dict) -> list[dict]:
        rules = await self.client.list_alert_rules()
        return [
            {
                "title": r.get("title"),
                "uid": r.get("uid"),
                "folder": r.get("folderUID"),
                "condition": r.get("condition"),
                "for": r.get("for"),
            }
            for r in (rules if isinstance(rules, list) else [])
        ]

    async def _handle_get_firing_alerts(self, args: dict) -> list[dict]:
        alerts = await self.client.get_alert_instances()
        firing = []
        for a in (alerts if isinstance(alerts, list) else []):
            state = a.get("status", {}).get("state", "")
            if state in ("firing", "active"):
                labels = a.get("labels", {})
                firing.append({
                    "alertname": labels.get("alertname", "unknown"),
                    "state": state,
                    "severity": labels.get("severity", "unknown"),
                    "summary": a.get("annotations", {}).get("summary", ""),
                    "labels": labels,
                    "starts_at": a.get("startsAt"),
                })
        return firing

    async def _handle_query_prometheus(self, args: dict) -> dict:
        return await self.query_executor.query_prometheus(
            expr=args["expr"],
            datasource_uid=args.get("datasource_uid"),
            range_str=args.get("range", "1h"),
            step=args.get("step", "60s"),
        )

    async def _handle_query_loki(self, args: dict) -> dict:
        return await self.query_executor.query_loki(
            expr=args["expr"],
            datasource_uid=args.get("datasource_uid"),
            range_str=args.get("range", "1h"),
            limit=args.get("limit", 100),
        )

    async def _handle_list_datasources(self, args: dict) -> list[dict]:
        sources = await self.client.list_datasources()
        return [
            {
                "uid": s.get("uid"),
                "name": s.get("name"),
                "type": s.get("type"),
                "url": s.get("url"),
                "is_default": s.get("isDefault", False),
            }
            for s in sources
        ]

    async def _handle_get_datasource_health(self, args: dict) -> dict:
        return await self.client.check_datasource_health(
            args["datasource_uid"]
        )

    async def _handle_list_annotations(self, args: dict) -> list[dict]:
        now = datetime.now(timezone.utc)
        duration = self._parse_td(args.get("range", "24h"))
        from_ts = int((now - duration).timestamp() * 1000)
        to_ts = int(now.timestamp() * 1000)

        tags = None
        if args.get("tags"):
            tags = [t.strip() for t in args["tags"].split(",")]

        annotations = await self.client.list_annotations(
            from_ts=from_ts,
            to_ts=to_ts,
            tags=tags,
            dashboard_uid=args.get("dashboard_uid"),
        )
        return [
            {
                "id": a.get("id"),
                "text": a.get("text"),
                "tags": a.get("tags", []),
                "time": a.get("time"),
                "created_by": a.get("login", ""),
            }
            for a in (annotations if isinstance(annotations, list) else [])
        ]

    async def _handle_get_org_info(self, args: dict) -> dict:
        org = await self.client.get_org()
        health = await self.client.get_health()
        return {
            "org": org,
            "health": health,
        }

    async def _handle_silence_alert(self, args: dict) -> dict:
        now = datetime.now(timezone.utc)
        duration = self._parse_td(args.get("duration", "1h"))
        ends_at = now + duration

        matchers = [
            {
                "name": "alertname",
                "value": args["alertname"],
                "isRegex": False,
                "isEqual": True,
            }
        ]

        result = await self.client.create_silence(
            matchers=matchers,
            starts_at=now.isoformat(),
            ends_at=ends_at.isoformat(),
            comment=args.get("comment", "Silenced via MCP"),
        )
        return {"silence_id": result.get("silenceID"), "status": "created"}

    async def _handle_create_annotation(self, args: dict) -> dict:
        tags = None
        if args.get("tags"):
            tags = [t.strip() for t in args["tags"].split(",")]

        result = await self.client.create_annotation(
            text=args["text"],
            tags=tags,
            dashboard_uid=args.get("dashboard_uid"),
        )
        return {"annotation_id": result.get("id"), "status": "created"}

    @staticmethod
    def _parse_td(s: str) -> timedelta:
        """Parse duration string to timedelta."""
        import re
        m = re.match(r"^(\d+)\s*(m|h|d)$", s.strip())
        if not m:
            return timedelta(hours=1)
        val = int(m.group(1))
        unit = m.group(2)
        if unit == "m":
            return timedelta(minutes=val)
        elif unit == "h":
            return timedelta(hours=val)
        elif unit == "d":
            return timedelta(days=val)
        return timedelta(hours=1)
