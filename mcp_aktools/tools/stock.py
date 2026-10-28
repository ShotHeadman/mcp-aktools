"""股票相关工具函数。

此模块包含所有股票相关的 MCP 工具函数，包括：
- 股票搜索
- 股票信息查询
- 历史价格和技术指标
- 财务指标
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import akshare as ak
import numpy as np
import pandas as pd
from pydantic import Field

from ..clients.akshare import AkShareClient
from ..config import get_settings
from ..exceptions import ValidationException
from ..utils.data_validators import OHLCValidator
from ..utils.formatting import dataframe_records, validate_response_format
from ..utils.indicators import add_technical_indicators
from ..utils.validators import Validators

logger = logging.getLogger(__name__)

# 新浪接口列名映射：英文到中文
SINA_COLUMN_MAPPING = {
    'day': '时间',
    'open': '开盘',
    'high': '最高',
    'low': '最低',
    'close': '收盘',
    'volume': '成交量',
}

# 初始化客户端
_akshare_client: Optional[AkShareClient] = None


def get_akshare_client() -> AkShareClient:
    """获取 AkShare 客户端单例。"""
    global _akshare_client
    if _akshare_client is None:
        _akshare_client = AkShareClient(get_settings())
    return _akshare_client


# 字段定义
field_symbol = Field(description="股票代码")
field_market = Field(
    "sh",
    description="股票市场，仅支持: sh(上证), sz(深证), hk(港股), us(美股)",
)


def search(
    keyword: str = Field(
        description="搜索关键词，公司名称、股票名称、股票代码、证券简称"
    ),
    market: str = field_market,
) -> str:
    """根据股票名称、公司名称等关键词查找股票代码。

    该工具比较耗时，当你知道股票代码或用户已指定股票代码时，建议直接通过股票代码使用其他工具。
    Args:
        keyword: 搜索关键词
        market: 市场类型（sh/sz/hk/us）

    Returns:
        str: 股票信息字符串或 "Not Found"
    """
    try:
        # 验证市场参数
        Validators.validate_market(market)

        logger.info(f"搜索股票: keyword={keyword}, market={market}")

        info = _ak_search(None, keyword, market)
        if info is not None:
            suffix = f"交易市场: {market}"
            return "\n".join([info.to_string(), suffix])

        return f"Not Found for {keyword}"

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"搜索股票失败: {e}", exc_info=True)
        return f"搜索失败: {str(e)}"


def stock_info(
    symbol: str = field_symbol,
    market: str = field_market,
) -> str:
    """根据股票代码和市场获取股票基本信息。

    Args:
        symbol: 股票代码
        market: 市场类型（sh/sz/hk/us）

    Returns:
        str: 股票信息字符串或 "Not Found"
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        Validators.validate_market(market)

        logger.info(f"获取股票信息: symbol={symbol}, market={market}")

        client = get_akshare_client()

        # 根据市场类型获取信息
        if market in ["sh", "sz"]:
            # A 股使用带备用接口的方法
            all_data = client.stock_individual_info_em(symbol=symbol, market=market, ttl=43200)
            if all_data is not None and not all_data.empty:
                return all_data.to_string()
        elif market == "hk":
            # 港股：优先尝试雪球接口，失败回退到东财
            try:
                all_data = client.call(ak.stock_individual_basic_info_hk_xq, symbol=symbol, ttl=43200)
                if all_data is not None and not all_data.empty:
                    return all_data.to_string()
            except Exception as xq_err:
                logger.warning(f"雪球港股信息接口失败，尝试东财: {xq_err}")
            all_data = client.call(ak.stock_hk_security_profile_em, symbol=symbol, ttl=43200)
            if all_data is not None and not all_data.empty:
                return all_data.to_string()
        elif market == "us":
            # 美股：优先使用雪球美股资料接口
            try:
                all_data = client.call(ak.stock_individual_basic_info_us_xq, symbol=symbol, ttl=43200)
                if all_data is not None and not all_data.empty:
                    return all_data.to_string()
            except Exception as us_err:
                logger.warning(f"雪球美股信息接口失败，回退至代码表搜索: {us_err}")

        # 如果上面的方法没有找到，尝试搜索
        info = _ak_search(symbol, None, market)
        if info is not None:
            return info.to_string()

        return f"Not Found for {symbol}.{market}"

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取股票信息失败: {e}", exc_info=True)
        fallback = _stock_info_fallback(symbol=symbol, market=market, error=e)
        if fallback is not None:
            return fallback
        return f"获取失败: 数据源暂不可用 ({type(e).__name__})"


