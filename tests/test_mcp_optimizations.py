"""MCP 工具优化相关测试。"""

from datetime import datetime, timedelta
import sqlite3

import pandas as pd
import pytest

from mcp_aktools.cache.sqlite_backend import SQLiteCacheBackend
from mcp_aktools.config import reload_settings
from mcp_aktools.exceptions import ValidationException
from mcp_aktools.tools.backtest import _parse_strategy_code
from mcp_aktools.tools.stock import stock_prices
from mcp_aktools.utils.formatting import dataframe_records, validate_response_format
from mcp_aktools.utils.user_code import validate_user_code


def test_user_code_validator_blocks_imports_by_default():
    with pytest.raises(ValidationException):
        validate_user_code("import os\nresult = 1")


def test_user_code_validator_blocks_dangerous_calls():
    with pytest.raises(ValidationException):
        validate_user_code("result = open('/tmp/file').read()")


def test_user_code_validator_allows_basic_strategy_shape():
    validate_user_code(
        """
class DemoStrategy(Strategy):
    def on_bar(self, bar):
        if len([1, 2, 3]) > 2:
            pass
"""
    )


def test_strategy_parser_runs_with_restricted_builtins(monkeypatch):
    monkeypatch.setenv("ENABLE_USER_CODE_EXECUTION", "true")
    reload_settings()

    strategy_class = _parse_strategy_code(
        """
class DemoStrategy(Strategy):
    def on_bar(self, bar):
        values = sorted([3, 1, 2])
        if len(values) == 3:
            return None
"""
    )

    assert strategy_class.__name__ == "DemoStrategy"

    monkeypatch.delenv("ENABLE_USER_CODE_EXECUTION", raising=False)
    reload_settings()


def test_validate_response_format_normalizes_and_rejects_invalid_values():
    assert validate_response_format("JSON") == "json"
    assert validate_response_format("") == "csv"
    assert validate_response_format("", default="json") == "json"

    with pytest.raises(ValidationException):
        validate_response_format("xml")


def test_price_tools_default_to_json_response_format():
    assert stock_prices.__defaults__[-1].default == "json"


def test_dataframe_records_replaces_nan_with_none():
    df = pd.DataFrame({"日期": pd.to_datetime(["2024-01-01"]), "值": [float("nan")]})

    records = dataframe_records(df)

    assert records == [{"日期": "2024-01-01 00:00:00", "值": None}]


def test_sqlite_backend_expires_non_price_entries(tmp_path):
    db_path = tmp_path / "cache.db"
    backend = SQLiteCacheBackend(str(db_path), disk_ttl=1)
    backend.set("stock_info-000001", b"payload", data_type="info", symbol="000001")

    old_time = (datetime.now() - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE cache_entries SET updated_at = ? WHERE cache_key = ?",
            (old_time, "stock_info-000001"),
        )
        conn.commit()

    assert backend.get("stock_info-000001") is None


def test_sqlite_backend_keeps_price_entries_persistent(tmp_path):
    db_path = tmp_path / "cache.db"
    backend = SQLiteCacheBackend(str(db_path), disk_ttl=1)
    backend.set("stock_zh_a_hist-000001", b"payload", data_type="price", symbol="000001")

    old_time = (datetime.now() - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE cache_entries SET updated_at = ? WHERE cache_key = ?",
            (old_time, "stock_zh_a_hist-000001"),
        )
        conn.commit()

    assert backend.get("stock_zh_a_hist-000001") == b"payload"
