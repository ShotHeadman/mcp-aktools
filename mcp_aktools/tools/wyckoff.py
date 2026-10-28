"""现代威科夫量价与流动性(SMC)结构分析工具模块。

理论体系与使用指南详见: trading/wyckoff_trading_guide.md
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import Field

from mcp_aktools.clients.akshare import AkShareClient
from mcp_aktools.config import get_settings
from mcp_aktools.logging_config import get_logger
from mcp_aktools.utils.validators import Validators

logger = get_logger("tools.wyckoff")

field_symbol = Field(description="股票代码")
field_market = Field(
    "sh",
    description="股票市场代码，sh 表示上海证券交易所，sz 表示深圳证券交易所，hk 表示香港证券交易所，us 表示美国证券交易所",
)

def get_akshare_client() -> AkShareClient:
    """获取 AkShare 客户端单例（复用 stock 模块的客户端实例）。"""
    from .stock import get_akshare_client as _get_client
    return _get_client()


def find_swing_levels(
    high_s: pd.Series,
    low_s: pd.Series,
    dates_s: pd.Series,
    w: int = 3,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """寻找波段高低点流动性池 (Swing Highs / Swing Lows)。"""
    n = len(high_s)
    swing_highs: List[Dict[str, Any]] = []
    swing_lows: List[Dict[str, Any]] = []

    for i in range(w, n - w):
        h_val = float(high_s.iloc[i])
        l_val = float(low_s.iloc[i])
        if h_val == float(high_s.iloc[i - w : i + w + 1].max()):
            swing_highs.append({
                "index": i,
                "date": str(dates_s.iloc[i]),
                "price": round(h_val, 3),
            })
        if l_val == float(low_s.iloc[i - w : i + w + 1].min()):
            swing_lows.append({
                "index": i,
                "date": str(dates_s.iloc[i]),
                "price": round(l_val, 3),
            })

    return swing_highs, swing_lows


def detect_effort_vs_result(
    open_s: pd.Series,
    high_s: pd.Series,
    low_s: pd.Series,
    close_s: pd.Series,
    vol_s: pd.Series,
    dates_s: pd.Series,
    vol_ma20: pd.Series,
    recent_bars: int = 10,
) -> List[str]:
    """检测供求与努力结果异常 (Stopping Volume / 放量滞涨)。"""
    n = len(close_s)
    anomalies: List[str] = []

    for i in range(max(1, n - recent_bars), n):
        bar_range = float(high_s.iloc[i] - low_s.iloc[i])
        if bar_range <= 0:
            continue
        lower_wick = float(min(open_s.iloc[i], close_s.iloc[i]) - low_s.iloc[i])
        upper_wick = float(high_s.iloc[i] - max(open_s.iloc[i], close_s.iloc[i]))
        body = abs(float(close_s.iloc[i] - open_s.iloc[i]))
        v_ratio = float(vol_s.iloc[i] / vol_ma20.iloc[i]) if float(vol_ma20.iloc[i] or 0) > 0 else 1.0
        d_str = str(dates_s.iloc[i])

        if lower_wick / bar_range >= 0.45 and v_ratio >= 1.3:
            anomalies.append(
                f"{d_str}: 长下影放量吸收 (Stopping Volume, 量比{v_ratio:.2f}, 下影占比{lower_wick/bar_range:.0%})"
            )
        elif v_ratio >= 1.8 and (body / bar_range <= 0.25 or upper_wick / bar_range >= 0.45):
            anomalies.append(
                f"{d_str}: 放量滞涨/上影派发 (量比{v_ratio:.2f}, 实体占比{body/bar_range:.0%})"
            )

    return anomalies


def detect_liquidity_sweep_and_mss(
    swing_highs: List[Dict[str, Any]],
    swing_lows: List[Dict[str, Any]],
    open_s: pd.Series,
    high_s: pd.Series,
    low_s: pd.Series,
    close_s: pd.Series,
    vol_s: pd.Series,
    dates_s: pd.Series,
    vol_ma20: pd.Series,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """检测流动性掠夺 (Sweep / Spring) 与结构转变 (MSS)。"""
    n = len(close_s)
    sweep_event = None

    if len(swing_lows) >= 1:
        prior_lows = [sl for sl in swing_lows if sl["index"] < n - 2]
        if prior_lows:
            ref_low = min(prior_lows[-3:], key=lambda x: x["price"])
            ref_price = ref_low["price"]
            for i in range(max(ref_low["index"] + 1, n - 12), n):
                if float(low_s.iloc[i]) < ref_price and float(close_s.iloc[i]) >= ref_price * 0.998:
                    sweep_event = {
                        "type": "Bullish Sweep (Spring 诱空收回)",
                        "date": str(dates_s.iloc[i]),
                        "swept_level": ref_price,
                        "sweep_low": round(float(low_s.iloc[i]), 3),
                        "close_price": round(float(close_s.iloc[i]), 3),
                    }

    mss_event = None
    if swing_highs:
        prior_highs = [sh for sh in swing_highs if sh["index"] < n - 1]
        if prior_highs:
            ref_high = prior_highs[-1]
            for i in range(max(ref_high["index"] + 1, n - 10), n):
                v_ratio = float(vol_s.iloc[i] / vol_ma20.iloc[i]) if float(vol_ma20.iloc[i] or 0) > 0 else 1.0
                if float(close_s.iloc[i]) > ref_high["price"] and float(close_s.iloc[i]) > float(open_s.iloc[i]):
                    mss_event = {
                        "type": "Bullish MSS (向上结构转变)",
                        "date": str(dates_s.iloc[i]),
                        "broken_level": ref_high["price"],
                        "breakout_close": round(float(close_s.iloc[i]), 3),
                        "volume_ratio": round(v_ratio, 2),
                    }
                    break

    return sweep_event, mss_event


def detect_fair_value_gaps(
    high_s: pd.Series,
    low_s: pd.Series,
    dates_s: pd.Series,
    recent_bars: int = 30,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """检测未回补失衡区 (Bullish / Bearish FVG)。"""
    n = len(high_s)
    bullish_fvgs: List[Dict[str, Any]] = []
    bearish_fvgs: List[Dict[str, Any]] = []

    for i in range(max(2, n - recent_bars), n):
        # Bullish FVG: bar[i-2].high < bar[i].low
        gap_low = float(high_s.iloc[i - 2])
        gap_high = float(low_s.iloc[i])
        if gap_high > gap_low * 1.002:
            subsequent_min = float(low_s.iloc[i + 1 :].min()) if i + 1 < n else gap_high
            if subsequent_min > gap_low:
                bullish_fvgs.append({
                    "date": str(dates_s.iloc[i - 1]),
                    "upper": round(gap_high, 3),
                    "ce_50pct": round((gap_high + gap_low) / 2, 3),
                    "lower": round(gap_low, 3),
                })

        # Bearish FVG: bar[i-2].low > bar[i].high
        b_gap_high = float(low_s.iloc[i - 2])
        b_gap_low = float(high_s.iloc[i])
        if b_gap_high > b_gap_low * 1.002:
            subsequent_max = float(high_s.iloc[i + 1 :].max()) if i + 1 < n else b_gap_low
            if subsequent_max < b_gap_high:
                bearish_fvgs.append({
                    "date": str(dates_s.iloc[i - 1]),
                    "upper": round(b_gap_high, 3),
                    "ce_50pct": round((b_gap_high + b_gap_low) / 2, 3),
                    "lower": round(b_gap_low, 3),
                })

    return bullish_fvgs, bearish_fvgs


def calculate_volume_profile(
    low_s: pd.Series,
    high_s: pd.Series,
    close_s: pd.Series,
    vol_s: pd.Series,
    bins: int = 24,
) -> Optional[Dict[str, Any]]:
    """本地计算筹码分布与成交量分布核心算法。"""
    min_price = float(low_s.min())
    max_price = float(high_s.max())
    curr_price = float(close_s.iloc[-1])

    if max_price <= min_price:
        return None

    edges = np.linspace(min_price, max_price, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    hist_vol = np.zeros(bins, dtype=float)

    n = len(close_s)
    decay_weights = np.exp(np.linspace(-1.2, 0.0, n))

    for idx in range(n):
        lo = float(low_s.iloc[idx])
        hi = float(high_s.iloc[idx])
        cl = float(close_s.iloc[idx])
        v = float(vol_s.iloc[idx]) * decay_weights[idx]
        if v <= 0:
            continue
        tp = (lo + hi + 2 * cl) / 4.0
        mask = (centers >= lo) & (centers <= hi)
        if not np.any(mask):
            nearest = int(np.argmin(np.abs(centers - tp)))
            hist_vol[nearest] += v
        else:
            dist = np.abs(centers[mask] - tp) + (max_price - min_price) / bins * 0.5
            w_local = 1.0 / dist
            w_local /= w_local.sum()
            hist_vol[mask] += v * w_local

    total_vol = float(hist_vol.sum())
    if total_vol <= 0:
        return None

    pct_dist = hist_vol / total_vol
    poc_idx = int(np.argmax(pct_dist))
    poc_price = round(float(centers[poc_idx]), 3)

    # 计算 70% 价值区 (Value Area: VAH / VAL)
    va_target = 0.70
    cum_pct = float(pct_dist[poc_idx])
    left_idx = poc_idx
    right_idx = poc_idx
    while cum_pct < va_target and (left_idx > 0 or right_idx < bins - 1):
        left_val = pct_dist[left_idx - 1] if left_idx > 0 else -1.0
        right_val = pct_dist[right_idx + 1] if right_idx < bins - 1 else -1.0
        if right_val >= left_val:
            right_idx += 1
            cum_pct += float(pct_dist[right_idx])
        else:
            left_idx -= 1
            cum_pct += float(pct_dist[left_idx])

    val_price = round(float(edges[left_idx]), 3)
    vah_price = round(float(edges[right_idx + 1]), 3)

    profit_ratio_pct = round(float(pct_dist[centers <= curr_price].sum() * 100), 2)
    avg_cost = round(float(np.sum(centers * pct_dist)), 3)

    cdf = np.cumsum(pct_dist)
    p5 = float(np.interp(0.05, cdf, centers))
    p95 = float(np.interp(0.95, cdf, centers))
    concentration_90 = round((p95 - p5) / (p95 + p5) * 100, 2) if (p95 + p5) > 0 else 0.0

    top_indices = np.argsort(pct_dist)[::-1][:3]
    top_peaks = [
        {
            "price_zone": f"{edges[i]:.2f}~{edges[i+1]:.2f}",
            "center_price": round(float(centers[i]), 3),
            "volume_share_pct": round(float(pct_dist[i] * 100), 2),
        }
        for i in top_indices
    ]

    return {
        "current_price": curr_price,
        "poc_price": poc_price,
        "value_area_70pct": {
            "vah_upper": vah_price,
            "val_lower": val_price,
        },
        "avg_chip_cost": avg_cost,
        "profit_ratio_pct": profit_ratio_pct,
        "chip_concentration_90pct": concentration_90,
        "top_volume_nodes": top_peaks,
    }


def analyze_wyckoff_structure(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field("daily", description="K线周期: daily(日线) 或 weekly(周线)"),
    lookback: int = Field(90, description="分析回溯K线根数，默认90，范围30-250", strict=False),
) -> Any:
    """现代威科夫量价与聪明钱(SMC)结构化诊断工具。

    理论体系请参考: trading/wyckoff_trading_guide.md
    自动识别并计算：
    1. 关键流动性池 (Liquidity Pools: 波段高低点 Swing High/Low、等高/等低点 EQH/EQL)
    2. 供求与努力结果指标 (量比、阳量/阴量比、长下影放量吸收 Stopping Volume、放量滞涨、缩量回踩)
    3. 流动性掠夺 (Liquidity Sweep / Spring / UT) 与结构转变 (MSS / CHoCH)
    4. 未回补失衡区 (Fair Value Gap - FVG 上沿/50%中轴/下沿)
    5. 硬性止损位、目标位、盈亏比 (R:R) 与 Tier 1~3 行动评级
    """
    try:
        from .stock import _stock_us_daily

        symbol = Validators.validate_symbol(symbol)
        Validators.validate_market(market)
        Validators.validate_period(period)
        lookback = max(30, min(int(lookback), 250))

        delta_days = (lookback + 60) * (7 if period == "weekly" else 2)
        start_date = (datetime.now() - timedelta(days=delta_days)).strftime("%Y%m%d")

        client = get_akshare_client()
        if market in ("sh", "sz"):
            df = client.stock_zh_a_hist(symbol=symbol, market=market, period=period, start_date=start_date)
        elif market == "hk":
            df = client.stock_hk_hist(symbol=symbol, period=period, start_date=start_date)
        else:
            df = client.call(_stock_us_daily, symbol=symbol, start_date=start_date, use_disk_cache=False)

        if df is None or df.empty or len(df) < 20:
            return f"数据不足，无法对 {symbol}.{market} 进行威科夫/SMC结构分析"

        df = df.tail(lookback).reset_index(drop=True).copy()
        date_col = next((c for c in ("日期", "date", "时间") if c in df.columns), df.columns[0])
        open_s = pd.to_numeric(df[next(c for c in ("开盘", "open") if c in df.columns)], errors="coerce")
        high_s = pd.to_numeric(df[next(c for c in ("最高", "high") if c in df.columns)], errors="coerce")
        low_s = pd.to_numeric(df[next(c for c in ("最低", "low") if c in df.columns)], errors="coerce")
        close_s = pd.to_numeric(df[next(c for c in ("收盘", "close") if c in df.columns)], errors="coerce")
        vol_col = next((c for c in ("成交量", "volume", "vol", "amount", "总手") if c in df.columns), None)
        vol_s = pd.to_numeric(df[vol_col], errors="coerce").fillna(0) if vol_col else pd.Series(0, index=df.index)
        dates_s = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")

        curr_close = float(close_s.iloc[-1])
        curr_date = str(dates_s.iloc[-1])

        # 1. 波段高低点
        swing_highs, swing_lows = find_swing_levels(high_s, low_s, dates_s, w=3)
        range_high = float(high_s.max())
        range_low = float(low_s.min())
        range_pos_pct = (
            round((curr_close - range_low) / (range_high - range_low) * 100, 1)
            if range_high > range_low
            else 50.0
        )

        recent_swing_low = swing_lows[-1] if swing_lows else {"date": str(dates_s.idxmin()), "price": round(range_low, 3)}
        recent_swing_high = swing_highs[-1] if swing_highs else {"date": str(dates_s.idxmax()), "price": round(range_high, 3)}

        # 2. 供求与努力结果
        vol_ma20 = vol_s.rolling(20, min_periods=5).mean()
        curr_vol_ratio = round(float(vol_s.iloc[-1] / vol_ma20.iloc[-1]), 2) if float(vol_ma20.iloc[-1] or 0) > 0 else 1.0

        recent_10_up_vol = float(vol_s.iloc[-10:][close_s.iloc[-10:] >= open_s.iloc[-10:]].sum())
        recent_10_down_vol = float(vol_s.iloc[-10:][close_s.iloc[-10:] < open_s.iloc[-10:]].sum())
        up_down_vol_ratio = round(recent_10_up_vol / recent_10_down_vol, 2) if recent_10_down_vol > 0 else 9.99

        anomalies = detect_effort_vs_result(open_s, high_s, low_s, close_s, vol_s, dates_s, vol_ma20)

        # 3. 流动性掠夺与结构转变
        sweep_event, mss_event = detect_liquidity_sweep_and_mss(
            swing_highs, swing_lows, open_s, high_s, low_s, close_s, vol_s, dates_s, vol_ma20
        )

        # 4. 未回补失衡区 (FVG)
        bullish_fvgs, bearish_fvgs = detect_fair_value_gaps(high_s, low_s, dates_s)

        # 5. 止损、目标位与盈亏比
        invalidation_sl = (
            sweep_event["sweep_low"]
            if sweep_event
            else round(min(float(recent_swing_low["price"]), float(low_s.iloc[-10:].min())) * 0.995, 3)
        )
        overhead_pools = sorted(
            {sh["price"] for sh in swing_highs if sh["price"] > curr_close * 1.01} | {round(range_high, 3)}
        )
        tp1 = overhead_pools[0] if overhead_pools else round(curr_close * 1.08, 3)
        tp2 = overhead_pools[1] if len(overhead_pools) > 1 else round(tp1 * 1.05, 3)

        risk = max(curr_close - invalidation_sl, 0.0001)
        reward = max(tp1 - curr_close, 0.0)
        rr_ratio = round(reward / risk, 2)

        if (sweep_event or mss_event) and rr_ratio >= 2.0 and up_down_vol_ratio >= 1.0:
            tier = "Tier 1 (高胜率行动: 结构确认 + 盈亏比合格)"
        elif sweep_event or mss_event or range_pos_pct <= 25.0:
            tier = "Tier 2 (观察待确认: 处于关键位置或初现异动，等待回踩确认或MSS突破)"
        else:
            tier = "Tier 3 (震荡中段或弱势: 赔率不足或缺乏主力承接，观望为主)"

        entry_zone = (
            f"{bullish_fvgs[-1]['lower']} ~ {bullish_fvgs[-1]['upper']} (50%中轴: {bullish_fvgs[-1]['ce_50pct']})"
            if bullish_fvgs
            else f"回踩近期支撑 {recent_swing_low['price']} 附近缩量企稳区"
        )

        return {
            "symbol": symbol,
            "market": market,
            "period": period,
            "as_of_date": curr_date,
            "current_price": curr_close,
            "structure_and_liquidity": {
                "range_high": round(range_high, 3),
                "range_low": round(range_low, 3),
                "range_position_pct": range_pos_pct,
                "recent_swing_low": recent_swing_low,
                "recent_swing_high": recent_swing_high,
                "recent_swing_lows": swing_lows[-3:],
                "recent_swing_highs": swing_highs[-3:],
            },
            "effort_vs_result": {
                "latest_volume_ratio_vs_ma20": curr_vol_ratio,
                "10bar_up_down_volume_ratio": up_down_vol_ratio,
                "volume_price_anomalies": anomalies[-5:],
            },
            "signals": {
                "liquidity_sweep": sweep_event,
                "market_structure_shift_mss": mss_event,
                "unmitigated_bullish_fvgs": bullish_fvgs[-3:],
                "unmitigated_bearish_fvgs": bearish_fvgs[-3:],
            },
            "execution_plan": {
                "entry_reference_zone": entry_zone,
                "hard_stop_loss_invalidation": invalidation_sl,
                "stop_loss_distance_pct": round((curr_close - invalidation_sl) / curr_close * 100, 2),
                "take_profit_1": tp1,
                "take_profit_2": tp2,
                "risk_reward_ratio": rr_ratio,
                "rating": tier,
            },
        }
    except Exception as e:
        logger.error(f"威科夫结构分析失败: {e}", exc_info=True)
        return f"威科夫结构分析失败: {str(e)}"


def stock_volume_profile(
    symbol: str = field_symbol,
    market: str = field_market,
    lookback: int = Field(90, description="统计周期K线数，默认90个交易日", strict=False),
    bins: int = Field(24, description="价格分档数量，默认24档", strict=False),
) -> Any:
    """本地计算股票筹码分布与成交量分布 (Volume Profile: POC / VAH / VAL / 获利盘比例)。

    基于本地缓存的日K线与换手衰减模型计算，不依赖易被限流的东财筹码接口。
    """
    try:
        from .stock import _stock_us_daily

        symbol = Validators.validate_symbol(symbol)
        Validators.validate_market(market)
        lookback = max(20, min(int(lookback), 250))
        bins = max(10, min(int(bins), 60))

        start_date = (datetime.now() - timedelta(days=int(lookback * 1.8) + 30)).strftime("%Y%m%d")
        client = get_akshare_client()
        if market in ("sh", "sz"):
            df = client.stock_zh_a_hist(symbol=symbol, market=market, period="daily", start_date=start_date)
        elif market == "hk":
            df = client.stock_hk_hist(symbol=symbol, period="daily", start_date=start_date)
        else:
            df = client.call(_stock_us_daily, symbol=symbol, start_date=start_date, use_disk_cache=False)

        if df is None or df.empty:
            return f"Not Found for {symbol}.{market}"

        df = df.tail(lookback).reset_index(drop=True).copy()
        high_s = pd.to_numeric(df[next(c for c in ("最高", "high") if c in df.columns)], errors="coerce")
        low_s = pd.to_numeric(df[next(c for c in ("最低", "low") if c in df.columns)], errors="coerce")
        close_s = pd.to_numeric(df[next(c for c in ("收盘", "close") if c in df.columns)], errors="coerce")
        vol_col = next((c for c in ("成交量", "volume", "vol", "amount", "总手") if c in df.columns), None)
        vol_s = pd.to_numeric(df[vol_col], errors="coerce").fillna(0) if vol_col else pd.Series(0, index=df.index)

        profile = calculate_volume_profile(low_s, high_s, close_s, vol_s, bins=bins)
        if not profile:
            return "价格区间无有效成交波动，无法计算筹码分布"

        return {
            "symbol": symbol,
            "market": market,
            "lookback_bars": len(df),
            **profile,
        }
    except Exception as e:
        logger.error(f"计算筹码/成交量分布失败: {e}", exc_info=True)
        return f"计算筹码分布失败: {str(e)}"