def stock_prices(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field(
        "daily", description="周期: daily(日线)、weekly(周线)、monthly(月线)；美股仅支持 daily"
    ),
    limit: int = Field(30, description="返回数量(int)", strict=False),
    ma_periods: str = Field("", description="均线周期列表，逗号分隔，如: 5,10,20,30,60,120,250 或斐波那契: 5,8,13,21,34,55,89,144,233"),
    include_trend: bool = Field(False, description="是否包含趋势指标(BBI/TREND/LINREG/SLOPE)"),
    response_format: str = Field("json", description="返回格式: json 或 csv"),
) -> Any:
    """根据股票代码和市场获取股票历史价格及技术指标。

    Args:
        symbol: 股票代码
        market: 市场类型（sh/sz/hk/us）
        period: 周期（daily/weekly/monthly）
        limit: 返回数量
        ma_periods: 均线周期列表，逗号分隔
        include_trend: 是否包含趋势指标

    Returns:
        str: CSV 格式的价格和技术指标数据
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        Validators.validate_market(market)
        Validators.validate_period(period)
        if market == "us" and period != "daily":
            raise ValidationException("period", "美股历史价格仅支持 daily")
        Validators.validate_limit(limit, min_val=1, max_val=300)
        response_format = validate_response_format(response_format, default="json")
        if not isinstance(ma_periods, str):
            ma_periods = getattr(ma_periods, "default", "") or ""
        if not isinstance(include_trend, bool):
            include_trend = bool(getattr(include_trend, "default", False))

        # 解析均线周期
        parsed_ma_periods = None
        if isinstance(ma_periods, str) and ma_periods.strip():
            try:
                parsed_ma_periods = [int(p.strip()) for p in ma_periods.split(",") if p.strip()]
            except ValueError:
                return "参数错误: ma_periods 格式不正确，应为逗号分隔的数字，如: 5,10,20,30,60"

        logger.info(
            f"获取股票价格: symbol={symbol}, market={market}, period={period}, limit={limit}, "
            f"ma_periods={parsed_ma_periods}, include_trend={include_trend}"
        )

        # 计算开始日期
        if period == "monthly":
            # 月线数据需要更长的时间范围
            delta = {"days": (limit + 12) * 31}  # 约 limit+12 个月
        elif period == "weekly":
            delta = {"weeks": limit + 62}
        else:
            delta = {"days": limit + 62}
        start_date = (datetime.now() - timedelta(**delta)).strftime("%Y%m%d")

        client = get_akshare_client()

        # 根据市场类型获取数据
        dfs = None
        
        if market in ["sh", "sz"]:
            # A 股使用带备用接口的方法
            dfs = client.stock_zh_a_hist(
                symbol=symbol,
                market=market,
                period=period,
                start_date=start_date,
                ttl=3600
            )
        elif market == "hk":
            dfs = client.stock_hk_hist(
                symbol=symbol,
                period=period,
                start_date=start_date,
                ttl=3600
            )
        elif market == "us":
            dfs = client.call(
                _stock_us_daily,
                symbol=symbol,
                start_date=start_date,
                ttl=3600,
                use_disk_cache=False,
            )
        else:
            # 尝试 ETF
            dfs = client.call(
                _fund_etf_hist_sina,
                symbol=symbol,
                market=market,
                start_date=start_date,
                ttl=3600
            )
        
        if dfs is not None and not dfs.empty:
            close_col = next((c for c in ["close", "收盘"] if c in dfs.columns), dfs.columns[1])
            low_col = next((c for c in ["low", "最低"] if c in dfs.columns), dfs.columns[1])
            high_col = next((c for c in ["high", "最高"] if c in dfs.columns), dfs.columns[1])

            # 添加技术指标
            try:
                add_technical_indicators(
                    dfs, dfs[close_col], dfs[low_col], dfs[high_col],
                    ma_periods=parsed_ma_periods,
                    include_trend=include_trend,
                )
            except Exception as ind_err:
                logger.warning(f"添加技术指标失败: {ind_err}")

            result_df = dfs.tail(int(limit))
            columns = list(result_df.columns)
            if response_format == "json":
                return {
                    "symbol": symbol,
                    "market": market,
                    "period": period,
                    "limit": limit,
                    "columns": columns,
                    "row_count": len(result_df),
                    "rows": dataframe_records(result_df),
                }

            all_lines = (
                result_df.to_csv(index=False, float_format="%.2f")
                .strip()
                .split("\n")
            )
            return "\n".join(all_lines)

        return f"Not Found for {symbol}.{market}"

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取股票价格失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_indicators_a(
    symbol: str = field_symbol,
) -> str:
    """获取中国A股市场(上证、深证)的股票财务报告关键指标。

    Args:
        symbol: 股票代码

    Returns:
        str: CSV 格式的财务指标数据
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)

        logger.info(f"获取A股关键指标: symbol={symbol}")

        client = get_akshare_client()
        dfs = None
        try:
            dfs = client.call(ak.stock_financial_abstract_ths, symbol=symbol, ttl=86400)
        except Exception as ths_err:
            logger.warning(f"同花顺财务摘要接口失败，尝试新浪财务摘要: {ths_err}")
            dfs = client.call(ak.stock_financial_abstract, symbol=symbol, ttl=86400)

        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}"

        keys = dfs.to_csv(index=False, float_format="%.3f").strip().split("\n")
        return "\n".join([keys[0], *keys[-15:]])

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取A股关键指标失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_indicators_hk(
    symbol: str = field_symbol,
) -> str:
    """获取港股市场的股票财务报告关键指标。

    Args:
        symbol: 股票代码

    Returns:
        str: CSV 格式的财务指标数据
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)

        logger.info(f"获取港股关键指标: symbol={symbol}")

        client = get_akshare_client()
        dfs = client.call(
            ak.stock_financial_hk_analysis_indicator_em,
            symbol=symbol,
            indicator="报告期",
            ttl=86400,
        )

        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}"

        keys = dfs.to_csv(index=False, float_format="%.3f").strip().split("\n")
        return "\n".join(keys[0:15])

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取港股关键指标失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_indicators_us(
    symbol: str = field_symbol,
) -> str:
    """获取美股市场的股票财务报告关键指标。

    Args:
        symbol: 股票代码

    Returns:
        str: CSV 格式的财务指标数据
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)

        logger.info(f"获取美股关键指标: symbol={symbol}")

        client = get_akshare_client()
        dfs = client.call(
            ak.stock_financial_us_analysis_indicator_em,
            symbol=symbol,
            indicator="单季报",
            ttl=86400,
        )

        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}"

        keys = dfs.to_csv(index=False, float_format="%.3f").strip().split("\n")
        return "\n".join(keys[0:15])

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取美股关键指标失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_zh_a_minute(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field("5", description="分钟周期，仅支持: 1, 5, 15, 30, 60"),
    adjust: str = Field("", description="复权类型: 空字符串(不复权), qfq(前复权), hfq(后复权)"),
    trading_days: int = Field(3, description="返回最近N个交易日的数据，默认3天，最大10天"),
) -> str:
    """获取A股分时K线数据。

    获取指定股票的历史分时数据，支持1/5/15/30/60分钟周期。
    优先使用新浪接口，失败或没有所需区间的数据时尝试东财。
    默认返回最近3个交易日的分时数据，以减少token消耗。

    Args:
        symbol: 股票代码
        market: 市场类型（sh/sz）
        period: 分钟周期（1/5/15/30/60）
        adjust: 复权类型（空字符串/qfq/hfq），注意1分钟数据不支持复权
        trading_days: 返回最近N个交易日的数据（默认3，最大10）

    Returns:
        str: CSV 格式的分时数据（最近N个交易日）
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        if market not in ["sh", "sz"]:
            return "参数错误: market 仅支持 sh(上证) 或 sz(深证)"
        if period not in ["1", "5", "15", "30", "60"]:
            return "参数错误: period 仅支持 1, 5, 15, 30, 60"
        if not isinstance(adjust, str):
            adjust = getattr(adjust, "default", "") or ""
        if adjust not in ["", "qfq", "hfq"]:
            return "参数错误: adjust 仅支持空字符串(不复权), qfq(前复权), hfq(后复权)"
        if period == "1" and adjust:
            return "参数错误: 1分钟行情仅支持不复权"
        
        # 处理并限制交易日数量（默认3天，最大10天）
        if not isinstance(trading_days, int):
            trading_days = 3  # 默认值
        trading_days = max(1, min(trading_days, 10))

        logger.info(f"获取A股分时数据: symbol={symbol}, market={market}, period={period}, adjust={adjust}, trading_days={trading_days}")

        client = get_akshare_client()
        now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        # 一次性预留 21 个自然日窗口（覆盖春节/国庆长假），避免在无日期参数的新浪分时接口上无效循环重试
        calendar_days = max(15, trading_days * 3)
        start_date = (now - timedelta(days=calendar_days)).strftime("%Y-%m-%d 09:30:00")
        end_date = now.strftime("%Y-%m-%d %H:%M:%S")

        dfs = client.stock_zh_a_hist_min(
            symbol=symbol,
            market=market,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            ttl=300,
        )

        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}.{market}"

        time_col = dfs.columns[0]
        dfs[time_col] = pd.to_datetime(dfs[time_col], errors='coerce')
        dfs['_trade_date'] = dfs[time_col].dt.date
        
        unique_dates = sorted(dfs['_trade_date'].unique(), reverse=True)
        
        # 取最近N个交易日
        if len(unique_dates) > trading_days:
            recent_dates = unique_dates[:trading_days]
            dfs = dfs[dfs['_trade_date'].isin(recent_dates)]
            logger.info(f"过滤后保留最近{trading_days}个交易日，共{len(dfs)}条记录")
        else:
            logger.info(f"数据包含{len(unique_dates)}个交易日，共{len(dfs)}条记录")
        
        # 删除临时列
        dfs = dfs.drop(columns=['_trade_date'])
        
        # 返回最近N个交易日的数据
        return dfs.to_csv(index=False, float_format="%.2f")

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取A股分时数据失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_tick_data(
    symbol: str = field_symbol,
    filter_type: str = Field("all", description="筛选类型: all(全部), big_buy(大单买入), big_sell(大单卖出), big_all(所有大单), summary(汇总统计)"),
    min_volume: int = Field(500, description="大单最小手数阈值，默认500手"),
) -> str:
    """获取A股最近一个交易日的分笔成交数据（腾讯接口）。

    获取指定股票最近一个交易日的分笔成交数据，支持智能筛选。
    数据包括：成交时间、成交价格、成交量(手)、买卖盘性质。
    
    筛选选项：
    - all: 返回所有分笔数据（可能有1000+条，token消耗大）
    - big_buy: 只返回大单买入（手数>=min_volume且为买盘）
    - big_sell: 只返回大单卖出（手数>=min_volume且为卖盘）
    - big_all: 返回所有大单（手数>=min_volume）
    - summary: 返回汇总统计（总成交、买卖盘统计、大单统计等）

    Args:
        symbol: 股票代码
        filter_type: 筛选类型
        min_volume: 大单最小手数阈值（默认500手）

    Returns:
        str: CSV格式的分笔数据或统计信息
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        if filter_type not in ["all", "big_buy", "big_sell", "big_all", "summary"]:
            return "参数错误: filter_type 仅支持 all, big_buy, big_sell, big_all, summary"
        
        min_volume = max(min_volume, 100)  # 最小100手

        logger.info(f"获取A股分笔数据: symbol={symbol}, filter_type={filter_type}, min_volume={min_volume}")

        client = get_akshare_client()
        
        # 使用腾讯接口获取分笔数据
        market_prefix = "sh" if symbol.startswith("6") else "sz"
        full_symbol = f"{market_prefix}{symbol}"
        logger.info(f"腾讯接口获取分笔数据: {full_symbol}")
        
        dfs = client.call(
            ak.stock_zh_a_tick_tx_js,
            symbol=full_symbol,
            ttl=60  # 1分钟缓存
        )
        
        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}"
        
        logger.info(f"腾讯接口成功，共{len(dfs)}条记录")
        
        # 统一列名
        dfs = dfs.rename(columns={
            '成交时间': '时间',
            '成交价格': '成交价',
            '成交量': '手数',
            '性质': '买卖盘性质'
        })
        
        # 确保必要的列存在
        required_cols = ['时间', '成交价', '手数', '买卖盘性质']
        for col in required_cols:
            if col not in dfs.columns:
                return f"数据格式错误: 缺少列 {col}"
        
        total_rows = len(dfs)
        
        # 根据筛选类型处理数据
        if filter_type == "all":
            info = f"# 共{total_rows}条分笔数据\n"
            return info + dfs[required_cols].to_csv(index=False, float_format="%.2f")
        
        elif filter_type == "summary":
            total_volume = dfs['手数'].sum()
            total_amount = (dfs['成交价'] * dfs['手数'] * 100).sum()
            avg_price = dfs['成交价'].mean()
            
            buy_count = len(dfs[dfs['买卖盘性质'] == '买盘'])
            sell_count = len(dfs[dfs['买卖盘性质'] == '卖盘'])
            neutral_count = total_rows - buy_count - sell_count
            
            buy_volume = dfs[dfs['买卖盘性质'] == '买盘']['手数'].sum()
            sell_volume = dfs[dfs['买卖盘性质'] == '卖盘']['手数'].sum()
            
            big_orders = dfs[dfs['手数'] >= min_volume]
            big_count = len(big_orders)
            big_volume = big_orders['手数'].sum() if big_count > 0 else 0
            big_buy_count = len(big_orders[big_orders['买卖盘性质'] == '买盘'])
            big_sell_count = len(big_orders[big_orders['买卖盘性质'] == '卖盘'])
            big_buy_volume = big_orders[big_orders['买卖盘性质'] == '买盘']['手数'].sum() if big_buy_count > 0 else 0
            big_sell_volume = big_orders[big_orders['买卖盘性质'] == '卖盘']['手数'].sum() if big_sell_count > 0 else 0
            
            summary = f"""# 分笔数据汇总统计 (股票代码: {symbol})

## 基本信息
- 总分笔数: {total_rows}
- 总成交量: {total_volume:,} 手 ({total_volume * 100:,} 股)
- 总成交额: {total_amount:,.2f} 元
- 均价: {avg_price:.2f} 元

## 买卖盘分布
- 买盘笔数: {buy_count} ({buy_count/total_rows*100:.1f}%)
- 卖盘笔数: {sell_count} ({sell_count/total_rows*100:.1f}%)
- 中性盘笔数: {neutral_count} ({neutral_count/total_rows*100:.1f}%)
- 买盘成交量: {buy_volume:,} 手
- 卖盘成交量: {sell_volume:,} 手
- 买卖比: {buy_volume/sell_volume if sell_volume > 0 else 0:.2f} (买盘/卖盘)

## 大单统计 (>={min_volume}手)
- 大单笔数: {big_count} ({big_count/total_rows*100:.1f}%)
- 大单成交量: {big_volume:,} 手 ({big_volume/total_volume*100 if total_volume > 0 else 0:.1f}%)
- 大单买入: {big_buy_count} 笔, {big_buy_volume:,} 手
- 大单卖出: {big_sell_count} 笔, {big_sell_volume:,} 手
- 大单买卖比: {big_buy_volume/big_sell_volume if big_sell_volume > 0 else 0:.2f}
"""
            return summary
        
        else:
            big_orders = dfs[dfs['手数'] >= min_volume]
            
            if filter_type == "big_buy":
                result = big_orders[big_orders['买卖盘性质'] == '买盘']
                info = f"# 大单买入 (>={min_volume}手): 共{len(result)}笔, 占总笔数{len(result)/total_rows*100:.1f}%\n"
            elif filter_type == "big_sell":
                result = big_orders[big_orders['买卖盘性质'] == '卖盘']
                info = f"# 大单卖出 (>={min_volume}手): 共{len(result)}笔, 占总笔数{len(result)/total_rows*100:.1f}%\n"
            else:  # big_all
                result = big_orders
                info = f"# 所有大单 (>={min_volume}手): 共{len(result)}笔, 占总笔数{len(result)/total_rows*100:.1f}%\n"
            
            if result.empty:
                return f"未找到符合条件的大单 (>={min_volume}手)"
            
            return info + result[required_cols].to_csv(index=False, float_format="%.2f")

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取A股分笔数据失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_quote_realtime(
    symbols: str = Field(
        description="股票/ETF/指数代码，支持单个或多个逗号分隔，如: sh600519,sz300054,sh510300,000001"
    ),
    include_orderbook: bool = Field(True, description="是否返回买一至买五/卖一至卖五盘口数据"),
    response_format: str = Field("json", description="返回格式: json 或 csv"),
) -> Any:
    """通过腾讯极速行情接口毫秒级获取单只或多只股票/ETF的五档盘口、量比、换手率、PE/PB及市值快照。"""
    try:
        if not symbols or not str(symbols).strip():
            return "参数错误: symbols 不能为空"
        response_format = validate_response_format(response_format, default="json")
        if not isinstance(include_orderbook, bool):
            include_orderbook = bool(getattr(include_orderbook, "default", True))

        symbol_list = [s.strip() for s in str(symbols).split(",") if s.strip()]
        if not symbol_list:
            return "参数错误: 未提供有效的股票代码"

        client = get_akshare_client()
        quotes = client.fetch_tencent_quotes(symbol_list)
        if not quotes:
            return f"Not Found for {symbols}"

        if not include_orderbook:
            for q in quotes:
                q.pop("bids", None)
                q.pop("asks", None)

        if response_format == "json":
            return {
                "count": len(quotes),
                "quotes": quotes,
            }

        flat_rows = []
        for q in quotes:
            row = {k: v for k, v in q.items() if k not in ("bids", "asks")}
            if include_orderbook:
                bids = q.get("bids") or []
                asks = q.get("asks") or []
                if bids:
                    row["买一价"] = bids[0]["price"]
                    row["买一手"] = bids[0]["volume_lots"]
                if asks:
                    row["卖一价"] = asks[0]["price"]
                    row["卖一手"] = asks[0]["volume_lots"]
            flat_rows.append(row)
        return pd.DataFrame(flat_rows).to_csv(index=False, float_format="%.2f").strip()
    except Exception as e:
        logger.error(f"获取实时五档行情失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def analyze_wyckoff_structure(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field("daily", description="K线周期: daily(日线) 或 weekly(周线)"),
    lookback: int = Field(90, description="分析回溯K线根数，默认90，范围30-250", strict=False),
) -> Any:
    """现代威科夫量价与聪明钱(SMC)结构化诊断工具。

    自动识别并计算：
    1. 关键流动性池 (Liquidity Pools: 波段高低点 Swing High/Low、等高/等低点 EQH/EQL)
    2. 供求与努力结果指标 (量比、阳量/阴量比、长下影放量吸收 Stopping Volume、放量滞涨、缩量回踩)
    3. 流动性掠夺 (Liquidity Sweep / Spring / UT) 与结构转变 (MSS / CHoCH)
    4. 未回补失衡区 (Fair Value Gap - FVG 上沿/50%中轴/下沿)
    5. 硬性止损位、目标位、盈亏比 (R:R) 与 Tier 1~3 行动评级
    """
    try:
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
        vol_s = pd.to_numeric(df[next(c for c in ("成交量", "volume") if c in df.columns)], errors="coerce")
        dates_s = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")

        n = len(df)
        curr_close = float(close_s.iloc[-1])
        curr_date = str(dates_s.iloc[-1])

        # 1. 寻找波段高低点 (Swing Highs / Swing Lows, 左右窗口 w=3)
        w = 3
        swing_highs: List[Dict[str, Any]] = []
        swing_lows: List[Dict[str, Any]] = []
        for i in range(w, n - w):
            h_val = float(high_s.iloc[i])
            l_val = float(low_s.iloc[i])
            if h_val == float(high_s.iloc[i - w : i + w + 1].max()):
                swing_highs.append({"index": i, "date": str(dates_s.iloc[i]), "price": round(h_val, 3)})
            if l_val == float(low_s.iloc[i]):
                if l_val == float(low_s.iloc[i - w : i + w + 1].min()):
                    swing_lows.append({"index": i, "date": str(dates_s.iloc[i]), "price": round(l_val, 3)})

        range_high = float(high_s.max())
        range_low = float(low_s.min())
        range_pos_pct = (
            round((curr_close - range_low) / (range_high - range_low) * 100, 1)
            if range_high > range_low
            else 50.0
        )

        # 最近的支撑/阻力流动性池
        recent_swing_low = swing_lows[-1] if swing_lows else {"date": str(dates_s.idxmin()), "price": round(range_low, 3)}
        recent_swing_high = swing_highs[-1] if swing_highs else {"date": str(dates_s.idxmax()), "price": round(range_high, 3)}

        # 2. 供求与努力结果 (Effort vs Result)
        vol_ma20 = vol_s.rolling(20, min_periods=5).mean()
        curr_vol_ratio = round(float(vol_s.iloc[-1] / vol_ma20.iloc[-1]), 2) if float(vol_ma20.iloc[-1] or 0) > 0 else 1.0

        recent_10_up_vol = float(vol_s.iloc[-10:][close_s.iloc[-10:] >= open_s.iloc[-10:]].sum())
        recent_10_down_vol = float(vol_s.iloc[-10:][close_s.iloc[-10:] < open_s.iloc[-10:]].sum())
        up_down_vol_ratio = round(recent_10_up_vol / recent_10_down_vol, 2) if recent_10_down_vol > 0 else 9.99

        # 检测近 10 根K线内的异动: Stopping Volume(长下影放量吸收) / 放量滞涨
        anomalies: List[str] = []
        for i in range(max(1, n - 10), n):
            bar_range = float(high_s.iloc[i] - low_s.iloc[i])
            if bar_range <= 0:
                continue
            lower_wick = float(min(open_s.iloc[i], close_s.iloc[i]) - low_s.iloc[i])
            upper_wick = float(high_s.iloc[i] - max(open_s.iloc[i], close_s.iloc[i]))
            body = abs(float(close_s.iloc[i] - open_s.iloc[i]))
            v_ratio = float(vol_s.iloc[i] / vol_ma20.iloc[i]) if float(vol_ma20.iloc[i] or 0) > 0 else 1.0
            d_str = str(dates_s.iloc[i])
            if lower_wick / bar_range >= 0.45 and v_ratio >= 1.3:
                anomalies.append(f"{d_str}: 长下影放量吸收 (Stopping Volume, 量比{v_ratio:.2f}, 下影占比{lower_wick/bar_range:.0%})")
            elif v_ratio >= 1.8 and (body / bar_range <= 0.25 or upper_wick / bar_range >= 0.45):
                anomalies.append(f"{d_str}: 放量滞涨/上影派发 (量比{v_ratio:.2f}, 实体占比{body/bar_range:.0%})")

        # 3. 检测流动性掠夺 (Sweep / Spring) 与 结构转变 (MSS)
        sweep_event = None
        if len(swing_lows) >= 1:
            # 检查最近 12 根K线是否刺破前期波段低点后收盘收回
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

        # 4. 检测未回补失衡区 (Fair Value Gaps - FVG)
        bullish_fvgs: List[Dict[str, Any]] = []
        bearish_fvgs: List[Dict[str, Any]] = []
        for i in range(max(2, n - 30), n):
            # Bullish FVG: bar[i-2].high < bar[i].low
            gap_low = float(high_s.iloc[i - 2])
            gap_high = float(low_s.iloc[i])
            if gap_high > gap_low * 1.002:
                # 检查后续是否完全回补 (后续最低价是否跌破 gap_low)
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

        # 5. 止损、目标位与盈亏比 (R:R) 测算
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

        # 综合 Tier 评级
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
        vol_s = pd.to_numeric(df[next(c for c in ("成交量", "volume") if c in df.columns)], errors="coerce").fillna(0)

        min_price = float(low_s.min())
        max_price = float(high_s.max())
        curr_price = float(close_s.iloc[-1])

        if max_price <= min_price:
            return "价格区间无波动，无法计算筹码分布"

        edges = np.linspace(min_price, max_price, bins + 1)
        centers = (edges[:-1] + edges[1:]) / 2.0
        hist_vol = np.zeros(bins, dtype=float)

        # 使用近期权重衰减模拟筹码留存（越近的成交留存权重越高）
        n = len(df)
        decay_weights = np.exp(np.linspace(-1.2, 0.0, n))

        for idx in range(n):
            lo = float(low_s.iloc[idx])
            hi = float(high_s.iloc[idx])
            cl = float(close_s.iloc[idx])
            v = float(vol_s.iloc[idx]) * decay_weights[idx]
            if v <= 0:
                continue
            # 典型价格 (TP) 与当日价格区间重叠分摊
            tp = (lo + hi + 2 * cl) / 4.0
            mask = (centers >= lo) & (centers <= hi)
            if not np.any(mask):
                nearest = int(np.argmin(np.abs(centers - tp)))
                hist_vol[nearest] += v
            else:
                # 越靠近收盘/典型价的档位分配更高权重
                dist = np.abs(centers[mask] - tp) + (max_price - min_price) / bins * 0.5
                w_local = 1.0 / dist
                w_local /= w_local.sum()
                hist_vol[mask] += v * w_local

        total_vol = float(hist_vol.sum())
        if total_vol <= 0:
            return "成交量为0，无法计算筹码分布"

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

        # 获利盘比例与平均成本
        profit_ratio_pct = round(float(pct_dist[centers <= curr_price].sum() * 100), 2)
        avg_cost = round(float(np.sum(centers * pct_dist)), 3)

        # 90% 筹码集中度
        cdf = np.cumsum(pct_dist)
        p5 = float(np.interp(0.05, cdf, centers))
        p95 = float(np.interp(0.95, cdf, centers))
        concentration_90 = round((p95 - p5) / (p95 + p5) * 100, 2) if (p95 + p5) > 0 else 0.0

        # 前三大筹码峰
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
            "symbol": symbol,
            "market": market,
            "lookback_bars": len(df),
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
    except Exception as e:
        logger.error(f"计算筹码/成交量分布失败: {e}", exc_info=True)
        return f"计算筹码分布失败: {str(e)}"


