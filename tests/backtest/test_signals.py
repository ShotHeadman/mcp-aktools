"""SignalCalculator 单元测试。"""

import pandas as pd
import numpy as np
import pytest

from mcp_aktools.backtest.signals import SignalCalculator, SignalType
from mcp_aktools.models.indicator import CustomIndicator


class TestSignalCalculatorCreation:
    """测试 SignalCalculator 创建。"""
    
    def test_create_with_empty_dataframe(self):
        """测试使用空 DataFrame 创建。"""
        df = pd.DataFrame()
        calc = SignalCalculator(df)
        assert calc.data is df
        assert calc.custom_indicators == {}
    
    def test_create_with_data(self):
        """测试使用数据创建。"""
        df = pd.DataFrame({
            "close": [10.0, 11.0, 12.0],
            "DIF": [0.1, 0.2, 0.3],
            "DEA": [0.15, 0.18, 0.25],
        })
        calc = SignalCalculator(df)
        assert len(calc.data) == 3


class TestCrossoverDetection:
    """测试金叉/死叉检测。"""
    
    @pytest.fixture
    def calc_with_cross_data(self):
        """创建包含交叉数据的计算器。"""
        # 设计数据使得在 index=2 处发生金叉，index=4 处发生死叉
        df = pd.DataFrame({
            "close": [10.0, 10.5, 11.0, 10.8, 10.2, 9.8],
            "fast": [0.1, 0.15, 0.25, 0.30, 0.20, 0.10],
            "slow": [0.2, 0.20, 0.20, 0.25, 0.25, 0.20],
        })
        return SignalCalculator(df)
    
    def test_crossover_at_index_0(self, calc_with_cross_data):
        """测试 index=0 时不会检测到金叉。"""
        calc = calc_with_cross_data
        result = calc._crossover(calc.data["fast"], calc.data["slow"], 0)
        assert result is False
    
    def test_crossover_detected(self, calc_with_cross_data):
        """测试金叉检测。"""
        calc = calc_with_cross_data
        # index=2: fast 从 0.15 变为 0.25，slow 从 0.20 变为 0.20
        # prev: fast(0.15) <= slow(0.20), curr: fast(0.25) > slow(0.20) -> 金叉
        result = calc._crossover(calc.data["fast"], calc.data["slow"], 2)
        assert result is True
    
    def test_crossunder_detected(self, calc_with_cross_data):
        """测试死叉检测。"""
        calc = calc_with_cross_data
        # index=4: fast 从 0.30 变为 0.20，slow 从 0.25 变为 0.25
        # prev: fast(0.30) >= slow(0.25), curr: fast(0.20) < slow(0.25) -> 死叉
        result = calc._crossunder(calc.data["fast"], calc.data["slow"], 4)
        assert result is True
    
    def test_crossover_with_nan(self):
        """测试包含 NaN 值时的处理。"""
        df = pd.DataFrame({
            "fast": [np.nan, 0.2, 0.3],
            "slow": [0.1, 0.15, 0.25],
        })
        calc = SignalCalculator(df)
        result = calc._crossover(calc.data["fast"], calc.data["slow"], 1)
        assert result is False


class TestMACDSignals:
    """测试 MACD 信号检测。"""
    
    @pytest.fixture
    def calc_with_macd(self):
        """创建包含 MACD 数据的计算器。"""
        # 设计数据使得在 index=2 处发生金叉
        df = pd.DataFrame({
            "close": [10.0, 10.5, 11.0, 10.8, 10.2],
            "DIF": [0.1, 0.15, 0.25, 0.30, 0.20],
            "DEA": [0.2, 0.20, 0.20, 0.25, 0.25],
        })
        return SignalCalculator(df)
    
    def test_macd_golden_cross(self, calc_with_macd):
        """测试 MACD 金叉检测。"""
        calc = calc_with_macd
        # index=2: DIF 从 0.15 变为 0.25，DEA 从 0.20 变为 0.20
        assert calc.macd_golden_cross(2) is True
        assert calc.macd_golden_cross(1) is False
    
    def test_macd_death_cross(self, calc_with_macd):
        """测试 MACD 死叉检测。"""
        calc = calc_with_macd
        # index=4: DIF 从 0.30 变为 0.20，DEA 从 0.25 变为 0.25
        assert calc.macd_death_cross(4) is True
        assert calc.macd_death_cross(3) is False
    
    def test_macd_missing_columns(self):
        """测试缺少 MACD 列时的处理。"""
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})
        calc = SignalCalculator(df)
        assert calc.macd_golden_cross(1) is False
        assert calc.macd_death_cross(1) is False


