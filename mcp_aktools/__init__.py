"""MCP AkTools - 基于 akshare 的 Model Context Protocol 服务器。

此包提供股票数据查询与分析功能。

主要模块：
- server: MCP 服务器创建和运行
- config: 配置管理
- exceptions: 自定义异常类
- tools: 工具函数（stock, market, news）
- clients: API 客户端（akshare）
- utils: 工具函数（validators, rate_limiter, retry, indicators）
- cache: 缓存管理
"""

from mcp_aktools.version import get_version

__version__ = get_version()

import argparse
import os

from mcp_aktools.config import get_settings, reload_settings
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

_LOGGER = get_logger(__name__)
mcp = create_mcp_server()


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
    "get_settings",
    "reload_settings",
    "main",
]


if __name__ == "__main__":
    main()
