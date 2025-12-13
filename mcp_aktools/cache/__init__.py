"""缓存管理模块。

此包将包含新的缓存管理器和策略实现。
为了向后兼容，我们从旧的 cache.py 模块重新导出 CacheKey。
"""

# 问题是既有 mcp_aktools/cache.py 又有 mcp_aktools/cache/ 目录
# Python 的导入系统优先考虑包（目录）而不是模块（文件）
# 所以我们需要显式地从父包的缓存模块导入

# 从旧的 cache.py 文件导入 CacheKey 以实现向后兼容
# 我们使用 __import__ 和 fromlist 来获取实际的模块，而不是包
import sys

# Import SQLiteCacheBackend from the new module
from mcp_aktools.cache.sqlite_backend import SQLiteCacheBackend
from mcp_aktools.cache.serialization import (
    serialize_dataframe,
    deserialize_dataframe,
    CacheSerializationError,
)
from mcp_aktools.cache.dividend_checker import DividendChecker, DEFAULT_DIVIDEND_TTL

cache_module = sys.modules.get("mcp_aktools.cache")
if cache_module and hasattr(cache_module, "CacheKey"):
    # 如果已导入，使用它
    CacheKey = cache_module.CacheKey
else:
    # 否则，我们需要以不同的方式导入
    # 由于这个 __init__.py 就是 mcp_aktools.cache，我们不能从自己导入
    # 我们需要直接导入 .py 文件
    import importlib.util
    from pathlib import Path

    cache_py_path = Path(__file__).parent.parent / "cache.py"
    spec = importlib.util.spec_from_file_location(
        "_mcp_aktools_cache_legacy", cache_py_path
    )
    if spec and spec.loader:
        legacy_cache = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy_cache)
        CacheKey = legacy_cache.CacheKey
    else:
        # 如果导入失败则回退
        CacheKey = None

__all__ = [
    "CacheKey",
    "SQLiteCacheBackend",
    "serialize_dataframe",
    "deserialize_dataframe",
    "CacheSerializationError",
    "DividendChecker",
    "DEFAULT_DIVIDEND_TTL",
]