class TestKDJSignals:
    """测试 KDJ 信号检测。"""
    
    @pytest.fixture
    def calc_with_kdj(self):
        """创建包含 KDJ 数据的计算器。"""
        df = pd.DataFrame({
            "close": [10.0, 10.5, 11.0, 10.8, 10.2],
            "K": [20, 25, 35, 40, 30],
            "D": [30, 30, 30, 35, 35],
        })
        return SignalCalculator(df)
    
    def test_kdj_golden_cross(self, calc_with_kdj):
        """测试 KDJ 金叉检测。"""
        calc = calc_with_kdj
        # index=2: K 从 25 变为 35，D 从 30 变为 30
        assert calc.kdj_golden_cross(2) is True
    
    def test_kdj_death_cross(self, calc_with_kdj):
        """测试 KDJ 死叉检测。"""
        calc = calc_with_kdj
        # index=4: K 从 40 变为 30，D 从 35 变为 35
        assert calc.kdj_death_cross(4) is True


class TestRSISignals:
    """测试 RSI 信号检测。"""
    
    @pytest.fixture
    def calc_with_rsi(self):
        """创建包含 RSI 数据的计算器。"""
        df = pd.DataFrame({
            "close": [10.0, 10.5, 11.0, 10.8, 10.2],
            "RSI_14": [50, 65, 75, 80, 25],
        })
        return SignalCalculator(df)
    
    def test_rsi_overbought(self, calc_with_rsi):
        """测试 RSI 超买检测。"""
        calc = calc_with_rsi
        assert calc.rsi_overbought(2) is True  # RSI=75 > 70
        assert calc.rsi_overbought(1) is False  # RSI=65 < 70
    
    def test_rsi_oversold(self, calc_with_rsi):
        """测试 RSI 超卖检测。"""
        calc = calc_with_rsi
        assert calc.rsi_oversold(4) is True  # RSI=25 < 30
        assert calc.rsi_oversold(0) is False  # RSI=50 > 30
    
    def test_rsi_custom_threshold(self, calc_with_rsi):
        """测试自定义阈值。"""
        calc = calc_with_rsi
        assert calc.rsi_overbought(1, threshold=60) is True  # RSI=65 > 60
        assert calc.rsi_oversold(0, threshold=55) is True  # RSI=50 < 55


class TestBOLLSignals:
    """测试布林带信号检测。"""
    
    @pytest.fixture
    def calc_with_boll(self):
        """创建包含布林带数据的计算器。"""
        df = pd.DataFrame({
            "close": [10.0, 10.5, 11.5, 10.8, 9.0],
            "BBU_20_2.0": [11.0, 11.0, 11.0, 11.0, 11.0],
            "BBL_20_2.0": [9.5, 9.5, 9.5, 9.5, 9.5],
        })
        return SignalCalculator(df)
    
    def test_boll_upper_break(self, calc_with_boll):
        """测试布林带上轨突破检测。"""
        calc = calc_with_boll
        # index=2: close 从 10.5 变为 11.5，BBU 保持 11.0
        # prev: close(10.5) <= BBU(11.0), curr: close(11.5) > BBU(11.0) -> 突破
        assert calc.boll_upper_break(2) is True
    
    def test_boll_lower_break(self, calc_with_boll):
        """测试布林带下轨突破检测。"""
        calc = calc_with_boll
        # index=4: close 从 10.8 变为 9.0，BBL 保持 9.5
        # prev: close(10.8) >= BBL(9.5), curr: close(9.0) < BBL(9.5) -> 突破
        assert calc.boll_lower_break(4) is True


class TestCustomIndicator:
    """测试自定义指标功能。"""
    
    def test_register_indicator(self):
        """测试注册自定义指标。"""
        df = pd.DataFrame({
            "close": [10.0, 11.0, 12.0, 13.0, 14.0],
        })
        calc = SignalCalculator(df)
        
        indicator = CustomIndicator(
            name="MA5",
            description="5日均线",
            function_code="result = df['close'].rolling(window=3).mean()"
        )
        
        calc.register_indicator(indicator)
        
        assert "ma5" in calc.custom_indicators
        assert "ma5" in calc.data.columns
    
    def test_register_indicator_with_error(self):
        """测试注册有错误的自定义指标。"""
        df = pd.DataFrame({
            "close": [10.0, 11.0, 12.0],
        })
        calc = SignalCalculator(df)
        
        indicator = CustomIndicator(
            name="BadIndicator",
            description="错误指标",
            function_code="result = undefined_variable"  # 会引发错误
        )
        
        calc.register_indicator(indicator)
        
        # 指标应该被注册，但值为 NaN
        assert "badindicator" in calc.custom_indicators
        assert "badindicator" in calc.data.columns
        assert calc.data["badindicator"].isna().all()


