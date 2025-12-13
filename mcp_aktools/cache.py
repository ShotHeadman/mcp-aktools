"""Cache management module with two-tier caching (Memory + SQLite).

This module provides the CacheKey class that implements a two-tier cache hierarchy:
1. Memory cache (TTLCache) for fast access
2. SQLite cache for persistent storage

Historical stock data is stored permanently in SQLite without expiration.
Cache invalidation is triggered by corporate actions (dividends).
"""

import logging
import pathlib
import sys
from typing import Any, Dict, Optional

import pandas as pd
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependencies
_SQLiteCacheBackend = None
_DividendChecker = None
_serialize_dataframe = None
_deserialize_dataframe = None
_CacheSerializationError = None


def _get_sqlite_backend_class():
    """Lazy import SQLiteCacheBackend to avoid circular imports."""
    global _SQLiteCacheBackend
    if _SQLiteCacheBackend is None:
        from mcp_aktools.cache.sqlite_backend import SQLiteCacheBackend
        _SQLiteCacheBackend = SQLiteCacheBackend
    return _SQLiteCacheBackend


def _get_dividend_checker_class():
    """Lazy import DividendChecker to avoid circular imports."""
    global _DividendChecker
    if _DividendChecker is None:
        from mcp_aktools.cache.dividend_checker import DividendChecker
        _DividendChecker = DividendChecker
    return _DividendChecker


def _get_serialization_functions():
    """Lazy import serialization functions to avoid circular imports."""
    global _serialize_dataframe, _deserialize_dataframe, _CacheSerializationError
    if _serialize_dataframe is None:
        from mcp_aktools.cache.serialization import (
            serialize_dataframe,
            deserialize_dataframe,
            CacheSerializationError,
        )
        _serialize_dataframe = serialize_dataframe
        _deserialize_dataframe = deserialize_dataframe
        _CacheSerializationError = CacheSerializationError
    return _serialize_dataframe, _deserialize_dataframe, _CacheSerializationError


