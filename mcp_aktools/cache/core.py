"""双层缓存管理模块（内存 + SQLite）。

此模块提供 CacheKey 类，实现双层缓存层次结构：
1. 内存缓存（TTLCache）用于快速访问
2. SQLite 缓存用于持久化存储

历史股票数据永久存储在 SQLite 中，不会过期。
缓存失效由公司行为（分红）触发。
"""

import logging
from typing import Any, Dict, Optional

import pandas as pd
from cachetools import TTLCache

logger = logging.getLogger(__name__)

from .dividend_checker import DividendChecker
from .serialization import CacheSerializationError, deserialize_dataframe, serialize_dataframe
from .sqlite_backend import SQLiteCacheBackend


class CacheKey:
    """支持内存 + SQLite 双层缓存的缓存键管理类。
    
    此类提供统一的缓存数据接口，具有以下特性：
    - 快速内存缓存（TTLCache）用于频繁访问的数据
    - 持久化 SQLite 存储用于历史数据
    - 公司行为（分红）时自动缓存失效
    
    属性:
        ALL: 类级别的所有 CacheKey 实例字典。
        _sqlite_backend: 类级别的 SQLite 后端单例。
        _dividend_checker: 类级别的 DividendChecker 单例。
        _sqlite_initialized: 标识 SQLite 后端是否已初始化的标志。
    """
    
    ALL: Dict[str, 'CacheKey'] = {}
    _sqlite_backend: Optional[Any] = None
    _dividend_checker: Optional[Any] = None
    _sqlite_initialized: bool = False

    def __init__(self, key: str, ttl: int = 600, maxsize: int = 100):
        """初始化缓存键。
        
        参数:
            key: 缓存键字符串。
            ttl: 内存缓存 TTL，单位为秒（默认：600）。
            maxsize: 内存缓存的最大容量（默认：100）。
        """
        self.key = key
        self.ttl = ttl
        self.cache1 = TTLCache(maxsize=maxsize, ttl=ttl)
        
        self._symbol = self._extract_symbol_from_key(key)
        self._data_type = self._extract_data_type_from_key(key)

    @staticmethod
    def init(key: str, ttl: int = 600, maxsize: int = 100) -> 'CacheKey':
        """初始化或获取 CacheKey 实例。
        
        这是一个工厂方法，如果给定键已存在 CacheKey 则返回现有实例，
        否则创建新实例。
        
        参数:
            key: 缓存键字符串。
            ttl: 内存缓存 TTL，单位为秒（默认：600）。
            maxsize: 内存缓存的最大容量（默认：100）。
            
        返回:
            给定键的 CacheKey 实例。
        """
        if key in CacheKey.ALL:
            return CacheKey.ALL[key]
        cache = CacheKey(key, ttl, maxsize)
        return CacheKey.ALL.setdefault(key, cache)

    @classmethod
    def init_sqlite_backend(
        cls,
        db_path: Optional[str] = None,
        disk_ttl: Optional[int] = None,
    ) -> None:
        """初始化 SQLite 后端单例。
        
        此方法应在应用程序启动时调用一次，以初始化 SQLite 缓存后端。
        如果未调用，缓存将回退到仅内存模式。
        
        参数:
            db_path: SQLite 数据库文件路径。如果为 None，则使用默认路径。
            disk_ttl: 磁盘缓存 TTL，单位为秒。None 或非正数表示不过期。
        """
        if cls._sqlite_initialized:
            logger.debug("SQLite 后端已初始化")
            return
        
        try:
            cls._sqlite_backend = SQLiteCacheBackend(db_path, disk_ttl=disk_ttl)
            cls._dividend_checker = DividendChecker(cls._sqlite_backend)
            cls._sqlite_initialized = True
            
            logger.info(f"SQLite 缓存后端已初始化，路径: {cls._sqlite_backend.db_path}")
        except Exception as e:
            logger.error(f"初始化 SQLite 后端失败: {e}", exc_info=True)
            cls._sqlite_backend = None
            cls._dividend_checker = None
            cls._sqlite_initialized = False

    @classmethod
    def get_sqlite_backend(cls) -> Optional[Any]:
        """获取 SQLite 后端单例。
        
        返回:
            SQLiteCacheBackend 实例，如果未初始化则返回 None。
        """
        return cls._sqlite_backend

    @classmethod
    def get_dividend_checker(cls) -> Optional[Any]:
        """获取 DividendChecker 单例。
        
        返回:
            DividendChecker 实例，如果未初始化则返回 None。
        """
        return cls._dividend_checker

    def _extract_symbol_from_key(self, key: str) -> Optional[str]:
        """从缓存键中提取股票代码。
        
        缓存键通常遵循以下模式：
        - stock_prices:600000:daily
        - stock_info:000001
        
        参数:
            key: 缓存键字符串。
            
        返回:
            提取的股票代码，如果未找到则返回 None。
        """
        parts = key.split(':')
        if len(parts) >= 2:
            # 第二部分通常是股票代码
            potential_symbol = parts[1]
            # 检查是否看起来像股票代码（仅数字，6 个字符）
            if potential_symbol.isdigit() and len(potential_symbol) == 6:
                return potential_symbol
        return None

    def _extract_data_type_from_key(self, key: str) -> Optional[str]:
        """从缓存键中提取数据类型。
        
        参数:
            key: 缓存键字符串。
            
        返回:
            数据类型字符串，如果未找到则返回 None。
        """
        parts = key.split(':')
        if len(parts) >= 1:
            first_part = parts[0].lower()
            if 'price' in first_part:
                return 'price'
            elif 'hist' in first_part or 'daily' in first_part or 'candle' in first_part:
                return 'price'
            elif 'dividend' in first_part:
                return 'dividend'
            elif 'info' in first_part:
                return 'info'
        return None

    def _is_price_data(self) -> bool:
        """检查此缓存键是否用于价格数据。
        
        返回:
            如果是价格数据返回 True，否则返回 False。
        """
        return self._data_type == 'price'

    def get(self) -> Optional[pd.DataFrame]:
        """获取缓存数据。
        
        实现双层缓存查找：
        1. 首先检查内存缓存
        2. 如果内存未命中，检查 SQLite 缓存
        3. 如果 SQLite 命中，填充内存缓存
        4. 对于价格数据，检查分红失效
        
        返回:
            缓存的 DataFrame，如果未找到则返回 None。
        """
        # 步骤 1：检查内存缓存
        try:
            value = self.cache1[self.key]
            logger.debug(f"缓存命中（内存）: {self.key}")
            return value
        except KeyError:
            pass

        # 步骤 2：如果已初始化，检查 SQLite 缓存
        if CacheKey._sqlite_backend is not None:
            try:
                # 对于价格数据，检查是否需要因分红而失效
                if self._is_price_data() and self._symbol and CacheKey._dividend_checker:
                    if CacheKey._dividend_checker.should_invalidate_cache(self._symbol):
                        logger.info(f"因股票 {self._symbol} 有新分红，缓存已失效")
                        CacheKey._dividend_checker.invalidate_stock_cache(self._symbol)
                        # 同时清除此键的内存缓存
                        self.cache1.pop(self.key, None)
                        return None
                
                # 尝试从 SQLite 获取
                cached_bytes = CacheKey._sqlite_backend.get(self.key)
                if cached_bytes is not None:
                    try:
                        value = deserialize_dataframe(cached_bytes)
                        logger.debug(f"缓存命中（SQLite）: {self.key}")
                        
                        self.cache1[self.key] = value
                        logger.debug(f"已从 SQLite 填充内存缓存: {self.key}")
                        
                        return value
                    except CacheSerializationError as e:
                        logger.error(f"反序列化 {self.key} 的缓存数据失败: {e}")
                        # 删除损坏的缓存条目
                        CacheKey._sqlite_backend.delete(self.key)
                        return None
            except Exception as e:
                logger.error(f"SQLite 缓存查找 {self.key} 失败: {e}", exc_info=True)

        logger.debug(f"缓存未命中: {self.key}")
        return None

    def set(self, val: pd.DataFrame) -> pd.DataFrame:
        """将数据存储到缓存中。
        
        将数据存储到内存缓存和 SQLite 缓存（如果已初始化）。
        
        参数:
            val: 要缓存的 DataFrame。
            
        返回:
            相同的 DataFrame（用于链式调用）。
        """
        try:
            # 存储到内存缓存
            self.cache1[self.key] = val
            logger.debug(f"缓存已设置（内存）: {self.key} (ttl={self.ttl}s)")
            
            # 如果已初始化，存储到 SQLite 缓存
            if CacheKey._sqlite_backend is not None:
                try:
                    serialized_data = serialize_dataframe(val)
                    CacheKey._sqlite_backend.set(
                        key=self.key,
                        value=serialized_data,
                        data_type=self._data_type,
                        symbol=self._symbol
                    )
                    logger.debug(f"缓存已设置（SQLite）: {self.key}")
                except Exception as e:
                    # SQLite 写入失败不应中断操作
                    logger.error(f"SQLite 缓存写入 {self.key} 失败: {e}", exc_info=True)
                    
        except Exception as e:
            # 缓存写入失败不应中断操作
            logger.error(f"缓存写入 {self.key} 失败: {e}", exc_info=True)
            
        return val

    def delete(self) -> None:
        """从内存和 SQLite 缓存中删除缓存数据。
        
        """
        try:
            # 从内存缓存删除
            self.cache1.pop(self.key, None)
            logger.debug(f"缓存已删除（内存）: {self.key}")
            
            # 如果已初始化，从 SQLite 缓存删除
            if CacheKey._sqlite_backend is not None:
                try:
                    CacheKey._sqlite_backend.delete(self.key)
                    logger.debug(f"缓存已删除（SQLite）: {self.key}")
                except Exception as e:
                    logger.error(f"SQLite 缓存删除 {self.key} 失败: {e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"缓存删除 {self.key} 失败: {e}", exc_info=True)

    @classmethod
    def clear_by_symbol(cls, symbol: str) -> int:
        """清除特定股票代码的所有缓存条目。
        
        参数:
            symbol: 要清除缓存的股票代码。
            
        返回:
            清除的条目数量。
        """
        cleared_count = 0
        
        # 从内存缓存清除（遍历所有 CacheKey 实例）
        keys_to_clear = []
        for key, cache_key in cls.ALL.items():
            if cache_key._symbol == symbol:
                keys_to_clear.append(key)
        
        for key in keys_to_clear:
            if key in cls.ALL:
                cls.ALL[key].cache1.pop(key, None)
                cleared_count += 1
        
        # 从 SQLite 缓存清除
        if cls._sqlite_backend is not None:
            try:
                sqlite_cleared = cls._sqlite_backend.delete_by_symbol(symbol)
                cleared_count = max(cleared_count, sqlite_cleared)
                logger.info(f"已清除股票 {symbol} 的 {sqlite_cleared} 条 SQLite 缓存条目")
            except Exception as e:
                logger.error(f"清除股票 {symbol} 的 SQLite 缓存失败: {e}", exc_info=True)
        
        return cleared_count

    @classmethod
    def clear_all(cls) -> int:
        """清除所有缓存条目。
        
        返回:
            清除的条目数量。
        """
        cleared_count = 0
        
        # 清除所有内存缓存
        for cache_key in cls.ALL.values():
            cleared_count += len(cache_key.cache1)
            cache_key.cache1.clear()
        
        # 清除 SQLite 缓存
        if cls._sqlite_backend is not None:
            try:
                sqlite_cleared = cls._sqlite_backend.clear()
                cleared_count = max(cleared_count, sqlite_cleared)
                logger.info(f"已清除所有缓存条目: SQLite 中 {sqlite_cleared} 条")
            except Exception as e:
                logger.error(f"清除 SQLite 缓存失败: {e}", exc_info=True)
        
        return cleared_count

    @classmethod
    def get_statistics(cls) -> Dict[str, Any]:
        """获取缓存统计信息。
        
        返回:
            包含缓存统计信息的字典。
        """
        stats = {
            "memory_cache_count": len(cls.ALL),
            "memory_entries": sum(len(ck.cache1) for ck in cls.ALL.values()),
            "sqlite_initialized": cls._sqlite_initialized,
        }
        
        if cls._sqlite_backend is not None:
            try:
                sqlite_stats = cls._sqlite_backend.get_stats()
                stats.update({
                    "sqlite_entry_count": sqlite_stats.get("entry_count", 0),
                    "sqlite_total_size_bytes": sqlite_stats.get("total_size_bytes", 0),
                    "sqlite_hits": sqlite_stats.get("hits", 0),
                    "sqlite_misses": sqlite_stats.get("misses", 0),
                    "sqlite_hit_rate": sqlite_stats.get("hit_rate", 0.0),
                    "sqlite_db_path": sqlite_stats.get("db_path", ""),
                })
            except Exception as e:
                logger.error(f"获取 SQLite 统计信息失败: {e}", exc_info=True)
                stats["sqlite_error"] = str(e)
        
        return stats
