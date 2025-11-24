import os
import time
import json
import logging
import akshare as ak
import argparse
import requests
import pandas as pd
from fastmcp import FastMCP
from pydantic import Field
from datetime import datetime, timedelta
from starlette.middleware.cors import CORSMiddleware
from mcp_aktools.cache import CacheKey
from mcp_aktools.logging_config import setup_logging, get_logger

# 初始化日志系统
setup_logging()

_LOGGER = get_logger(__name__)

mcp = FastMCP(name="mcp-aktools", version="0.1.13")

field_symbol = Field(description="股票代码")
field_market = Field("sh", description="股票市场，仅支持: sh(上证), sz(深证), hk(港股), us(美股), 不支持加密货币")

OKX_BASE_URL = os.getenv("OKX_BASE_URL") or "https://www.okx.com"
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL") or "https://www.binance.com"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10) AppleWebKit/537.36 Chrome/139"


# 导入股票工具模块
from mcp_aktools.tools import stock as stock_tools
# 导入加密货币工具模块
from mcp_aktools.tools import crypto as crypto_tools
# 导入市场数据工具模块
from mcp_aktools.tools import market as market_tools
# 导入新闻工具模块
from mcp_aktools.tools import news as news_tools

@mcp.tool(
    title="查找股票代码",
    description="根据股票名称、公司名称等关键词查找股票代码, 不支持加密货币。"
                "该工具比较耗时，当你知道股票代码或用户已指定股票代码时，建议直接通过股票代码使用其他工具",
)
def search(
    keyword: str = Field(description="搜索关键词，公司名称、股票名称、股票代码、证券简称"),
    market: str = field_market,
):
    return stock_tools.search(keyword=keyword, market=market)


@mcp.tool(
    title="获取股票信息",
    description="根据股票代码和市场获取股票基本信息, 不支持加密货币",
)
def stock_info(
    symbol: str = field_symbol,
    market: str = field_market,
):
    return stock_tools.stock_info(symbol=symbol, market=market)


@mcp.tool(
    title="获取股票历史价格",
    description="根据股票代码和市场获取股票历史价格及技术指标, 不支持加密货币",
)
def stock_prices(
    symbol: str = field_symbol,
    market: str = field_market,
    period: str = Field("daily", description="周期，如: daily(日线), weekly(周线，不支持美股)"),
    limit: int = Field(30, description="返回数量(int)", strict=False),
):
    return stock_tools.stock_prices(symbol=symbol, market=market, period=period, limit=limit)


# 辅助函数已迁移到 tools/stock.py


@mcp.tool(
    title="获取股票/加密货币相关新闻",
    description="根据股票代码或加密货币符号获取近期相关新闻",
)
def stock_news(
    symbol: str = Field(description="股票代码/加密货币符号"),
    limit: int = Field(15, description="返回数量(int)", strict=False),
):
    return news_tools.stock_news(symbol=symbol, limit=limit)


@mcp.tool(
    title="A股关键指标",
    description="获取中国A股市场(上证、深证)的股票财务报告关键指标",
)
def stock_indicators_a(
    symbol: str = field_symbol,
):
    return stock_tools.stock_indicators_a(symbol=symbol)


@mcp.tool(
    title="港股关键指标",
    description="获取港股市场的股票财务报告关键指标",
)
def stock_indicators_hk(
    symbol: str = field_symbol,
):
    return stock_tools.stock_indicators_hk(symbol=symbol)


@mcp.tool(
    title="美股关键指标",
    description="获取美股市场的股票财务报告关键指标",
)
def stock_indicators_us(
    symbol: str = field_symbol,
):
    return stock_tools.stock_indicators_us(symbol=symbol)


@mcp.tool(
    title="获取当前时间及A股交易日信息",
    description="获取当前系统时间及A股交易日信息，建议在调用其他需要日期参数的工具前使用该工具",
)
def get_current_time():
    return market_tools.get_current_time()


