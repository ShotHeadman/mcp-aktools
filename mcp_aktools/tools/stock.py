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


# 威科夫结构分析与筹码分布工具已拆分至 wyckoff 模块，此处保持兼容重导出
from .wyckoff import (
    analyze_wyckoff_structure,
    stock_volume_profile,
)


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
