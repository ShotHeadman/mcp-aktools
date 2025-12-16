"""SQLite 缓存后端实现。

此模块提供基于 SQLite 的持久化缓存后端，用于存储 API 查询结果。
支持历史数据的永久存储和线程安全操作。
"""

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

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

        logger.debug("SQLite 缓存后端已初始化，路径: %s", self.db_path)
    
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

                # 主缓存表（包含日期范围列）
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS cache_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cache_key TEXT UNIQUE NOT NULL,
                        data BLOB NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        data_type TEXT,
                        symbol TEXT,
                        start_date DATE,
                        end_date DATE
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
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_date_range
                    ON cache_entries(symbol, start_date, end_date)
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
                logger.error("创建数据库表失败: %s", e, exc_info=True)
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
                    logger.debug("SQLite 缓存命中: %s", key)
                    self._record_hit()
                    return row[0]
                logger.debug("SQLite 缓存未命中: %s", key)
                self._record_miss()
                return None
            except sqlite3.Error as e:
                logger.error("获取缓存条目失败: %s", e, exc_info=True)
                return None

    def get_with_range(
        self,
        key: str
    ) -> Optional[Tuple[bytes, Optional[datetime], Optional[datetime]]]:
        """获取缓存数据及其时间范围。

        此方法扩展了基本的 get() 方法，同时返回缓存数据和其时间范围元数据。
        如果缓存条目不存在，返回 None。

        参数:
            key: 缓存键。

        返回:
            (数据, 起始日期, 结束日期) 的元组，如果未找到则返回 None。
            起始日期和结束日期可能为 None（如果未设置）。
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT data, start_date, end_date FROM cache_entries WHERE cache_key = ?",
                    (key,)
                )
                row = cursor.fetchone()

                if row:
                    data, start_date_str, end_date_str = row

                    # 将 ISO 格式字符串转换回 datetime 对象
                    start_date = None
                    end_date = None

                    if start_date_str:
                        try:
                            start_date = datetime.fromisoformat(start_date_str)
                        except (ValueError, TypeError) as e:
                            logger.warning("解析起始日期失败: %s, 错误: %s", start_date_str, e)

                    if end_date_str:
                        try:
                            end_date = datetime.fromisoformat(end_date_str)
                        except (ValueError, TypeError) as e:
                            logger.warning("解析结束日期失败: %s, 错误: %s", end_date_str, e)

                    logger.debug(
                        "SQLite 缓存命中（含范围）: %s, 范围=[%s, %s]",
                        key, start_date, end_date
                    )
                    self._record_hit()
                    return (data, start_date, end_date)

                logger.debug("SQLite 缓存未命中: %s", key)
                self._record_miss()
                return None
            except sqlite3.Error as e:
                logger.error("获取缓存条目（含范围）失败: %s", e, exc_info=True)
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
                logger.debug("SQLite 缓存已设置: %s", key)
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("设置缓存条目失败: %s", e, exc_info=True)
                raise

    def set_with_range(
        self,
        key: str,
        value: bytes,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
        data_type: Optional[str] = None,
        symbol: Optional[str] = None
    ) -> None:
        """存储数据及其时间范围到缓存中。

        此方法扩展了基本的 set() 方法，增加了对时间范围元数据的支持。
        日期使用 ISO 格式 (YYYY-MM-DD) 存储到数据库中。

        参数:
            key: 缓存键。
            value: 序列化后的字节数据。
            start_date: 数据的起始日期。如果为 None，则不记录起始日期。
            end_date: 数据的结束日期。如果为 None，则不记录结束日期。
            created_at: 创建时间戳。如果为 None，则使用当前时间。
            data_type: 数据类型（如 'price'、'info'、'dividend'）。
            symbol: 股票代码，用于缓存失效处理。

        需求 5.3: 数据库操作失败时回滚事务并抛出异常
        """
        if created_at is None:
            created_at = datetime.now()

        # 将 datetime 转换为 ISO 格式字符串
        created_at_str = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
        updated_at_str = datetime.now().isoformat()

        # 将日期转换为 ISO 格式字符串 (YYYY-MM-DD)
        start_date_str = start_date.date().isoformat() if start_date else None
        end_date_str = end_date.date().isoformat() if end_date else None

        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO cache_entries (
                        cache_key, data, created_at, updated_at,
                        data_type, symbol, start_date, end_date
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        data = excluded.data,
                        updated_at = excluded.updated_at,
                        data_type = excluded.data_type,
                        symbol = excluded.symbol,
                        start_date = excluded.start_date,
                        end_date = excluded.end_date
                """, (key, value, created_at_str, updated_at_str, data_type, symbol,
                      start_date_str, end_date_str))
                conn.commit()
                logger.debug(
                    "SQLite 缓存已设置（含范围）: %s, 范围=[%s, %s], 数据大小=%d bytes",
                    key, start_date_str, end_date_str, len(value)
                )
            except sqlite3.Error as e:
                # 需求 5.3: 数据库操作失败 - 回滚事务并抛出异常
                conn.rollback()
                # 需求 6.4: 记录详细的错误信息和上下文
                logger.error(
                    "设置缓存条目（含范围）失败: %s, key=%s, symbol=%s, "
                    "start_date=%s, end_date=%s, 数据大小=%d bytes",
                    e, key, symbol, start_date_str, end_date_str, len(value),
                    exc_info=True
                )
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
                    logger.debug("SQLite 缓存已删除: %s", key)
                return deleted
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("删除缓存条目失败: %s", e, exc_info=True)
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
                    logger.debug(
                        "SQLite 缓存已删除 %d 条前缀为 %s 的条目",
                        deleted_count, prefix
                    )
                return deleted_count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("根据前缀删除缓存条目失败: %s", e, exc_info=True)
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
                    logger.debug(
                        "SQLite 缓存已删除股票 %s 的 %d 条条目",
                        symbol, deleted_count
                    )
                    self._record_invalidation(deleted_count)
                return deleted_count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("根据股票代码删除缓存条目失败: %s", e, exc_info=True)
                return 0
    
    def delete_adjusted_price_cache(self, symbol: str) -> int:
        """根据股票代码删除复权价格缓存数据（qfq/hfq），保留原始价格数据。
        
        此方法仅删除包含 adjust='qfq' 或 adjust='hfq' 的缓存条目，
        保留 adjust='' 的原始价格数据缓存。
        
        参数:
            symbol: 股票代码。
            
        返回:
            删除的条目数量。
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # 删除缓存键中包含 adjust='qfq' 或 adjust='hfq' 的条目
                # 缓存键格式类似: stock_zh_a_hist-('000001',)-[('adjust', 'qfq'), ...]
                cursor.execute("""
                    DELETE FROM cache_entries 
                    WHERE symbol = ? 
                    AND (
                        cache_key LIKE '%adjust%qfq%' 
                        OR cache_key LIKE '%adjust%hfq%'
                    )
                """, (symbol,))
                conn.commit()
                deleted_count = cursor.rowcount
                if deleted_count > 0:
                    logger.info(
                        "SQLite 缓存已删除股票 %s 的 %d 条复权价格缓存条目 "
                        "(qfq/hfq)，保留原始价格数据",
                        symbol, deleted_count
                    )
                    self._record_invalidation(deleted_count)
                return deleted_count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("根据股票代码删除复权价格缓存条目失败: %s", e, exc_info=True)
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

                logger.debug("SQLite 缓存已清除: 删除了 %d 条条目", count)
                return count
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("清除缓存失败: %s", e, exc_info=True)
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
                logger.error("获取缓存统计信息失败: %s", e, exc_info=True)
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
            logger.debug("更新缓存统计失败: %s", e)
    
    @staticmethod
    def extract_date_range(df: pd.DataFrame) -> Tuple[Optional[datetime], Optional[datetime]]:
        """从 DataFrame 提取时间范围。

        尝试从常见的日期列名中提取最早和最晚的日期。
        支持的日期列名包括：'日期', 'date', '时间', 'datetime', '交易日期'。

        参数:
            df: pandas DataFrame

        返回:
            (起始日期, 结束日期) 的元组，如果无法提取则返回 (None, None)

        需求 5.3: 日期解析错误处理 - 使用 coerce 并记录警告
        """
        # 处理空 DataFrame
        if df is None or df.empty:
            logger.debug("DataFrame 为空，返回 (None, None)")
            return (None, None)

        # 常见的日期列名（按优先级排序）
        date_column_names = ['日期', 'date', '时间', 'datetime', '交易日期', 'Date', 'DateTime', 'TIME']

        # 查找第一个存在的日期列
        date_column = None
        for col_name in date_column_names:
            if col_name in df.columns:
                date_column = col_name
                logger.debug("找到日期列: %s", col_name)
                break

        # 如果没有找到日期列，返回 (None, None)
        if date_column is None:
            logger.debug("未找到日期列，可用列: %s", list(df.columns))
            return (None, None)

        try:
            # 需求 5.3: 使用 pd.to_datetime 解析日期，errors='coerce' 会将无效日期转换为 NaT
            dates = pd.to_datetime(df[date_column], errors='coerce')

            # 统计无效日期数量
            invalid_count = dates.isna().sum()
            if invalid_count > 0:
                # 需求 5.3, 6.4: 记录日期解析警告
                logger.warning(
                    "日期解析警告: 列 '%s' 中有 %d 个无效日期被转换为 NaT (总共 %d 行)",
                    date_column, invalid_count, len(df)
                )

            # 过滤掉 NaT 值
            valid_dates = dates.dropna()

            # 如果所有日期都无效，返回 (None, None)
            if valid_dates.empty:
                # 需求 5.3, 6.4: 记录所有日期无效的错误
                logger.warning(
                    "日期解析错误: 列 '%s' 中所有日期都无效，无法提取时间范围",
                    date_column
                )
                return (None, None)

            # 提取最早和最晚日期
            start_date = valid_dates.min().to_pydatetime()
            end_date = valid_dates.max().to_pydatetime()

            logger.debug("提取的日期范围: %s 到 %s", start_date, end_date)
            return (start_date, end_date)

        except Exception as e:
            # 需求 5.3, 6.4: 记录详细的错误信息和上下文
            logger.error(
                "提取日期范围时出错: %s, 列名: %s, DataFrame形状: %s",
                e, date_column, df.shape,
                exc_info=True
            )
            return (None, None)
    
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
        except (AttributeError, sqlite3.Error):
            pass
