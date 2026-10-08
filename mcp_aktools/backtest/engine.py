"""回测引擎模块。

该模块实现回测引擎核心类，负责：
- 加载K线数据
- 执行策略
- 处理订单
- 计算绩效指标
"""

import logging
import math
from collections import deque
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd

from ..clients.akshare import AkShareClient
from ..config import get_settings
from ..exceptions import BacktestError
from ..models.trade import OrderType, PriceType, TradeRecord
from ..utils.indicators import add_technical_indicators
from .config import BacktestConfig
from .signals import SignalCalculator
from .strategy import PendingOrder, Strategy

logger = logging.getLogger(__name__)


class BacktestEngine:
    """回测引擎核心类。
    
    负责加载K线数据、执行策略、处理订单和计算绩效指标。
    
    Attributes:
        config: 回测配置对象
        data: K线数据 DataFrame
        signal_calculator: 信号计算器
        _akshare_client: AkShare 客户端
    """
    
    def __init__(self, config: BacktestConfig):
        """初始化回测引擎。
        
        Args:
            config: 回测配置对象
        """
        self.config = config
        self.data: Optional[pd.DataFrame] = None
        self.full_data: Optional[pd.DataFrame] = None  # 完整数据（包含预热期）
        self.signal_calculator: Optional[SignalCalculator] = None
        self._akshare_client: Optional[AkShareClient] = None
        
        # 熔断状态
        self._circuit_breaker_triggered = False
        self._peak_equity = config.initial_capital
        
        logger.info(
            f"回测引擎初始化: 股票={config.stock_code}, "
            f"区间={config.start_date}~{config.end_date}, "
            f"初始资金={config.initial_capital:,.2f}"
        )
    
    def _get_akshare_client(self) -> AkShareClient:
        """获取 AkShare 客户端（延迟初始化）。
        
        Returns:
            AkShareClient 实例
        """
        if self._akshare_client is None:
            self._akshare_client = AkShareClient(get_settings())
        return self._akshare_client
    
    def load_data(self) -> None:
        """加载K线数据并计算技术指标。
        
        使用 akshare 获取股票历史数据，并计算 MACD、KDJ、RSI、BOLL 等技术指标。
        
        为了确保技术指标能够正确计算，会在回测开始日期之前额外加载历史数据：
        - KDJ 需要至少 9 根 K 线
        - MACD 需要至少 26 根 K 线
        - 为保险起见，额外加载 60 根 K 线（约 90 个自然日）
        
        Raises:
            BacktestError: 数据加载失败时抛出
        """
        logger.info(f"开始加载数据: {self.config.stock_code}")
        
        try:
            # 解析股票代码
            market, symbol = self._parse_stock_code(self.config.stock_code)
            
            # 获取 AkShare 客户端
            client = self._get_akshare_client()
            
            # 计算需要额外加载的历史数据
            # MACD需要26根K线，KDJ需要9+3根，BOLL需要20根
            # 为了保险起见，额外加载60根K线
            warmup_bars = 60
            start_date_obj = datetime.strptime(self.config.start_date, "%Y-%m-%d")
            
            # 根据K线周期计算需要往前推多少天
            # 目标：获取至少60根K线用于技术指标计算
            # 日K: 60根 ≈ 90天（考虑节假日，约1.5倍）
            # 周K: 60根 ≈ 450天（约1.1倍，考虑节假日）
            # 月K: 60根 ≈ 1900天（约1.05倍）
            # 分钟K: 根据每天K线数量计算，加50%buffer
            freq = self.config.frequency
            if freq == "d":
                warmup_days = int(warmup_bars * 1.5)  # 90天
            elif freq == "w":
                warmup_days = int(warmup_bars * 7.5)  # 450天
            elif freq == "m":
                warmup_days = int(warmup_bars * 32)  # 1920天
            else:
                # 分钟K线（1/5/15/30/60分钟）
                # A股每天约4小时交易 = 240分钟
                minutes_per_bar = int(freq) if freq.isdigit() else 5
                bars_per_day = max(1, 240 // minutes_per_bar)
                warmup_days = int((warmup_bars / bars_per_day) * 1.5) + 5
            
            from datetime import timedelta
            extended_start_date = (start_date_obj - timedelta(days=warmup_days)).strftime("%Y%m%d")
            
            logger.info(
                f"扩展数据加载范围: 原始开始日期={self.config.start_date}, "
                f"扩展开始日期={extended_start_date}, 预热K线数={warmup_bars}"
            )
            
            # 分钟回测使用分钟接口；所有请求都限定到回测结束日。
            if freq in {"1", "5", "15", "30", "60"}:
                df = client.stock_zh_a_hist_min(
                    symbol=symbol,
                    market=market,
                    period=freq,
                    start_date=extended_start_date,
                    end_date=self.config.end_date,
                    ttl=300,
                )
            else:
                df = client.stock_zh_a_hist(
                    symbol=symbol,
                    market=market,
                    period={"d": "daily", "w": "weekly", "m": "monthly"}[freq],
                    start_date=extended_start_date,
                    end_date=self.config.end_date.replace("-", ""),
                    ttl=3600,
                )
            
            if df is None or df.empty:
                raise BacktestError(
                    "data_loading",
                    f"无法获取股票 {self.config.stock_code} 的数据"
                )
            
            # 标准化列名
            df = self._standardize_columns(df)
            
            # 先计算技术指标（使用完整的扩展数据，确保指标计算准确）
            # 注意：必须在过滤日期范围之前计算，否则 MACD/KDJ 等指标会因数据不足而不准确
            self._calculate_indicators(df)
            
            # 保存完整数据（用于实时指标计算）
            self.full_data = df.copy()
            
            # 过滤日期范围（在计算指标之后）
            df = self._filter_date_range(df)
            
            if df.empty:
                raise BacktestError(
                    "data_loading",
                    f"指定日期范围内无数据: {self.config.start_date} ~ {self.config.end_date}"
                )
            
            # 重置索引
            df = df.reset_index(drop=True)
            
            self.data = df
            
            # 初始化信号计算器
            self.signal_calculator = SignalCalculator(self.data)
            
            logger.info(
                f"数据加载完成: {len(self.data)} 条K线, "
                f"日期范围: {self.data['date'].iloc[0]} ~ {self.data['date'].iloc[-1]}"
            )
            
        except BacktestError:
            raise
        except Exception as e:
            logger.error(f"数据加载失败: {e}", exc_info=True)
            raise BacktestError("data_loading", str(e)) from e
    
    def _parse_stock_code(self, stock_code: str) -> tuple:
        """解析股票代码。
        
        Args:
            stock_code: 股票代码，格式为 sh.XXXXXX 或 sz.XXXXXX
            
        Returns:
            (market, symbol) 元组
        """
        parts = stock_code.split(".")
        market = parts[0]  # sh 或 sz
        symbol = parts[1]  # 6位数字代码
        return market, symbol
    
    def _standardize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 DataFrame 列名。
        
        将中文列名转换为英文小写列名，便于后续处理。
        
        Args:
            df: 原始 DataFrame
            
        Returns:
            标准化后的 DataFrame
        """
        # 列名映射
        column_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
            "换手率": "turnover",
        }
        
        # 重命名列
        df = df.rename(columns=column_map)
        
        # 确保日期列存在
        if "date" not in df.columns:
            # 尝试其他可能的日期列名
            for col in ["时间", "day", "datetime", "交易日期", "trade_date"]:
                if col in df.columns:
                    df = df.rename(columns={col: "date"})
                    break
        
        # 转换日期列为 datetime 类型
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
        
        return df
    
    def _filter_date_range(self, df: pd.DataFrame) -> pd.DataFrame:
        """过滤日期范围。
        
        Args:
            df: 原始 DataFrame
            
        Returns:
            过滤后的 DataFrame
        """
        if "date" not in df.columns:
            return df
        
        start_date = pd.to_datetime(self.config.start_date)
        end_date = pd.to_datetime(self.config.end_date) + pd.Timedelta(days=1)
        
        # 结束日期包含全天，保留结束日的分钟 K 线。
        mask = (df["date"] >= start_date) & (df["date"] < end_date)
        return df[mask].copy()
    
    def _calculate_indicators(self, df: pd.DataFrame) -> None:
        """计算技术指标。
        
        计算 MACD、KDJ、RSI、BOLL 等技术指标。
        
        Args:
            df: K线数据 DataFrame（原地修改）
        """
        logger.debug("开始计算技术指标")
        
        # 确保必要的列存在
        required_cols = ["close", "high", "low"]
        for col in required_cols:
            if col not in df.columns:
                raise BacktestError(
                    "data_loading",
                    f"数据缺少必要列: {col}"
                )
        
        # 使用工具函数计算技术指标
        add_technical_indicators(
            df,
            close=df["close"],
            low=df["low"],
            high=df["high"],
        )
        
        # 重命名指标列以便信号计算器识别
        # KDJ 指标
        if "KDJ.K" in df.columns:
            df["K"] = df["KDJ.K"]
        if "KDJ.D" in df.columns:
            df["D"] = df["KDJ.D"]
        if "KDJ.J" in df.columns:
            df["J"] = df["KDJ.J"]
        
        # BOLL 指标
        if "BOLL.U" in df.columns:
            df["BBU"] = df["BOLL.U"]
        if "BOLL.M" in df.columns:
            df["BBM"] = df["BOLL.M"]
        if "BOLL.L" in df.columns:
            df["BBL"] = df["BOLL.L"]
        
        logger.debug("技术指标计算完成")

    def _process_pending_orders(
        self,
        strategy: Strategy,
        bar: pd.Series,
        bar_index: int
    ) -> List[TradeRecord]:
        """处理待执行订单。
        
        按照配置的信号优先级处理策略发出的订单。
        
        Args:
            strategy: 策略实例
            bar: 当前K线数据
            bar_index: 当前K线索引
            
        Returns:
            执行的交易记录列表
        """
        executed_trades: List[TradeRecord] = []
        
        # 获取待执行订单
        pending_orders = strategy.get_pending_orders()
        
        if not pending_orders:
            return executed_trades
        
        # 按信号优先级排序订单
        if self.config.signal_priority == "buy_first":
            # 买入优先：先执行买入订单，再执行卖出订单
            pending_orders = sorted(
                pending_orders,
                key=lambda o: 0 if o.action == "BUY" else 1
            )
        elif self.config.signal_priority == "sell_first":
            # 卖出优先：先执行卖出订单，再执行买入订单
            pending_orders = sorted(
                pending_orders,
                key=lambda o: 0 if o.action == "SELL" else 1
            )
        elif self.config.signal_priority == "none":
            # 同时触发买卖信号时不执行任何操作
            has_buy = any(o.action == "BUY" for o in pending_orders)
            has_sell = any(o.action == "SELL" for o in pending_orders)
            if has_buy and has_sell:
                logger.debug("同时触发买卖信号，根据 signal_priority=none 不执行任何操作")
                strategy.clear_pending_orders()
                return executed_trades
        
        # 执行订单
        for order in pending_orders:
            trade = self._execute_order(strategy, order, bar, bar_index)
            if trade is not None:
                executed_trades.append(trade)
        
        # 清空待执行订单
        strategy.clear_pending_orders()
        
        return executed_trades
    
    def _execute_order(
        self,
        strategy: Strategy,
        order: PendingOrder,
        bar: pd.Series,
        bar_index: int
    ) -> Optional[TradeRecord]:
        """执行单个订单。
        
        根据订单类型计算数量，应用滑点，执行买入或卖出。
        
        Args:
            strategy: 策略实例
            order: 待执行订单
            bar: 当前K线数据
            bar_index: 当前K线索引
            
        Returns:
            交易记录，如果订单被拒绝则返回 None
        """
        # 获取执行价格
        price = self._get_execution_price(order.price_type, bar)
        
        if price <= 0:
            logger.warning(f"无效的执行价格: {price}")
            return None
        
        # 应用滑点
        slippage = self.config.get_random_slippage()
        if order.action == "BUY":
            # 买入时价格上滑
            price = price * (1 + slippage)
        else:
            # 卖出时价格下滑
            price = price * (1 - slippage)
        
        # 根据订单动作执行
        if order.action == "BUY":
            return self._execute_buy(strategy, order, price, bar, bar_index)
        else:
            return self._execute_sell(strategy, order, price, bar, bar_index)
    
    def _get_execution_price(self, price_type: PriceType, bar: pd.Series) -> float:
        """获取执行价格。
        
        Args:
            price_type: 价格类型
            bar: K线数据
            
        Returns:
            执行价格
        """
        price_map = {
            PriceType.OPEN: "open",
            PriceType.CLOSE: "close",
            PriceType.HIGH: "high",
            PriceType.LOW: "low",
        }
        
        col_name = price_map.get(price_type, "close")
        return float(bar.get(col_name, 0))
    
    def _calculate_quantity(
        self,
        order: PendingOrder,
        price: float,
        cash: float,
        position_quantity: int
    ) -> int:
        """计算订单数量。
        
        根据订单类型计算实际交易数量，并向下取整到100股（1手）。
        
        Args:
            order: 订单信息
            price: 执行价格
            cash: 可用现金
            position_quantity: 当前持仓数量
            
        Returns:
            交易数量（股），已取整到100的倍数
        """
        quantity = 0
        
        if order.action == "BUY":
            if order.order_type == OrderType.LOTS:
                # 手数：value 表示手数
                quantity = int(order.value) * 100
            elif order.order_type == OrderType.CASH_PERCENT:
                # 现金比例：value 表示使用现金的百分比
                available_cash = cash * (order.value / 100)
                quantity = int(available_cash / price)
            else:
                # POSITION_PERCENT 对买入不适用
                logger.warning("买入订单不支持 POSITION_PERCENT 类型")
                return 0
        else:  # SELL
            if order.order_type == OrderType.LOTS:
                # 手数：value 表示手数
                quantity = int(order.value) * 100
            elif order.order_type == OrderType.POSITION_PERCENT:
                # 持仓比例：value 表示卖出持仓的百分比
                quantity = int(position_quantity * (order.value / 100))
            else:
                # CASH_PERCENT 对卖出不适用
                logger.warning("卖出订单不支持 CASH_PERCENT 类型")
                return 0
        
        # 向下取整到100股（1手）
        quantity = (quantity // 100) * 100
        
        return quantity
    
    def _calculate_commission(self, amount: float) -> float:
        """计算手续费。
        
        Args:
            amount: 交易金额
            
        Returns:
            手续费
        """
        commission = amount * self.config.commission_rate
        # 应用最低手续费
        return max(commission, self.config.min_commission)
    
    def _execute_buy(
        self,
        strategy: Strategy,
        order: PendingOrder,
        price: float,
        bar: pd.Series,
        bar_index: int
    ) -> Optional[TradeRecord]:
        """执行买入订单。
        
        Args:
            strategy: 策略实例
            order: 订单信息
            price: 执行价格（已含滑点）
            bar: K线数据
            bar_index: K线索引
            
        Returns:
            交易记录，如果订单被拒绝则返回 None
        """
        # 检查金字塔加仓限制
        if strategy.has_position:
            if not self.config.enable_pyramiding:
                logger.debug("金字塔加仓未启用，拒绝买入订单")
                return None
            if strategy.position.layers >= self.config.max_pyramid_layers:
                logger.debug(
                    f"已达到最大加仓层数 ({self.config.max_pyramid_layers})，拒绝买入订单"
                )
                return None
        
        # 计算买入数量
        quantity = self._calculate_quantity(
            order, price, strategy.cash, strategy.position.quantity
        )
        
        if quantity <= 0:
            logger.debug("计算的买入数量为0，拒绝订单")
            return None
        
        # 应用金字塔缩放因子
        if strategy.has_position and self.config.enable_pyramiding:
            scale = self.config.pyramid_scale_factor ** strategy.position.layers
            quantity = int(quantity * scale)
            quantity = (quantity // 100) * 100  # 重新取整
            if quantity <= 0:
                logger.debug("金字塔缩放后数量为0，拒绝订单")
                return None
        
        # 计算总成本
        amount = quantity * price
        commission = self._calculate_commission(amount)
        total_cost = amount + commission
        
        # 检查资金是否充足
        if total_cost > strategy.cash:
            logger.warning(
                f"资金不足，拒绝买入订单: 需要 {total_cost:,.2f}, 可用 {strategy.cash:,.2f}"
            )
            return None
        
        # 执行买入
        strategy.cash -= total_cost
        strategy.position.update_on_buy(quantity, price)
        
        # 获取时间戳
        timestamp = self._get_timestamp(bar)
        
        # 创建交易记录
        trade = TradeRecord(
            timestamp=timestamp,
            action="BUY",
            quantity=quantity,
            price=price,
            commission=commission,
            cash_after=strategy.cash,
            position_after=strategy.position.quantity,
        )
        
        strategy.trades.append(trade)
        
        logger.info(
            f"买入成交: {quantity}股 @ {price:.2f}, "
            f"手续费={commission:.2f}, 剩余现金={strategy.cash:,.2f}"
        )
        
        return trade
    
    def _execute_sell(
        self,
        strategy: Strategy,
        order: PendingOrder,
        price: float,
        bar: pd.Series,
        bar_index: int
    ) -> Optional[TradeRecord]:
        """执行卖出订单。
        
        Args:
            strategy: 策略实例
            order: 订单信息
            price: 执行价格（已含滑点）
            bar: K线数据
            bar_index: K线索引
            
        Returns:
            交易记录，如果订单被拒绝则返回 None
        """
        # 检查是否有持仓
        if not strategy.has_position:
            logger.debug("无持仓，拒绝卖出订单")
            return None
        
        # 计算卖出数量
        quantity = self._calculate_quantity(
            order, price, strategy.cash, strategy.position.quantity
        )
        
        if quantity <= 0:
            logger.debug("计算的卖出数量为0，拒绝订单")
            return None
        
        # 检查持仓是否充足
        if quantity > strategy.position.quantity:
            logger.warning(
                f"持仓不足，拒绝卖出订单: 需要 {quantity}股, 持有 {strategy.position.quantity}股"
            )
            return None
        
        # 计算收入
        amount = quantity * price
        commission = self._calculate_commission(amount)
        net_revenue = amount - commission
        
        # 执行卖出
        strategy.cash += net_revenue
        strategy.position.update_on_sell(quantity)
        
        # 获取时间戳
        timestamp = self._get_timestamp(bar)
        
        # 创建交易记录
        trade = TradeRecord(
            timestamp=timestamp,
            action="SELL",
            quantity=quantity,
            price=price,
            commission=commission,
            cash_after=strategy.cash,
            position_after=strategy.position.quantity,
        )
        
        strategy.trades.append(trade)
        
        logger.info(
            f"卖出成交: {quantity}股 @ {price:.2f}, "
            f"手续费={commission:.2f}, 剩余现金={strategy.cash:,.2f}"
        )
        
        return trade
    
    def _get_timestamp(self, bar: pd.Series) -> str:
        """获取K线时间戳字符串。

        Args:
            bar: K线数据

        Returns:
            时间戳字符串
        """
        date_val = bar.get("date")
        if date_val is not None:
            if isinstance(date_val, pd.Timestamp):
                return date_val.strftime("%Y-%m-%d")
            return str(date_val)
        return datetime.now().strftime("%Y-%m-%d")

    def _get_risk_reference_price(self, bar: pd.Series) -> float:
        """获取止损止盈的风控参考价。

        实时（开盘价）模式下，风控决策发生在开盘时，只能使用开盘价，
        避免用当根收盘价造成未来函数；标准（收盘价）模式使用收盘价。

        Args:
            bar: 当前K线数据

        Returns:
            风控参考价
        """
        if self.config.realtime_indicator:
            open_price = float(bar.get("open", 0))
            if open_price > 0:
                return open_price
        return float(bar.get("close", 0))

    def _check_stop_loss_take_profit(
        self,
        strategy: Strategy,
        bar: pd.Series,
        bar_index: int
    ) -> Optional[TradeRecord]:
        """检查止损止盈条件并自动平仓。

        当持仓盈亏比例达到止损或止盈阈值时，自动平仓。
        盈亏比例按风控参考价计算（实时模式开盘价 / 标准模式收盘价）。

        Args:
            strategy: 策略实例
            bar: 当前K线数据
            bar_index: 当前K线索引

        Returns:
            如果触发止损止盈则返回交易记录，否则返回 None
        """
        # 检查是否有持仓
        if not strategy.has_position or strategy.position.avg_cost <= 0:
            return None

        # 获取风控参考价
        ref_price = self._get_risk_reference_price(bar)
        if ref_price <= 0:
            return None

        # 按风控参考价计算持仓盈亏比例
        pnl_ratio = (ref_price - strategy.position.avg_cost) / strategy.position.avg_cost

        # 检查止损条件
        if self.config.stop_loss is not None:
            if pnl_ratio <= -self.config.stop_loss:
                logger.info(
                    f"触发止损: 当前亏损 {pnl_ratio:.2%}, "
                    f"止损阈值 -{self.config.stop_loss:.2%}"
                )
                return self._close_position(
                    strategy, bar, bar_index, reason="止损"
                )

        # 检查止盈条件
        if self.config.take_profit is not None:
            if pnl_ratio >= self.config.take_profit:
                logger.info(
                    f"触发止盈: 当前盈利 {pnl_ratio:.2%}, "
                    f"止盈阈值 +{self.config.take_profit:.2%}"
                )
                return self._close_position(
                    strategy, bar, bar_index, reason="止盈"
                )

        return None

    def _close_position(
        self,
        strategy: Strategy,
        bar: pd.Series,
        bar_index: int,
        reason: str = ""
    ) -> Optional[TradeRecord]:
        """平仓操作。

        以当前收盘价卖出全部持仓。

        Args:
            strategy: 策略实例
            bar: 当前K线数据
            bar_index: 当前K线索引
            reason: 平仓原因（用于日志）

        Returns:
            交易记录，如果平仓失败则返回 None
        """
        if not strategy.has_position:
            return None

        # 获取平仓参考价（实时模式用开盘价，标准模式用收盘价）
        price = self._get_risk_reference_price(bar)
        if price <= 0:
            logger.warning("无效的平仓参考价，无法平仓")
            return None

        # 应用滑点（卖出时价格下滑）
        slippage = self.config.get_random_slippage()
        price = price * (1 - slippage)

        # 获取全部持仓数量
        quantity = strategy.position.quantity

        # 计算收入
        amount = quantity * price
        commission = self._calculate_commission(amount)
        net_revenue = amount - commission

        # 执行卖出
        strategy.cash += net_revenue
        strategy.position.update_on_sell(quantity)

        # 获取时间戳
        timestamp = self._get_timestamp(bar)

        # 创建交易记录
        trade = TradeRecord(
            timestamp=timestamp,
            action="SELL",
            quantity=quantity,
            price=price,
            commission=commission,
            cash_after=strategy.cash,
            position_after=strategy.position.quantity,
        )

        strategy.trades.append(trade)

        reason_str = f"({reason})" if reason else ""
        logger.info(
            f"平仓{reason_str}: {quantity}股 @ {price:.2f}, "
            f"手续费={commission:.2f}, 剩余现金={strategy.cash:,.2f}"
        )

        return trade

    def _check_circuit_breaker(self, strategy: Strategy) -> bool:
        """检查熔断机制。

        当组合回撤达到最大回撤限制时，触发熔断并停止所有交易。

        Args:
            strategy: 策略实例

        Returns:
            如果熔断已触发则返回 True，否则返回 False
        """
        # 如果熔断已触发，直接返回
        if self._circuit_breaker_triggered:
            return True

        # 如果未配置最大回撤限制，不检查
        if self.config.max_drawdown_limit is None:
            return False

        # 获取当前总权益
        current_equity = strategy.total_equity

        # 更新峰值权益
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        # 计算当前回撤
        if self._peak_equity > 0:
            current_drawdown = (self._peak_equity - current_equity) / self._peak_equity
        else:
            current_drawdown = 0.0

        # 检查是否触发熔断
        if current_drawdown >= self.config.max_drawdown_limit:
            self._circuit_breaker_triggered = True
            logger.warning(
                f"触发熔断机制: 当前回撤 {current_drawdown:.2%}, "
                f"熔断阈值 {self.config.max_drawdown_limit:.2%}, "
                f"峰值权益 {self._peak_equity:,.2f}, "
                f"当前权益 {current_equity:,.2f}"
            )
            return True

        return False

    def reset_circuit_breaker(self) -> None:
        """重置熔断状态。

        用于在新的回测开始前重置熔断状态。
        """
        self._circuit_breaker_triggered = False
        self._peak_equity = self.config.initial_capital

    def run(self, strategy_class: type) -> "BacktestResult":
        """执行回测。

        按时间顺序遍历K线数据，执行策略逻辑，处理订单，跟踪权益曲线。

        Args:
            strategy_class: 策略类（必须继承自 Strategy）

        Returns:
            BacktestResult: 回测结果对象，包含交易记录和绩效指标

        Raises:
            BacktestError: 回测执行失败时抛出
        """
        from .results import BacktestResult

        # 确保数据已加载
        if self.data is None or self.data.empty:
            self.load_data()

        # 重置熔断状态
        self.reset_circuit_breaker()

        # 重置滑点随机数发生器，确保相同 random_seed 下回测结果可复现
        self.config.reset_rng()

        # 创建策略实例
        strategy = strategy_class(self.config)

        # 设置策略的数据引用
        strategy._data = self.data
        strategy._signal_calculator = self.signal_calculator
        
        # 如果启用实时指标模式，初始化实时指标计算器
        if self.config.realtime_indicator:
            from .realtime_indicators import RealtimeIndicatorCalculator
            # 使用完整数据（包含预热期）进行实时指标计算
            strategy._realtime_calculator = RealtimeIndicatorCalculator(self.full_data)
            
            # 创建索引映射：self.data的索引 -> self.full_data的索引
            # 一次性构建 日期→full_data索引 映射表，避免每根K线全表扫描（O(n²)→O(n)）
            # K线日期唯一，dict 直接以日期为键
            date_to_full_idx = dict(zip(self.full_data['date'], self.full_data.index))
            self._index_map = {
                i: date_to_full_idx[d]
                for i, d in enumerate(self.data['date'])
                if d in date_to_full_idx
            }
            
            logger.info("已启用实时指标模式（开盘价模式）")

        # 权益曲线列表
        equity_curve: List[float] = []

        logger.info(
            f"开始回测: {len(self.data)} 根K线, "
            f"初始资金={self.config.initial_capital:,.2f}"
        )

        # 按时间顺序遍历K线
        for i in range(len(self.data)):
            bar = self.data.iloc[i]

            # 更新策略的当前K线引用
            strategy._current_bar = bar
            strategy._current_index = i
            
            # 如果启用实时模式，设置映射后的完整数据索引
            if self.config.realtime_indicator and hasattr(self, '_index_map'):
                strategy._full_data_index = self._index_map.get(i, i)

            # 获取历史数据（包含当前K线）
            history = self.data.iloc[:i + 1]

            # 检查熔断状态
            if self._check_circuit_breaker(strategy):
                # 熔断触发后，只记录权益，不执行任何交易
                equity_curve.append(strategy.total_equity)
                continue

            # 检查止损止盈（在策略执行前检查）
            self._check_stop_loss_take_profit(strategy, bar, i)

            # 根据模式选择不同的执行流程
            if self.config.realtime_indicator:
                # 实时模式：开盘时判断信号并立即执行
                # 1. 执行策略逻辑（产生订单）
                try:
                    strategy.on_bar(bar, history)
                except Exception as e:
                    logger.error(f"策略执行异常: {e}", exc_info=True)
                    raise BacktestError("strategy_execution", str(e)) from e
                
                # 2. 立即处理订单（当天开盘价执行）
                self._process_pending_orders(strategy, bar, i)
            else:
                # 标准模式：收盘时判断信号，下一根K线执行
                # 1. 处理待执行订单（来自上一根K线的策略信号）
                self._process_pending_orders(strategy, bar, i)

                # 2. 执行策略逻辑（可能产生新的订单，下一根K线执行）
                try:
                    strategy.on_bar(bar, history)
                except Exception as e:
                    logger.error(f"策略执行异常: {e}", exc_info=True)
                    raise BacktestError("strategy_execution", str(e)) from e

            # 记录当前权益
            current_equity = strategy.total_equity
            equity_curve.append(current_equity)

        logger.info(
            f"回测完成: 最终权益={strategy.total_equity:,.2f}, "
            f"交易次数={len(strategy.trades)}"
        )

        # 计算并返回回测结果
        return self._calculate_result(strategy, equity_curve)

    def _calculate_result(
        self,
        strategy: Strategy,
        equity_curve: List[float]
    ) -> "BacktestResult":
        """计算回测结果和绩效指标。

        Args:
            strategy: 策略实例
            equity_curve: 权益曲线列表

        Returns:
            BacktestResult: 包含所有绩效指标的回测结果
        """
        from .results import BacktestResult

        initial_capital = self.config.initial_capital
        final_capital = strategy.total_equity

        # 计算策略收益率
        strategy_return = (final_capital - initial_capital) / initial_capital

        # 计算基准收益率（买入持有策略）
        benchmark_return = 0.0
        if self.data is not None and len(self.data) >= 2:
            first_close = float(self.data.iloc[0]["close"])
            last_close = float(self.data.iloc[-1]["close"])
            if first_close > 0:
                benchmark_return = (last_close - first_close) / first_close

        # 计算最大回撤
        max_drawdown = self._calculate_max_drawdown(equity_curve)

        # 计算波动率（年化）
        volatility = self._calculate_volatility(equity_curve)

        # 计算夏普比率（使用3%无风险利率）
        sharpe_ratio = self._calculate_sharpe_ratio(
            strategy_return, volatility, risk_free_rate=0.03
        )

        # 计算胜率和盈亏比
        win_rate, profit_factor = self._calculate_trade_metrics(strategy.trades)

        # 总交易次数
        total_trades = len(strategy.trades)

        return BacktestResult(
            config=self.config,
            trades=strategy.trades,
            final_capital=final_capital,
            benchmark_return=benchmark_return,
            strategy_return=strategy_return,
            max_drawdown=max_drawdown,
            volatility=volatility,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=total_trades,
            equity_curve=equity_curve,
        )

    def _calculate_max_drawdown(self, equity_curve: List[float]) -> float:
        """计算最大回撤。

        最大回撤是权益曲线中从峰值到谷值的最大跌幅百分比。

        Args:
            equity_curve: 权益曲线列表

        Returns:
            最大回撤比例（0-1之间的小数）
        """
        if not equity_curve:
            return 0.0

        eq = np.asarray(equity_curve, dtype=float)
        running_peak = np.maximum.accumulate(eq)
        # 仅在峰值为正处计算回撤，避免除零
        valid = running_peak > 0
        if not valid.any():
            return 0.0
        drawdowns = (running_peak[valid] - eq[valid]) / running_peak[valid]
        return float(np.max(drawdowns))

    def _get_annualization_factor(self) -> float:
        """根据K线频率返回年化因子（每年的周期数）。

        - 日线 252（交易日）；周线 52；月线 12
        - 分钟线按每日 240 分钟 / 周期分钟数 × 252 个交易日

        Returns:
            年化因子
        """
        freq = self.config.frequency
        if freq == "d":
            return 252.0
        if freq == "w":
            return 52.0
        if freq == "m":
            return 12.0
        if freq.isdigit():
            bars_per_day = max(1, 240 // int(freq))
            return 252.0 * bars_per_day
        return 252.0

    def _calculate_volatility(self, equity_curve: List[float]) -> float:
        """计算年化波动率。

        使用周期收益率的标准差，再按K线频率对应的年化因子年化。

        Args:
            equity_curve: 权益曲线列表

        Returns:
            年化波动率
        """
        if len(equity_curve) < 2:
            return 0.0

        eq = np.asarray(equity_curve, dtype=float)
        prev = eq[:-1]
        # 仅对前值为正的区间计算收益率，避免除零
        mask = prev > 0
        if not mask.any():
            return 0.0
        returns = (eq[1:][mask] - prev[mask]) / prev[mask]
        if returns.size < 1:
            return 0.0

        std_dev = float(np.std(returns))  # 总体标准差（ddof=0），与原实现口径一致
        return std_dev * math.sqrt(self._get_annualization_factor())

    def _calculate_sharpe_ratio(
        self,
        strategy_return: float,
        volatility: float,
        risk_free_rate: float = 0.03
    ) -> float:
        """计算夏普比率。

        夏普比率 = (策略收益率 - 无风险利率) / 波动率

        Args:
            strategy_return: 策略收益率
            volatility: 年化波动率
            risk_free_rate: 无风险利率，默认3%

        Returns:
            夏普比率
        """
        if volatility == 0:
            return 0.0

        # 计算回测期间的天数
        if self.data is not None and len(self.data) >= 2:
            start_date = pd.to_datetime(self.data.iloc[0]["date"])
            end_date = pd.to_datetime(self.data.iloc[-1]["date"])
            days = (end_date - start_date).days
            if days > 0:
                # 将策略收益率年化
                years = days / 365.0
                annualized_return = (1 + strategy_return) ** (1 / years) - 1
            else:
                annualized_return = strategy_return
        else:
            annualized_return = strategy_return

        sharpe = (annualized_return - risk_free_rate) / volatility
        return sharpe

    def _calculate_trade_metrics(
        self,
        trades: List[TradeRecord]
    ) -> tuple:
        """计算交易相关指标。

        计算胜率和盈亏比。

        Args:
            trades: 交易记录列表

        Returns:
            (win_rate, profit_factor) 元组
        """
        if not trades:
            return 0.0, 0.0

        # 维护未平仓买入份额队列（FIFO），每项: [price, remaining_qty, commission_per_share]
        # 按"份额"逐手配对，正确处理部分成交与金字塔加仓（买卖数量可能不等）
        open_buys: deque = deque()
        profits: List[float] = []
        losses: List[float] = []

        for trade in trades:
            if trade.quantity <= 0:
                continue

            if trade.action == "BUY":
                comm_per_share = trade.commission / trade.quantity
                open_buys.append([trade.price, trade.quantity, comm_per_share])
            elif trade.action == "SELL":
                sell_qty = trade.quantity
                sell_comm_per_share = trade.commission / trade.quantity
                pnl = 0.0
                # 一笔卖出可能跨多笔买入配对
                while sell_qty > 0 and open_buys:
                    buy = open_buys[0]
                    matched = min(sell_qty, buy[1])
                    # 价差盈亏，并扣除买入与卖出手续费（按配对份额分摊）
                    pnl += (trade.price - buy[0]) * matched
                    pnl -= buy[2] * matched
                    pnl -= sell_comm_per_share * matched
                    buy[1] -= matched
                    sell_qty -= matched
                    if buy[1] <= 0:
                        open_buys.popleft()
                # 按每笔卖出（平仓）的净盈亏统计胜负
                if pnl > 0:
                    profits.append(pnl)
                elif pnl < 0:
                    losses.append(abs(pnl))

        # 计算胜率（按已平仓的卖出笔数）
        total_closed = len(profits) + len(losses)
        win_rate = len(profits) / total_closed if total_closed > 0 else 0.0

        # 计算盈亏比（总盈利 / 总亏损）
        total_profit = sum(profits)
        total_loss = sum(losses)
        if total_loss > 0:
            profit_factor = total_profit / total_loss
        elif total_profit > 0:
            profit_factor = 999.99  # 无亏损交易，记为较大值
        else:
            profit_factor = 0.0

        return win_rate, profit_factor
