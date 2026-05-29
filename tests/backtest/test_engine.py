"""BacktestEngine 单元测试。

通过预设 engine.data / engine.signal_calculator 绕过网络数据加载，
对订单执行、风控、绩效计算等核心逻辑进行本地单元测试。
"""

import math

import numpy as np
import pandas as pd
import pytest

from mcp_aktools.backtest.config import BacktestConfig
from mcp_aktools.backtest.engine import BacktestEngine
from mcp_aktools.backtest.signals import SignalCalculator
from mcp_aktools.backtest.realtime_indicators import (
    RealtimeIndicatorCalculator,
    calc_macd_realtime,
)
from mcp_aktools.backtest.strategy import PendingOrder, Strategy
from mcp_aktools.models.trade import OrderType, PriceType, TradeRecord


# ---------------------------------------------------------------------------
# 辅助函数与策略
# ---------------------------------------------------------------------------

def make_config(**overrides) -> BacktestConfig:
    """创建测试用配置，默认关闭滑点以便精确断言。"""
    params = dict(
        stock_code="sh.600000",
        start_date="2024-01-01",
        end_date="2024-12-31",
        initial_capital=100000.0,
        frequency="d",
        slippage_min=0.0,
        slippage_max=0.0,
    )
    params.update(overrides)
    return BacktestConfig(**params)


def make_data(n: int = 5, prices=None) -> pd.DataFrame:
    """构造简单的 OHLCV K线数据（open=close=指定价格）。"""
    if prices is None:
        prices = [10.0 + i for i in range(n)]
    dates = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p + 0.5 for p in prices],
        "low": [p - 0.5 for p in prices],
        "close": prices,
        "volume": [10000] * len(prices),
    })


def make_bar(open_=10.0, high=11.0, low=9.0, close=10.0, date="2024-01-01") -> pd.Series:
    """构造单根 K线 Series。"""
    return pd.Series({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 10000,
        "date": pd.Timestamp(date),
    })


class NoopStrategy(Strategy):
    """不发任何订单的策略，用于测试引擎内部方法。"""

    def on_bar(self, bar: pd.Series, history: pd.DataFrame) -> None:
        pass


class FirstBarBuyStrategy(Strategy):
    """第一根 K线以 90% 现金买入后不再操作。"""

    def on_bar(self, bar: pd.Series, history: pd.DataFrame) -> None:
        if self._current_index == 0 and not self.has_position:
            self.buy(PriceType.CLOSE, OrderType.CASH_PERCENT, 90)


def make_engine(config: BacktestConfig, data: pd.DataFrame) -> BacktestEngine:
    """构造已预设数据的引擎（不触发网络加载）。"""
    engine = BacktestEngine(config)
    engine.data = data
    engine.full_data = data.copy()
    engine.signal_calculator = SignalCalculator(data)
    return engine


# ---------------------------------------------------------------------------
# 执行价格与数量计算
# ---------------------------------------------------------------------------

class TestExecutionPrice:
    """测试执行价格选取。"""

    def test_price_by_type(self):
        engine = make_engine(make_config(), make_data())
        bar = make_bar(open_=10.0, high=11.0, low=9.0, close=10.5)
        assert engine._get_execution_price(PriceType.OPEN, bar) == 10.0
        assert engine._get_execution_price(PriceType.HIGH, bar) == 11.0
        assert engine._get_execution_price(PriceType.LOW, bar) == 9.0
        assert engine._get_execution_price(PriceType.CLOSE, bar) == 10.5


