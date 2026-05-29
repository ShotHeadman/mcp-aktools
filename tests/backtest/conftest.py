"""回测测试的共享夹具和配置。"""

import pytest
from mcp_aktools.backtest.config import BacktestConfig
from mcp_aktools.models.trade import TradeRecord, Position


@pytest.fixture
def valid_config():
    """创建有效的回测配置。"""
    return BacktestConfig(
        stock_code="sh.600000",
        start_date="2024-01-01",
        end_date="2024-06-30",
        initial_capital=1000000.0,
        frequency="d",
    )


@pytest.fixture
def sample_trade_record():
    """创建示例交易记录。"""
    return TradeRecord(
        timestamp="2024-01-15 09:30:00",
        action="BUY",
        quantity=1000,
        price=10.50,
        commission=8.97,
        cash_after=989491.03,
        position_after=1000,
    )


@pytest.fixture
def sample_position():
    """创建示例持仓。"""
    position = Position()
    position.update_on_buy(1000, 10.50)
    return position
