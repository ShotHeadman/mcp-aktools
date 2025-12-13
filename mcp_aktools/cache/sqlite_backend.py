"""SQLite cache backend implementation.

This module provides a SQLite-based persistent cache backend for storing
API query results. It supports permanent storage for historical data
and thread-safe operations.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class SQLiteCacheBackend:
    """SQLite cache backend implementation.
    
    This class provides a persistent cache storage using SQLite database.
    It supports thread-safe operations and permanent storage for historical data.
    
    Attributes:
        db_path: Path to the SQLite database file.
        _lock: Threading lock for thread-safe operations.
        _local: Thread-local storage for database connections.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize SQLite cache backend.
        
        Args:
            db_path: Database file path. If None, uses default path in user's cache directory.
        """
        self.db_path = db_path or self._get_default_db_path()
        self._lock = threading.Lock()
        self._local = threading.local()
        
        # Ensure parent directory exists
        self._ensure_db_directory()
        
        # Create tables
        self._create_tables()
        
        logger.debug(f"SQLite cache backend initialized at: {self.db_path}")
    
    def _get_default_db_path(self) -> str:
        """Get default database path in project's data directory.
        
        Returns:
            str: Default database file path (data/stock_data.db).
        """
        # Get the project root directory (where mcp_aktools package is located)
        package_dir = Path(__file__).parent.parent.parent
        db_dir = package_dir / "data"
        return str(db_dir / "stock_data.db")

    def _ensure_db_directory(self) -> None:
        """Ensure the database directory exists."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection.
        
        Returns:
            sqlite3.Connection: Database connection for current thread.
        """
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            # Enable WAL mode for better concurrent access
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
        return self._local.connection
    
    def _create_tables(self) -> None:
        """Create required database tables if they don't exist."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Main cache table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cache_key TEXT UNIQUE NOT NULL,
                        data BLOB NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        data_type TEXT,
                        symbol TEXT
                    )
                """)
                
                # Create indexes for faster lookups
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_cache_key 
                    ON cache_entries(cache_key)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_symbol 
                    ON cache_entries(symbol)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_data_type 
                    ON cache_entries(data_type)
                """)
                
                # Dividend records table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS dividend_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        ex_dividend_date DATE NOT NULL,
                        dividend_type TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(symbol, ex_dividend_date)
                    )
                """)
                
                # Cache statistics table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cache_stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        stat_date DATE NOT NULL,
                        hits INTEGER DEFAULT 0,
                        misses INTEGER DEFAULT 0,
                        invalidations INTEGER DEFAULT 0,
                        UNIQUE(stat_date)
                    )
                """)
                
                conn.commit()
                logger.debug("Database tables created successfully")
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to create database tables: {e}", exc_info=True)
                raise

    def get(self, key: str) -> Optional[bytes]:
        """Get cached data by key.
        
        Args:
            key: Cache key.
            
        Returns:
            Serialized data as bytes, or None if not found.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT data FROM cache_entries WHERE cache_key = ?",
                    (key,)
                )
                row = cursor.fetchone()
                
                if row:
                    logger.debug(f"SQLite cache hit: {key}")
                    self._record_hit()
                    return row[0]
                else:
                    logger.debug(f"SQLite cache miss: {key}")
                    self._record_miss()
                    return None
            except sqlite3.Error as e:
                logger.error(f"Failed to get cache entry: {e}", exc_info=True)
                return None
    
    def set(
        self, 
        key: str, 
        value: bytes, 
        created_at: Optional[datetime] = None,
        data_type: Optional[str] = None,
        symbol: Optional[str] = None
    ) -> None:
        """Store data in cache.
        
        Args:
            key: Cache key.
            value: Serialized data as bytes.
            created_at: Creation timestamp. If None, uses current time.
            data_type: Type of data (e.g., 'price', 'info', 'dividend').
            symbol: Stock symbol for cache invalidation purposes.
        """
        if created_at is None:
            created_at = datetime.now()
        
        # Convert datetime to ISO format string to avoid Python 3.12 deprecation warning
        created_at_str = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
        updated_at_str = datetime.now().isoformat()
        
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO cache_entries (cache_key, data, created_at, updated_at, data_type, symbol)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        data = excluded.data,
                        updated_at = excluded.updated_at,
                        data_type = excluded.data_type,
                        symbol = excluded.symbol
                """, (key, value, created_at_str, updated_at_str, data_type, symbol))
                conn.commit()
                logger.debug(f"SQLite cache set: {key}")
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to set cache entry: {e}", exc_info=True)
                raise
    
    def delete(self, key: str) -> bool:
        """Delete cached data by key.
        
        Args:
            key: Cache key.
            
        Returns:
            True if entry was deleted, False otherwise.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM cache_entries WHERE cache_key = ?",
                    (key,)
                )
                conn.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.debug(f"SQLite cache deleted: {key}")
                return deleted
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to delete cache entry: {e}", exc_info=True)
                return False

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete cached data by key prefix.
        
        Args:
            prefix: Cache key prefix.
            
        Returns:
            Number of deleted entries.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM cache_entries WHERE cache_key LIKE ?",
                    (f"{prefix}%",)
                )
                conn.commit()
                deleted_count = cursor.rowcount
                if deleted_count > 0:
                    logger.debug(f"SQLite cache deleted {deleted_count} entries with prefix: {prefix}")
                return deleted_count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to delete cache entries by prefix: {e}", exc_info=True)
                return 0
    
    def delete_by_symbol(self, symbol: str) -> int:
        """Delete cached data by stock symbol.
        
        Args:
            symbol: Stock symbol.
            
        Returns:
            Number of deleted entries.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM cache_entries WHERE symbol = ?",
                    (symbol,)
                )
                conn.commit()
                deleted_count = cursor.rowcount
                if deleted_count > 0:
                    logger.debug(f"SQLite cache deleted {deleted_count} entries for symbol: {symbol}")
                    self._record_invalidation(deleted_count)
                return deleted_count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to delete cache entries by symbol: {e}", exc_info=True)
                return 0
    
    def clear(self) -> int:
        """Clear all cached data.
        
        Returns:
            Number of deleted entries.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM cache_entries")
                count = cursor.fetchone()[0]
                
                cursor.execute("DELETE FROM cache_entries")
                conn.commit()
                
                logger.debug(f"SQLite cache cleared: {count} entries deleted")
                return count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"Failed to clear cache: {e}", exc_info=True)
                return 0

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary containing entry_count, total_size, hits, misses, etc.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Get entry count
                cursor.execute("SELECT COUNT(*) FROM cache_entries")
                entry_count = cursor.fetchone()[0]
                
                # Get total data size
                cursor.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM cache_entries")
                total_size = cursor.fetchone()[0]
                
                # Get today's statistics
                today = datetime.now().date().isoformat()
                cursor.execute(
                    "SELECT hits, misses, invalidations FROM cache_stats WHERE stat_date = ?",
                    (today,)
                )
                row = cursor.fetchone()
                
                if row:
                    hits, misses, invalidations = row
                else:
                    hits, misses, invalidations = 0, 0, 0
                
                # Calculate hit rate
                total_requests = hits + misses
                hit_rate = (hits / total_requests * 100) if total_requests > 0 else 0.0
                
                return {
                    "entry_count": entry_count,
                    "total_size_bytes": total_size,
                    "hits": hits,
                    "misses": misses,
                    "invalidations": invalidations,
                    "hit_rate": round(hit_rate, 2),
                    "db_path": self.db_path
                }
            except sqlite3.Error as e:
                logger.error(f"Failed to get cache stats: {e}", exc_info=True)
                return {
                    "entry_count": 0,
                    "total_size_bytes": 0,
                    "hits": 0,
                    "misses": 0,
                    "invalidations": 0,
                    "hit_rate": 0.0,
                    "db_path": self.db_path,
                    "error": str(e)
                }
    
    def _record_hit(self) -> None:
        """Record a cache hit in statistics."""
        self._update_stats("hits")
    
    def _record_miss(self) -> None:
        """Record a cache miss in statistics."""
        self._update_stats("misses")
    
    def _record_invalidation(self, count: int = 1) -> None:
        """Record cache invalidations in statistics."""
        self._update_stats("invalidations", count)
    
    def _update_stats(self, field: str, increment: int = 1) -> None:
        """Update statistics for a given field.
        
        Args:
            field: Field to update ('hits', 'misses', or 'invalidations').
            increment: Amount to increment by.
        """
        conn = self._get_connection()
        try:
            today = datetime.now().date().isoformat()
            cursor = conn.cursor()
            
            # Try to insert or update
            cursor.execute(f"""
                INSERT INTO cache_stats (stat_date, {field})
                VALUES (?, ?)
                ON CONFLICT(stat_date) DO UPDATE SET
                    {field} = {field} + ?
            """, (today, increment, increment))
            conn.commit()
        except sqlite3.Error as e:
            # Statistics update failure should not affect main operations
            logger.debug(f"Failed to update cache stats: {e}")
    
    def close(self) -> None:
        """Close database connection for current thread."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
            logger.debug("SQLite cache backend connection closed")
    
    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            pass