def stock_valuation_dividend(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field("近三年", description="历史估值统计周期: 近一年, 近三年, 近五年, 近十年, 全部"),
) -> Any:
    """获取个股历史估值分位数(PE/PB)及历史分红派息情况。"""
    try:
        symbol = Validators.validate_symbol(symbol)
        Validators.validate_market(market)
        client = get_akshare_client()

        result: Dict[str, Any] = {
            "symbol": symbol,
            "market": market,
            "period": period,
            "realtime_snapshot": {},
            "valuation_percentiles": {},
            "dividend_summary": {},
        }

        # 1. 腾讯实时估值快照
        if market in ("sh", "sz", "hk"):
            try:
                quotes = client.fetch_tencent_quotes([f"{market}{symbol}"])
                if quotes:
                    q = quotes[0]
                    result["realtime_snapshot"] = {
                        "name": q.get("name"),
                        "price": q.get("price"),
                        "pe_ttm": q.get("pe_ttm"),
                        "pb": q.get("pb"),
                        "total_market_cap_yi": q.get("total_market_cap_yi"),
                        "float_market_cap_yi": q.get("float_market_cap_yi"),
                    }
            except Exception as q_err:
                logger.debug(f"实时估值快照获取失败: {q_err}")

        # 2. 百度历史估值分位计算 (支持 A股/港股/美股)
        val_func = {
            "sh": ak.stock_zh_valuation_baidu,
            "sz": ak.stock_zh_valuation_baidu,
            "hk": ak.stock_hk_valuation_baidu,
            "us": ak.stock_us_valuation_baidu,
        }.get(market)

        if val_func is not None:
            for indicator in ("市盈率(TTM)", "市净率"):
                try:
                    v_df = client.call(
                        val_func,
                        symbol=symbol,
                        indicator=indicator,
                        period=period,
                        ttl=43200,
                        enable_range_completion=False,
                    )
                    if v_df is not None and not v_df.empty and "value" in v_df.columns:
                        vals = pd.to_numeric(v_df["value"], errors="coerce").dropna()
                        if not vals.empty:
                            curr_val = float(vals.iloc[-1])
                            pct_rank = round(float((vals <= curr_val).mean() * 100), 1)
                            result["valuation_percentiles"][indicator] = {
                                "current": round(curr_val, 2),
                                "percentile_pct": pct_rank,
                                "min": round(float(vals.min()), 2),
                                "median": round(float(vals.median()), 2),
                                "max": round(float(vals.max()), 2),
                                "status": (
                                    "低估区间" if pct_rank <= 25 else "高估区间" if pct_rank >= 75 else "合理区间"
                                ),
                            }
                except Exception as v_err:
                    logger.debug(f"获取 {indicator} 历史估值失败: {v_err}")

        # 3. 历史分红记录统计 (A股)
        if market in ("sh", "sz"):
            try:
                div_df = client.call(
                    ak.stock_history_dividend_detail,
                    symbol=symbol,
                    indicator="分红",
                    ttl=86400,
                    enable_range_completion=False,
                )
                if div_df is not None and not div_df.empty:
                    # 筛选已实施的分红记录
                    recent_divs = div_df.head(10).to_dict(orient="records")
                    result["dividend_summary"] = {
                        "total_records": len(div_df),
                        "recent_dividends": recent_divs[:5],
                    }
            except Exception as d_err:
                logger.debug(f"获取分红历史失败: {d_err}")

        return result
    except Exception as e:
        logger.error(f"获取估值与分红数据失败: {e}", exc_info=True)
        return f"获取估值与分红数据失败: {str(e)}"