class CacheKey:
    """Cache key management class supporting Memory + SQLite two-tier caching.
    
    This class provides a unified interface for caching data with:
    - Fast memory cache (TTLCache) for frequently accessed data
    - Persistent SQLite storage for historical data
    - Automatic cache invalidation on corporate actions (dividends)
    
    Attributes:
        ALL: Class-level dictionary of all CacheKey instances.
        _sqlite_backend: Class-level SQLite backend singleton.
        _dividend_checker: Class-level DividendChecker singleton.
        _sqlite_initialized: Flag indicating if SQLite backend is initialized.
    """
    
    ALL: Dict[str, 'CacheKey'] = {}
    _sqlite_backend: Optional[Any] = None
    _dividend_checker: Optional[Any] = None
    _sqlite_initialized: bool = False

    def __init__(self, key: str, ttl: int = 600, ttl2: Optional[int] = None, maxsize: int = 100):
        """Initialize a cache key.
        
        Args:
            key: The cache key string.
            ttl: Memory cache TTL in seconds (default: 600).
            ttl2: Disk cache TTL in seconds (deprecated, kept for backward compatibility).
            maxsize: Maximum size of memory cache (default: 100).
        """
        self.key = key
        self.ttl = ttl
        self.ttl2 = ttl2 or (ttl * 2)  # Kept for backward compatibility
        self.cache1 = TTLCache(maxsize=maxsize, ttl=ttl)
        
        # Extract symbol from cache key if present (for dividend checking)
        self._symbol = self._extract_symbol_from_key(key)
        
        # Extract data type from cache key if present
        self._data_type = self._extract_data_type_from_key(key)

    @staticmethod
    def init(key: str, ttl: int = 600, ttl2: Optional[int] = None, maxsize: int = 100) -> 'CacheKey':
        """Initialize or retrieve a CacheKey instance.
        
        This is a factory method that returns an existing CacheKey if one
        exists for the given key, or creates a new one.
        
        Args:
            key: The cache key string.
            ttl: Memory cache TTL in seconds (default: 600).
            ttl2: Disk cache TTL in seconds (deprecated, kept for backward compatibility).
            maxsize: Maximum size of memory cache (default: 100).
            
        Returns:
            CacheKey instance for the given key.
        """
        if key in CacheKey.ALL:
            return CacheKey.ALL[key]
        cache = CacheKey(key, ttl, ttl2, maxsize)
        return CacheKey.ALL.setdefault(key, cache)

    @classmethod
    def init_sqlite_backend(cls, db_path: Optional[str] = None) -> None:
        """Initialize the SQLite backend singleton.
        
        This method should be called once during application startup to
        initialize the SQLite cache backend. If not called, the cache
        will fall back to memory-only mode.
        
        Args:
            db_path: Path to the SQLite database file. If None, uses default path.
        """
        if cls._sqlite_initialized:
            logger.debug("SQLite backend already initialized")
            return
        
        try:
            SQLiteCacheBackend = _get_sqlite_backend_class()
            DividendChecker = _get_dividend_checker_class()
            
            cls._sqlite_backend = SQLiteCacheBackend(db_path)
            cls._dividend_checker = DividendChecker(cls._sqlite_backend)
            cls._sqlite_initialized = True
            
            logger.info(f"SQLite cache backend initialized at: {cls._sqlite_backend.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize SQLite backend: {e}", exc_info=True)
            cls._sqlite_backend = None
            cls._dividend_checker = None
            cls._sqlite_initialized = False

    @classmethod
    def get_sqlite_backend(cls) -> Optional[Any]:
        """Get the SQLite backend singleton.
        
        Returns:
            SQLiteCacheBackend instance or None if not initialized.
        """
        return cls._sqlite_backend

    @classmethod
    def get_dividend_checker(cls) -> Optional[Any]:
        """Get the DividendChecker singleton.
        
        Returns:
            DividendChecker instance or None if not initialized.
        """
        return cls._dividend_checker

    def _extract_symbol_from_key(self, key: str) -> Optional[str]:
        """Extract stock symbol from cache key.
        
        Cache keys typically follow patterns like:
        - stock_prices:600000:daily
        - stock_info:000001
        
        Args:
            key: The cache key string.
            
        Returns:
            Extracted symbol or None if not found.
        """
        parts = key.split(':')
        if len(parts) >= 2:
            # The second part is usually the symbol
            potential_symbol = parts[1]
            # Check if it looks like a stock symbol (digits only, 6 chars)
            if potential_symbol.isdigit() and len(potential_symbol) == 6:
                return potential_symbol
        return None

    def _extract_data_type_from_key(self, key: str) -> Optional[str]:
        """Extract data type from cache key.
        
        Args:
            key: The cache key string.
            
        Returns:
            Data type string or None if not found.
        """
        parts = key.split(':')
        if len(parts) >= 1:
            first_part = parts[0].lower()
            if 'price' in first_part:
                return 'price'
            elif 'dividend' in first_part:
                return 'dividend'
            elif 'info' in first_part:
                return 'info'
        return None

    def _is_price_data(self) -> bool:
        """Check if this cache key is for price data.
        
        Returns:
            True if this is price data, False otherwise.
        """
        return self._data_type == 'price'

    def get(self) -> Optional[pd.DataFrame]:
        """Get cached data.
        
        Implements the two-tier cache lookup:
        1. Check memory cache first
        2. If memory miss, check SQLite cache
        3. If SQLite hit, populate memory cache
        4. For price data, check for dividend invalidation
        
        Returns:
            Cached DataFrame or None if not found.
        """
        # Step 1: Check memory cache
        try:
            value = self.cache1[self.key]
            logger.debug(f"Cache hit (memory): {self.key}")
            return value
        except KeyError:
            pass

        # Step 2: Check SQLite cache if initialized
        if CacheKey._sqlite_backend is not None:
            try:
                # For price data, check if we need to invalidate due to dividend
                if self._is_price_data() and self._symbol and CacheKey._dividend_checker:
                    if CacheKey._dividend_checker.should_invalidate_cache(self._symbol):
                        logger.info(f"Cache invalidated due to new dividend for {self._symbol}")
                        CacheKey._dividend_checker.invalidate_stock_cache(self._symbol)
                        # Also clear memory cache for this key
                        self.cache1.pop(self.key, None)
                        return None
                
                # Try to get from SQLite
                cached_bytes = CacheKey._sqlite_backend.get(self.key)
                if cached_bytes is not None:
                    serialize_df, deserialize_df, SerializationError = _get_serialization_functions()
                    try:
                        value = deserialize_df(cached_bytes)
                        logger.debug(f"Cache hit (SQLite): {self.key}")
                        
                        # Step 3: Populate memory cache (Requirement 3.3)
                        self.cache1[self.key] = value
                        logger.debug(f"Memory cache populated from SQLite: {self.key}")
                        
                        return value
                    except SerializationError as e:
                        logger.error(f"Failed to deserialize cached data for {self.key}: {e}")
                        # Delete corrupted cache entry
                        CacheKey._sqlite_backend.delete(self.key)
                        return None
            except Exception as e:
                logger.error(f"SQLite cache lookup failed for {self.key}: {e}", exc_info=True)

        logger.debug(f"Cache miss: {self.key}")
        return None

    def set(self, val: pd.DataFrame) -> pd.DataFrame:
        """Store data in cache.
        
        Stores data in both memory cache and SQLite cache (if initialized).
        This implements Requirement 3.5: dual storage on API fetch.
        
        Args:
            val: DataFrame to cache.
            
        Returns:
            The same DataFrame (for chaining).
        """
        try:
            # Store in memory cache
            self.cache1[self.key] = val
            logger.debug(f"Cache set (memory): {self.key} (ttl={self.ttl}s)")
            
            # Store in SQLite cache if initialized (permanent storage)
            if CacheKey._sqlite_backend is not None:
                try:
                    serialize_df, deserialize_df, SerializationError = _get_serialization_functions()
                    serialized_data = serialize_df(val)
                    CacheKey._sqlite_backend.set(
                        key=self.key,
                        value=serialized_data,
                        data_type=self._data_type,
                        symbol=self._symbol
                    )
                    logger.debug(f"Cache set (SQLite): {self.key}")
                except Exception as e:
                    # SQLite write failure should not interrupt operation
                    logger.error(f"SQLite cache write failed for {self.key}: {e}", exc_info=True)
                    
        except Exception as e:
            # Cache write failure should not interrupt operation
            logger.error(f"Cache write failed for {self.key}: {e}", exc_info=True)
            
        return val

    def delete(self) -> None:
        """Delete cached data from both memory and SQLite caches.
        
        This implements Requirement 8.3: dual cache clearing.
        """
        try:
            # Delete from memory cache
            self.cache1.pop(self.key, None)
            logger.debug(f"Cache deleted (memory): {self.key}")
            
            # Delete from SQLite cache if initialized
            if CacheKey._sqlite_backend is not None:
                try:
                    CacheKey._sqlite_backend.delete(self.key)
                    logger.debug(f"Cache deleted (SQLite): {self.key}")
                except Exception as e:
                    logger.error(f"SQLite cache delete failed for {self.key}: {e}", exc_info=True)
                    
        except Exception as e:
            logger.error(f"Cache delete failed for {self.key}: {e}", exc_info=True)

    @classmethod
    def clear_by_symbol(cls, symbol: str) -> int:
        """Clear all cache entries for a specific stock symbol.
        
        This implements Requirement 8.1: selective cache clearing.
        
        Args:
            symbol: Stock symbol to clear cache for.
            
        Returns:
            Number of entries cleared.
        """
        cleared_count = 0
        
        # Clear from memory cache (iterate through all CacheKey instances)
        keys_to_clear = []
        for key, cache_key in cls.ALL.items():
            if cache_key._symbol == symbol:
                keys_to_clear.append(key)
        
        for key in keys_to_clear:
            if key in cls.ALL:
                cls.ALL[key].cache1.pop(key, None)
                cleared_count += 1
        
        # Clear from SQLite cache
        if cls._sqlite_backend is not None:
            try:
                sqlite_cleared = cls._sqlite_backend.delete_by_symbol(symbol)
                cleared_count = max(cleared_count, sqlite_cleared)
                logger.info(f"Cleared {sqlite_cleared} SQLite cache entries for symbol: {symbol}")
            except Exception as e:
                logger.error(f"Failed to clear SQLite cache for symbol {symbol}: {e}", exc_info=True)
        
        return cleared_count

    @classmethod
    def clear_all(cls) -> int:
        """Clear all cache entries.
        
        This implements Requirement 8.2: complete cache clearing.
        
        Returns:
            Number of entries cleared.
        """
        cleared_count = 0
        
        # Clear all memory caches
        for cache_key in cls.ALL.values():
            cleared_count += len(cache_key.cache1)
            cache_key.cache1.clear()
        
        # Clear SQLite cache
        if cls._sqlite_backend is not None:
            try:
                sqlite_cleared = cls._sqlite_backend.clear()
                cleared_count = max(cleared_count, sqlite_cleared)
                logger.info(f"Cleared all cache entries: {sqlite_cleared} from SQLite")
            except Exception as e:
                logger.error(f"Failed to clear SQLite cache: {e}", exc_info=True)
        
        return cleared_count

    @classmethod
    def get_statistics(cls) -> Dict[str, Any]:
        """Get cache statistics.
        
        This implements Requirement 7.4: cache statistics.
        
        Returns:
            Dictionary containing cache statistics.
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
                logger.error(f"Failed to get SQLite stats: {e}", exc_info=True)
                stats["sqlite_error"] = str(e)
        
        return stats

    def get_cache_dir(self) -> pathlib.Path:
        """Get the cache directory path.
        
        This method is kept for backward compatibility.
        
        Returns:
            Path to the cache directory.
        """
        home = pathlib.Path.home()
        name = __package__
        if sys.platform == "win32":
            return home / "AppData" / "Local" / "Cache" / name
        return home / ".cache" / name
