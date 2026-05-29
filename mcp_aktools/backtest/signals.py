"""信号计算模块。

该模块实现技术信号计算器，支持：
- MACD 金叉/死叉信号
- KDJ 金叉/死叉信号
- RSI 超买/超卖信号
- BOLL 突破信号
- 自定义指标信号
"""

import logging
from enum import Enum
from typing import Dict, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from mcp_aktools.models.indicator import CustomIndicator

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """信号类型枚举。"""
    GOLDEN_CROSS = "golden_cross"    # 金叉
    DEATH_CROSS = "death_cross"      # 死叉
    OVERBOUGHT = "overbought"        # 超买
    OVERSOLD = "oversold"            # 超卖
    UPPER_BREAK = "upper_break"      # 上轨突破
    LOWER_BREAK = "lower_break"      # 下轨突破


class SignalCalculator:
    """技术信号计算器。
    
    基于K线数据和技术指标计算各种交易信号。
    
    Attributes:
        data: 包含OHLCV和技术指标的DataFrame
        custom_indicators: 已注册的自定义指标字典
    """
    
    def __init__(self, data: pd.DataFrame):
        """初始化信号计算器。
        
        Args:
            data: 包含OHLCV和技术指标的DataFrame，
                  需要包含 DIF, DEA, K, D, RSI, BBL, BBU 等列
        """
        self.data = data
        self.custom_indicators: Dict[str, "CustomIndicator"] = {}
    
    def _crossover(self, fast: pd.Series, slow: pd.Series, index: int) -> bool:
        """检测金叉（快线上穿慢线）。
        
        金叉条件：前一根K线 fast <= slow，当前K线 fast > slow
        
        Args:
            fast: 快线数据
            slow: 慢线数据
            index: 当前K线索引
        
        Returns:
            是否发生金叉
        """
        if index < 1:
            return False
        
        prev_fast = fast.iloc[index - 1]
        prev_slow = slow.iloc[index - 1]
        curr_fast = fast.iloc[index]
        curr_slow = slow.iloc[index]
        
        # 检查是否有 NaN 值
        if pd.isna(prev_fast) or pd.isna(prev_slow) or pd.isna(curr_fast) or pd.isna(curr_slow):
            return False
        
        return bool(prev_fast <= prev_slow and curr_fast > curr_slow)
    
    def _crossunder(self, fast: pd.Series, slow: pd.Series, index: int) -> bool:
        """检测死叉（快线下穿慢线）。
        
        死叉条件：前一根K线 fast >= slow，当前K线 fast < slow
        
        Args:
            fast: 快线数据
            slow: 慢线数据
            index: 当前K线索引
        
        Returns:
            是否发生死叉
        """
        if index < 1:
            return False
        
        prev_fast = fast.iloc[index - 1]
        prev_slow = slow.iloc[index - 1]
        curr_fast = fast.iloc[index]
        curr_slow = slow.iloc[index]
        
        # 检查是否有 NaN 值
        if pd.isna(prev_fast) or pd.isna(prev_slow) or pd.isna(curr_fast) or pd.isna(curr_slow):
            return False
        
        return bool(prev_fast >= prev_slow and curr_fast < curr_slow)
    
    def _get_macd_columns(self) -> tuple:
        """获取MACD指标列名。
        
        Returns:
            (dif_col, dea_col) 或 (None, None)
        """
        # 优先查找 DIF 和 DEA 列（我们自己计算的列名）
        if 'DIF' in self.data.columns and 'DEA' in self.data.columns:
            return 'DIF', 'DEA'
        
        # 否则查找 pandas-ta 生成的 MACD 列名格式
        dif_col = None
        dea_col = None
        
        for col in self.data.columns:
            if col.startswith("MACD_") and not col.startswith("MACDs_") and not col.startswith("MACDh_"):
                dif_col = col
            elif col.startswith("MACDs_"):
                dea_col = col
        
        return dif_col, dea_col
    
    def _get_kdj_columns(self) -> tuple:
        """获取KDJ指标列名。
        
        Returns:
            (k_col, d_col) 或 (None, None)
        """
        k_col = None
        d_col = None
        
        for col in self.data.columns:
            if col.startswith("STOCHk_") or col == "K":
                k_col = col
            elif col.startswith("STOCHd_") or col == "D":
                d_col = col
        
        return k_col, d_col
    
    def _get_rsi_column(self) -> Optional[str]:
        """获取RSI指标列名。
        
        Returns:
            RSI列名或None
        """
        for col in self.data.columns:
            if col.startswith("RSI_") or col == "RSI":
                return col
        return None
    
    def _get_boll_columns(self) -> tuple:
        """获取布林带指标列名。
        
        Returns:
            (bbl_col, bbu_col) 或 (None, None)
        """
        bbl_col = None
        bbu_col = None
        
        for col in self.data.columns:
            if col.startswith("BBL_") or col == "BBL":
                bbl_col = col
            elif col.startswith("BBU_") or col == "BBU":
                bbu_col = col
        
        return bbl_col, bbu_col
    
    def macd_golden_cross(self, index: int) -> bool:
        """MACD金叉判断。
        
        当 DIF 上穿 DEA 时触发。
        
        Args:
            index: 当前K线索引
        
        Returns:
            是否发生MACD金叉
        """
        dif_col, dea_col = self._get_macd_columns()
        if dif_col is None or dea_col is None:
            logger.warning("未找到 MACD 指标列")
            return False
        
        return self._crossover(self.data[dif_col], self.data[dea_col], index)
    
    def macd_death_cross(self, index: int) -> bool:
        """MACD死叉判断。
        
        当 DIF 下穿 DEA 时触发。
        
        Args:
            index: 当前K线索引
        
        Returns:
            是否发生MACD死叉
        """
        dif_col, dea_col = self._get_macd_columns()
        if dif_col is None or dea_col is None:
            logger.warning("未找到 MACD 指标列")
            return False
        
        return self._crossunder(self.data[dif_col], self.data[dea_col], index)

    
    def macd_death_cross_strong(self, index: int, dif_threshold: float = 10.0,
                                frequency: str = "d") -> bool:
        """MACD强死叉判断（支持自适应阈值）。
        
        在普通MACD死叉的基础上，增加过滤条件，只在以下情况触发：
        1. DIF从正变负（跨越零轴）- 趋势反转信号
        2. 或者DIF > dif_threshold（大级别行情回调）
        
        Args:
            index: 当前K线索引
            dif_threshold: DIF阈值，默认10.0
            frequency: K线频率，用于自适应阈值选择
        
        Returns:
            是否发生强MACD死叉
        """
        if index < 1:
            return False
        
        dif_col, dea_col = self._get_macd_columns()
        if dif_col is None or dea_col is None:
            logger.warning("未找到 MACD 指标列")
            return False
        
        # 自适应阈值选择
        if dif_threshold is None:
            adaptive_thresholds = {
                "d": 10.0,
                "60": 3.5,
                "30": 2.5,
                "15": 3.0,
                "5": 1.5,
            }
            dif_threshold = adaptive_thresholds.get(frequency, 10.0)
        
        prev_dif = self.data[dif_col].iloc[index - 1]
        prev_dea = self.data[dea_col].iloc[index - 1]
        curr_dif = self.data[dif_col].iloc[index]
        curr_dea = self.data[dea_col].iloc[index]
        
        # 检查NaN
        if pd.isna(prev_dif) or pd.isna(prev_dea) or pd.isna(curr_dif) or pd.isna(curr_dea):
            return False
        
        # 首先检查是否是普通死叉
        is_death_cross = (prev_dif >= prev_dea) and (curr_dif < curr_dea)
        if not is_death_cross:
            return False
        
        # 检查额外条件
        cross_zero = (prev_dif > 0) and (curr_dif < 0)  # DIF从正变负
        high_dif = prev_dif > dif_threshold  # DIF > 阈值
        
        return cross_zero or high_dif
    
    def macd_golden_cross_strong(self, index: int, dif_threshold: float = -10.0,
                                 frequency: str = "d") -> bool:
        """MACD强金叉判断（支持自适应阈值）。
        
        在普通MACD金叉的基础上，增加过滤条件，只在以下情况触发：
        1. DIF从负变正（跨越零轴）- 趋势反转信号
        2. 或者DIF < dif_threshold（大级别行情反弹）
        
        Args:
            index: 当前K线索引
            dif_threshold: DIF阈值，默认-10.0
            frequency: K线频率，用于自适应阈值选择
        
        Returns:
            是否发生强MACD金叉
        """
        if index < 1:
            return False
        
        dif_col, dea_col = self._get_macd_columns()
        if dif_col is None or dea_col is None:
            logger.warning("未找到 MACD 指标列")
            return False
        
        # 自适应阈值选择
        if dif_threshold is None:
            adaptive_thresholds = {
                "d": -10.0,
                "60": -3.5,
                "30": -2.5,
                "15": -3.0,
                "5": -1.5,
            }
            dif_threshold = adaptive_thresholds.get(frequency, -10.0)
        
        prev_dif = self.data[dif_col].iloc[index - 1]
        prev_dea = self.data[dea_col].iloc[index - 1]
        curr_dif = self.data[dif_col].iloc[index]
        curr_dea = self.data[dea_col].iloc[index]
        
        # 检查NaN
        if pd.isna(prev_dif) or pd.isna(prev_dea) or pd.isna(curr_dif) or pd.isna(curr_dea):
            return False
        
        # 首先检查是否是普通金叉
        is_golden_cross = (prev_dif <= prev_dea) and (curr_dif > curr_dea)
        if not is_golden_cross:
            return False
        
        # 检查额外条件
        cross_zero = (prev_dif < 0) and (curr_dif > 0)  # DIF从负变正
        low_dif = prev_dif < dif_threshold  # DIF < 阈值
        
        return cross_zero or low_dif
    
    def kdj_golden_cross(self, index: int) -> bool:
        """KDJ金叉判断。
        
        当 K 线上穿 D 线时触发。
        
        Args:
            index: 当前K线索引
        
        Returns:
            是否发生KDJ金叉
        """
        k_col, d_col = self._get_kdj_columns()
        
        if k_col is None or d_col is None:
            logger.warning("未找到 KDJ 指标列")
            return False
        
        return self._crossover(self.data[k_col], self.data[d_col], index)
    
    def kdj_death_cross(self, index: int) -> bool:
        """KDJ死叉判断。
        
        当 K 线下穿 D 线时触发。
        
        Args:
            index: 当前K线索引
        
        Returns:
            是否发生KDJ死叉
        """
        k_col, d_col = self._get_kdj_columns()
        
        if k_col is None or d_col is None:
            logger.warning("未找到 KDJ 指标列")
            return False
        
        return self._crossunder(self.data[k_col], self.data[d_col], index)
    
    def rsi_overbought(self, index: int, threshold: float = 70) -> bool:
        """RSI超买判断。
        
        当 RSI 超过阈值时触发。
        
        Args:
            index: 当前K线索引
            threshold: 超买阈值，默认 70
        
        Returns:
            是否处于超买状态
        """
        rsi_col = self._get_rsi_column()
        
        if rsi_col is None:
            logger.warning("未找到 RSI 指标列")
            return False
        
        rsi_value = self.data[rsi_col].iloc[index]
        if pd.isna(rsi_value):
            return False
        
        return bool(rsi_value > threshold)
    
    def rsi_oversold(self, index: int, threshold: float = 30) -> bool:
        """RSI超卖判断。
        
        当 RSI 低于阈值时触发。
        
        Args:
            index: 当前K线索引
            threshold: 超卖阈值，默认 30
        
        Returns:
            是否处于超卖状态
        """
        rsi_col = self._get_rsi_column()
        
        if rsi_col is None:
            logger.warning("未找到 RSI 指标列")
            return False
        
        rsi_value = self.data[rsi_col].iloc[index]
        if pd.isna(rsi_value):
            return False
        
        return bool(rsi_value < threshold)
    
    def boll_upper_break(self, index: int) -> bool:
        """布林带上轨突破判断。
        
        当价格上穿布林带上轨时触发。
        
        Args:
            index: 当前K线索引
        
        Returns:
            是否发生上轨突破
        """
        _, bbu_col = self._get_boll_columns()
        
        if bbu_col is None or "close" not in self.data.columns:
            logger.warning("未找到 BOLL 上轨或收盘价列")
            return False
        
        return self._crossover(self.data["close"], self.data[bbu_col], index)
    
    def boll_lower_break(self, index: int) -> bool:
        """布林带下轨突破判断。
        
        当价格下穿布林带下轨时触发。
        
        Args:
            index: 当前K线索引
        
        Returns:
            是否发生下轨突破
        """
        bbl_col, _ = self._get_boll_columns()
        
        if bbl_col is None or "close" not in self.data.columns:
            logger.warning("未找到 BOLL 下轨或收盘价列")
            return False
        
        return self._crossunder(self.data["close"], self.data[bbl_col], index)

    
    def register_indicator(self, indicator: "CustomIndicator") -> None:
        """注册自定义指标。
        
        注册指标后会立即计算指标值并添加到 DataFrame 中，
        以便策略可以通过 get_indicator(name) 获取当前 bar 的指标值。
        
        Args:
            indicator: 自定义指标对象
        """
        self.custom_indicators[indicator.name.lower()] = indicator
        
        # 计算指标值并添加到 DataFrame
        try:
            result = indicator.calculate(self.data)
            # 使用指标名称作为列名添加到 DataFrame
            self.data[indicator.name.lower()] = result
            logger.info(f"注册自定义指标: {indicator.name}，已计算并添加到数据中")
        except Exception as e:
            # 计算失败时记录错误，并添加 NaN 列
            logger.error(f"计算自定义指标 {indicator.name} 失败: {e}")
            self.data[indicator.name.lower()] = pd.Series(
                [float("nan")] * len(self.data), index=self.data.index
            )
    
    def evaluate_condition(self, condition: str, index: int) -> bool:
        """评估信号条件表达式。
        
        支持的条件格式：
        - macd_golden_cross / macd_death_cross
        - macd_golden_cross_strong / macd_death_cross_strong
        - kdj_golden_cross / kdj_death_cross
        - rsi_overbought / rsi_oversold
        - boll_upper_break / boll_lower_break
        - 自定义指标名称
        
        Args:
            condition: 条件表达式字符串
            index: 当前K线索引
        
        Returns:
            条件是否满足
        """
        condition = condition.lower().strip()
        
        # 内置信号映射
        signal_map = {
            "macd_golden_cross": self.macd_golden_cross,
            "macd_death_cross": self.macd_death_cross,
            "macd_golden_cross_strong": self.macd_golden_cross_strong,
            "macd_death_cross_strong": self.macd_death_cross_strong,
            "kdj_golden_cross": self.kdj_golden_cross,
            "kdj_death_cross": self.kdj_death_cross,
            "rsi_overbought": self.rsi_overbought,
            "rsi_oversold": self.rsi_oversold,
            "boll_upper_break": self.boll_upper_break,
            "boll_lower_break": self.boll_lower_break,
        }
        
        if condition in signal_map:
            return signal_map[condition](index)
        
        # 检查自定义指标
        if condition in self.custom_indicators:
            indicator = self.custom_indicators[condition]
            try:
                result = indicator.calculate(self.data)
                if index < len(result):
                    value = result.iloc[index]
                    # 如果是布尔值直接返回，否则返回是否为真值
                    if isinstance(value, (bool, np.bool_)):
                        return bool(value)
                    return bool(value) if not pd.isna(value) else False
            except Exception as e:
                logger.error(f"自定义指标 {condition} 计算失败: {e}")
                return False
        
        logger.warning(f"未知的信号条件: {condition}")
        return False
    
    def calculate_all_custom_indicators(self) -> None:
        """计算所有已注册的自定义指标。
        
        在回测开始时调用，确保所有自定义指标的值都已计算并添加到 DataFrame 中。
        """
        for name, indicator in self.custom_indicators.items():
            try:
                result = indicator.calculate(self.data)
                self.data[name] = result
                logger.debug(f"计算自定义指标: {name}")
            except Exception as e:
                logger.error(f"计算自定义指标 {name} 失败: {e}")
                self.data[name] = pd.Series(
                    [float("nan")] * len(self.data), index=self.data.index
                )
    
    def get_indicator_value(self, name: str, index: int) -> Optional[float]:
        """获取指定指标在指定索引处的值。
        
        Args:
            name: 指标名称
            index: K线索引
        
        Returns:
            指标值，如果不存在或为NaN则返回None
        """
        name_lower = name.lower()
        
        # 首先检查是否在 DataFrame 列中
        if name_lower in self.data.columns:
            value = self.data[name_lower].iloc[index]
            return None if pd.isna(value) else float(value)
        
        # 检查原始列名（可能是大写或混合大小写）
        for col in self.data.columns:
            if col.lower() == name_lower:
                value = self.data[col].iloc[index]
                return None if pd.isna(value) else float(value)
        
        # 检查自定义指标
        if name_lower in self.custom_indicators:
            indicator = self.custom_indicators[name_lower]
            try:
                result = indicator.calculate(self.data)
                if index < len(result):
                    value = result.iloc[index]
                    return None if pd.isna(value) else float(value)
            except Exception as e:
                logger.error(f"获取自定义指标 {name} 值失败: {e}")
        
        return None


__all__ = ["SignalCalculator", "SignalType"]
