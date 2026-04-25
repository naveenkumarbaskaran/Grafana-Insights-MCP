"""CLI entry point for Grafana Insights MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from grafana_mcp.client import GrafanaClient
from grafana_mcp.server import GrafanaMCPServer

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Grafana Insights MCP — LLM access to Grafana"
    )
    p.add_argument("--url", required=True, help="Grafana base URL")
    p.add_argument("--token", help="Grafana API key / service account token")
    p.add_argument("--user", help="Basic auth user")
    p.add_argument("--password", help="Basic auth password")
    p.add_argument(
        "--allow-writes",
        action="store_true",
        help="Enable write tools (silence, annotate)",
    )
    p.add_argument(
        "--folders",
        help="Comma-separated allowed folder names",
    )
    p.add_argument(
        "--exclude-folders",
        help="Comma-separated folder names to exclude",
    )
    p.add_argument(
        "--query-timeout",
        type=int,
        default=30,
        help="Query timeout in seconds (default: 30)",
    )
    p.add_argument(
        "--max-time-range",
        default="48h",
        help="Max lookback for queries (default: 48h)",
    )
    p.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def create_server(args: argparse.Namespace) -> GrafanaMCPServer:
    client = GrafanaClient(
        base_url=args.url,
        api_key=args.token,
        user=args.user,
        password=args.password,
        timeout=float(args.query_timeout),
    )

    allowed_folders = None
    if args.folders:
        allowed_folders = [f.strip() for f in args.folders.split(",")]
    excluded_folders = []
    if args.exclude_folders:
        excluded_folders = [f.strip() for f in args.exclude_folders.split(",")]

    return GrafanaMCPServer(
        client=client,
        allow_writes=args.allow_writes,
        allowed_folders=allowed_folders,
        excluded_folders=excluded_folders,
        max_time_range=args.max_time_range,
        query_timeout=args.query_timeout,
    )


async def handle_message(
    server: GrafanaMCPServer, message: dict[str, Any]
) -> dict[str, Any]:
    """Handle a single JSON-RPC message."""
    method = message.get("method", "")
    msg_id = message.get("id")
    params = message.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "grafana-insights-mcp",
                    "version": "0.1.0",
                },
            },
        }

    if method == "tools/list":
        tools = server.get_tools()
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": tools},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        result = await server.handle_tool_call(tool_name, tool_args)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, default=str)}
                ]
            },
        }

    if method == "notifications/initialized":
        return None  # No response needed for notifications

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


async def run_stdio(server: GrafanaMCPServer) -> None:
    """Run the MCP server over stdio (JSON-RPC)."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    w_transport, w_protocol = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(w_transport, w_protocol, reader, asyncio.get_event_loop())

    while True:
        line = await reader.readline()
        if not line:
            break

        try:
            message = json.loads(line.decode("utf-8").strip())
        except json.JSONDecodeError:
            continue

        response = await handle_message(server, message)
        if response is not None:
            out = json.dumps(response) + "\n"
            writer.write(out.encode("utf-8"))
            await writer.drain()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level))

    server = create_server(args)
    try:
        asyncio.run(run_stdio(server))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