# 辅助函数


def _stock_info_fallback(symbol: str, market: str, error: Exception) -> Optional[str]:
    """Return basic stock information from fast Tencent quote or code/name tables."""
    try:
        logger.warning(
            "股票基本信息主接口不可用，尝试腾讯极速行情/代码表降级: symbol=%s, market=%s, error=%s",
            symbol,
            market,
            error,
        )
        rows = [
            {"项目": "股票代码", "值": symbol},
            {"项目": "交易市场", "值": market},
            {"项目": "数据说明", "值": "主接口暂不可用，以下为实时行情和代码表降级信息"},
        ]

        # 优先使用毫秒级腾讯单股行情接口（避免全量拉取新浪 5000 只股票）
        try:
            client = get_akshare_client()
            quotes = client.fetch_tencent_quotes([f"{market}{symbol}"])
            if quotes:
                q = quotes[0]
                mapping = [
                    ("名称", q.get("name")),
                    ("最新价", q.get("price")),
                    ("涨跌额", q.get("change")),
                    ("涨跌幅(%)", q.get("pct_change")),
                    ("换手率(%)", q.get("turnover_pct")),
                    ("市盈率(TTM)", q.get("pe_ttm")),
                    ("市净率(PB)", q.get("pb")),
                    ("总市值(亿元)", q.get("total_market_cap_yi")),
                    ("流通市值(亿元)", q.get("float_market_cap_yi")),
                    ("时间戳", q.get("timestamp")),
                ]
                for k, v in mapping:
                    if v is not None:
                        rows.append({"项目": k, "值": v})
        except Exception as qt_error:
            logger.warning("腾讯极速行情降级源失败: %s", qt_error)

        try:
            info = _ak_search(symbol=symbol, keyword=None, market=market)
            if info is not None:
                existing_keys = {row["项目"] for row in rows}
                info_dict = info.to_dict() if hasattr(info, "to_dict") else {}
                for key, value in info_dict.items():
                    if key not in existing_keys:
                        rows.append({"项目": key, "值": value})
        except Exception as search_error:
            logger.warning("股票代码表降级源失败: %s", search_error)

        if len(rows) > 3:
            return pd.DataFrame(rows).to_string(index=False)
    except Exception as fallback_error:
        logger.warning("股票信息降级查询失败: %s", fallback_error, exc_info=True)
    return None


