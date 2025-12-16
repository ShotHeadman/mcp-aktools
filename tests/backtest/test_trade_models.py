"""交易模型测试模块。

测试 Position、TradeRecord 等交易相关模型。
"""

import pytest
from mcp_aktools.models.trade import Position, TradeRecord, PriceType, OrderType


class TestPriceType:
    """测试 PriceType 枚举。"""

    def test_price_type_values(self):
        """测试价格类型枚举值。"""
        assert PriceType.CLOSE.value == "close"
        assert PriceType.HIGH.value == "high"
        assert PriceType.LOW.value == "low"
        assert PriceType.OPEN.value == "open"


class TestOrderType:
    """测试 OrderType 枚举。"""

    def test_order_type_values(self):
        """测试订单类型枚举值。"""
        assert OrderType.LOTS.value == "lots"
        assert OrderType.CASH_PERCENT.value == "cash_percent"
        assert OrderType.POSITION_PERCENT.value == "position_percent"


class TestPosition:
    """测试 Position 持仓类。"""

    def test_initial_position(self):
        """测试初始持仓为空。"""
        position = Position()
        assert position.quantity == 0
        assert position.avg_cost == 0.0
        assert position.layers == 0

    def test_update_on_buy_new_position(self):
        """测试新建仓位。"""
        position = Position()
        position.update_on_buy(1000, 10.0)
        assert position.quantity == 1000
        assert position.avg_cost == 10.0
        assert position.layers == 1

    def test_update_on_buy_add_position(self):
        """测试加仓更新平均成本。"""
        position = Position()
        position.update_on_buy(1000, 10.0)
        position.update_on_buy(1000, 12.0)
        assert position.quantity == 2000
        assert position.avg_cost == 11.0  # (10*1000 + 12*1000) / 2000
        assert position.layers == 2

    def test_update_on_sell_partial(self):
        """测试部分卖出。"""
        position = Position()
        position.update_on_buy(1000, 10.0)
        position.update_on_sell(500)
        assert position.quantity == 500
        assert position.avg_cost == 10.0  # 平均成本不变
        assert position.layers == 1

    def test_update_on_sell_all(self):
        """测试全部卖出清仓。"""
        position = Position()
        position.update_on_buy(1000, 10.0)
        position.update_on_sell(1000)
        assert position.quantity == 0
        assert position.avg_cost == 0.0
        assert position.layers == 0


class TestTradeRecord:
    """测试 TradeRecord 交易记录类。"""

    def test_trade_record_creation(self, sample_trade_record):
        """测试交易记录创建。"""
        assert sample_trade_record.timestamp == "2024-01-15 09:30:00"
        assert sample_trade_record.action == "BUY"
        assert sample_trade_record.quantity == 1000
        assert sample_trade_record.price == 10.50

    def test_trade_record_to_json(self, sample_trade_record):
        """测试交易记录序列化。"""
        json_str = sample_trade_record.to_json()
        assert "2024-01-15 09:30:00" in json_str
        assert "BUY" in json_str
        assert "1000" in json_str

    def test_trade_record_from_json(self, sample_trade_record):
        """测试交易记录反序列化。"""
        json_str = sample_trade_record.to_json()
        restored = TradeRecord.from_json(json_str)
        assert restored.timestamp == sample_trade_record.timestamp
        assert restored.action == sample_trade_record.action
        assert restored.quantity == sample_trade_record.quantity
        assert restored.price == sample_trade_record.price

    def test_trade_record_round_trip(self, sample_trade_record):
        """测试交易记录序列化往返。"""
        json_str = sample_trade_record.to_json()
        restored = TradeRecord.from_json(json_str)
        json_str2 = restored.to_json()
        assert json_str == json_str2
