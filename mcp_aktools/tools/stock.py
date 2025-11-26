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