def _normalize_sina_columns(df: pd.DataFrame) -> pd.DataFrame:
    """统一新浪接口的列名为中文。
    
    将新浪分时接口返回的英文列名映射为中文列名，
    以保持与东财接口返回数据格式的一致性。
    
    Args:
        df: 包含英文列名的 DataFrame
        
    Returns:
        pd.DataFrame: 列名已映射为中文的 DataFrame，数据值保持不变
    """
    if df is None or df.empty:
        return df
    
    # 只重命名存在的列
    rename_dict = {k: v for k, v in SINA_COLUMN_MAPPING.items() if k in df.columns}
    if rename_dict:
        df = df.rename(columns=rename_dict)
    
    return df


def _stock_us_daily(
    symbol: str, start_date: str = "2025-01-01", period: str = "daily"
) -> Optional[pd.DataFrame]:
    """获取美股日线数据（内部辅助函数）。

    Args:
        symbol: 股票代码
        start_date: 开始日期
        period: 周期（未使用，保持接口一致）

    Returns:
        Optional[pd.DataFrame]: 日线数据或 None
    """
    dfs = ak.stock_us_daily(symbol=symbol)
    if dfs is None or dfs.empty:
        return None

    dfs.rename(
        columns={
            "date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "volume": "成交量",
        },
        inplace=True,
    )
    dfs["换手率"] = None
    dfs.index = pd.to_datetime(dfs["日期"], errors="coerce")
    return dfs[start_date:"2222-01-01"]  # 使用与 AkShare 文档一致的默认结束日期


