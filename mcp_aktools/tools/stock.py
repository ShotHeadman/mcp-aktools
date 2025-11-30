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
from ..utils.indicators import add_technical_indicators
from ..utils.validators import Validators

logger = logging.getLogger(__name__)

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
        "daily", description="周期，如: daily(日线), weekly(周线，不支持美股)"
    ),
    limit: int = Field(30, description="返回数量(int)", strict=False),
) -> str:
    """根据股票代码和市场获取股票历史价格及技术指标。

    不支持加密货币。

    Args:
        symbol: 股票代码
        market: 市场类型（sh/sz/hk/us）
        period: 周期（daily/weekly）
        limit: 返回数量

    Returns:
        str: CSV 格式的价格和技术指标数据
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        Validators.validate_market(market)
        Validators.validate_period(period)
        Validators.validate_limit(limit, min_val=1, max_val=300)

        logger.info(
            f"获取股票价格: symbol={symbol}, market={market}, period={period}, limit={limit}"
        )

        # 计算开始日期
        if period == "weekly":
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

            # 添加技术指标
            add_technical_indicators(dfs, dfs["收盘"], dfs["最低"], dfs["最高"])

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
) -> str:
    """获取A股分时K线数据。

    获取指定股票的历史分时数据，支持1/5/15/30/60分钟周期。
    优先使用东财接口（数据更丰富），失败时自动切换到新浪备用接口。
    返回所有可用的分时数据。

    Args:
        symbol: 股票代码
        market: 市场类型（sh/sz）
        period: 分钟周期（1/5/15/30/60）
        adjust: 复权类型（空字符串/qfq/hfq），注意1分钟数据不支持复权

    Returns:
        str: CSV 格式的分时数据
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

        logger.info(f"获取A股分时数据: symbol={symbol}, market={market}, period={period}, adjust={adjust}")

        client = get_akshare_client()
        dfs = None
        
        # 优先尝试东财接口（数据更丰富）
        try:
            logger.info("尝试东财接口获取分时数据")
            dfs = client.call(
                ak.stock_zh_a_hist_min_em,
                symbol=symbol,
                start_date="1979-09-01 09:32:00",
                end_date="2222-01-01 09:32:00",
                period=period,
                adjust=adjust,
                ttl=300  # 5分钟缓存
            )
            if dfs is not None and not dfs.empty:
                logger.info("东财接口成功获取数据")
        except Exception as e:
            logger.warning(f"东财接口失败: {e}")
        
        # 如果东财接口失败，尝试新浪备用接口
        if dfs is None or dfs.empty:
            try:
                logger.info("尝试新浪备用接口获取分时数据")
                # 新浪接口需要格式: sh600000 或 sz000001
                full_symbol = f"{market.lower()}{symbol}"
                dfs = client.call(
                    ak.stock_zh_a_minute,
                    symbol=full_symbol,
                    period=period,
                    adjust=adjust,
                    ttl=300  # 5分钟缓存
                )
                if dfs is not None and not dfs.empty:
                    logger.info("新浪备用接口成功获取数据")
            except Exception as e:
                logger.error(f"新浪备用接口也失败: {e}")

        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}.{market}"

        # 返回所有数据
        return dfs.to_csv(index=False, float_format="%.2f")

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取A股分时数据失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_intraday_em(
    symbol: str = field_symbol,
    filter_type: str = Field("all", description="筛选类型: all(全部), big_buy(大单买入), big_sell(大单卖出), big_all(所有大单), summary(汇总统计)"),
    min_volume: int = Field(500, description="大单最小手数阈值，默认500手"),
) -> str:
    """获取A股日内分笔数据（东财接口）。

    获取指定股票最近一个交易日的实时分笔数据，支持智能筛选。
    数据包括：时间、成交价、手数、买卖盘性质。
    
    筛选选项：
    - all: 返回所有分笔数据（可能有4000+条，token消耗大）
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

        logger.info(f"获取A股日内分笔数据: symbol={symbol}, filter_type={filter_type}, min_volume={min_volume}")

        client = get_akshare_client()
        
        dfs = client.call(
            ak.stock_intraday_em,
            symbol=symbol,
            ttl=60  # 1分钟缓存
        )

        if dfs is None or dfs.empty:
            return f"Not Found for {symbol}"

        total_rows = len(dfs)
        
        # 根据筛选类型处理数据
        if filter_type == "all":
            # 返回全部数据
            info = f"# 共{total_rows}条分笔数据\n"
            return info + dfs.to_csv(index=False, float_format="%.2f")
        
        elif filter_type == "summary":
            # 返回汇总统计
            total_volume = dfs['手数'].sum()
            total_amount = (dfs['成交价'] * dfs['手数'] * 100).sum()  # 手数转股数
            avg_price = dfs['成交价'].mean()
            
            # 买卖盘统计
            buy_count = len(dfs[dfs['买卖盘性质'] == '买盘'])
            sell_count = len(dfs[dfs['买卖盘性质'] == '卖盘'])
            neutral_count = len(dfs[dfs['买卖盘性质'] == '中性盘'])
            
            buy_volume = dfs[dfs['买卖盘性质'] == '买盘']['手数'].sum()
            sell_volume = dfs[dfs['买卖盘性质'] == '卖盘']['手数'].sum()
            
            # 大单统计
            big_orders = dfs[dfs['手数'] >= min_volume]
            big_count = len(big_orders)
            big_volume = big_orders['手数'].sum()
            big_buy_count = len(big_orders[big_orders['买卖盘性质'] == '买盘'])
            big_sell_count = len(big_orders[big_orders['买卖盘性质'] == '卖盘'])
            big_buy_volume = big_orders[big_orders['买卖盘性质'] == '买盘']['手数'].sum()
            big_sell_volume = big_orders[big_orders['买卖盘性质'] == '卖盘']['手数'].sum()
            
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
- 买卖比: {buy_volume/sell_volume:.2f} (买盘/卖盘)

## 大单统计 (>={min_volume}手)
- 大单笔数: {big_count} ({big_count/total_rows*100:.1f}%)
- 大单成交量: {big_volume:,} 手 ({big_volume/total_volume*100:.1f}%)
- 大单买入: {big_buy_count} 笔, {big_buy_volume:,} 手
- 大单卖出: {big_sell_count} 笔, {big_sell_volume:,} 手
- 大单买卖比: {big_buy_volume/big_sell_volume if big_sell_volume > 0 else 0:.2f}
"""
            return summary
        
        else:
            # 筛选大单
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
            
            return info + result.to_csv(index=False, float_format="%.2f")

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取A股日内分笔数据失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


# 辅助函数


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
    return dfs[start_date:"2222-01-01"]


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
