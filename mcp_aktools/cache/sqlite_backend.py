"""SQLite 缓存后端实现。

此模块提供基于 SQLite 的持久化缓存后端，用于存储 API 查询结果。
支持历史数据的永久存储和线程安全操作。
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
    """SQLite 缓存后端实现。
    
    此类提供使用 SQLite 数据库的持久化缓存存储。
    支持线程安全操作和历史数据的永久存储。
    
    属性:
        db_path: SQLite 数据库文件路径。
        _lock: 用于线程安全操作的线程锁。
        _local: 用于数据库连接的线程本地存储。
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """初始化 SQLite 缓存后端。
        
        参数:
            db_path: 数据库文件路径。如果为 None，则使用用户缓存目录中的默认路径。
        """
        self.db_path = db_path or self._get_default_db_path()
        self._lock = threading.Lock()
        self._local = threading.local()
        
        # 确保父目录存在
        self._ensure_db_directory()
        
        # 创建数据表
        self._create_tables()
        
        logger.debug(f"SQLite 缓存后端已初始化，路径: {self.db_path}")
    
    def _get_default_db_path(self) -> str:
        """获取项目数据目录中的默认数据库路径。
        
        返回:
            str: 默认数据库文件路径 (data/stock_data.db)。
        """
        # 获取项目根目录（mcp_aktools 包所在位置）
        package_dir = Path(__file__).parent.parent.parent
        db_dir = package_dir / "data"
        return str(db_dir / "stock_data.db")

    def _ensure_db_directory(self) -> None:
        """确保数据库目录存在。"""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取线程本地的数据库连接。
        
        返回:
            sqlite3.Connection: 当前线程的数据库连接。
        """
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            # 启用 WAL 模式以获得更好的并发访问性能
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
        return self._local.connection
    
    def _create_tables(self) -> None:
        """如果数据表不存在则创建。"""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # 主缓存表
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
                
                # 创建索引以加快查询速度
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
                
                # 分红记录表
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
                
                # 缓存统计表
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
                logger.debug("数据库表创建成功")
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"创建数据库表失败: {e}", exc_info=True)
                raise

    def get(self, key: str) -> Optional[bytes]:
        """根据键获取缓存数据。
        
        参数:
            key: 缓存键。
            
        返回:
            序列化后的字节数据，如果未找到则返回 None。
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
                    logger.debug(f"SQLite 缓存命中: {key}")
                    self._record_hit()
                    return row[0]
                else:
                    logger.debug(f"SQLite 缓存未命中: {key}")
                    self._record_miss()
                    return None
            except sqlite3.Error as e:
                logger.error(f"获取缓存条目失败: {e}", exc_info=True)
                return None
    
    def set(
        self, 
        key: str, 
        value: bytes, 
        created_at: Optional[datetime] = None,
        data_type: Optional[str] = None,
        symbol: Optional[str] = None
    ) -> None:
        """将数据存储到缓存中。
        
        参数:
            key: 缓存键。
            value: 序列化后的字节数据。
            created_at: 创建时间戳。如果为 None，则使用当前时间。
            data_type: 数据类型（如 'price'、'info'、'dividend'）。
            symbol: 股票代码，用于缓存失效处理。
        """
        if created_at is None:
            created_at = datetime.now()
        
        # 将 datetime 转换为 ISO 格式字符串，避免 Python 3.12 弃用警告
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
                logger.debug(f"SQLite 缓存已设置: {key}")
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"设置缓存条目失败: {e}", exc_info=True)
                raise
    
    def delete(self, key: str) -> bool:
        """根据键删除缓存数据。
        
        参数:
            key: 缓存键。
            
        返回:
            如果条目被删除则返回 True，否则返回 False。
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
                    logger.debug(f"SQLite 缓存已删除: {key}")
                return deleted
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"删除缓存条目失败: {e}", exc_info=True)
                return False

    def delete_by_prefix(self, prefix: str) -> int:
        """根据键前缀删除缓存数据。
        
        参数:
            prefix: 缓存键前缀。
            
        返回:
            删除的条目数量。
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
                    logger.debug(f"SQLite 缓存已删除 {deleted_count} 条前缀为 {prefix} 的条目")
                return deleted_count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"根据前缀删除缓存条目失败: {e}", exc_info=True)
                return 0
    
    def delete_by_symbol(self, symbol: str) -> int:
        """根据股票代码删除缓存数据。
        
        参数:
            symbol: 股票代码。
            
        返回:
            删除的条目数量。
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
                    logger.debug(f"SQLite 缓存已删除股票 {symbol} 的 {deleted_count} 条条目")
                    self._record_invalidation(deleted_count)
                return deleted_count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"根据股票代码删除缓存条目失败: {e}", exc_info=True)
                return 0
    
    def clear(self) -> int:
        """清除所有缓存数据。
        
        返回:
            删除的条目数量。
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM cache_entries")
                count = cursor.fetchone()[0]
                
                cursor.execute("DELETE FROM cache_entries")
                conn.commit()
                
                logger.debug(f"SQLite 缓存已清除: 删除了 {count} 条条目")
                return count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error(f"清除缓存失败: {e}", exc_info=True)
                return 0

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息。
        
        返回:
            包含 entry_count、total_size、hits、misses 等信息的字典。
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # 获取条目数量
                cursor.execute("SELECT COUNT(*) FROM cache_entries")
                entry_count = cursor.fetchone()[0]
                
                # 获取总数据大小
                cursor.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM cache_entries")
                total_size = cursor.fetchone()[0]
                
                # 获取今日统计数据
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
                
                # 计算命中率
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
                logger.error(f"获取缓存统计信息失败: {e}", exc_info=True)
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
        """记录一次缓存命中。"""
        self._update_stats("hits")
    
    def _record_miss(self) -> None:
        """记录一次缓存未命中。"""
        self._update_stats("misses")
    
    def _record_invalidation(self, count: int = 1) -> None:
        """记录缓存失效次数。"""
        self._update_stats("invalidations", count)
    
    def _update_stats(self, field: str, increment: int = 1) -> None:
        """更新指定字段的统计数据。
        
        参数:
            field: 要更新的字段（'hits'、'misses' 或 'invalidations'）。
            increment: 增加的数量。
        """
        conn = self._get_connection()
        try:
            today = datetime.now().date().isoformat()
            cursor = conn.cursor()
            
            # 尝试插入或更新
            cursor.execute(f"""
                INSERT INTO cache_stats (stat_date, {field})
                VALUES (?, ?)
                ON CONFLICT(stat_date) DO UPDATE SET
                    {field} = {field} + ?
            """, (today, increment, increment))
            conn.commit()
        except sqlite3.Error as e:
            # 统计更新失败不应影响主要操作
            logger.debug(f"更新缓存统计失败: {e}")
    
    def close(self) -> None:
        """关闭当前线程的数据库连接。"""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
            logger.debug("SQLite 缓存后端连接已关闭")
    
    def __del__(self):
        """析构时清理资源。"""
        try:
            self.close()
        except Exception:
            pass
