"""交易相关模型模块。

该模块定义交易相关的数据类和枚举，包括：
- PriceType: 价格类型枚举
- OrderType: 订单类型枚举
- Position: 持仓信息
- TradeRecord: 交易记录
"""

from dataclasses import dataclass
from enum import Enum
import json


class PriceType(Enum):
    """价格类型枚举。
    
    定义订单执行时使用的价格类型。
    """
    CLOSE = "close"  # 收盘价
    HIGH = "high"    # 最高价
    LOW = "low"      # 最低价
    OPEN = "open"    # 开盘价


class OrderType(Enum):
    """订单类型枚举。
    
    定义订单数量的计算方式。
    """
    LOTS = "lots"                        # 手数（100股为1手）
    CASH_PERCENT = "cash_percent"        # 现金比例
    POSITION_PERCENT = "position_percent"  # 持仓比例


@dataclass
class Position:
    """持仓信息数据类。
    
    记录当前持有的股票数量、成本和加仓层数。
    
    Attributes:
        quantity: 持仓数量（股）
        avg_cost: 平均成本价
        layers: 加仓层数（用于金字塔加仓）
    """
    quantity: int = 0
    avg_cost: float = 0.0
    layers: int = 0
    
    def update_on_buy(self, quantity: int, price: float) -> None:
        """买入时更新持仓信息。
        
        Args:
            quantity: 买入数量
            price: 买入价格
        """
        if self.quantity == 0:
            # 新建仓位
            self.avg_cost = price
            self.quantity = quantity
            self.layers = 1
        else:
            # 加仓，计算新的平均成本
            total_cost = self.avg_cost * self.quantity + price * quantity
            self.quantity += quantity
            self.avg_cost = total_cost / self.quantity
            self.layers += 1
    
    def update_on_sell(self, quantity: int) -> None:
        """卖出时更新持仓信息。
        
        Args:
            quantity: 卖出数量
        """
        self.quantity -= quantity
        if self.quantity <= 0:
            # 清仓
            self.quantity = 0
            self.avg_cost = 0.0
            self.layers = 0


@dataclass
class TradeRecord:
    """交易记录数据类。
    
    记录单笔买入或卖出的详细信息。
    
    Attributes:
        timestamp: 交易时间戳
        action: 交易动作（"BUY" 或 "SELL"）
        quantity: 交易数量
        price: 成交价格
        commission: 手续费
        cash_after: 交易后现金余额
        position_after: 交易后持仓数量
    """
    timestamp: str
    action: str  # "BUY" 或 "SELL"
    quantity: int
    price: float
    commission: float
    cash_after: float
    position_after: int
    
    def to_json(self) -> str:
        """序列化为 JSON 字符串。
        
        Returns:
            JSON 格式的字符串
        """
        return json.dumps({
            "timestamp": self.timestamp,
            "action": self.action,
            "quantity": self.quantity,
            "price": self.price,
            "commission": self.commission,
            "cash_after": self.cash_after,
            "position_after": self.position_after,
        }, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, json_str: str) -> "TradeRecord":
        """从 JSON 字符串反序列化。
        
        Args:
            json_str: JSON 格式的字符串
            
        Returns:
            TradeRecord 实例
        """
        data = json.loads(json_str)
        return cls(
            timestamp=data["timestamp"],
            action=data["action"],
            quantity=data["quantity"],
            price=data["price"],
            commission=data["commission"],
            cash_after=data["cash_after"],
            position_after=data["position_after"],
        )
