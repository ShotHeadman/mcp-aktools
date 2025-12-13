"""Dividend checker for cache invalidation.

This module provides functionality to check for corporate actions (dividends)
and invalidate cached price data when new ex-dividend dates are detected.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

from mcp_aktools.cache.sqlite_backend import SQLiteCacheBackend

logger = logging.getLogger(__name__)

# Default TTL for dividend data cache (24 hours in seconds)
DEFAULT_DIVIDEND_TTL = 86400


class DividendChecker:
    """Dividend checker for detecting ex-dividend events and triggering cache invalidation.
    
    This class monitors dividend events for stocks and invalidates cached price data
    when new ex-dividend dates are detected. Dividend data itself is cached with a
    24-hour TTL to minimize API calls.
    
    Attributes:
        cache_backend: SQLite cache backend for storing dividend records.
        ttl: TTL for dividend data cache in seconds (default: 24 hours).
    """
    
    def __init__(
        self, 
        cache_backend: SQLiteCacheBackend, 
        ttl: int = DEFAULT_DIVIDEND_TTL
    ):
        """Initialize dividend checker.
        
        Args:
            cache_backend: SQLite cache backend instance.
            ttl: TTL for dividend data cache in seconds (default: 86400 = 24 hours).
        """
        self.cache_backend = cache_backend
        self.ttl = ttl
        logger.debug(f"DividendChecker initialized with TTL={ttl}s")
    
    def _get_dividend_cache_key(self, symbol: str) -> str:
        """Generate cache key for dividend data.
        
        Args:
            symbol: Stock symbol.
            
        Returns:
            Cache key string.
        """
        return f"dividend_check:{symbol}"
    
    def _is_cache_expired(self, created_at: datetime) -> bool:
        """Check if cached dividend data has expired.
        
        Args:
            created_at: Timestamp when the cache entry was created.
            
        Returns:
            True if cache has expired, False otherwise.
        """
        expiry_time = created_at + timedelta(seconds=self.ttl)
        return datetime.now() > expiry_time

    def get_latest_ex_dividend_date(self, symbol: str) -> Optional[datetime]:
        """Get the latest ex-dividend date for a stock.
        
        This method first checks the cache for dividend data. If the cache is
        expired or missing, it fetches fresh data from the AkShare API using
        stock_history_dividend_detail(symbol, indicator='分红').
        
        Args:
            symbol: Stock symbol (e.g., '000001' for 平安银行).
            
        Returns:
            The latest ex-dividend date as datetime, or None if no dividend records exist.
        """
        cache_key = self._get_dividend_cache_key(symbol)
        
        # Try to get from cache first
        cached_data = self._get_cached_dividend_data(symbol)
        if cached_data is not None:
            return cached_data
        
        # Cache miss or expired, fetch from API
        try:
            logger.info(f"Fetching dividend data from API for symbol: {symbol}")
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            
            if df is None or df.empty:
                logger.debug(f"No dividend records found for symbol: {symbol}")
                # Cache the "no dividend" result to avoid repeated API calls
                self._cache_dividend_result(symbol, None)
                return None
            
            # Find the latest ex-dividend date (除权除息日)
            # The column name may vary, common names: '除权除息日', '除息日'
            ex_div_date_col = None
            for col in ['除权除息日', '除息日', 'ex_dividend_date']:
                if col in df.columns:
                    ex_div_date_col = col
                    break
            
            if ex_div_date_col is None:
                logger.warning(f"Could not find ex-dividend date column in dividend data for {symbol}")
                logger.debug(f"Available columns: {df.columns.tolist()}")
                self._cache_dividend_result(symbol, None)
                return None
            
            # Parse dates and find the latest one
            df[ex_div_date_col] = pd.to_datetime(df[ex_div_date_col], errors='coerce')
            valid_dates = df[ex_div_date_col].dropna()
            
            if valid_dates.empty:
                logger.debug(f"No valid ex-dividend dates found for symbol: {symbol}")
                self._cache_dividend_result(symbol, None)
                return None
            
            latest_date = valid_dates.max()
            latest_date_dt = latest_date.to_pydatetime() if hasattr(latest_date, 'to_pydatetime') else latest_date
            
            logger.debug(f"Latest ex-dividend date for {symbol}: {latest_date_dt}")
            
            # Cache the result
            self._cache_dividend_result(symbol, latest_date_dt)
            
            # Store in dividend_records table for tracking
            self._store_dividend_record(symbol, latest_date_dt)
            
            return latest_date_dt
            
        except Exception as e:
            logger.error(f"Failed to fetch dividend data for {symbol}: {e}", exc_info=True)
            return None
    
    def _get_cached_dividend_data(self, symbol: str) -> Optional[datetime]:
        """Get cached dividend data if not expired.
        
        Args:
            symbol: Stock symbol.
            
        Returns:
            Cached ex-dividend date if valid, None otherwise.
        """
        cache_key = self._get_dividend_cache_key(symbol)
        
        try:
            conn = self.cache_backend._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT data, created_at FROM cache_entries 
                WHERE cache_key = ? AND data_type = 'dividend'
            """, (cache_key,))
            
            row = cursor.fetchone()
            if row is None:
                return None
            
            data, created_at_str = row
            created_at = datetime.fromisoformat(created_at_str) if isinstance(created_at_str, str) else created_at_str
            
            # Check if cache has expired
            if self._is_cache_expired(created_at):
                logger.debug(f"Dividend cache expired for {symbol}")
                return None
            
            # Decode the cached date
            if data == b'none':
                return None
            
            date_str = data.decode('utf-8')
            return datetime.fromisoformat(date_str)
            
        except Exception as e:
            logger.error(f"Failed to get cached dividend data for {symbol}: {e}", exc_info=True)
            return None

    def _cache_dividend_result(self, symbol: str, ex_dividend_date: Optional[datetime]) -> None:
        """Cache dividend check result.
        
        Args:
            symbol: Stock symbol.
            ex_dividend_date: The ex-dividend date to cache, or None if no dividend.
        """
        cache_key = self._get_dividend_cache_key(symbol)
        
        try:
            # Encode the date as bytes
            if ex_dividend_date is None:
                data = b'none'
            else:
                data = ex_dividend_date.isoformat().encode('utf-8')
            
            self.cache_backend.set(
                key=cache_key,
                value=data,
                data_type='dividend',
                symbol=symbol
            )
            logger.debug(f"Cached dividend result for {symbol}: {ex_dividend_date}")
            
        except Exception as e:
            logger.error(f"Failed to cache dividend result for {symbol}: {e}", exc_info=True)
    
    def _store_dividend_record(self, symbol: str, ex_dividend_date: datetime) -> None:
        """Store dividend record in the dividend_records table.
        
        Args:
            symbol: Stock symbol.
            ex_dividend_date: The ex-dividend date.
        """
        try:
            conn = self.cache_backend._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO dividend_records (symbol, ex_dividend_date, dividend_type)
                VALUES (?, ?, ?)
            """, (symbol, ex_dividend_date.date().isoformat(), 'cash'))
            
            conn.commit()
            logger.debug(f"Stored dividend record for {symbol}: {ex_dividend_date}")
            
        except Exception as e:
            logger.error(f"Failed to store dividend record for {symbol}: {e}", exc_info=True)
    
    def _get_stored_ex_dividend_date(self, symbol: str) -> Optional[datetime]:
        """Get the previously stored ex-dividend date from dividend_records table.
        
        Args:
            symbol: Stock symbol.
            
        Returns:
            The stored ex-dividend date, or None if not found.
        """
        try:
            conn = self.cache_backend._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT MAX(ex_dividend_date) FROM dividend_records WHERE symbol = ?
            """, (symbol,))
            
            row = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            
            return datetime.fromisoformat(row[0])
            
        except Exception as e:
            logger.error(f"Failed to get stored ex-dividend date for {symbol}: {e}", exc_info=True)
            return None
    
    def should_invalidate_cache(self, symbol: str) -> bool:
        """Check if cache should be invalidated due to new dividend event.
        
        This method compares the latest ex-dividend date from the API with
        the previously stored date. If a new (more recent) ex-dividend date
        is detected, the cache should be invalidated.
        
        Args:
            symbol: Stock symbol.
            
        Returns:
            True if cache should be invalidated, False otherwise.
        """
        try:
            # Get the previously stored ex-dividend date
            stored_date = self._get_stored_ex_dividend_date(symbol)
            
            # Get the latest ex-dividend date from API (may use cache)
            latest_date = self.get_latest_ex_dividend_date(symbol)
            
            if latest_date is None:
                # No dividend data, no need to invalidate
                return False
            
            if stored_date is None:
                # First time seeing dividend data, store it but don't invalidate
                # (the cache was just populated)
                return False
            
            # Compare dates - invalidate if new date is more recent
            if latest_date > stored_date:
                logger.info(
                    f"New ex-dividend date detected for {symbol}: "
                    f"{latest_date} > {stored_date}, cache invalidation required"
                )
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check cache invalidation for {symbol}: {e}", exc_info=True)
            return False
    
    def invalidate_stock_cache(self, symbol: str) -> int:
        """Invalidate all cached price data for a stock.
        
        This method removes all cache entries for the specified stock symbol
        from the database.
        
        Args:
            symbol: Stock symbol.
            
        Returns:
            Number of deleted cache entries.
        """
        try:
            deleted_count = self.cache_backend.delete_by_symbol(symbol)
            logger.info(f"Invalidated {deleted_count} cache entries for symbol: {symbol}")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to invalidate cache for {symbol}: {e}", exc_info=True)
            return 0
    
    def check_and_invalidate(self, symbol: str) -> bool:
        """Check for new dividend and invalidate cache if needed.
        
        This is a convenience method that combines should_invalidate_cache
        and invalidate_stock_cache.
        
        Args:
            symbol: Stock symbol.
            
        Returns:
            True if cache was invalidated, False otherwise.
        """
        if self.should_invalidate_cache(symbol):
            self.invalidate_stock_cache(symbol)
            return True
        return False