class TestQuantityCalculation:
    """测试订单数量计算。"""

    def setup_method(self):
        self.engine = make_engine(make_config(), make_data())

    def test_buy_lots(self):
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.LOTS, 10)
        assert self.engine._calculate_quantity(order, 10.0, 100000, 0) == 1000

    def test_buy_cash_percent(self):
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.CASH_PERCENT, 50)
        # 50% * 100000 = 50000, / 10 = 5000 股
        assert self.engine._calculate_quantity(order, 10.0, 100000, 0) == 5000

    def test_buy_cash_percent_rounds_down_to_lot(self):
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.CASH_PERCENT, 100)
        # 100000 / 10.3 = 9708.7 → 9708 → 取整百 → 9700
        assert self.engine._calculate_quantity(order, 10.3, 100000, 0) == 9700

    def test_sell_position_percent(self):
        order = PendingOrder("SELL", PriceType.CLOSE, OrderType.POSITION_PERCENT, 50)
        assert self.engine._calculate_quantity(order, 10.0, 100000, 1000) == 500

    def test_buy_position_percent_unsupported(self):
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.POSITION_PERCENT, 50)
        assert self.engine._calculate_quantity(order, 10.0, 100000, 0) == 0

    def test_sell_cash_percent_unsupported(self):
        order = PendingOrder("SELL", PriceType.CLOSE, OrderType.CASH_PERCENT, 50)
        assert self.engine._calculate_quantity(order, 10.0, 100000, 1000) == 0


class TestCommission:
    """测试手续费计算。"""

    def test_min_commission_applied(self):
        engine = make_engine(make_config(commission_rate=0.000854, min_commission=5.0), make_data())
        # 小额交易：1000 * 0.000854 = 0.854 < 5 → 取 5
        assert engine._calculate_commission(1000) == 5.0

    def test_rate_commission(self):
        engine = make_engine(make_config(commission_rate=0.001, min_commission=5.0), make_data())
        # 100000 * 0.001 = 100 > 5
        assert engine._calculate_commission(100000) == 100.0


# ---------------------------------------------------------------------------
# 买入/卖出执行
# ---------------------------------------------------------------------------

class TestBuyExecution:
    """测试买入订单执行。"""

    def test_successful_buy(self):
        config = make_config(initial_capital=100000.0)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        bar = make_bar(close=10.0)
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.LOTS, 5)  # 500 股

        trade = engine._execute_buy(strategy, order, 10.0, bar, 0)

        assert trade is not None
        assert trade.action == "BUY"
        assert trade.quantity == 500
        assert trade.commission == 5.0  # 5000*0.000854=4.27 < 5
        assert strategy.position.quantity == 500
        assert strategy.cash == pytest.approx(100000 - 5000 - 5)

    def test_buy_rejected_insufficient_cash(self):
        config = make_config(initial_capital=1000.0)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        bar = make_bar(close=10.0)
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.LOTS, 5)  # 5000 元 > 1000

        trade = engine._execute_buy(strategy, order, 10.0, bar, 0)
        assert trade is None
        assert strategy.position.quantity == 0

    def test_buy_rejected_zero_quantity(self):
        config = make_config()
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        bar = make_bar(close=10.0)
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.CASH_PERCENT, 0)

        trade = engine._execute_buy(strategy, order, 10.0, bar, 0)
        assert trade is None

    def test_pyramiding_disabled_rejects_add(self):
        config = make_config(enable_pyramiding=False)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(500, 10.0)  # 已有持仓
        bar = make_bar(close=10.0)
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.LOTS, 5)

        trade = engine._execute_buy(strategy, order, 10.0, bar, 0)
        assert trade is None

    def test_pyramiding_max_layers(self):
        config = make_config(enable_pyramiding=True, max_pyramid_layers=2, pyramid_scale_factor=1.0)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        # 已加仓到 2 层
        strategy.position.update_on_buy(500, 10.0)
        strategy.position.update_on_buy(500, 10.0)
        assert strategy.position.layers == 2
        bar = make_bar(close=10.0)
        order = PendingOrder("BUY", PriceType.CLOSE, OrderType.LOTS, 5)

        trade = engine._execute_buy(strategy, order, 10.0, bar, 0)
        assert trade is None  # 已达上限


