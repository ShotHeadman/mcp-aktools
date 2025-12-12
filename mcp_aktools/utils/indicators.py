"""
技术指标计算模块

提供常用的技术指标计算功能，包括 MACD、KDJ、RSI、BOLL、MA均线系统等。
所有函数都包含完整的类型注解。

所有指标均使用 pandas-ta 专业库计算，确保与主流行情软件保持一致：
- MACD: 使用 ta.macd() 计算
- KDJ: 使用 ta.kdj() 计算，采用传统中国市场算法
- RSI: 使用 ta.rsi() 计算
- BOLL: 使用 ta.bbands() 计算布林带
- MA: 使用 ta.sma() 计算简单移动平均线
"""

from typing import List, Optional, Tuple

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:
    ta = None


def calculate_macd(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    计算 MACD (Moving Average Convergence Divergence) 指标

    使用 pandas-ta 库计算标准 MACD 指标。
    MACD 是一种趋势跟踪动量指标，通过计算两个指数移动平均线的差值来显示价格趋势的变化。

    注意：需要至少 slow_period 个数据点才能计算 MACD。

    Args:
        close: 收盘价序列（建议至少 35+ 个数据点以获得有效结果）
        fast_period: 快速 EMA 周期，默认 12
        slow_period: 慢速 EMA 周期，默认 26
        signal_period: 信号线 EMA 周期，默认 9

    Returns:
        Tuple[pd.Series, pd.Series, pd.Series]: (DIF, DEA, MACD)
        - DIF: 快线，快速 EMA 与慢速 EMA 的差值 (MACD line)
        - DEA: 慢线/信号线，DIF 的 EMA (Signal line)
        - MACD: MACD 柱状图，(DIF - DEA) * 2 (Histogram)

    Example:
        >>> close = pd.Series(range(100, 150))  # 至少 50 个数据点
        >>> dif, dea, macd = calculate_macd(close)
    """
    if ta is None:
        raise ImportError(
            "pandas-ta is required. Install it with: pip install pandas-ta"
        )

    # 使用 pandas-ta 计算 MACD
    macd_result = ta.macd(
        close, fast=fast_period, slow=slow_period, signal=signal_period
    )

    if macd_result is None:
        # 如果数据不足，返回空的 Series
        empty_series = pd.Series([None] * len(close), index=close.index)
        return empty_series, empty_series, empty_series

    # pandas-ta 返回的列名格式: MACD_fast_slow_signal, MACDh_fast_slow_signal, MACDs_fast_slow_signal
    dif = macd_result[
        f"MACD_{fast_period}_{slow_period}_{signal_period}"
    ]  # MACD line (DIF)
    dea = macd_result[
        f"MACDs_{fast_period}_{slow_period}_{signal_period}"
    ]  # Signal line (DEA)
    histogram = macd_result[
        f"MACDh_{fast_period}_{slow_period}_{signal_period}"
    ]  # Histogram

    # 中国市场习惯使用 (DIF - DEA) * 2 作为 MACD 柱状图
    macd = histogram * 2

    return dif, dea, macd


def calculate_kdj(
    close: pd.Series,
    low: pd.Series,
    high: pd.Series,
    period: int = 9,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    计算 KDJ (Stochastic Oscillator) 指标

    使用 pandas-ta 库的 kdj 函数计算，采用传统的中国市场KDJ计算方法。
    KDJ 是一种随机指标，用于衡量价格在一定周期内的相对位置，帮助判断超买超卖状态。

    注意：需要至少 period 个数据点才能计算 KDJ。

    Args:
        close: 收盘价序列（建议至少 15+ 个数据点）
        low: 最低价序列
        high: 最高价序列
        period: RSV 计算周期，默认 9
        k_smooth: K 值平滑周期，默认 3
        d_smooth: D 值平滑周期，默认 3

    Returns:
        Tuple[pd.Series, pd.Series, pd.Series]: (K, D, J)
        - K: K 值，RSV 的平滑移动平均
        - D: D 值，K 值的平滑移动平均
        - J: J 值，3*K - 2*D

    Example:
        >>> close = pd.Series(range(100, 120))
        >>> low = pd.Series(range(99, 119))
        >>> high = pd.Series(range(101, 121))
        >>> k, d, j = calculate_kdj(close, low, high)
    """
    if ta is None:
        raise ImportError(
            "pandas-ta is required. Install it with: pip install pandas-ta"
        )

    # 使用 pandas-ta 的 kdj 函数计算
    # 注意：kdj 函数的 signal 参数对应 k_smooth（K值平滑周期）
    kdj_result = ta.kdj(high, low, close, length=period, signal=k_smooth)

    if kdj_result is None:
        # 如果数据不足，返回空的 Series
        empty_series = pd.Series([None] * len(close), index=close.index)
        return empty_series, empty_series, empty_series

    # pandas-ta 返回的列名格式: K_period_signal, D_period_signal, J_period_signal
    k = kdj_result[f"K_{period}_{k_smooth}"]
    d = kdj_result[f"D_{period}_{k_smooth}"]
    j = kdj_result[f"J_{period}_{k_smooth}"]

    return k, d, j


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    计算 RSI (Relative Strength Index) 指标

    使用 pandas-ta 库计算标准 RSI 指标。
    RSI 是一种动量振荡器，衡量价格变动的速度和幅度，用于识别超买超卖状态。

    注意：需要至少 period + 1 个数据点才能计算 RSI。

    Args:
        close: 收盘价序列（建议至少 20+ 个数据点）
        period: 计算周期，默认 14

    Returns:
        pd.Series: RSI 值，范围 0-100
        - RSI > 70 通常被认为超买
        - RSI < 30 通常被认为超卖

    Example:
        >>> close = pd.Series(range(100, 125))
        >>> rsi = calculate_rsi(close)
    """
    if ta is None:
        raise ImportError(
            "pandas-ta is required. Install it with: pip install pandas-ta"
        )

    # 使用 pandas-ta 计算 RSI
    rsi = ta.rsi(close, length=period)

    if rsi is None:
        # 如果数据不足，返回空的 Series
        return pd.Series([None] * len(close), index=close.index)

    return rsi


def calculate_boll(
    close: pd.Series, period: int = 20, std_dev: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    计算 BOLL (Bollinger Bands) 指标

    使用 pandas-ta 库计算标准布林带指标。
    布林带是一种波动率指标，由中轨（移动平均线）和上下轨（标准差带）组成，
    用于衡量价格的波动范围和相对高低位置。

    注意：需要至少 period 个数据点才能计算布林带。

    Args:
        close: 收盘价序列（建议至少 25+ 个数据点）
        period: 移动平均周期，默认 20
        std_dev: 标准差倍数，默认 2.0

    Returns:
        Tuple[pd.Series, pd.Series, pd.Series]: (upper, middle, lower)
        - upper: 上轨 = 中轨 + std_dev * 标准差
        - middle: 中轨 = 简单移动平均线
        - lower: 下轨 = 中轨 - std_dev * 标准差

    Example:
        >>> close = pd.Series(range(100, 130))
        >>> upper, middle, lower = calculate_boll(close)
    """
    if ta is None:
        raise ImportError(
            "pandas-ta is required. Install it with: pip install pandas-ta"
        )

    # 使用 pandas-ta 计算 Bollinger Bands
    bbands_result = ta.bbands(close, length=period, std=std_dev)

    if bbands_result is None:
        # 如果数据不足，返回空的 Series
        empty_series = pd.Series([None] * len(close), index=close.index)
        return empty_series, empty_series, empty_series

    # pandas-ta 返回的列名格式: BBL_period_std_std, BBM_period_std_std, BBU_period_std_std
    # 注意：pandas-ta 的 bbands 函数会重复 std 参数
    lower = bbands_result[f"BBL_{period}_{std_dev}_{std_dev}"]
    middle = bbands_result[f"BBM_{period}_{std_dev}_{std_dev}"]
    upper = bbands_result[f"BBU_{period}_{std_dev}_{std_dev}"]

    return upper, middle, lower


def calculate_ma(close: pd.Series, period: int) -> pd.Series:
    """
    计算简单移动平均线 (Simple Moving Average)

    使用 pandas-ta 库计算 SMA 指标。

    Args:
        close: 收盘价序列
        period: 移动平均周期

    Returns:
        pd.Series: MA 值
    """
    if ta is None:
        raise ImportError(
            "pandas-ta is required. Install it with: pip install pandas-ta"
        )

    sma = ta.sma(close, length=period)
    if sma is None:
        return pd.Series([None] * len(close), index=close.index)
    return sma


def add_technical_indicators(
    df: pd.DataFrame,
    close: pd.Series,
    low: pd.Series,
    high: pd.Series,
    macd_params: Tuple[int, int, int] = (12, 26, 9),
    kdj_params: Tuple[int, int, int] = (9, 3, 3),
    rsi_period: int = 14,
    boll_params: Tuple[int, float] = (20, 2.0),
    ma_periods: Optional[List[int]] = None,
    include_trend: bool = False,
) -> None:
    """
    向 DataFrame 添加所有技术指标

    这是一个便捷函数，一次性计算并添加 MACD、KDJ、RSI、BOLL 指标到 DataFrame。
    所有指标都会作为新列添加到原 DataFrame 中（原地修改）。
    使用 pandas-ta 专业库进行计算，确保指标的准确性和标准性。

    Args:
        df: 目标 DataFrame，将在此 DataFrame 中添加指标列
        close: 收盘价序列
        low: 最低价序列
        high: 最高价序列
        macd_params: MACD 参数 (fast_period, slow_period, signal_period)，默认 (12, 26, 9)
        kdj_params: KDJ 参数 (period, k_smooth, d_smooth)，默认 (9, 3, 3)
        rsi_period: RSI 周期，默认 14
        boll_params: BOLL 参数 (period, std_dev)，默认 (20, 2.0)
        ma_periods: 均线周期列表，传入后会计算对应周期的MA均线
            - 常用均线: [5, 10, 20, 30, 60, 120, 250]
            - 斐波那契均线: [5, 8, 13, 21, 34, 55, 89, 144, 233]
        include_trend: 是否包含趋势指标 (ADX, 线性回归)，默认 False

    Returns:
        None: 函数直接修改传入的 DataFrame

    添加的列：
        - DIF, DEA, MACD: MACD 指标
        - KDJ.K, KDJ.D, KDJ.J: KDJ 指标
        - RSI: RSI 指标
        - BOLL.U, BOLL.M, BOLL.L: 布林带指标（上轨、中轨、下轨）
        - MA{period}: 均线（根据 ma_periods 参数）
        - ADX, DI+, DI-, TREND: 趋势指标（可选）
        - LINREG, SLOPE: 线性回归指标（可选）

    Example:
        >>> df = pd.DataFrame({
        ...     '收盘': [100, 102, 101, 103, 105],
        ...     '最低': [99, 100, 99, 101, 103],
        ...     '最高': [101, 103, 102, 104, 106]
        ... })
        >>> add_technical_indicators(df, df['收盘'], df['最低'], df['最高'])
        >>> print(df.columns)
    """
    # 计算 MACD 指标
    dif, dea, macd = calculate_macd(close, *macd_params)
    df["DIF"] = dif
    df["DEA"] = dea
    df["MACD"] = macd

    # 计算 KDJ 指标
    k, d, j = calculate_kdj(close, low, high, *kdj_params)
    df["KDJ.K"] = k
    df["KDJ.D"] = d
    df["KDJ.J"] = j

    # 计算 RSI 指标
    rsi = calculate_rsi(close, rsi_period)
    df["RSI"] = rsi

    # 计算布林带指标
    upper, middle, lower = calculate_boll(close, *boll_params)
    df["BOLL.U"] = upper
    df["BOLL.M"] = middle
    df["BOLL.L"] = lower

    # === 均线系统 ===
    if ma_periods:
        for p in ma_periods:
            ma = calculate_ma(close, p)
            df[f"MA{p}"] = ma

    # === 趋势指标 ===
    if include_trend:
        # 创建临时 DataFrame 用于 pandas-ta 计算
        temp_df = pd.DataFrame({
            'open': close,
            'high': high,
            'low': low,
            'close': close,
        }, index=df.index)

        # ADX
        adx_result = temp_df.ta.adx(length=14)
        if adx_result is not None:
            df["ADX"] = adx_result.iloc[:, 0]
            df["DI+"] = adx_result.iloc[:, 1]
            df["DI-"] = adx_result.iloc[:, 2]
            # 趋势方向: ADX>25 且 DI+>DI- 为上升，反之为下降
            df["TREND"] = 0
            strong = adx_result.iloc[:, 0] > 25
            df.loc[strong & (adx_result.iloc[:, 1] > adx_result.iloc[:, 2]), "TREND"] = 1
            df.loc[strong & (adx_result.iloc[:, 2] > adx_result.iloc[:, 1]), "TREND"] = -1

        # 线性回归
        linreg = ta.linreg(close, length=14)
        if linreg is not None:
            df["LINREG"] = linreg

        slope = ta.linreg(close, length=14, slope=True)
        if slope is not None:
            df["SLOPE"] = slope
