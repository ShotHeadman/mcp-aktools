"""MCP 工具函数模块。"""

# 导入股票工具
# 导入加密货币工具
from .crypto import (
    binance_ai_report,
    okx_loan_ratios,
    okx_prices,
    okx_taker_volume,
)

# 导入市场数据工具
from .market import (
    get_current_time,
    stock_lhb_ggtj_sina,
    stock_sector_fund_flow_rank,
    stock_zt_pool_em,
    stock_zt_pool_strong_em,
)

# 导入新闻工具
from .news import (
    stock_news,
    stock_news_global,
)
from .stock import (
    search,
    stock_indicators_a,
    stock_indicators_hk,
    stock_indicators_us,
    stock_info,
    stock_prices,
)

__all__ = [
    # 股票工具
    "search",
    "stock_info",
    "stock_prices",
    "stock_indicators_a",
    "stock_indicators_hk",
    "stock_indicators_us",
    # 加密货币工具
    "okx_prices",
    "okx_loan_ratios",
    "okx_taker_volume",
    "binance_ai_report",
    # 市场数据工具
    "get_current_time",
    "stock_zt_pool_em",
    "stock_zt_pool_strong_em",
    "stock_lhb_ggtj_sina",
    "stock_sector_fund_flow_rank",
    # 新闻工具
    "stock_news",
    "stock_news_global",
]