class TestSellExecution:
    """测试卖出订单执行。"""

    def test_successful_sell(self):
        config = make_config(initial_capital=100000.0)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(1000, 10.0)
        strategy.cash = 90000.0
        bar = make_bar(close=12.0)
        order = PendingOrder("SELL", PriceType.CLOSE, OrderType.POSITION_PERCENT, 100)

        trade = engine._execute_sell(strategy, order, 12.0, bar, 0)

        assert trade is not None
        assert trade.action == "SELL"
        assert trade.quantity == 1000
        # 收入 12000 - 手续费
        commission = max(12000 * 0.000854, 5)
        assert strategy.cash == pytest.approx(90000 + 12000 - commission)
        assert strategy.position.quantity == 0

    def test_sell_rejected_no_position(self):
        config = make_config()
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        bar = make_bar(close=10.0)
        order = PendingOrder("SELL", PriceType.CLOSE, OrderType.POSITION_PERCENT, 100)

        trade = engine._execute_sell(strategy, order, 10.0, bar, 0)
        assert trade is None

    def test_execute_order_rejects_invalid_price(self):
        config = make_config()
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        bar = make_bar(open_=0.0, close=0.0)  # 无效价格
        order = PendingOrder("BUY", PriceType.OPEN, OrderType.LOTS, 5)

        trade = engine._execute_order(strategy, order, bar, 0)
        assert trade is None


# ---------------------------------------------------------------------------
# 止损止盈（标准模式，使用收盘价）
# ---------------------------------------------------------------------------

class TestStopLossTakeProfit:
    """测试止损止盈（标准模式）。"""

    def test_stop_loss_triggers(self):
        config = make_config(stop_loss=0.05)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(1000, 10.0)
        strategy.cash = 0.0
        bar = make_bar(close=9.4)  # 亏损 6% > 5%
        strategy._current_bar = bar

        trade = engine._check_stop_loss_take_profit(strategy, bar, 0)
        assert trade is not None
        assert trade.action == "SELL"
        assert strategy.position.quantity == 0

    def test_take_profit_triggers(self):
        config = make_config(take_profit=0.10)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(1000, 10.0)
        strategy.cash = 0.0
        bar = make_bar(close=11.5)  # 盈利 15% > 10%
        strategy._current_bar = bar

        trade = engine._check_stop_loss_take_profit(strategy, bar, 0)
        assert trade is not None
        assert trade.action == "SELL"

    def test_no_trigger_within_range(self):
        config = make_config(stop_loss=0.05, take_profit=0.10)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(1000, 10.0)
        bar = make_bar(close=10.2)  # 盈利 2%，不触发
        strategy._current_bar = bar

        trade = engine._check_stop_loss_take_profit(strategy, bar, 0)
        assert trade is None

    def test_realtime_mode_uses_open_price(self):
        """实时模式用开盘价判断止损，消除收盘价前视偏差。"""
        config = make_config(stop_loss=0.05, realtime_indicator=True)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(1000, 10.0)
        strategy.cash = 0.0
        # 开盘 9.4（亏6%）触发止损；收盘 10.5（涨5%）不应被用于判断
        bar = make_bar(open_=9.4, close=10.5)
        strategy._current_bar = bar

        trade = engine._check_stop_loss_take_profit(strategy, bar, 0)
        assert trade is not None
        assert trade.action == "SELL"
        assert trade.price == pytest.approx(9.4)  # 平仓价基于开盘价（无滑点）

    def test_standard_mode_uses_close_price(self):
        """标准模式用收盘价判断：同一根 bar（开盘跌收盘涨）不触发止损。"""
        config = make_config(stop_loss=0.05, realtime_indicator=False)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(1000, 10.0)
        bar = make_bar(open_=9.4, close=10.5)  # 收盘涨 5%
        strategy._current_bar = bar

        trade = engine._check_stop_loss_take_profit(strategy, bar, 0)
        assert trade is None


