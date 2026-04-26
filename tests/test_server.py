"""Tests for Grafana MCP server tool definitions and routing."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from grafana_mcp.server import GrafanaMCPServer
from grafana_mcp.client import GrafanaClient


class TestToolDefinitions:
    def _make_server(self, allow_writes=False):
        client = MagicMock(spec=GrafanaClient)
        return GrafanaMCPServer(client=client, allow_writes=allow_writes)

    def test_read_only_tool_count(self):
        server = self._make_server(allow_writes=False)
        tools = server.get_tools()
        # 10 read tools, 0 write tools
        assert len(tools) == 10
        names = {t["name"] for t in tools}
        assert "silence_alert" not in names
        assert "create_annotation" not in names

    def test_read_write_tool_count(self):
        server = self._make_server(allow_writes=True)
        tools = server.get_tools()
        # 10 read + 2 write = 12
        assert len(tools) == 12
        names = {t["name"] for t in tools}
        assert "silence_alert" in names
        assert "create_annotation" in names

    def test_all_tools_have_schemas(self):
        server = self._make_server(allow_writes=True)
        for tool in server.get_tools():
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool

    def test_required_fields(self):
        server = self._make_server()
        tools_by_name = {t["name"]: t for t in server.get_tools()}
        # get_dashboard requires uid
        schema = tools_by_name["get_dashboard"]["inputSchema"]
        assert "uid" in schema.get("required", [])
        # query_prometheus requires expr
        schema = tools_by_name["query_prometheus"]["inputSchema"]
        assert "expr" in schema.get("required", [])


class TestParseDuration:
    def test_hours(self):
        from datetime import timedelta
        assert GrafanaMCPServer._parse_td("1h") == timedelta(hours=1)

    def test_days(self):
        from datetime import timedelta
        assert GrafanaMCPServer._parse_td("7d") == timedelta(days=7)

    def test_minutes(self):
        from datetime import timedelta
        assert GrafanaMCPServer._parse_td("30m") == timedelta(minutes=30)

    def test_invalid_defaults_to_1h(self):
        from datetime import timedelta
        assert GrafanaMCPServer._parse_td("nope") == timedelta(hours=1)


class TestFolderFiltering:
    @pytest.mark.asyncio
    async def test_allowed_folders(self):
        client = MagicMock(spec=GrafanaClient)
        client.search_dashboards = AsyncMock(return_value=[
            {"uid": "a", "title": "Prod Dashboard", "folderTitle": "Production", "tags": []},
            {"uid": "b", "title": "Test Dashboard", "folderTitle": "Test", "tags": []},
        ])
        server = GrafanaMCPServer(client=client, allowed_folders=["Production"])
        result = await server._handle_list_dashboards({})
        assert len(result) == 1
        assert result[0]["folder"] == "Production"

    @pytest.mark.asyncio
    async def test_excluded_folders(self):
        client = MagicMock(spec=GrafanaClient)
        client.search_dashboards = AsyncMock(return_value=[
            {"uid": "a", "title": "Prod Dashboard", "folderTitle": "Production", "tags": []},
            {"uid": "b", "title": "Sandbox Dashboard", "folderTitle": "Sandbox", "tags": []},
        ])
        server = GrafanaMCPServer(client=client, excluded_folders=["Sandbox"])
        result = await server._handle_list_dashboards({})
        assert len(result) == 1
        assert result[0]["title"] == "Prod Dashboard"


class TestToolRouting:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        client = MagicMock(spec=GrafanaClient)
        server = GrafanaMCPServer(client=client)
        result = await server.handle_tool_call("nonexistent_tool", {})
        assert "error" in result