@mcp.tool(
    title="A股涨停股池",
    description="获取中国A股市场(上证、深证)的所有涨停股票",
)
def stock_zt_pool_em(
    date: str = Field("", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
):
    return market_tools.stock_zt_pool_em(date=date, limit=limit)


@mcp.tool(
    title="A股强势股池",
    description="获取中国A股市场(上证、深证)的强势股池数据",
)
def stock_zt_pool_strong_em(
    date: str = Field("", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
):
    return market_tools.stock_zt_pool_strong_em(date=date, limit=limit)


@mcp.tool(
    title="A股龙虎榜统计",
    description="获取中国A股市场(上证、深证)的龙虎榜个股上榜统计数据",
)
def stock_lhb_ggtj_sina(
    days: str = Field("5", description="统计最近天数，仅支持: [5/10/30/60]"),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
):
    return market_tools.stock_lhb_ggtj_sina(days=days, limit=limit)


@mcp.tool(
    title="A股板块资金流",
    description="获取中国A股市场(上证、深证)的行业资金流向数据",
)
def stock_sector_fund_flow_rank(
    days: str = Field("今日", description="天数，仅支持: {'今日','5日','10日'}，如果需要获取今日数据，请确保是交易日"),
    cate: str = Field("行业资金流", description="仅支持: {'行业资金流','概念资金流','地域资金流'}"),
):
    return market_tools.stock_sector_fund_flow_rank(days=days, cate=cate)


@mcp.tool(
    title="全球财经快讯",
    description="获取最新的全球财经快讯",
)
def stock_news_global():
    return news_tools.stock_news_global()


@mcp.tool(
    title="获取加密货币历史价格",
    description="获取OKX加密货币的历史K线数据，包括价格、交易量和技术指标",
)
def okx_prices(
    instId: str = Field("BTC-USDT", description="产品ID，格式: BTC-USDT"),
    bar: str = Field("1H", description="K线时间粒度，仅支持: [1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/2D/3D/1W/1M/3M] 除分钟为小写m外,其余均为大写"),
    limit: int = Field(100, description="返回数量(int)，最大300，最小建议30", strict=False),
):
    return crypto_tools.okx_prices(inst_id=instId, bar=bar, limit=limit)


@mcp.tool(
    title="获取加密货币杠杆多空比",
    description="获取OKX加密货币借入计价货币与借入交易货币的累计数额比值",
)
def okx_loan_ratios(
    symbol: str = Field("BTC", description="币种，格式: BTC 或 ETH"),
    period: str = Field("1H", description="时间粒度，仅支持: [5m/1H/1D] 注意大小写，仅分钟为小写m"),
):
    return crypto_tools.okx_loan_ratios(symbol=symbol, period=period)


@mcp.tool(
    title="获取加密货币主动买卖情况",
    description="获取OKX加密货币主动买入和卖出的交易量",
)
def okx_taker_volume(
    symbol: str = Field("BTC", description="币种，格式: BTC 或 ETH"),
    period: str = Field("1H", description="时间粒度，仅支持: [5m/1H/1D] 注意大小写，仅分钟为小写m"),
    instType: str = Field("SPOT", description="产品类型 SPOT:现货 CONTRACTS:衍生品"),
):
    return crypto_tools.okx_taker_volume(symbol=symbol, period=period, inst_type=instType)


@mcp.tool(
    title="获取加密货币分析报告",
    description="获取币安对加密货币的AI分析报告，此工具对分析加密货币非常有用，推荐使用",
)
def binance_ai_report(
    symbol: str = Field("BTC", description="加密货币币种，格式: BTC 或 ETH"),
):
    return crypto_tools.binance_ai_report(symbol=symbol)


@mcp.tool(
    title="给出投资建议",
    description="基于AI对其他工具提供的数据分析结果给出具体投资建议",
)
def trading_suggest(
    symbol: str = Field(description="股票代码或加密币种"),
    action: str = Field(description="推荐操作: buy/sell/hold"),
    score: int = Field(description="置信度，范围: 0-100"),
    reason: str = Field(description="推荐理由"),
):
    return {
        "symbol": symbol,
        "action": action,
        "score": score,
        "reason": reason,
    }


# ak_search 函数已迁移到 tools/stock.py


def ak_cache(fun, *args, **kwargs) -> pd.DataFrame | None:
    key = kwargs.pop("key", None)
    if not key:
        key = f"{fun.__name__}-{args}-{kwargs}"
    ttl1 = kwargs.pop("ttl", 86400)
    ttl2 = kwargs.pop("ttl2", None)
    cache = CacheKey.init(key, ttl1, ttl2)
    all = cache.get()
    if all is None:
        try:
            _LOGGER.info("Request akshare: %s", [key, args, kwargs])
            all = fun(*args, **kwargs)
            cache.set(all)
        except Exception as exc:
            _LOGGER.exception(str(exc))
    return all

# 技术指标计算已迁移到 utils/indicators.py，使用 pandas-ta 专业库
# 导入新的实现以保持向后兼容
from mcp_aktools.utils.indicators import add_technical_indicators


def main():
    mode = os.getenv("TRANSPORT")
    port = int(os.getenv("PORT", 0)) or 80
    parser = argparse.ArgumentParser(description="AkTools MCP 服务器")
    parser.add_argument("--http", action="store_true", help="使用可流式 HTTP 模式而不是 stdio")
    parser.add_argument("--host", default="0.0.0.0", help="绑定的主机（默认：0.0.0.0）")
    parser.add_argument("--port", type=int, default=port, help=f"监听的端口（默认：{port}）")

    args = parser.parse_args()
    if args.http or mode == "http":
        app = mcp.http_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id", "mcp-protocol-version"],
            max_age=86400,
        )
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()

if __name__ == "__main__":
    main()