# ---------------------------------------------------------------------------
# 熔断机制
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    """测试最大回撤熔断。"""

    def test_circuit_breaker_triggers(self):
        config = make_config(initial_capital=100000.0, max_drawdown_limit=0.10)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        engine.reset_circuit_breaker()  # peak = 100000
        strategy.cash = 88000.0  # 回撤 12% > 10%

        assert engine._check_circuit_breaker(strategy) is True
        assert engine._circuit_breaker_triggered is True

    def test_circuit_breaker_not_triggered(self):
        config = make_config(initial_capital=100000.0, max_drawdown_limit=0.20)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        engine.reset_circuit_breaker()
        strategy.cash = 88000.0  # 回撤 12% < 20%

        assert engine._check_circuit_breaker(strategy) is False

    def test_no_limit_never_triggers(self):
        config = make_config(max_drawdown_limit=None)
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.cash = 1.0
        assert engine._check_circuit_breaker(strategy) is False


# ---------------------------------------------------------------------------
# 信号优先级
# ---------------------------------------------------------------------------

class TestSignalPriority:
    """测试同时触发买卖信号时的优先级处理。"""

    def test_none_priority_skips_both(self):
        config = make_config(signal_priority="none")
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.pending_orders = [
            PendingOrder("BUY", PriceType.CLOSE, OrderType.LOTS, 5),
            PendingOrder("SELL", PriceType.CLOSE, OrderType.POSITION_PERCENT, 100),
        ]
        bar = make_bar(close=10.0)

        trades = engine._process_pending_orders(strategy, bar, 0)
        assert trades == []
        assert strategy.pending_orders == []  # 已清空

    def test_buy_first_executes_buy_before_sell(self):
        # 启用金字塔加仓，使持仓状态下 BUY 也能成交，从而验证买卖排序
        config = make_config(
            signal_priority="buy_first", initial_capital=100000.0,
            enable_pyramiding=True, max_pyramid_layers=5, pyramid_scale_factor=1.0,
        )
        engine = make_engine(config, make_data())
        strategy = NoopStrategy(config)
        strategy.position.update_on_buy(1000, 10.0)
        strategy.cash = 50000.0
        # 故意把 SELL 放前面，验证排序后 BUY 先执行
        strategy.pending_orders = [
            PendingOrder("SELL", PriceType.CLOSE, OrderType.POSITION_PERCENT, 100),
            PendingOrder("BUY", PriceType.CLOSE, OrderType.LOTS, 1),
        ]
        bar = make_bar(close=10.0)

        trades = engine._process_pending_orders(strategy, bar, 0)
        assert len(trades) == 2
        assert trades[0].action == "BUY"
        assert trades[1].action == "SELL"


# ---------------------------------------------------------------------------
# 绩效指标
# ---------------------------------------------------------------------------

