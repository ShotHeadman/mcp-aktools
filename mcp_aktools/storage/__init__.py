"""存储模块。

该模块提供数据持久化功能，包括：
- SQLite 数据库存储
- 数据库迁移管理

注意：SQLiteStorage 将在任务 13 中实现。
"""

__all__ = [
    "SQLiteStorage",
]


def __getattr__(name: str):
    """延迟导入机制，支持按需加载组件。"""
    if name == "SQLiteStorage":
        from mcp_aktools.storage.sqlite import SQLiteStorage
        return SQLiteStorage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