class TestEvaluateCondition:
    """测试条件评估功能。"""
    
    @pytest.fixture
    def calc_with_all_indicators(self):
        """创建包含所有指标的计算器。"""
        df = pd.DataFrame({
            "close": [10.0, 10.5, 11.0, 10.8, 10.2],
            "DIF": [0.1, 0.15, 0.25, 0.30, 0.20],
            "DEA": [0.2, 0.20, 0.20, 0.25, 0.25],
            "K": [20, 25, 35, 40, 30],
            "D": [30, 30, 30, 35, 35],
            "RSI_14": [50, 65, 75, 80, 25],
        })
        return SignalCalculator(df)
    
    def test_evaluate_macd_golden_cross(self, calc_with_all_indicators):
        """测试评估 MACD 金叉条件。"""
        calc = calc_with_all_indicators
        assert calc.evaluate_condition("macd_golden_cross", 2) is True
        assert calc.evaluate_condition("MACD_GOLDEN_CROSS", 2) is True  # 大小写不敏感
    
    def test_evaluate_rsi_overbought(self, calc_with_all_indicators):
        """测试评估 RSI 超买条件。"""
        calc = calc_with_all_indicators
        assert calc.evaluate_condition("rsi_overbought", 2) is True
    
    def test_evaluate_unknown_condition(self, calc_with_all_indicators):
        """测试评估未知条件。"""
        calc = calc_with_all_indicators
        assert calc.evaluate_condition("unknown_signal", 2) is False

    def test_evaluate_custom_indicator_reads_cached_column(self):
        """注册后 evaluate_condition 应优先读取已缓存的列（不再重算）。"""
        df = pd.DataFrame({"close": [10.0, 11.0, 12.0]})
        calc = SignalCalculator(df)
        indicator = CustomIndicator(
            name="signal_flag",
            description="布尔信号",
            function_code="result = df['close'] > 10.5",
        )
        calc.register_indicator(indicator)
        # 列在注册时已写入；evaluate_condition 直接读列值
        assert "signal_flag" in calc.data.columns
        assert calc.evaluate_condition("signal_flag", 0) is False  # 10.0 > 10.5 → False
        assert calc.evaluate_condition("signal_flag", 2) is True   # 12.0 > 10.5 → True


class TestGetIndicatorValue:
    """测试获取指标值功能。"""
    
    def test_get_indicator_value(self):
        """测试获取指标值。"""
        df = pd.DataFrame({
            "close": [10.0, 11.0, 12.0],
            "DIF": [0.1, 0.2, 0.3],
        })
        calc = SignalCalculator(df)
        
        assert calc.get_indicator_value("DIF", 0) == 0.1
        assert calc.get_indicator_value("dif", 1) == 0.2  # 大小写不敏感
    
    def test_get_indicator_value_nan(self):
        """测试获取 NaN 指标值。"""
        df = pd.DataFrame({
            "close": [10.0, 11.0, 12.0],
            "DIF": [np.nan, 0.2, 0.3],
        })
        calc = SignalCalculator(df)
        
        assert calc.get_indicator_value("DIF", 0) is None
    
    def test_get_indicator_value_not_found(self):
        """测试获取不存在的指标值。"""
        df = pd.DataFrame({
            "close": [10.0, 11.0, 12.0],
        })
        calc = SignalCalculator(df)
        
        assert calc.get_indicator_value("UNKNOWN", 0) is None


class TestSignalType:
    """测试信号类型枚举。"""
    
    def test_signal_type_values(self):
        """测试信号类型枚举值。"""
        assert SignalType.GOLDEN_CROSS.value == "golden_cross"
        assert SignalType.DEATH_CROSS.value == "death_cross"
        assert SignalType.OVERBOUGHT.value == "overbought"
        assert SignalType.OVERSOLD.value == "oversold"
        assert SignalType.UPPER_BREAK.value == "upper_break"
        assert SignalType.LOWER_BREAK.value == "lower_break"


class TestAdaptiveStrongCross:
    """测试 MACD 强金叉/死叉的自适应阈值（按频率生效）。

    修复前 dif_threshold 默认为数值，自适应分支永不执行，d 与 60 行为相同；
    修复后默认 None 触发自适应，不同频率使用不同阈值。
    """

    def test_death_cross_strong_adapts_to_frequency(self):
        # index=1 发生普通死叉，prev_dif=4（未跨零轴）
        df = pd.DataFrame({"DIF": [4.0, 2.0], "DEA": [3.0, 3.0]})
        calc = SignalCalculator(df)
        # 日线阈值 10：4 未超过，不触发强死叉
        assert calc.macd_death_cross_strong(1, frequency="d") is False
        # 60分钟阈值 3.5：4 超过，触发强死叉
        assert calc.macd_death_cross_strong(1, frequency="60") is True

    def test_golden_cross_strong_adapts_to_frequency(self):
        # index=1 发生普通金叉，prev_dif=-4（未跨零轴）
        df = pd.DataFrame({"DIF": [-4.0, -2.0], "DEA": [-3.0, -3.0]})
        calc = SignalCalculator(df)
        assert calc.macd_golden_cross_strong(1, frequency="d") is False
        assert calc.macd_golden_cross_strong(1, frequency="60") is True

    def test_explicit_threshold_overrides_adaptive(self):
        # 显式传入阈值时不走自适应字典
        df = pd.DataFrame({"DIF": [4.0, 2.0], "DEA": [3.0, 3.0]})
        calc = SignalCalculator(df)
        assert calc.macd_death_cross_strong(1, dif_threshold=3.5) is True
        assert calc.macd_death_cross_strong(1, dif_threshold=100.0) is False