class TestPerformanceMetrics:
    """测试绩效指标计算。"""

    def test_max_drawdown(self):
        engine = make_engine(make_config(), make_data())
        # 峰值 120，谷值 90 → 回撤 (120-90)/120 = 0.25
        curve = [100, 120, 90, 110]
        assert engine._calculate_max_drawdown(curve) == pytest.approx(0.25)

    def test_max_drawdown_monotonic_increase(self):
        engine = make_engine(make_config(), make_data())
        assert engine._calculate_max_drawdown([100, 110, 120]) == 0.0

    def test_max_drawdown_empty(self):
        engine = make_engine(make_config(), make_data())
        assert engine._calculate_max_drawdown([]) == 0.0

    def test_volatility_zero_when_flat(self):
        engine = make_engine(make_config(), make_data())
        assert engine._calculate_volatility([100, 100, 100]) == 0.0

    def test_volatility_positive_when_varying(self):
        engine = make_engine(make_config(), make_data())
        assert engine._calculate_volatility([100, 110, 105, 115]) > 0

    def test_sharpe_zero_when_no_volatility(self):
        engine = make_engine(make_config(), make_data())
        assert engine._calculate_sharpe_ratio(0.1, 0.0) == 0.0

    def test_trade_metrics_single_winning_round(self):
        engine = make_engine(make_config(), make_data())
        trades = [
            TradeRecord("2024-01-01", "BUY", 1000, 10.0, 5.0, 0, 1000),
            TradeRecord("2024-01-05", "SELL", 1000, 12.0, 5.0, 0, 0),
        ]
        win_rate, profit_factor = engine._calculate_trade_metrics(trades)
        assert win_rate == 1.0
        assert profit_factor > 0

    def test_trade_metrics_empty(self):
        engine = make_engine(make_config(), make_data())
        assert engine._calculate_trade_metrics([]) == (0.0, 0.0)

    def test_trade_metrics_one_win_one_loss(self):
        engine = make_engine(make_config(), make_data())
        trades = [
            TradeRecord("2024-01-01", "BUY", 1000, 10.0, 5.0, 0, 1000),
            TradeRecord("2024-01-02", "SELL", 1000, 12.0, 5.0, 0, 0),  # +2000 盈
            TradeRecord("2024-01-03", "BUY", 1000, 12.0, 5.0, 0, 1000),
            TradeRecord("2024-01-04", "SELL", 1000, 11.0, 5.0, 0, 0),  # -1000 亏
        ]
        win_rate, profit_factor = engine._calculate_trade_metrics(trades)
        assert win_rate == pytest.approx(0.5)

    def test_trade_metrics_partial_sells(self):
        """部分成交：一次买入分两次卖出，应正确配对两笔（旧逻辑会漏配第二笔）。"""
        engine = make_engine(make_config(), make_data())
        trades = [
            TradeRecord("2024-01-01", "BUY", 1000, 10.0, 5.0, 0, 1000),
            TradeRecord("2024-01-02", "SELL", 500, 12.0, 5.0, 0, 500),   # 盈
            TradeRecord("2024-01-03", "SELL", 500, 8.0, 5.0, 0, 0),      # 亏
        ]
        win_rate, profit_factor = engine._calculate_trade_metrics(trades)
        # 两笔卖出都被配对：一胜一负
        assert win_rate == pytest.approx(0.5)
        assert profit_factor > 0

    def test_trade_metrics_pyramiding_then_sell(self):
        """金字塔加仓后一次性卖出：单笔卖出跨多笔买入配对。"""
        engine = make_engine(make_config(), make_data())
        trades = [
            TradeRecord("2024-01-01", "BUY", 500, 10.0, 5.0, 0, 500),
            TradeRecord("2024-01-02", "BUY", 500, 12.0, 5.0, 0, 1000),
            TradeRecord("2024-01-03", "SELL", 1000, 13.0, 10.0, 0, 0),
        ]
        win_rate, profit_factor = engine._calculate_trade_metrics(trades)
        # 配对 500@10→13 与 500@12→13，单笔卖出净盈利 → 1 胜 0 负
        assert win_rate == 1.0

    def test_annualization_factor_by_frequency(self):
        assert make_engine(make_config(frequency="d"), make_data())._get_annualization_factor() == 252.0
        assert make_engine(make_config(frequency="w"), make_data())._get_annualization_factor() == 52.0
        assert make_engine(make_config(frequency="m"), make_data())._get_annualization_factor() == 12.0
        # 60分钟：240//60=4 根/日 × 252 = 1008
        assert make_engine(make_config(frequency="60"), make_data())._get_annualization_factor() == 1008.0

    def test_volatility_scales_with_frequency(self):
        curve = [100, 110, 105, 115, 108]
        daily = make_engine(make_config(frequency="d"), make_data())._calculate_volatility(curve)
        weekly = make_engine(make_config(frequency="w"), make_data())._calculate_volatility(curve)
        # 同一收益序列，日线年化因子(252) > 周线(52)，年化波动率应成 sqrt 比例
        assert daily > weekly > 0
        assert daily / weekly == pytest.approx(math.sqrt(252 / 52))


# ---------------------------------------------------------------------------
# 端到端
# ---------------------------------------------------------------------------

