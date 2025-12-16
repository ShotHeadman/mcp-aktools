"""策略基类模块。

该模块定义策略抽象基类，用户通过继承实现具体的交易逻辑。
包括：
- Strategy: 策略抽象基类
- 订单排队机制
- 指标和信号获取接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional

import pandas as pd

from ..models.trade import OrderType, Position, PriceType, TradeRecord

if TYPE_CHECKING:
    from .config import BacktestConfig


@dataclass
class PendingOrder:
    """待执行订单数据类。
    
    存储策略发出的订单信息，等待引擎在下一个K线执行。
    
    Attributes:
        action: 订单动作，"BUY" 或 "SELL"
        price_type: 价格类型（开盘价、收盘价等）
        order_type: 订单类型（手数、现金比例、持仓比例）
        value: 订单数值（具体含义取决于 order_type）
    """
    action: str  # "BUY" 或 "SELL"
    price_type: PriceType
    order_type: OrderType
    value: float


class Strategy(ABC):
    """策略抽象基类。
    
    用户通过继承此类并实现 on_bar() 方法来定义交易逻辑。
    策略可以通过 buy() 和 sell() 方法发出订单，
    通过 get_indicator() 和 get_signal() 方法获取技术指标和信号。
    
    Attributes:
        config: 回测配置对象
        position: 当前持仓信息
        cash: 当前可用现金
        trades: 已执行的交易记录列表
        pending_orders: 待执行的订单队列
        _current_bar: 当前K线数据（由引擎设置）
        _current_index: 当前K线索引（由引擎设置）
        _data: 完整K线数据（由引擎设置）
        _signal_calculator: 信号计算器（由引擎设置）
    
    Example:
        >>> class MyStrategy(Strategy):
        ...     def on_bar(self, bar: pd.Series, history: pd.DataFrame) -> None:
        ...         # 获取 KDJ 指标
        ...         kdj_j = self.get_indicator('KDJ.J')
        ...         if kdj_j is not None and kdj_j < 20:
        ...             # J 值低于 20 时买入
        ...             self.buy(PriceType.OPEN, OrderType.CASH_PERCENT, 50)
        ...         
        ...         # 检查 MACD 死叉信号
        ...         if self.get_signal('macd', 'death_cross'):
        ...             # 死叉时卖出全部持仓
        ...             self.sell(PriceType.OPEN, OrderType.POSITION_PERCENT, 100)
    """
    
    def __init__(self, config: "BacktestConfig"):
        """初始化策略实例。
        
        Args:
            config: 回测配置对象，包含初始资金等参数
        """
        self.config = config
        self.position = Position()
        self.cash = config.initial_capital
        self.trades: List[TradeRecord] = []
        self.pending_orders: List[PendingOrder] = []
        
        # 以下属性由引擎在运行时设置
        self._current_bar: Optional[pd.Series] = None
        self._current_index: int = 0
        self._data: Optional[pd.DataFrame] = None
        self._signal_calculator: Optional[Any] = None  # SignalCalculator 类型
    
    @abstractmethod
    def on_bar(self, bar: pd.Series, history: pd.DataFrame) -> None:
        """处理每根K线的回调方法。
        
        用户必须实现此方法来定义交易逻辑。
        该方法在每根K线到来时被调用，可以访问当前K线数据和历史数据。
        
        Args:
            bar: 当前K线数据，包含 open, high, low, close, volume 等字段
            history: 历史K线数据（包含当前K线），用于计算指标
        
        Note:
            在此方法中调用 buy() 或 sell() 发出的订单会被加入队列，
            在下一根K线开盘时执行（如果使用开盘价）或当前K线收盘时执行。
        """
        pass
    
    def buy(
        self,
        price_type: PriceType,
        order_type: OrderType,
        value: float
    ) -> bool:
        """发出买入订单。
        
        订单会被加入待执行队列，由引擎在适当时机执行。
        
        Args:
            price_type: 执行价格类型（OPEN/CLOSE/HIGH/LOW）
            order_type: 订单类型
                - LOTS: value 表示手数（1手=100股）
                - CASH_PERCENT: value 表示使用现金的百分比（0-100）
                - POSITION_PERCENT: 买入时不适用，会被忽略
            value: 订单数值，具体含义取决于 order_type
        
        Returns:
            bool: 订单是否成功加入队列（始终返回 True，实际执行结果由引擎决定）
        
        Example:
            >>> # 以开盘价买入 10 手
            >>> self.buy(PriceType.OPEN, OrderType.LOTS, 10)
            >>> # 以收盘价使用 50% 现金买入
            >>> self.buy(PriceType.CLOSE, OrderType.CASH_PERCENT, 50)
        """
        order = PendingOrder(
            action="BUY",
            price_type=price_type,
            order_type=order_type,
            value=value
        )
        self.pending_orders.append(order)
        return True
    
    def sell(
        self,
        price_type: PriceType,
        order_type: OrderType,
        value: float
    ) -> bool:
        """发出卖出订单。
        
        订单会被加入待执行队列，由引擎在适当时机执行。
        
        Args:
            price_type: 执行价格类型（OPEN/CLOSE/HIGH/LOW）
            order_type: 订单类型
                - LOTS: value 表示手数（1手=100股）
                - CASH_PERCENT: 卖出时不适用，会被忽略
                - POSITION_PERCENT: value 表示卖出持仓的百分比（0-100）
            value: 订单数值，具体含义取决于 order_type
        
        Returns:
            bool: 订单是否成功加入队列（始终返回 True，实际执行结果由引擎决定）
        
        Example:
            >>> # 以开盘价卖出 5 手
            >>> self.sell(PriceType.OPEN, OrderType.LOTS, 5)
            >>> # 以收盘价卖出 100% 持仓
            >>> self.sell(PriceType.CLOSE, OrderType.POSITION_PERCENT, 100)
        """
        order = PendingOrder(
            action="SELL",
            price_type=price_type,
            order_type=order_type,
            value=value
        )
        self.pending_orders.append(order)
        return True
    
    def get_indicator(self, name: str) -> Optional[float]:
        """获取指标当前值。
        
        从当前K线数据中获取指定技术指标的值。
        
        Args:
            name: 指标名称，支持以下格式：
                - 简单名称: "DIF", "DEA", "MACD", "RSI", "K", "D", "J"
                - 点分名称: "KDJ.K", "KDJ.D", "KDJ.J", "BOLL.UPPER", "BOLL.MID", "BOLL.LOWER"
        
        Returns:
            Optional[float]: 指标值，如果指标不存在或数据不可用则返回 None
        
        Example:
            >>> kdj_j = self.get_indicator('KDJ.J')
            >>> if kdj_j is not None and kdj_j < 20:
            ...     print("KDJ J值超卖")
        """
        if self._current_bar is None:
            return None
        
        # 处理点分名称格式（如 KDJ.J -> J）
        indicator_name = name
        if "." in name:
            parts = name.split(".")
            # 对于 KDJ.J 这样的格式，取第二部分
            indicator_name = parts[-1]
        
        # 尝试从当前K线数据中获取指标值
        if indicator_name in self._current_bar.index:
            value = self._current_bar[indicator_name]
            # 检查是否为有效数值
            if pd.notna(value):
                return float(value)
        
        # 尝试使用原始名称
        if name in self._current_bar.index:
            value = self._current_bar[name]
            if pd.notna(value):
                return float(value)
        
        return None
    
    def get_signal(self, indicator: str, signal_type: str) -> bool:
        """获取技术信号。
        
        评估指定指标的信号条件，返回是否触发。
        
        Args:
            indicator: 指标名称，支持：
                - "macd": MACD 指标
                - "kdj": KDJ 指标
                - "rsi": RSI 指标
                - "boll": 布林带指标
            signal_type: 信号类型，支持：
                - "golden_cross": 金叉（DIF上穿DEA 或 K上穿D）
                - "death_cross": 死叉（DIF下穿DEA 或 K下穿D）
                - "death_cross_strong": 强死叉（死叉且在零轴下方）
                - "overbought": 超买（RSI > 70）
                - "oversold": 超卖（RSI < 30）
                - "upper_break": 突破上轨（价格突破布林带上轨）
                - "lower_break": 突破下轨（价格突破布林带下轨）
        
        Returns:
            bool: 信号是否触发，如果信号计算器不可用则返回 False
        
        Example:
            >>> if self.get_signal('macd', 'golden_cross'):
            ...     print("MACD 金叉信号")
            >>> if self.get_signal('kdj', 'death_cross'):
            ...     print("KDJ 死叉信号")
        """
        if self._signal_calculator is None:
            return False
        
        # 将指标名称和信号类型转换为小写以便匹配
        indicator = indicator.lower()
        signal_type = signal_type.lower()
        
        try:
            # 根据指标类型调用相应的信号计算方法
            if indicator == "macd":
                if signal_type == "golden_cross":
                    return self._signal_calculator.macd_golden_cross(self._current_index)
                elif signal_type == "death_cross":
                    return self._signal_calculator.macd_death_cross(self._current_index)
                elif signal_type == "death_cross_strong":
                    # 强死叉：死叉且 DIF 在零轴下方
                    if self._signal_calculator.macd_death_cross(self._current_index):
                        dif = self.get_indicator("DIF")
                        return dif is not None and dif < 0
                    return False
            
            elif indicator == "kdj":
                if signal_type == "golden_cross":
                    return self._signal_calculator.kdj_golden_cross(self._current_index)
                elif signal_type == "death_cross":
                    return self._signal_calculator.kdj_death_cross(self._current_index)
            
            elif indicator == "rsi":
                if signal_type == "overbought":
                    return self._signal_calculator.rsi_overbought(self._current_index)
                elif signal_type == "oversold":
                    return self._signal_calculator.rsi_oversold(self._current_index)
            
            elif indicator == "boll":
                if signal_type == "upper_break":
                    return self._signal_calculator.boll_upper_break(self._current_index)
                elif signal_type == "lower_break":
                    return self._signal_calculator.boll_lower_break(self._current_index)
        
        except (AttributeError, IndexError, KeyError):
            # 信号计算器方法不存在或索引越界
            pass
        
        return False
    
    def clear_pending_orders(self) -> None:
        """清空待执行订单队列。
        
        由引擎在处理完订单后调用。
        """
        self.pending_orders.clear()
    
    def get_pending_orders(self) -> List[PendingOrder]:
        """获取待执行订单列表。
        
        Returns:
            List[PendingOrder]: 待执行订单列表的副本
        """
        return list(self.pending_orders)
    
    @property
    def has_position(self) -> bool:
        """检查是否持有仓位。
        
        Returns:
            bool: 如果持仓数量大于0则返回 True
        """
        return self.position.quantity > 0
    
    @property
    def position_value(self) -> float:
        """计算当前持仓市值。
        
        使用当前K线的收盘价计算持仓市值。
        
        Returns:
            float: 持仓市值，如果无持仓或无数据则返回 0
        """
        if not self.has_position or self._current_bar is None:
            return 0.0
        
        close_price = self._current_bar.get("close", 0)
        return self.position.quantity * close_price
    
    @property
    def total_equity(self) -> float:
        """计算总权益（现金 + 持仓市值）。
        
        Returns:
            float: 总权益
        """
        return self.cash + self.position_value
    
    @property
    def position_pnl(self) -> float:
        """计算当前持仓盈亏。
        
        Returns:
            float: 持仓盈亏金额，正数为盈利，负数为亏损
        """
        if not self.has_position or self._current_bar is None:
            return 0.0
        
        close_price = self._current_bar.get("close", 0)
        return (close_price - self.position.avg_cost) * self.position.quantity
    
    @property
    def position_pnl_ratio(self) -> float:
        """计算当前持仓盈亏比例。
        
        Returns:
            float: 盈亏比例，如 0.05 表示盈利 5%，-0.03 表示亏损 3%
        """
        if not self.has_position or self.position.avg_cost == 0:
            return 0.0
        
        if self._current_bar is None:
            return 0.0
        
        close_price = self._current_bar.get("close", 0)
        return (close_price - self.position.avg_cost) / self.position.avg_cost
