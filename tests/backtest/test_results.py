"""BacktestResult 测试模块。

测试回测结果的创建和序列化功能。
"""

import pytest
from mcp_aktools.backtest.config import BacktestConfig
from mcp_aktools.backtest.results import BacktestResult
from mcp_aktools.models.trade import TradeRecord


class TestBacktestResultCreation:
    """测试 BacktestResult 创建。"""

    def test_basic_result_creation(self, valid_config):
        """测试基本结果创建。"""
        result = BacktestResult(
            config=valid_config,
            final_capital=1100000.0,
            strategy_return=0.10,
        )
        assert result.config == valid_config
        assert result.final_capital == 1100000.0
        assert result.strategy_return == 0.10

    def test_result_with_trades(self, valid_config, sample_trade_record):
        """测试带交易记录的结果。"""
        result = BacktestResult(
            config=valid_config,
            trades=[sample_trade_record],
            final_capital=1050000.0,
            total_trades=1,
        )
        assert len(result.trades) == 1
        assert result.trades[0].action == "BUY"


class TestBacktestResultSummary:
    """测试 BacktestResult 摘要功能。"""

    def test_get_summary(self, valid_config):
        """测试获取结果摘要。"""
        result = BacktestResult(
            config=valid_config,
            final_capital=1100000.0,
            strategy_return=0.10,
            max_drawdown=0.05,
            sharpe_ratio=1.5,
            win_rate=0.6,
            total_trades=10,
        )
        summary = result.get_summary()
        assert "sh.600000" in summary
        assert "1,100,000.00" in summary
        assert "10.00%" in summary


class TestBacktestResultSerialization:
    """测试 BacktestResult 序列化。"""

    def test_to_json(self, valid_config):
        """测试序列化为 JSON。"""
        result = BacktestResult(
            config=valid_config,
            final_capital=1100000.0,
            strategy_return=0.10,
        )
        json_str = result.to_json()
        assert "sh.600000" in json_str
        assert "1100000.0" in json_str

    def test_from_json(self, valid_config):
        """测试从 JSON 反序列化。"""
        result = BacktestResult(
            config=valid_config,
            final_capital=1100000.0,
            strategy_return=0.10,
            max_drawdown=0.05,
        )
        json_str = result.to_json()
        restored = BacktestResult.from_json(json_str)
        assert restored.config.stock_code == valid_config.stock_code
        assert restored.final_capital == 1100000.0
        assert restored.strategy_return == 0.10
        assert restored.max_drawdown == 0.05

    def test_round_trip_with_trades(self, valid_config, sample_trade_record):
        """测试带交易记录的序列化往返。"""
        result = BacktestResult(
            config=valid_config,
            trades=[sample_trade_record],
            final_capital=1050000.0,
            total_trades=1,
        )
        json_str = result.to_json()
        restored = BacktestResult.from_json(json_str)
        assert len(restored.trades) == 1
        assert restored.trades[0].action == sample_trade_record.action
        assert restored.trades[0].quantity == sample_trade_record.quantity

    def test_round_trip_with_equity_curve(self, valid_config):
        """测试带权益曲线的序列化往返。"""
        equity_curve = [1000000.0, 1010000.0, 1005000.0, 1020000.0]
        result = BacktestResult(
            config=valid_config,
            final_capital=1020000.0,
            equity_curve=equity_curve,
        )
        json_str = result.to_json()
        restored = BacktestResult.from_json(json_str)
        assert restored.equity_curve == equity_curve
