"""Tests for Grafana query executor."""

import pytest
from datetime import timedelta
from grafana_mcp.query_executor import QueryExecutor


class TestParseStepMs:
    def setup_method(self):
        from unittest.mock import MagicMock
        self.executor = QueryExecutor(client=MagicMock())

    def test_seconds(self):
        assert self.executor._parse_step_ms("60s") == 60_000

    def test_minutes(self):
        assert self.executor._parse_step_ms("5m") == 300_000

    def test_hours(self):
        assert self.executor._parse_step_ms("1h") == 3_600_000

    def test_invalid_defaults(self):
        assert self.executor._parse_step_ms("abc") == 60_000


class TestParseDuration:
    def setup_method(self):
        from unittest.mock import MagicMock
        self.executor = QueryExecutor(client=MagicMock())

    def test_minutes(self):
        assert self.executor._parse_duration("30m") == timedelta(minutes=30)

    def test_hours(self):
        assert self.executor._parse_duration("6h") == timedelta(hours=6)

    def test_days(self):
        assert self.executor._parse_duration("7d") == timedelta(days=7)

    def test_invalid_returns_default(self):
        result = self.executor._parse_duration("invalid")
        assert result == timedelta(hours=1)


class TestQueryValidation:
    def setup_method(self):
        from unittest.mock import MagicMock
        self.executor = QueryExecutor(client=MagicMock())

    def test_valid_promql(self):
        self.executor._validate_query("rate(http_requests_total[5m])")

    def test_blocks_script_injection(self):
        with pytest.raises(ValueError, match="Blocked"):
            self.executor._validate_query("<script>alert(1)</script>")

    def test_blocks_eval(self):
        with pytest.raises(ValueError, match="Blocked"):
            self.executor._validate_query("eval(bad_code)")

    def test_blocks_long_query(self):
        with pytest.raises(ValueError, match="too long"):
            self.executor._validate_query("x" * 2001)


class TestFormatPrometheusResult:
    def setup_method(self):
        from unittest.mock import MagicMock
        self.executor = QueryExecutor(client=MagicMock())

    def test_empty_result(self):
        result = self.executor._format_prometheus_result({"results": {}})
        assert result["query_type"] == "prometheus"
        assert result["series"] == []

    def test_single_series(self):
        raw = {
            "results": {
                "A": {
                    "frames": [
                        {
                            "schema": {
                                "name": "cpu",
                                "fields": [
                                    {"name": "time"},
                                    {"name": "value", "labels": {"instance": "prod-1"}},
                                ],
                            },
                            "data": {
                                "values": [
                                    [1000, 2000, 3000],
                                    [0.45, 0.67, 0.89],
                                ]
                            },
                        }
                    ]
                }
            }
        }
        result = self.executor._format_prometheus_result(raw)
        assert len(result["series"]) == 1
        assert result["series"][0]["latest_value"] == 0.89
        assert result["series"][0]["labels"]["instance"] == "prod-1"


class TestFormatLokiResult:
    def setup_method(self):
        from unittest.mock import MagicMock
        self.executor = QueryExecutor(client=MagicMock())

    def test_empty_result(self):
        result = self.executor._format_loki_result({"results": {}})
        assert result["query_type"] == "loki"
        assert result["total_lines"] == 0

    def test_log_lines(self):
        raw = {
            "results": {
                "A": {
                    "frames": [
                        {
                            "schema": {},
                            "data": {
                                "values": [
                                    [3000, 2000, 1000],
                                    ["error msg 3", "error msg 2", "error msg 1"],
                                ]
                            },
                        }
                    ]
                }
            }
        }
        result = self.executor._format_loki_result(raw)
        assert result["total_lines"] == 3
        assert result["lines"][0]["line"] == "error msg 3"
