"""MCP 服务器初始化和配置。"""

from typing import Optional

from fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware

from .config import get_settings
from .logging_config import get_logger, setup_logging
from .version import get_version

# 初始化日志系统
setup_logging()
logger = get_logger(__name__)


def create_mcp_server() -> FastMCP:
    """创建并配置 MCP 服务器实例。"""
    app_version = get_version()
    mcp = FastMCP(name="mcp-aktools", version=app_version)

    logger.info("MCP server created: name=mcp-aktools, version=%s", app_version)

    register_tools(mcp)

    return mcp


def register_tools(mcp: FastMCP) -> None:
    """注册所有工具函数。"""
    from pydantic import Field

    field_symbol = Field(description="股票代码")
    field_market = Field(
        "sh",
        description="股票市场，仅支持: sh(上证), sz(深证), hk(港股), us(美股)",
    )

    from .tools import market as market_tools
    from .tools import news as news_tools
    from .tools import stock as stock_tools

    logger.info("Registering tools from modules: stock, market, news")

    @mcp.tool(
        title="查找股票代码",
        description="根据股票名称、公司名称等关键词查找股票代码。"
        "该工具比较耗时，当你知道股票代码或用户已指定股票代码时，建议直接通过股票代码使用其他工具",
    )
    def search(
        keyword: str = Field(
            description="搜索关键词，公司名称、股票名称、股票代码、证券简称"
        ),
        market: str = field_market,
    ):
        return stock_tools.search(keyword=keyword, market=market)

    @mcp.tool(
        title="获取股票信息",
        description="根据股票代码和市场获取股票基本信息",
    )
    def stock_info(
        symbol: str = field_symbol,
        market: str = field_market,
    ):
        return stock_tools.stock_info(symbol=symbol, market=market)

    @mcp.tool(
        title="获取股票历史价格",
        description="根据股票代码和市场获取股票日线、周线或月线级别的历史价格及技术指标。"
        "此接口用于获取日线(daily)、周线(weekly)或月线(monthly)数据，不适用于分钟级别数据。"
        "如需分钟级别的分时数据，请使用 stock_zh_a_minute 接口。"
        "可通过ma_periods添加均线系统，如常用均线5,10,20,30,60,120,250或斐波那契均线5,8,13,21,34,55,89,144,233。"
        "可通过include_trend添加趋势指标(BBI/TREND/LINREG/SLOPE)。",
    )
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
    ):
        return stock_tools.stock_prices(
            symbol=symbol, market=market, period=period, limit=limit,
            ma_periods=ma_periods, include_trend=include_trend,
            response_format=response_format,
        )

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
        title="A股分时K线数据",
        description="获取A股分钟级别的分时K线数据，支持1/5/15/30/60分钟周期。"
        "此接口仅用于获取分时数据，仅在用户明确请求分时数据、分钟数据或分钟K线时使用。"
        "如需日线、周线或月线数据，请使用 stock_prices 接口。"
        "优先使用新浪接口，失败或无所需区间数据时尝试东财。默认返回最近3个交易日数据",
    )
    def stock_zh_a_minute(
        symbol: str = field_symbol,
        market: str = field_market,
        period: str = Field("5", description="分钟周期，仅支持: 1, 5, 15, 30, 60"),
        adjust: str = Field(
            "", description="复权类型: 空字符串(不复权), qfq(前复权), hfq(后复权)；1分钟仅支持不复权"
        ),
        trading_days: int = Field(3, description="返回最近N个交易日的数据，默认3天，最大10天", strict=False),
    ):
        return stock_tools.stock_zh_a_minute(
            symbol=symbol, market=market, period=period, adjust=adjust, trading_days=trading_days
        )

    @mcp.tool(
        name="stock_tick_data",
        title="A股分笔成交数据",
        description="获取A股最近一个交易日的分笔成交数据，支持智能筛选：all(全部), big_buy(大单买入), big_sell(大单卖出), big_all(所有大单), summary(汇总统计)。推荐使用summary查看概况，或筛选大单以减少token消耗",
    )
    def stock_tick_data(
        symbol: str = field_symbol,
        filter_type: str = Field(
            "summary",
            description="筛选类型: all(全部), big_buy(大单买入), big_sell(大单卖出), big_all(所有大单), summary(汇总统计)",
        ),
        min_volume: int = Field(500, description="大单最小手数阈值，默认500手", strict=False),
    ):
        return stock_tools.stock_tick_data(
            symbol=symbol, filter_type=filter_type, min_volume=min_volume
        )

    @mcp.tool(
        name="stock_quote_realtime",
        title="个股/批量实时五档盘口快照",
        description="基于腾讯高速行情接口获取单只或多只A股/ETF的毫秒级实时快照，包含最新价、涨跌幅、五档买卖盘口、外盘/内盘、换手率、量比、PE(TTM)、PB、总市值与流通市值。完全不依赖东方财富，极速防封",
    )
    def stock_quote_realtime(
        symbols: str = Field(
            description="股票或ETF代码，支持逗号分隔批量查询，如 '600519,000001,510300'"
        ),
    ):
        return stock_tools.stock_quote_realtime(symbols=symbols)

    @mcp.tool(
        name="analyze_wyckoff_structure",
        title="现代威科夫与聪明钱(SMC)量价结构分析",
        description="结合现代威科夫(Modern Wyckoff)与聪明钱(SMC)体系，对目标股票进行深度量价与市场结构诊断：包含Effort vs Result(放量滞涨/缩量企稳)、Spring/Upthrust流动性掠夺(Sweep)、MSS结构转变、FVG失衡缺口、OB订单块及筹码POC/VAH/VAL位置",
    )
    def analyze_wyckoff_structure(
        symbol: str = field_symbol,
        market: str = field_market,
        period: str = Field(
            "daily", description="分析周期: daily(日线)、weekly(周线)、monthly(月线)"
        ),
        lookback: int = Field(
            90, description="回溯K线根数，默认90根(范围30-250)", strict=False
        ),
    ):
        return stock_tools.analyze_wyckoff_structure(
            symbol=symbol, market=market, period=period, lookback=lookback
        )

    @mcp.tool(
        name="stock_volume_profile",
        title="筹码分布与成交量分布(Volume Profile)",
        description="计算股票在指定回溯周期内的成交量分布(Volume Profile / 筹码峰结构)，识别控制点(POC)、70%价值区上下沿(VAH/VAL)、高量节点(HVN支撑阻力)与低量真空区(LVN)及获利盘比例",
    )
    def stock_volume_profile(
        symbol: str = field_symbol,
        market: str = field_market,
        lookback: int = Field(
            90, description="统计回溯交易日数量，默认90天(范围20-250)", strict=False
        ),
        bins: int = Field(20, description="价格分箱数量，默认20", strict=False),
    ):
        return stock_tools.stock_volume_profile(
            symbol=symbol, market=market, lookback=lookback, bins=bins
        )

    @mcp.tool(
        name="stock_valuation_dividend",
        title="历史估值分位与分红派息汇总",
        description="获取股票的历史估值(PE/PB/市值)当前所处百分位水平，以及历年分红派息记录（优先使用百度股市通与新浪财经，无需访问东方财富）",
    )
    def stock_valuation_dividend(
        symbol: str = field_symbol,
        market: str = field_market,
        period: str = Field(
            "近三年",
            description="估值分位统计周期: 近一年, 近三年, 近五年, 近十年, 全部",
        ),
    ):
        return stock_tools.stock_valuation_dividend(
            symbol=symbol, market=market, period=period
        )

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
        date: str = Field(
            "", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"
        ),
        limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
    ):
        return market_tools.stock_zt_pool_em(date=date, limit=limit)

    @mcp.tool(
        title="A股强势股池",
        description="获取中国A股市场(上证、深证)的强势股池数据",
    )
    def stock_zt_pool_strong_em(
        date: str = Field(
            "", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"
        ),
        limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
    ):
        return market_tools.stock_zt_pool_strong_em(date=date, limit=limit)

    @mcp.tool(
        title="A股炸板股池",
        description="获取中国A股市场(上证、深证)的炸板股池数据，包含曾经涨停但后来打开涨停的股票。注意：只能获取最近30个交易日的数据",
    )
    def stock_zt_pool_zbgc_em(
        date: str = Field(
            "", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"
        ),
        limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
    ):
        return market_tools.stock_zt_pool_zbgc_em(date=date, limit=limit)

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
        title="A股实时行情",
        description="获取中国A股市场(上证、深证)的实时行情数据，包括股票代码、名称、最新价、涨跌幅、成交量、成交额等信息",
    )
    def stock_zh_a_spot_em(
        limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
    ):
        return market_tools.stock_zh_a_spot_em(limit=limit)

    @mcp.tool(
        title="融资融券数据",
        description="获取中国A股市场(上证、深证)的融资融券数据，包括上海和深圳两市的融资买入额、融资余额、融券余量、融券余额、融资融券余额等信息",
    )
    def margin_trading(
        limit: int = Field(30, description="返回数量(int)，默认30条", strict=False),
    ):
        return market_tools.margin_trading(limit=limit)

    @mcp.tool(
        title="A股指数历史行情",
        description="获取A股指数历史行情数据，包括上证指数(000001)、深证成指(399001)、创业板指(399006)、沪深300(000300)等。返回OHLCV数据及MACD、KDJ、RSI、BOLL等技术指标",
    )
    def index_zh_a_hist(
        symbol: str = Field(description="指数代码，如 000001(上证指数), 399001(深证成指), 000300(沪深300)"),
        period: str = Field("daily", description="周期，支持: daily(日线), weekly(周线), monthly(月线)"),
        limit: int = Field(30, description="返回数量(int)", strict=False),
    ):
        return market_tools.index_zh_a_hist(symbol=symbol, period=period, limit=limit)

    @mcp.tool(
        title="个股资金流向历史",
        description="获取单个股票的历史资金流向数据，包括主力净流入、超大单净流入、大单净流入、中单净流入、小单净流入等信息（东财限流时自动降级至同花顺或分笔大单统计）",
    )
    def stock_individual_fund_flow(
        symbol: str = field_symbol,
        market: str = Field("sh", description="股票市场，仅支持: sh(上证), sz(深证)"),
        limit: int = Field(30, description="返回最近N个交易日的数据，默认30条", strict=False),
    ):
        return market_tools.stock_individual_fund_flow(
            symbol=symbol, market=market, limit=limit
        )

    @mcp.tool(
        name="stock_sector_fund_flow_rank",
        title="A股行业与概念板块资金流排行",
        description="获取A股行业板块或概念板块的资金流向与涨跌幅排行榜。优先采用同花顺(10jqka)与新浪财经接口，规避东方财富高频封禁，支持即时、3日、5日、10日、20日资金流排行",
    )
    def stock_sector_fund_flow_rank(
        sector_type: str = Field(
            "industry",
            description="板块类型: industry(行业板块资金流), concept(概念板块资金流), ths_summary(同花顺行业总览), sina_sector(新浪行业行情)",
        ),
        period: str = Field(
            "即时",
            description="统计周期: 即时, 3日排行, 5日排行, 10日排行, 20日排行",
        ),
        limit: int = Field(30, description="返回数量(int, 5-100)", strict=False),
    ):
        return market_tools.stock_sector_fund_flow_rank(
            sector_type=sector_type, period=period, limit=limit
        )

    @mcp.tool(
        title="获取股票相关新闻",
        description="根据股票代码获取近期相关新闻",
    )
    def stock_news(
        symbol: str = Field(description="股票代码"),
        limit: int = Field(15, description="返回数量(int)", strict=False),
    ):
        return news_tools.stock_news(symbol=symbol, limit=limit)

    @mcp.tool(
        title="全球财经快讯",
        description="获取最新的全球财经快讯",
    )
    def stock_news_global():
        return news_tools.stock_news_global()

    @mcp.tool(
        title="获取交易规则",
        description="获取用户配置的交易规则文件内容，在分析走势和买入卖出点时应以此规则为最高基准",
    )
    def get_trading_rules():
        from .config import get_trading_rules

        rules = get_trading_rules()
        if rules:
            return f"交易规则内容:\n{rules}"
        return "未配置交易规则文件，请在环境变量中设置 TRADING_RULE_FILE 指定规则文件路径"

    @mcp.tool(
        title="给出投资建议",
        description="基于AI对其他工具提供的数据分析结果给出具体投资建议。注意：应先调用 get_trading_rules 获取用户配置的交易规则，并以此为最高基准进行判断",
    )
    def trading_suggest(
        symbol: str = Field(description="股票代码"),
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

    from .tools import backtest as backtest_tools

    @mcp.tool(
        name="run_backtest",
        title="运行量化回测",
        description="运行量化回测，接受策略代码字符串，解析并执行回测，返回格式化结果",
    )
    def run_backtest(
        stock_code: str = Field(description="股票代码，格式: sh.XXXXXX 或 sz.XXXXXX"),
        start_date: str = Field(description="开始日期，格式: YYYY-MM-DD"),
        end_date: str = Field(description="结束日期，格式: YYYY-MM-DD"),
        strategy_code: str = Field(description="策略代码，必须定义继承自 Strategy 的类"),
        initial_capital: float = Field(1000000, description="初始资金(int)"),
        frequency: str = Field("d", description="K线周期，仅支持: [1/5/15/30/60/d/w/m]"),
        commission_rate: float = Field(0.000854, description="手续费率"),
        slippage_min: float = Field(0.0001, description="滑点最小值"),
        slippage_max: float = Field(0.003, description="滑点最大值"),
        stop_loss: float = Field(None, description="止损比例，如 0.05 表示 -5%"),
        take_profit: float = Field(None, description="止盈比例，如 0.15 表示 +15%"),
        enable_pyramiding: bool = Field(False, description="是否启用金字塔加仓"),
        max_pyramid_layers: int = Field(3, description="最大加仓层数(int)"),
        pyramid_scale_factor: float = Field(0.5, description="加仓比例递减因子"),
        realtime_indicator: bool = Field(False, description="是否使用实时指标模式"),
        signal_priority: str = Field("buy_first", description="信号优先级: buy_first/sell_first/none"),
        random_seed: int = Field(None, description="随机种子，设置后滑点可复现，便于复现与策略对比"),
    ):
        return backtest_tools.run_backtest(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            strategy_code=strategy_code,
            initial_capital=initial_capital,
            frequency=frequency,
            commission_rate=commission_rate,
            slippage_min=slippage_min,
            slippage_max=slippage_max,
            stop_loss=stop_loss,
            take_profit=take_profit,
            enable_pyramiding=enable_pyramiding,
            max_pyramid_layers=max_pyramid_layers,
            pyramid_scale_factor=pyramid_scale_factor,
            realtime_indicator=realtime_indicator,
            signal_priority=signal_priority,
            random_seed=random_seed,
        )

    @mcp.tool(
        name="compare_strategies",
        title="对比回测策略",
        description="对比多个回测结果，并排显示指标对比",
    )
    def compare_strategies(
        result_ids: str = Field(description="逗号分隔的回测结果 ID 列表，如: 1,2,3"),
    ):
        return backtest_tools.compare_strategies(result_ids=result_ids)

    @mcp.tool(
        name="register_indicator",
        title="注册自定义指标",
        description="注册自定义技术指标，接受指标名称、描述和函数代码",
    )
    def register_indicator(
        name: str = Field(description="指标名称"),
        description: str = Field(description="指标描述"),
        function_code: str = Field(description="计算指标的 Python 代码，需要设置 result 变量"),
    ):
        return backtest_tools.register_indicator(
            name=name,
            description=description,
            function_code=function_code,
        )

    @mcp.tool(
        name="evaluate_signals",
        title="评估技术信号",
        description="评估指定股票在特定日期的技术信号状态，包括 MACD、KDJ、RSI、BOLL 等",
    )
    def evaluate_signals(
        stock_code: str = Field(description="股票代码，格式: sh.XXXXXX 或 sz.XXXXXX"),
        date: str = Field("", description="评估日期，格式: YYYY-MM-DD，默认为最近交易日"),
    ):
        return backtest_tools.evaluate_signals(stock_code=stock_code, date=date)

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
    port: Optional[int] = None,
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
    settings = get_settings()

    transport = transport or settings.transport
    host = host or settings.host
    port = port or settings.port

    logger.info(f"Starting MCP server: transport={transport}, host={host}, port={port}")

    if transport == "http":
        app = mcp.http_app()
        configure_cors(app)
        mcp.run(transport="http", host=host, port=port)
    else:
        mcp.run()
