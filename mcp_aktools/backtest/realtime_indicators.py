"""实时指标计算模块。

该模块实现"历史收盘价 + 当天开盘价"的实时指标计算逻辑，
用于模拟富途等平台的开盘价交易模式。

在开盘价模式下：
- 信号判断时机：当天开盘价出来后（9:25集合竞价结束）
- 执行时机：立刻以开盘价执行
- 技术指标计算：使用"历史收盘价 + 当天开盘价"实时计算
"""

import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
except ImportError:
    ta = None

logger = logging.getLogger(__name__)

# 实时指标滑动窗口：仅用最近 N 根历史计算。EMA/RSI/KDJ/BOLL 权重随距离快速衰减，
# 窗口足够大时与全量计算数值等价，从而把每根K线的 O(n) 重算降为常数窗口（总体 ~O(n)）
_REALTIME_WINDOW = 300


def calc_ema(series: pd.Series, span: int) -> pd.Series:
    """计算EMA（指数移动平均）。
    
    Args:
        series: 价格序列
        span: EMA周期
        
    Returns:
        EMA序列
    """
    return series.ewm(span=span, adjust=False).mean()


def calc_macd_realtime(
    history_close: np.ndarray,
    current_open: float,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> Tuple[float, float, float, float]:
    """用历史收盘价 + 当天开盘价实时计算MACD。
    
    Args:
        history_close: 历史收盘价数组
        current_open: 当天开盘价
        fast: 快线周期，默认12
        slow: 慢线周期，默认26
        signal: 信号线周期，默认9
        
    Returns:
        (当前DIF, 当前DEA, 前一天DIF, 前一天DEA)
    """
    # 将当天开盘价追加到历史收盘价
    close_with_open = np.append(history_close, current_open)
    close_series = pd.Series(close_with_open)
    
    # 计算EMA
    ema_fast = calc_ema(close_series, fast)
    ema_slow = calc_ema(close_series, slow)
    
    # 计算DIF和DEA
    dif_line = ema_fast - ema_slow
    dea_line = calc_ema(dif_line, signal)
    
    # 返回当前和前一天的值
    curr_dif = float(dif_line.iloc[-1])
    curr_dea = float(dea_line.iloc[-1])
    prev_dif = float(dif_line.iloc[-2]) if len(dif_line) > 1 else curr_dif
    prev_dea = float(dea_line.iloc[-2]) if len(dea_line) > 1 else curr_dea
    
    return curr_dif, curr_dea, prev_dif, prev_dea


def calc_kdj_realtime(
    history_high: np.ndarray,
    history_low: np.ndarray,
    history_close: np.ndarray,
    current_open: float,
    period: int = 9,
    k_smooth: int = 3,
    d_smooth: int = 3
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """用历史数据 + 当天开盘价实时计算KDJ。
    
    在开盘时，当天的high=low=close=open（因为只有开盘价）。
    
    Args:
        history_high: 历史最高价数组
        history_low: 历史最低价数组
        history_close: 历史收盘价数组
        current_open: 当天开盘价
        period: RSV计算周期，默认9
        k_smooth: K值平滑周期，默认3
        d_smooth: D值平滑周期，默认3
        
    Returns:
        (K, D, J) 或 (None, None, None) 如果数据不足
    """
    if ta is None:
        logger.warning("pandas-ta 未安装，无法计算实时KDJ")
        return None, None, None
    
    # 构建临时DataFrame，添加当天开盘价作为新的一行
    # 开盘时 high=low=close=open
    temp_df = pd.DataFrame({
        'high': np.append(history_high, current_open),
        'low': np.append(history_low, current_open),
        'close': np.append(history_close, current_open)
    })
    
    # 使用 pandas-ta 计算KDJ
    kdj_result = ta.kdj(
        temp_df['high'], 
        temp_df['low'], 
        temp_df['close'],
        length=period,
        signal=k_smooth
    )
    
    if kdj_result is None or len(kdj_result) == 0:
        return None, None, None
    
    # 获取最后一行的KDJ值
    k_col = f"K_{period}_{k_smooth}"
    d_col = f"D_{period}_{k_smooth}"
    j_col = f"J_{period}_{k_smooth}"
    
    k = float(kdj_result[k_col].iloc[-1]) if k_col in kdj_result.columns else None
    d = float(kdj_result[d_col].iloc[-1]) if d_col in kdj_result.columns else None
    j = float(kdj_result[j_col].iloc[-1]) if j_col in kdj_result.columns else None
    
    return k, d, j


def calc_rsi_realtime(
    history_close: np.ndarray,
    current_open: float,
    period: int = 14
) -> Optional[float]:
    """用历史收盘价 + 当天开盘价实时计算RSI。
    
    Args:
        history_close: 历史收盘价数组
        current_open: 当天开盘价
        period: RSI周期，默认14
        
    Returns:
        RSI值，如果数据不足返回None
    """
    if ta is None:
        logger.warning("pandas-ta 未安装，无法计算实时RSI")
        return None
    
    # 将当天开盘价追加到历史收盘价
    close_with_open = np.append(history_close, current_open)
    close_series = pd.Series(close_with_open)
    
    rsi = ta.rsi(close_series, length=period)
    
    if rsi is None or len(rsi) == 0:
        return None
    
    return float(rsi.iloc[-1])


def calc_boll_realtime(
    history_close: np.ndarray,
    current_open: float,
    period: int = 20,
    std_dev: float = 2.0
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """用历史收盘价 + 当天开盘价实时计算布林带。
    
    Args:
        history_close: 历史收盘价数组
        current_open: 当天开盘价
        period: 移动平均周期，默认20
        std_dev: 标准差倍数，默认2.0
        
    Returns:
        (上轨, 中轨, 下轨) 或 (None, None, None) 如果数据不足
    """
    if ta is None:
        logger.warning("pandas-ta 未安装，无法计算实时BOLL")
        return None, None, None
    
    # 将当天开盘价追加到历史收盘价
    close_with_open = np.append(history_close, current_open)
    close_series = pd.Series(close_with_open)
    
    bbands = ta.bbands(close_series, length=period, std=std_dev)
    
    if bbands is None or len(bbands) == 0:
        return None, None, None
    
    # 获取最后一行的布林带值
    bbl_col = f"BBL_{period}_{std_dev}_{std_dev}"
    bbm_col = f"BBM_{period}_{std_dev}_{std_dev}"
    bbu_col = f"BBU_{period}_{std_dev}_{std_dev}"
    
    upper = float(bbands[bbu_col].iloc[-1]) if bbu_col in bbands.columns else None
    middle = float(bbands[bbm_col].iloc[-1]) if bbm_col in bbands.columns else None
    lower = float(bbands[bbl_col].iloc[-1]) if bbl_col in bbands.columns else None
    
    return upper, middle, lower


class RealtimeIndicatorCalculator:
    """实时指标计算器。
    
    在每根K线开盘时，使用"历史收盘价 + 当天开盘价"计算技术指标。
    """
    
    def __init__(self, data: pd.DataFrame):
        """初始化实时指标计算器。
        
        Args:
            data: 完整的K线数据DataFrame
        """
        self.data = data
        self._cache = {}  # 缓存计算结果
    
    def calculate_at_open(self, bar_index: int) -> dict:
        """在指定K线开盘时计算所有实时指标。
        
        Args:
            bar_index: 当前K线索引
            
        Returns:
            包含所有实时指标的字典
        """
        # 检查缓存
        if bar_index in self._cache:
            return self._cache[bar_index]
        
        if bar_index < 1:
            return {}

        # 获取历史数据和当天开盘价
        # 仅取最近 _REALTIME_WINDOW 根历史，避免每根K线复制全量历史（O(n²)→~O(n)）
        start = max(0, bar_index - _REALTIME_WINDOW)
        history = self.data.iloc[start:bar_index]
        current_bar = self.data.iloc[bar_index]
        current_open = float(current_bar['open'])
        
        history_close = history['close'].values
        history_high = history['high'].values
        history_low = history['low'].values
        
        indicators = {}
        
        # 计算实时MACD
        try:
            dif, dea, prev_dif, prev_dea = calc_macd_realtime(
                history_close, current_open
            )
            indicators['DIF'] = dif
            indicators['DEA'] = dea
            indicators['MACD'] = (dif - dea) * 2
            indicators['_prev_DIF'] = prev_dif
            indicators['_prev_DEA'] = prev_dea
        except Exception as e:
            logger.debug(f"实时MACD计算失败: {e}")
        
        # 计算实时KDJ
        try:
            k, d, j = calc_kdj_realtime(
                history_high, history_low, history_close, current_open
            )
            if k is not None:
                indicators['K'] = k
                indicators['KDJ.K'] = k
            if d is not None:
                indicators['D'] = d
                indicators['KDJ.D'] = d
            if j is not None:
                indicators['J'] = j
                indicators['KDJ.J'] = j
        except Exception as e:
            logger.debug(f"实时KDJ计算失败: {e}")
        
        # 计算实时RSI
        try:
            rsi = calc_rsi_realtime(history_close, current_open)
            if rsi is not None:
                indicators['RSI'] = rsi
        except Exception as e:
            logger.debug(f"实时RSI计算失败: {e}")
        
        # 计算实时BOLL
        try:
            upper, middle, lower = calc_boll_realtime(history_close, current_open)
            if upper is not None:
                indicators['BOLL.U'] = upper
                indicators['BBU'] = upper
            if middle is not None:
                indicators['BOLL.M'] = middle
                indicators['BBM'] = middle
            if lower is not None:
                indicators['BOLL.L'] = lower
                indicators['BBL'] = lower
        except Exception as e:
            logger.debug(f"实时BOLL计算失败: {e}")
        
        # 缓存结果
        self._cache[bar_index] = indicators
        
        return indicators
    
    def get_indicator(self, bar_index: int, name: str) -> Optional[float]:
        """获取指定K线的实时指标值。
        
        Args:
            bar_index: K线索引
            name: 指标名称
            
        Returns:
            指标值，如果不存在返回None
        """
        indicators = self.calculate_at_open(bar_index)
        
        # 处理点分名称格式（如 KDJ.J -> J）
        if name in indicators:
            return indicators[name]
        
        # 尝试简化名称
        if "." in name:
            simple_name = name.split(".")[-1]
            if simple_name in indicators:
                return indicators[simple_name]
        
        return None
    
    def check_macd_death_cross_strong(
        self, 
        bar_index: int, 
        dif_threshold: float = 10.0,
        rapid_decline_threshold: float = 0.7,
        near_zero_threshold: float = 2.0
    ) -> bool:
        """检查实时MACD强死叉信号（自适应版本）。
        
        强死叉条件：
        1. DIF下穿DEA（普通死叉）
        2. 且满足以下任一条件：
           - DIF从正变负（跨越零轴）
           - 或者前一天DIF > dif_threshold（高位死叉）
           - 或者DIF在零轴附近快速下降（零轴附近死叉）
        
        零轴附近快速下降的定义：
        - |前一天DIF| < near_zero_threshold（在零轴附近）
        - 且 DIF下降幅度 > rapid_decline_threshold（快速下降）
        
        Args:
            bar_index: K线索引
            dif_threshold: 高位DIF阈值，默认10.0
            rapid_decline_threshold: 快速下降阈值，默认0.7
            near_zero_threshold: 零轴附近阈值，默认2.0
            
        Returns:
            是否触发强死叉
        """
        indicators = self.calculate_at_open(bar_index)
        
        dif = indicators.get('DIF')
        dea = indicators.get('DEA')
        prev_dif = indicators.get('_prev_DIF')
        prev_dea = indicators.get('_prev_DEA')
        
        if any(v is None for v in [dif, dea, prev_dif, prev_dea]):
            return False
        
        # 检查是否是死叉
        is_death_cross = (prev_dif >= prev_dea) and (dif < dea)
        if not is_death_cross:
            return False
        
        # 条件1: DIF从正变负（跨越零轴）
        cross_zero = (prev_dif > 0) and (dif < 0)
        
        # 条件2: 高位死叉（前一天DIF > 阈值）
        high_dif = prev_dif > dif_threshold
        
        # 条件3: 零轴附近快速下降
        # - DIF在零轴附近（|DIF| < 2）
        # - 且DIF快速下降（下降幅度 > 0.7）
        near_zero = abs(prev_dif) < near_zero_threshold
        rapid_decline = (prev_dif - dif) > rapid_decline_threshold
        near_zero_rapid = near_zero and rapid_decline

        return bool(cross_zero or high_dif or near_zero_rapid)
    
    def check_macd_golden_cross_strong(
        self, 
        bar_index: int, 
        dif_threshold: float = -10.0,
        rapid_rise_threshold: float = 0.7,
        near_zero_threshold: float = 2.0
    ) -> bool:
        """检查实时MACD强金叉信号（自适应版本）。
        
        强金叉条件：
        1. DIF上穿DEA（普通金叉）
        2. 且满足以下任一条件：
           - DIF从负变正（跨越零轴）
           - 或者前一天DIF < dif_threshold（低位金叉）
           - 或者DIF在零轴附近快速上升（零轴附近金叉）
        
        零轴附近快速上升的定义：
        - |前一天DIF| < near_zero_threshold（在零轴附近）
        - 且 DIF上升幅度 > rapid_rise_threshold（快速上升）
        
        Args:
            bar_index: K线索引
            dif_threshold: 低位DIF阈值，默认-10.0
            rapid_rise_threshold: 快速上升阈值，默认0.7
            near_zero_threshold: 零轴附近阈值，默认2.0
            
        Returns:
            是否触发强金叉
        """
        indicators = self.calculate_at_open(bar_index)
        
        dif = indicators.get('DIF')
        dea = indicators.get('DEA')
        prev_dif = indicators.get('_prev_DIF')
        prev_dea = indicators.get('_prev_DEA')
        
        if any(v is None for v in [dif, dea, prev_dif, prev_dea]):
            return False
        
        # 检查是否是金叉
        is_golden_cross = (prev_dif <= prev_dea) and (dif > dea)
        if not is_golden_cross:
            return False
        
        # 条件1: DIF从负变正（跨越零轴）
        cross_zero = (prev_dif < 0) and (dif > 0)
        
        # 条件2: 低位金叉（前一天DIF < 阈值）
        low_dif = prev_dif < dif_threshold
        
        # 条件3: 零轴附近快速上升
        # - DIF在零轴附近（|DIF| < 2）
        # - 且DIF快速上升（上升幅度 > 0.7）
        near_zero = abs(prev_dif) < near_zero_threshold
        rapid_rise = (dif - prev_dif) > rapid_rise_threshold
        near_zero_rapid = near_zero and rapid_rise

        return bool(cross_zero or low_dif or near_zero_rapid)
    
    def clear_cache(self) -> None:
        """清空缓存。"""
        self._cache.clear()


__all__ = [
    'RealtimeIndicatorCalculator',
    'calc_macd_realtime',
    'calc_kdj_realtime',
    'calc_rsi_realtime',
    'calc_boll_realtime',
]
