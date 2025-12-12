"""股票相关工具函数。

此模块包含所有股票相关的 MCP 工具函数，包括：
- 股票搜索
- 股票信息查询
- 历史价格和技术指标
- 财务指标
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
from pydantic import Field

from ..clients.akshare import AkShareClient
from ..config import get_settings
from ..exceptions import ValidationException
from ..utils.data_validators import OHLCValidator
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
    description="股票市场，仅支持: sh(上证), sz(深证), hk(港股), us(美股), 不支持加密货币",
)


def search(
    keyword: str = Field(
        description="搜索关键词，公司名称、股票名称、股票代码、证券简称"
    ),
    market: str = field_market,
) -> str:
    """根据股票名称、公司名称等关键词查找股票代码。

    该工具比较耗时，当你知道股票代码或用户已指定股票代码时，建议直接通过股票代码使用其他工具。
    不支持加密货币。

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

    不支持加密货币。

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
            # 港股
            all_data = client.call(ak.stock_hk_security_profile_em, symbol=symbol, ttl=43200)
            if all_data is not None and not all_data.empty:
                return all_data.to_string()

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
        return f"获取失败: {str(e)}"


def stock_prices(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field(
        "daily", description="周期，如: daily(日线), weekly(周线), monthly(月线，不支持美股)"
    ),
    limit: int = Field(30, description="返回数量(int)", strict=False),
    ma_periods: str = Field("", description="均线周期列表，逗号分隔，如: 5,10,20,30,60,120,250 或斐波那契: 5,8,13,21,34,55,89,144,233"),
    include_trend: bool = Field(False, description="是否包含趋势指标(ADX/DI+/DI-/TREND/LINREG/SLOPE)"),
) -> str:
    """根据股票代码和市场获取股票历史价格及技术指标。

    不支持加密货币。

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
        Validators.validate_limit(limit, min_val=1, max_val=300)

        # 解析均线周期
        parsed_ma_periods = None
        if ma_periods and ma_periods.strip():
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
            dfs = client.call(
                ak.stock_hk_hist,
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
                ttl=3600
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
            # 验证 OHLC 数据
            dfs, is_valid, warnings = OHLCValidator.validate(dfs)
            if not is_valid:
                for warning in warnings:
                    logger.warning(f"OHLC 数据验证警告 ({symbol}.{market}): {warning}")

            # 添加技术指标
            add_technical_indicators(
                dfs, dfs["收盘"], dfs["最低"], dfs["最高"],
                ma_periods=parsed_ma_periods,
                include_trend=include_trend,
            )

            # 选择要返回的列
            columns = [
                "日期",
                "开盘",
                "收盘",
                "最高",
                "最低",
                "成交量",
                "换手率",
                "MACD",
                "DIF",
                "DEA",
                "KDJ.K",
                "KDJ.D",
                "KDJ.J",
                "RSI",
                "BOLL.U",
                "BOLL.M",
                "BOLL.L",
            ]
            
            # 添加均线列
            if parsed_ma_periods:
                for p in parsed_ma_periods:
                    columns.append(f"MA{p}")
            
            # 添加趋势指标列
            if include_trend:
                columns.extend(["ADX", "DI+", "DI-", "TREND", "LINREG", "SLOPE"])

            # 转换为 CSV 格式
            all_lines = (
                dfs.to_csv(columns=columns, index=False, float_format="%.2f")
                .strip()
                .split("\n")
            )
            return "\n".join([all_lines[0], *all_lines[-limit:]])

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
        dfs = client.call(ak.stock_financial_abstract_ths, symbol=symbol)

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
    优先使用东财接口（数据更丰富），失败时自动切换到新浪备用接口。
    默认返回最近5个交易日的分时数据，以减少token消耗。

    Args:
        symbol: 股票代码
        market: 市场类型（sh/sz）
        period: 分钟周期（1/5/15/30/60）
        adjust: 复权类型（空字符串/qfq/hfq），注意1分钟数据不支持复权
        trading_days: 返回最近N个交易日的数据（默认5，最大30）

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
        if adjust not in ["", "qfq", "hfq"]:
            return "参数错误: adjust 仅支持空字符串(不复权), qfq(前复权), hfq(后复权)"
        
        # 处理并限制交易日数量（默认3天，最大10天）
        if not isinstance(trading_days, int):
            trading_days = 3  # 默认值
        trading_days = max(1, min(trading_days, 10))

        logger.info(f"获取A股分时数据: symbol={symbol}, market={market}, period={period}, adjust={adjust}, trading_days={trading_days}")

        client = get_akshare_client()
        
        # 尝试获取数据，如果交易日不足则扩大范围重试
        max_retries = 3
        calendar_days = 7  # 初始获取7个自然日的数据
        dfs = None
        
        for retry in range(max_retries):
            start_date = (datetime.now() - timedelta(days=calendar_days)).strftime("%Y-%m-%d 09:30:00")
            end_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 优先尝试东财接口（数据更丰富）
            try:
                logger.info(f"尝试东财接口获取分时数据，时间范围: {start_date} 至 {end_date} (第{retry+1}次尝试)")
                dfs = client.call(
                    ak.stock_zh_a_hist_min_em,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    period=period,
                    adjust=adjust,
                    ttl=300  # 5分钟缓存
                )
                if dfs is not None and not dfs.empty:
                    logger.info(f"东财接口成功获取数据，共{len(dfs)}条记录")
            except Exception as e:
                logger.warning(f"东财接口失败: {e}")
            
            # 如果东财接口失败，尝试新浪备用接口
            if dfs is None or dfs.empty:
                try:
                    logger.info("尝试新浪备用接口获取分时数据")
                    # 格式化 symbol 为小写市场前缀加代码（如 sz000876）
                    full_symbol = f"{market.lower()}{symbol}"
                    dfs = client.call(
                        ak.stock_zh_a_minute,
                        symbol=full_symbol,
                        period=period,
                        adjust=adjust,
                        ttl=300  # 5分钟缓存
                    )
                    if dfs is not None and not dfs.empty:
                        logger.info(f"新浪备用接口成功获取数据，共{len(dfs)}条记录")
                        # 统一新浪接口的列名为中文
                        dfs = _normalize_sina_columns(dfs)
                        # 新浪接口返回所有数据，需要手动过滤
                        time_col = dfs.columns[0]
                        dfs[time_col] = pd.to_datetime(dfs[time_col], errors='coerce')
                        cutoff_date = datetime.now() - timedelta(days=calendar_days)
                        dfs = dfs[dfs[time_col] >= cutoff_date]
                        logger.info(f"过滤后剩余{len(dfs)}条记录")
                except Exception as e:
                    logger.error(f"新浪备用接口也失败: {e}")
            
            # 检查是否获取到数据
            if dfs is None or dfs.empty:
                if retry < max_retries - 1:
                    calendar_days += 7  # 扩大7天继续尝试
                    logger.warning(f"未获取到数据，扩大范围到{calendar_days}天后重试")
                    continue
                else:
                    return f"Not Found for {symbol}.{market}"
            
            # 检查交易日数量
            time_col = dfs.columns[0]
            dfs[time_col] = pd.to_datetime(dfs[time_col], errors='coerce')
            dfs['_trade_date'] = dfs[time_col].dt.date
            unique_dates = sorted(dfs['_trade_date'].unique(), reverse=True)
            actual_trading_days = len(unique_dates)
            
            logger.info(f"获取到{actual_trading_days}个交易日的数据")
            
            # 如果交易日数量不足，且还有重试机会，则扩大范围重试
            if actual_trading_days < trading_days and retry < max_retries - 1:
                calendar_days += 7  # 扩大7天
                logger.warning(f"交易日数量不足（{actual_trading_days}<{trading_days}），扩大范围到{calendar_days}天后重试")
                dfs = None  # 清空数据，准备重试
                continue
            
            # 数据充足或已达到最大重试次数，跳出循环
            break
        
        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}.{market}"
        
        # 过滤最近N个交易日的数据
        time_col = dfs.columns[0]
        if '_trade_date' not in dfs.columns:
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


# 辅助函数


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
) -> Optional[pd.Series]:
    """搜索股票信息（内部辅助函数）。

    Args:
        symbol: 股票代码（可选）
        keyword: 搜索关键词（可选）
        market: 市场类型（可选）

    Returns:
        Optional[pd.Series]: 股票信息或 None
    """
    client = get_akshare_client()

    markets = [
        ["sh", ak.stock_info_a_code_name, "code", "name"],
        ["sh", ak.stock_info_sh_name_code, "证券代码", "证券简称"],
        ["sz", ak.stock_info_sz_name_code, "A股代码", "A股简称"],
        ["hk", ak.stock_hk_spot, "代码", "中文名称"],
        ["hk", ak.stock_hk_spot_em, "代码", "名称"],
        ["us", ak.get_us_stock_name, "symbol", "cname"],
        ["us", ak.get_us_stock_name, "symbol", "name"],
        ["sh", ak.fund_etf_spot_ths, "基金代码", "基金名称"],
        ["sz", ak.fund_etf_spot_ths, "基金代码", "基金名称"],
        ["sh", ak.fund_info_index_em, "基金代码", "基金名称"],
        ["sz", ak.fund_info_index_em, "基金代码", "基金名称"],
        ["sh", ak.fund_etf_spot_em, "代码", "名称"],
        ["sz", ak.fund_etf_spot_em, "代码", "名称"],
    ]

    for m in markets:
        if market and market != m[0]:
            continue

        all_data = client.call(m[1], ttl=86400, ttl2=86400 * 7)
        if all_data is None or all_data.empty:
            continue

        # 精确匹配
        for _, v in all_data.iterrows():
            code, name = str(v[m[2]]).upper(), str(v[m[3]]).upper()
            if symbol and symbol.upper() == code:
                return v
            if keyword and keyword.upper() in [code, name]:
                return v

        # 模糊匹配
        if keyword:
            for _, v in all_data.iterrows():
                name = str(v[m[3]])
                if len(keyword) >= 4 and keyword in name:
                    return v
                if name.startswith(keyword):
                    return v

    return None
