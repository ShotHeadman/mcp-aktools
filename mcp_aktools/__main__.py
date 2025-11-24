"""MCP AkTools 包入口点。

当使用 `python -m mcp_aktools` 运行时，此模块会被执行。

此模块负责：
- 从命令行启动 MCP 服务器
- 解析命令行参数
- 调用主入口函数
"""

from mcp_aktools import main

if __name__ == "__main__":
    main()