def _fund_etf_hist_sina(
    symbol: str,
    market: str = "sh",
    start_date: str = "2025-01-01",
    period: str = "daily",
) -> Optional[pd.DataFrame]:
    """获取 ETF 基金历史数据（内部辅助函数）。

    Args:
        symbol: 基金代码
        market: 市场（sh/sz）
        start_date: 开始日期
        period: 周期（未使用，保持接口一致）

    Returns:
        Optional[pd.DataFrame]: 历史数据或 None
    """
    dfs = ak.fund_etf_hist_sina(symbol=f"{market}{symbol}")
    if dfs is None or dfs.empty:
        return None

    dfs.rename(
        columns={
            "date": "日期",
            "open": "开盘",
            "close": "收盘",
            "high": "最高",
            "low": "最低",
            "volume": "成交量",
        },
        inplace=True,
    )
    dfs["换手率"] = None
    dfs.index = pd.to_datetime(dfs["日期"], errors="coerce")
    return dfs[start_date:"2222-01-01"]


def _ak_search(
    symbol: Optional[str] = None,
    keyword: Optional[str] = None,
    market: Optional[str] = None,
) -> Optional[pd.Series | pd.DataFrame]:
    """使用 Pandas 矢量化检索股票或 ETF 信息（内部辅助函数）。"""
    client = get_akshare_client()

    markets = [
        ["sh", ak.stock_info_a_code_name, "code", "name"],
        ["sz", ak.stock_info_a_code_name, "code", "name"],
        ["sh", ak.fund_etf_spot_ths, "基金代码", "基金名称"],
        ["sz", ak.fund_etf_spot_ths, "基金代码", "基金名称"],
        ["sh", ak.stock_info_sh_name_code, "证券代码", "证券简称"],
        ["sz", ak.stock_info_sz_name_code, "A股代码", "A股简称"],
        ["hk", ak.stock_hk_spot, "代码", "中文名称"],
        ["us", ak.get_us_stock_name, "symbol", "cname"],
        ["us", ak.get_us_stock_name, "symbol", "name"],
    ]

    sym_upper = symbol.strip().upper() if symbol else None
    kw_upper = keyword.strip().upper() if keyword else None

    for m_market, func, code_col, name_col in markets:
        if market and market != m_market:
            continue

        try:
            all_data = client.call(func, ttl=86400)
        except Exception:
            continue
        if all_data is None or all_data.empty:
            continue
        if code_col not in all_data.columns or name_col not in all_data.columns:
            continue

        codes = all_data[code_col].astype(str).str.upper()
        names = all_data[name_col].astype(str).str.upper()

        # 精确匹配
        if sym_upper:
            exact = all_data[codes == sym_upper]
            if not exact.empty:
                return exact.iloc[0]

        if kw_upper:
            exact_kw = all_data[(codes == kw_upper) | (names == kw_upper)]
            if not exact_kw.empty:
                return exact_kw.iloc[0]

            # 矢量化模糊匹配（支持返回最多前 10 条候选记录）
            fuzzy_mask = names.str.contains(kw_upper, case=False, na=False, regex=False) | codes.str.contains(
                kw_upper, case=False, na=False, regex=False
            )
            fuzzy_matches = all_data[fuzzy_mask]
            if not fuzzy_matches.empty:
                if len(fuzzy_matches) == 1:
                    return fuzzy_matches.iloc[0]
                return fuzzy_matches[[code_col, name_col]].head(10).reset_index(drop=True)

    return None
