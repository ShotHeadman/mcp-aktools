"""MCP AkTools - 基于 akshare 的 Model Context Protocol 服务器。

此包提供股票和加密货币的数据查询与分析功能。

主要模块：
- server: MCP 服务器创建和运行
- config: 配置管理
- exceptions: 自定义异常类
- tools: 工具函数（stock, crypto, market, news）
- clients: API 客户端（akshare, okx, binance）
- utils: 工具函数（validators, rate_limiter, retry, indicators）
- cache: 缓存管理
"""

__version__ = "0.1.13"

# ========== 标准库导入 ==========
import argparse
import os

# ========== 第三方库导入 ==========
import pandas as pd

# ========== 核心模块导入 ==========
from mcp_aktools.cache import CacheKey
from mcp_aktools.config import Settings, get_settings, reload_settings
from mcp_aktools.exceptions import (
    APIException,
    CacheException,
    DataParsingException,
    MCPAkToolsException,
    RateLimitException,
    RetryExhaustedException,
    ValidationException,
)
from mcp_aktools.logging_config import get_logger, setup_logging
from mcp_aktools.server import create_mcp_server, run_server

# ========== 工具模块导入（用于向后兼容和直接访问）==========
from mcp_aktools.tools import crypto as crypto_tools
from mcp_aktools.tools import market as market_tools
from mcp_aktools.tools import news as news_tools
from mcp_aktools.tools import stock as stock_tools

# ========== 工具函数导入 ==========
from mcp_aktools.utils.indicators import add_technical_indicators
from mcp_aktools.utils.rate_limiter import RateLimiter
from mcp_aktools.utils.validators import Validators

# ========== 初始化 ==========
# 获取日志记录器
_LOGGER = get_logger(__name__)

# 创建全局 MCP 服务器实例（用于向后兼容）
mcp = create_mcp_server()


# ========== 向后兼容性函数 ==========


def ak_cache(fun, *args, **kwargs) -> pd.DataFrame | None:
    """AkShare 函数缓存包装器（用于向后兼容）。

    此函数为 AkShare 函数调用提供缓存功能。

    Args:
        fun: AkShare 函数
        *args: 位置参数
        **kwargs: 关键字参数（可包含 key, ttl, ttl2）

    Returns:
        pd.DataFrame | None: 函数返回的数据或 None
    """
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


def main():
    """主入口点：解析命令行参数并启动 MCP 服务器。"""
    # 从环境变量读取默认值
    mode = os.getenv("TRANSPORT")
    port = int(os.getenv("PORT", 0)) or 80

    # 解析命令行参数
    parser = argparse.ArgumentParser(description="AkTools MCP 服务器")
    parser.add_argument(
        "--http", action="store_true", help="使用可流式 HTTP 模式而不是 stdio"
    )
    parser.add_argument("--host", default="0.0.0.0", help="绑定的主机（默认：0.0.0.0）")
    parser.add_argument(
        "--port", type=int, default=port, help=f"监听的端口（默认：{port}）"
    )

    args = parser.parse_args()

    # 确定传输模式
    transport = "http" if (args.http or mode == "http") else "stdio"

    # 运行服务器
    run_server(mcp=mcp, transport=transport, host=args.host, port=args.port)


# ========== 公共 API ==========

__all__ = [
    # 版本
    "__version__",
    # 服务器
    "create_mcp_server",
    "run_server",
    "mcp",
    # 配置
    "Settings",
    "get_settings",
    "reload_settings",
    # 异常
    "MCPAkToolsException",
    "APIException",
    "DataParsingException",
    "ValidationException",
    "RateLimitException",
    "RetryExhaustedException",
    "CacheException",
    # 日志
    "setup_logging",
    "get_logger",
    # 工具模块
    "stock_tools",
    "crypto_tools",
    "market_tools",
    "news_tools",
    # 工具函数
    "add_technical_indicators",
    "Validators",
    "RateLimiter",
    # 向后兼容
    "ak_cache",
    "main",
]


if __name__ == "__main__":
    main()
