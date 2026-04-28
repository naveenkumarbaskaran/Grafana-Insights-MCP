<p align="center">
  <img src="assets/banner.svg" alt="Grafana Insights MCP" width="700">
</p>

# Grafana Insights MCP

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-1.0-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Give your LLM access to Grafana dashboards, alerts, and datasources through the Model Context Protocol.**

Query metrics, check firing alerts, list dashboards, read annotations — all through natural language. Your AI assistant becomes an SRE co-pilot.

```
"Are there any firing alerts in production?"
  ↓
Claude/GPT → Grafana MCP → Grafana HTTP API → Alert rules + state
  ↓
"3 alerts are currently firing:
 1. HIGH CPU on prod-api-03 (92% for 15min)
 2. Error rate spike on payment-service (5.2% vs 0.3% baseline)
 3. Disk space critical on db-replica-02 (94% used)"
```

## Quick Start

```bash
pip install grafana-insights-mcp

# Or from source
git clone https://github.com/naveenkumarbaskaran/Grafana-Insights-MCP.git
cd Grafana-Insights-MCP
pip install -e .

# Run
grafana-mcp --url https://grafana.yourcompany.com --token YOUR_API_KEY
```

### Claude Desktop Config

```json
{
  "mcpServers": {
    "grafana": {
      "command": "grafana-mcp",
      "args": ["--url", "https://grafana.yourcompany.com", "--token", "glsa_xxxx"]
    }
  }
}
```

## Features

### Dashboard Operations

| Tool | Description |
|------|-------------|
| `list_dashboards` | Search/list dashboards with folder, tags, and star count |
| `get_dashboard` | Get dashboard panels, queries, and variables |
| `get_panel_data` | Execute a panel's query and return results |
| `list_folders` | List dashboard folders |

### Alert Operations

| Tool | Description |
|------|-------------|
| `list_alerts` | List alert rules with states (firing, pending, normal) |
| `get_firing_alerts` | Get currently firing/pending alerts only |
| `get_alert_history` | Alert state history over time |
| `silence_alert` | Create a silence for an alert (write) |

### Data Query

| Tool | Description |
|------|-------------|
| `query_prometheus` | Execute PromQL queries against Prometheus datasource |
| `query_loki` | Execute LogQL queries against Loki datasource |
| `list_datasources` | List configured datasources |
| `get_datasource_health` | Check datasource connectivity |

### Annotations & Metadata

| Tool | Description |
|------|-------------|
| `list_annotations` | Get annotations (deploy markers, incidents) in a time range |
| `create_annotation` | Add an annotation to a dashboard |
| `get_org_info` | Grafana org/instance metadata |

## Architecture

```
┌────────────────────────────────────────────┐
│               MCP Client                    │
│          (Claude, GPT, Cursor)              │
└────────────────────┬───────────────────────┘
                     │ MCP Protocol (stdio)
                     ▼
┌────────────────────────────────────────────┐
│          Grafana Insights MCP               │
│                                            │
│  ┌──────────────┐  ┌───────────────────┐  │
│  │  Query        │  │  Alert Engine     │  │
│  │  Executor     │  │  (state, history, │  │
│  │  (PromQL,     │  │   silences)       │  │
│  │   LogQL)      │  └────────┬──────────┘  │
│  └──────┬───────┘           │             │
│         │          ┌────────▼──────────┐  │
│         │          │  Response         │  │
│         │          │  Formatter        │  │
│         │          │  (tables, charts) │  │
│         │          └────────┬──────────┘  │
│         ▼                   ▼             │
│  ┌──────────────────────────────────────┐ │
│  │       Grafana HTTP API v9+           │ │
│  │  /api/dashboards  /api/alerting      │ │
│  │  /api/ds/query     /api/annotations  │ │
│  └──────────────────────────────────────┘ │
└────────────────────────────────────────────┘
```

## Safety Features

### Read-Only by Default

Write operations (`silence_alert`, `create_annotation`) require explicit `--allow-writes` flag:

```bash
# Read-only (default)
grafana-mcp --url https://... --token ...

# Enable writes
grafana-mcp --url https://... --token ... --allow-writes
```

### Dashboard Folder Filtering

```bash
# Only expose dashboards in specific folders
grafana-mcp --url https://... --folders Production,SRE

# Exclude folders
grafana-mcp --url https://... --exclude-folders Test,Sandbox
```

### Query Timeouts

```bash
# Limit query execution time (default: 30s)
grafana-mcp --url https://... --query-timeout 15
```

### Time Range Limits

```bash
# Max lookback for queries (default: 24h)
grafana-mcp --url https://... --max-time-range 48h
```

## Example Conversations

**"What dashboards do we have for the payment service?"**
→ `list_dashboards(query="payment")` → matching dashboards with UIDs

**"Is anything on fire right now?"**
→ `get_firing_alerts()` → list of firing/pending alerts with labels

**"What's the P95 latency for the API gateway over the last hour?"**
→ `query_prometheus(expr="histogram_quantile(0.95, rate(http_duration_seconds_bucket{service='api-gateway'}[5m]))", range="1h")`

**"Show me deploy markers from today"**
→ `list_annotations(tags=["deploy"], from="today")` → deployment annotations

**"Silence the disk space alert for 2 hours while we clean up"**
→ `silence_alert(alert_id="...", duration="2h", comment="Disk cleanup in progress")`

## Query Language Support

| Datasource | Language | Tool |
|------------|----------|------|
| Prometheus | PromQL | `query_prometheus` |
| Loki | LogQL | `query_loki` |
| Others | Via panel execution | `get_panel_data` |

For datasources without direct query tools (InfluxDB, Elasticsearch, etc.), use `get_panel_data` to execute queries through existing dashboard panels.

## Testing

```bash
pytest tests/ -v

# Integration tests (requires Grafana instance)
GRAFANA_URL=https://... GRAFANA_TOKEN=... pytest tests/test_integration.py -v
```

## License

MIT
