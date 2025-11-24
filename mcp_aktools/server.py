"""MCP AkTools 服务器初始化和配置。

此模块负责：
- 创建和配置 FastMCP 服务器实例
- 自动注册工具函数
- 配置 CORS 中间件
- 集成配置管理
- 集成日志系统
"""

import os
import logging
from typing import Optional
from fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware

from .config import get_settings
from .logging_config import setup_logging, get_logger


# 初始化日志系统
setup_logging()
logger = get_logger(__name__)


def create_mcp_server() -> FastMCP:
    """创建并配置 MCP 服务器实例。
    
    此函数负责：
    1. 创建 FastMCP 服务器实例
    2. 从配置加载设置
    3. 自动注册所有工具函数
    
    Returns:
        FastMCP: 配置好的 MCP 服务器实例
    """
    # 获取配置
    settings = get_settings()
    
    # 创建 MCP 服务器实例
    mcp = FastMCP(name="mcp-aktools", version="0.1.13")
    
    logger.info("MCP server created: name=mcp-aktools, version=0.1.13")
    
    # 注册所有工具函数
    register_tools(mcp)
    
    return mcp


def register_tools(mcp: FastMCP) -> None:
    """自动注册所有工具函数。
    
    此函数从各个工具模块导入工具函数并注册到 MCP 服务器。
    工具函数按功能域组织在不同的模块中：
    - stock: 股票相关工具
    - crypto: 加密货币工具
    - market: 市场数据工具
    - news: 新闻资讯工具
    
    Args:
        mcp: FastMCP 服务器实例
    """
    from pydantic import Field
    
    # 定义常用字段
    field_symbol = Field(description="股票代码")
    field_market = Field("sh", description="股票市场，仅支持: sh(上证), sz(深证), hk(港股), us(美股), 不支持加密货币")
    
    # 导入工具模块
    from .tools import stock as stock_tools
    from .tools import crypto as crypto_tools
    from .tools import market as market_tools
    from .tools import news as news_tools
    
    logger.info("Registering tools from modules: stock, crypto, market, news")
    
    # ========== 股票工具 ==========
    
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
    
    # ========== 加密货币工具 ==========
    
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
    
    # ========== 市场数据工具 ==========
    
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
    
    # ========== 新闻工具 ==========
    
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
        title="全球财经快讯",
        description="获取最新的全球财经快讯",
    )
    def stock_news_global():
        return news_tools.stock_news_global()
    
    # ========== 投资建议工具 ==========
    
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
    
    logger.info("All tools registered successfully")


def configure_cors(app) -> None:
    """配置 CORS 中间件。
    
    此函数为 HTTP 模式的 MCP 服务器配置 CORS 中间件，
    允许跨域请求访问 MCP 服务器。
    
    Args:
        app: Starlette 应用实例
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id", "mcp-protocol-version"],
        max_age=86400,
    )
    
    logger.info("CORS middleware configured")


def run_server(
    mcp: FastMCP,
    transport: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None
) -> None:
    """运行 MCP 服务器。
    
    此函数根据配置启动 MCP 服务器，支持两种传输模式：
    - stdio: 标准输入输出模式（默认）
    - http: HTTP 模式（支持 CORS）
    
    Args:
        mcp: FastMCP 服务器实例
        transport: 传输模式 ("stdio" 或 "http")，如果为 None 则从配置读取
        host: HTTP 模式的绑定主机，如果为 None 则从配置读取
        port: HTTP 模式的监听端口，如果为 None 则从配置读取
    """
    # 获取配置
    settings = get_settings()
    
    # 使用提供的参数或从配置读取
    transport = transport or settings.transport
    host = host or settings.host
    port = port or settings.port
    
    logger.info(f"Starting MCP server: transport={transport}, host={host}, port={port}")
    
    if transport == "http":
        # HTTP 模式：配置 CORS 并启动 HTTP 服务器
        app = mcp.http_app()
        configure_cors(app)
        mcp.run(transport="http", host=host, port=port)
    else:
        # stdio 模式：启动标准输入输出服务器
        mcp.run()
