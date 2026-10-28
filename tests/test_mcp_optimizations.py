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


def test_eastmoney_circuit_breaker_opens_and_resets():
    from mcp_aktools.clients.akshare import AkShareClient
    from mcp_aktools.config import Settings

    AkShareClient.reset_circuit_breaker()
    client = AkShareClient(Settings(cache_backend="memory", eastmoney_cooldown_seconds=60))

    def stock_zh_a_hist_em(symbol="000001"):
        raise ConnectionError("RemoteDisconnected")

    assert AkShareClient._infer_data_source(stock_zh_a_hist_em) == "eastmoney"
    assert not client._is_source_open("eastmoney")

    client._record_source_failure("eastmoney", ConnectionError("RemoteDisconnected"))
    assert client._is_source_open("eastmoney")

    AkShareClient.reset_circuit_breaker("eastmoney")
    assert not client._is_source_open("eastmoney")


def test_range_completion_consolidates_into_canonical_cache_key(tmp_path):
    from mcp_aktools.clients.akshare import AkShareClient
    from mcp_aktools.config import Settings

    db_path = tmp_path / "range_cache.db"
    settings = Settings(
        database_url=str(db_path),
        enable_range_completion=True,
    )
    client = AkShareClient(settings)

    call_log = []

    def fake_stock_hist(symbol: str, start_date: str, end_date: str, adjust: str = ""):
        call_log.append((start_date, end_date))
        dates = pd.date_range(start=start_date, end=end_date, freq="D")
        return pd.DataFrame(
            {
                "日期": dates.strftime("%Y-%m-%d"),
                "开盘": [10.0] * len(dates),
                "收盘": [10.5] * len(dates),
                "最高": [11.0] * len(dates),
                "最低": [9.8] * len(dates),
                "成交量": [1000] * len(dates),
            }
        )

    df1 = client.call(
        fake_stock_hist,
        symbol="sh600519",
        start_date="20250101",
        end_date="20250105",
        adjust="",
        use_range_completion=True,
    )
    assert len(df1) == 5
    assert len(call_log) == 1

    # 第二次请求扩展结束日期至 20250108，应触发增量补全且只保存一条规范化键
    df2 = client.call(
        fake_stock_hist,
        symbol="sh600519",
        start_date="20250103",
        end_date="20250108",
        adjust="",
        use_range_completion=True,
    )
    assert len(df2) == 6
    assert pd.to_datetime(df2["日期"]).min() == pd.Timestamp("2025-01-03")
    assert pd.to_datetime(df2["日期"]).max() == pd.Timestamp("2025-01-08")

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT cache_key, start_date, end_date FROM cache_entries").fetchall()
    assert len(rows) == 1
    assert "start_date" not in rows[0][0]
    assert rows[0][1] == "2025-01-01"
    assert rows[0][2] == "2025-01-08"


def test_sqlite_cleanup_redundant_entries_merges_fragments_and_removes_transients(tmp_path):
    from mcp_aktools.cache.serialization import deserialize_dataframe, serialize_dataframe

    db_path = tmp_path / "cleanup.db"
    backend = SQLiteCacheBackend(str(db_path))

    # 插入瞬态缓存条目
    backend.set("stock_zh_a_tick_tx_js-[('symbol', 'sh600519')]", b"tick", data_type="price", symbol="600519")
    backend.set("wave_analysis_600519_daily", b"wave", data_type="info", symbol="600519")

    # 插入两个碎片化的带起止日期的价格缓存
    df_part1 = pd.DataFrame({"日期": ["2025-01-01", "2025-01-02"], "收盘": [10.0, 10.5]})
    df_part2 = pd.DataFrame({"日期": ["2025-01-02", "2025-01-03"], "收盘": [10.5, 11.0]})
    backend.set(
        "stock_zh_a_hist_tx-[('adjust', ''), ('end_date', '20250102'), ('start_date', '20250101'), ('symbol', 'sh600519')]",
        serialize_dataframe(df_part1),
        data_type="price",
        symbol="600519",
    )
    backend.set(
        "stock_zh_a_hist_tx-[('adjust', ''), ('end_date', '20250103'), ('start_date', '20250102'), ('symbol', 'sh600519')]",
        serialize_dataframe(df_part2),
        data_type="price",
        symbol="600519",
    )

    stats = backend.cleanup_redundant_entries(vacuum=True)
    assert stats["transient_deleted"] == 2
    assert stats["consolidated_keys"] == 1
    assert stats["remaining_entries"] == 1

    canonical_key = "stock_zh_a_hist_tx-[('adjust', ''), ('symbol', 'sh600519')]"
    raw = backend.get(canonical_key)
    assert raw is not None
    merged_df = deserialize_dataframe(raw)
    assert len(merged_df) == 3
    assert list(merged_df["日期"]) == ["2025-01-01", "2025-01-02", "2025-01-03"]


def test_new_wyckoff_and_volume_profile_tools(monkeypatch):
    import numpy as np
    from mcp_aktools.tools import stock as stock_tools

    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    np.random.seed(42)
    closes = 100 + np.cumsum(np.random.normal(0.1, 1.2, size=60))
    highs = closes + np.abs(np.random.normal(0.8, 0.4, size=60))
    lows = closes - np.abs(np.random.normal(0.8, 0.4, size=60))
    opens = closes - np.random.normal(0.05, 0.5, size=60)
    vols = np.random.randint(50000, 150000, size=60)

    mock_df = pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": opens,
            "收盘": closes,
            "最高": highs,
            "最低": lows,
            "成交量": vols,
            "涨跌幅": np.round(np.gradient(closes) / closes * 100, 2),
            "涨跌额": np.round(np.gradient(closes), 2),
            "振幅": np.round((highs - lows) / closes * 100, 2),
        }
    )

    monkeypatch.setattr(
        stock_tools.get_akshare_client(),
        "stock_zh_a_hist",
        lambda **kwargs: mock_df.copy(),
    )

    wyckoff_report = stock_tools.analyze_wyckoff_structure(
        symbol="600519", market="sh", period="daily", lookback=50
    )
    assert isinstance(wyckoff_report, dict)
    assert wyckoff_report["symbol"] == "600519"
    assert "structure_and_liquidity" in wyckoff_report
    assert "effort_vs_result" in wyckoff_report
    assert "execution_plan" in wyckoff_report

    vp_report = stock_tools.stock_volume_profile(
        symbol="600519", market="sh", lookback=50, bins=10
    )
    assert isinstance(vp_report, dict)
    assert "poc_price" in vp_report
    assert "value_area_70pct" in vp_report
    assert "top_volume_nodes" in vp_report


def test_index_zh_a_hist_supports_weekly_resampling(monkeypatch):
    from mcp_aktools.tools import market as market_tools

    dates = pd.date_range("2025-01-01", periods=40, freq="B")
    mock_index_df = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [3200.0] * len(dates),
            "high": [3250.0] * len(dates),
            "low": [3180.0] * len(dates),
            "close": [3220.0 + i for i in range(len(dates))],
            "volume": [1000000] * len(dates),
        }
    )

    monkeypatch.setattr(
        market_tools._get_akshare_client(),
        "call",
        lambda *args, **kwargs: mock_index_df.copy(),
    )

    weekly_csv = market_tools.index_zh_a_hist(symbol="000001", period="weekly", limit=10)
    assert "日期" in weekly_csv and "收盘" in weekly_csv and "成交量" in weekly_csv
    lines = weekly_csv.strip().splitlines()
    assert 2 <= len(lines) <= 11