class TestRunEndToEnd:
    """测试完整回测流程。"""

    def test_run_produces_result(self):
        config = make_config(initial_capital=100000.0)
        engine = make_engine(config, make_data(n=5))

        result = engine.run(FirstBarBuyStrategy)

        assert result is not None
        assert len(result.equity_curve) == 5
        assert result.total_trades >= 1
        # 第一根发单，第二根买入；价格递增，策略应盈利
        assert result.final_capital > 0

    def test_realtime_mode_run_and_index_map(self):
        """实时模式：索引映射正确建立，回测完整跑通。"""
        full = make_data(n=40)
        data = full.iloc[30:].reset_index(drop=True)  # 后10根作为回测区间
        config = make_config(realtime_indicator=True)
        engine = make_engine(config, data)
        engine.full_data = full

        result = engine.run(NoopStrategy)

        assert len(result.equity_curve) == len(data)
        # 索引映射覆盖回测区间全部K线，并映射到 full_data 的正确位置
        assert len(engine._index_map) == len(data)
        assert engine._index_map[0] == 30
        assert engine._index_map[len(data) - 1] == 39

    def test_run_no_trades_when_strategy_idle(self):
        config = make_config()
        engine = make_engine(config, make_data(n=5))

        result = engine.run(NoopStrategy)

        assert result.total_trades == 0
        # 无交易，最终资金等于初始资金
        assert result.final_capital == pytest.approx(config.initial_capital)


class TestReproducibility:
    """测试滑点随机种子的可复现性。"""

    def test_same_seed_same_slippage_sequence(self):
        c1 = make_config(slippage_min=0.001, slippage_max=0.01, random_seed=42)
        c2 = make_config(slippage_min=0.001, slippage_max=0.01, random_seed=42)
        seq1 = [c1.get_random_slippage() for _ in range(10)]
        seq2 = [c2.get_random_slippage() for _ in range(10)]
        assert seq1 == seq2

    def test_reset_rng_restarts_sequence(self):
        c = make_config(slippage_min=0.001, slippage_max=0.01, random_seed=42)
        seq1 = [c.get_random_slippage() for _ in range(5)]
        c.reset_rng()
        seq2 = [c.get_random_slippage() for _ in range(5)]
        assert seq1 == seq2

    def test_different_seed_different_sequence(self):
        c1 = make_config(slippage_min=0.001, slippage_max=0.01, random_seed=1)
        c2 = make_config(slippage_min=0.001, slippage_max=0.01, random_seed=2)
        seq1 = [c1.get_random_slippage() for _ in range(10)]
        seq2 = [c2.get_random_slippage() for _ in range(10)]
        assert seq1 != seq2

    def test_run_reproducible_with_seed(self):
        """同一 random_seed 两次回测，最终资金与权益曲线完全一致。"""
        def run_once():
            config = make_config(slippage_min=0.001, slippage_max=0.01, random_seed=42)
            engine = make_engine(config, make_data(n=8))
            return engine.run(FirstBarBuyStrategy)

        r1 = run_once()
        r2 = run_once()
        assert r1.final_capital == r2.final_capital
        assert r1.equity_curve == r2.equity_curve


class TestRealtimeIndicators:
    """测试实时指标计算与滑动窗口等价性。"""

    def test_macd_window_equivalence(self):
        """用最近300根 vs 全量400根计算实时MACD，DIF/DEA 数值等价（窗口截断安全）。"""
        rng = np.random.default_rng(0)
        close = np.cumsum(rng.standard_normal(400)) + 100.0
        current_open = float(close[-1] + 0.5)
        full = calc_macd_realtime(close, current_open)
        windowed = calc_macd_realtime(close[-300:], current_open)
        assert abs(full[0] - windowed[0]) < 1e-6  # DIF
        assert abs(full[1] - windowed[1]) < 1e-6  # DEA

    def test_calculate_at_open_on_large_index(self):
        """大索引下 calculate_at_open 正常输出指标并命中缓存（验证窗口切片）。"""
        prices = list(np.cumsum(np.random.default_rng(1).standard_normal(400)) + 100.0)
        df = make_data(prices=prices)
        calc = RealtimeIndicatorCalculator(df)
        indicators = calc.calculate_at_open(380)
        assert "DIF" in indicators
        assert "DEA" in indicators
        # 同一索引再次调用应命中缓存，返回同一对象
        assert calc.calculate_at_open(380) is indicators
