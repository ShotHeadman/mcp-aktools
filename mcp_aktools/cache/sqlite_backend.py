"""SQLite 缓存后端实现。

此模块提供基于 SQLite 的持久化缓存后端，用于存储 API 查询结果。
支持历史数据的永久存储和线程安全操作。
"""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
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
    
    def __init__(self, db_path: Optional[str] = None, disk_ttl: Optional[int] = None):
        """初始化 SQLite 缓存后端。
        
        参数:
            db_path: 数据库文件路径。如果为 None，则使用用户缓存目录中的默认路径。
        """
        self.db_path = db_path or self._get_default_db_path()
        self.disk_ttl = disk_ttl
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

    def _is_expired(
        self,
        updated_at: Optional[str],
        data_type: Optional[str],
    ) -> bool:
        """判断缓存条目是否超过磁盘 TTL。"""
        if not self.disk_ttl or self.disk_ttl <= 0:
            return False
        if data_type == "price":
            return False
        if not updated_at:
            return False

        try:
            updated = datetime.fromisoformat(updated_at)
        except (TypeError, ValueError):
            logger.warning("无法解析缓存更新时间: %s", updated_at)
            return False

        return datetime.now() - updated > timedelta(seconds=self.disk_ttl)
    
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
                    "SELECT data, updated_at, data_type FROM cache_entries WHERE cache_key = ?",
                    (key,)
                )
                row = cursor.fetchone()

                if row:
                    data, updated_at, data_type = row
                    if self._is_expired(updated_at, data_type):
                        logger.debug("SQLite 缓存已过期: %s", key)
                        cursor.execute(
                            "DELETE FROM cache_entries WHERE cache_key = ?",
                            (key,)
                        )
                        conn.commit()
                        self._record_miss()
                        return None
                    logger.debug("SQLite 缓存命中: %s", key)
                    self._record_hit()
                    return data
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
                    "SELECT data, start_date, end_date, updated_at, data_type FROM cache_entries WHERE cache_key = ?",
                    (key,)
                )
                row = cursor.fetchone()

                if row:
                    data, start_date_str, end_date_str, updated_at, data_type = row
                    if self._is_expired(updated_at, data_type):
                        logger.debug("SQLite 缓存已过期（含范围）: %s", key)
                        cursor.execute(
                            "DELETE FROM cache_entries WHERE cache_key = ?",
                            (key,)
                        )
                        conn.commit()
                        self._record_miss()
                        return None

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
    
    @staticmethod
    def _symbol_variants(symbol: str) -> Tuple[str, ...]:
        """生成股票代码常见变体（如 600519, sh600519, sz600519）。"""
        raw = str(symbol).strip()
        lower = raw.lower()
        if len(lower) == 8 and lower[:2] in ("sh", "sz", "bj") and lower[2:].isdigit():
            base = lower[2:]
        else:
            base = raw
        if base.isdigit() and len(base) == 6:
            return (base, f"sh{base}", f"sz{base}", f"bj{base}", f"SH{base}", f"SZ{base}")
        return (raw, lower, raw.upper())

    def delete_by_symbol(self, symbol: str) -> int:
        """根据股票代码删除缓存数据。
        
        参数:
            symbol: 股票代码。
            
        返回:
            删除的条目数量。
        """
        variants = self._symbol_variants(symbol)
        placeholders = ",".join("?" for _ in variants)
        base_code = variants[0]
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    DELETE FROM cache_entries
                    WHERE symbol IN ({placeholders})
                       OR cache_key LIKE ?
                       OR cache_key LIKE ?
                    """,
                    (*variants, f"%{base_code}%", f"%{symbol}%"),
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
        variants = self._symbol_variants(symbol)
        placeholders = ",".join("?" for _ in variants)
        base_code = variants[0]
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    DELETE FROM cache_entries 
                    WHERE (symbol IN ({placeholders}) OR cache_key LIKE ?)
                    AND (
                        cache_key LIKE '%adjust%qfq%' 
                        OR cache_key LIKE '%adjust%hfq%'
                    )
                    """,
                    (*variants, f"%{base_code}%"),
                )
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

    def cleanup_redundant_entries(self, vacuum: bool = True) -> Dict[str, Any]:
        """清理并合并数据库中的冗余/过期/碎片化缓存数据。

        处理内容：
        1. 删除瞬态高频不应落盘的条目（分笔 tick、分钟行情、全市场实时行情快照、临时波浪分析等）。
        2. 合并或清理旧版因 cache_key 包含 start_date/end_date 产生的重复历史行情碎片，
           将其归并到不含起止日期的标准范围缓存键中。
        3. 清理已过期的非价格类缓存。
        4. 执行 VACUUM 收缩数据库文件体积。
        """
        import os
        import re
        from .serialization import deserialize_dataframe, serialize_dataframe

        size_before = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        transient_deleted = 0
        fragment_deleted = 0
        consolidated_keys = 0
        expired_deleted = 0

        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 1. 删除瞬态/临时条目
            transient_prefixes = (
                "stock_zh_a_tick_tx_js%",
                "stock_zh_a_minute%",
                "stock_zh_a_hist_min_em%",
                "stock_zh_a_spot%",
                "fund_etf_spot_%",
                "wave_analysis_%",
            )
            for prefix in transient_prefixes:
                cursor.execute("DELETE FROM cache_entries WHERE cache_key LIKE ?", (prefix,))
                transient_deleted += cursor.rowcount

            # 2. 查找包含 start_date 或 end_date 的碎片化价格缓存并归并
            cursor.execute(
                """
                SELECT cache_key, data, data_type, symbol
                FROM cache_entries
                WHERE cache_key LIKE '%''start_date''%'
                   OR cache_key LIKE '%''end_date''%'
                """
            )
            fragment_rows = cursor.fetchall()
            grouped_fragments: Dict[str, list] = {}

            date_tuple_pattern = re.compile(
                r"\('(?:start_date|end_date|timeout)',\s*[^)]+\)(?:,\s*)?"
            )

            for old_key, blob, dtype, sym in fragment_rows:
                # 规范化键名：移除 start_date, end_date, timeout
                canonical_key = date_tuple_pattern.sub("", old_key)
                canonical_key = re.sub(r",\s*\]", "]", canonical_key)
                grouped_fragments.setdefault(canonical_key, []).append((old_key, blob, dtype, sym))

            for canonical_key, items in grouped_fragments.items():
                dfs = []
                inferred_sym = None
                for _, blob, _, sym in items:
                    if sym and not inferred_sym:
                        inferred_sym = sym
                    try:
                        df = deserialize_dataframe(blob)
                        if df is not None and not df.empty:
                            dfs.append(df)
                    except Exception:
                        pass

                # 如果 canonical_key 已经存在，也纳入合并
                cursor.execute(
                    "SELECT data, symbol FROM cache_entries WHERE cache_key = ?",
                    (canonical_key,),
                )
                existing_row = cursor.fetchone()
                if existing_row:
                    try:
                        existing_df = deserialize_dataframe(existing_row[0])
                        if existing_df is not None and not existing_df.empty:
                            dfs.append(existing_df)
                    except Exception:
                        pass
                    if existing_row[1] and not inferred_sym:
                        inferred_sym = existing_row[1]

                # 删除所有旧碎片
                for old_key, _, _, _ in items:
                    if old_key != canonical_key:
                        cursor.execute("DELETE FROM cache_entries WHERE cache_key = ?", (old_key,))
                        fragment_deleted += cursor.rowcount

                if dfs:
                    try:
                        merged = pd.concat(dfs, ignore_index=True)
                        date_col = next(
                            (c for c in ("日期", "date", "时间", "datetime", "交易日期", "trade_date") if c in merged.columns),
                            None,
                        )
                        if date_col:
                            merged["_sort_dt"] = pd.to_datetime(merged[date_col], errors="coerce")
                            merged = (
                                merged.dropna(subset=["_sort_dt"])
                                .sort_values("_sort_dt", kind="stable")
                                .drop_duplicates(subset=["_sort_dt"], keep="last")
                                .drop(columns=["_sort_dt"])
                                .reset_index(drop=True)
                            )
                        start_dt, end_dt = self.extract_date_range(merged)
                        if not inferred_sym:
                            m = re.search(r"\('(?:symbol|stock)',\s*'([^']+)'\)", canonical_key)
                            if m:
                                raw_s = m.group(1)
                                inferred_sym = raw_s[2:] if len(raw_s) == 8 and raw_s[:2].lower() in ("sh", "sz", "bj") else raw_s
                        serialized = serialize_dataframe(merged)
                        now_iso = datetime.now().isoformat()
                        cursor.execute(
                            """
                            INSERT INTO cache_entries (
                                cache_key, data, created_at, updated_at,
                                data_type, symbol, start_date, end_date
                            )
                            VALUES (?, ?, ?, ?, 'price', ?, ?, ?)
                            ON CONFLICT(cache_key) DO UPDATE SET
                                data = excluded.data,
                                updated_at = excluded.updated_at,
                                data_type = 'price',
                                symbol = excluded.symbol,
                                start_date = excluded.start_date,
                                end_date = excluded.end_date
                            """,
                            (
                                canonical_key,
                                serialized,
                                now_iso,
                                now_iso,
                                inferred_sym,
                                start_dt.date().isoformat() if start_dt else None,
                                end_dt.date().isoformat() if end_dt else None,
                            ),
                        )
                        consolidated_keys += 1
                    except Exception as merge_err:
                        logger.warning("归并缓存键 %s 失败: %s", canonical_key, merge_err)

            # 3. 清理无 data_type 的陈旧非价格缓存（如旧新闻、旧资金流等超过24小时的条目）
            cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
            cursor.execute(
                """
                DELETE FROM cache_entries
                WHERE (data_type IS NULL OR data_type NOT IN ('price', 'dividend'))
                  AND updated_at < ?
                """,
                (cutoff,),
            )
            expired_deleted += cursor.rowcount

            # 4. 补全历史条目的 symbol/data_type 元数据，并将旧版 pickle 序列化条目升级为高压缩 Parquet，
            #    同时裁剪新浪全量接口产生但超出近 5 年(1250个交易日)的超长历史冗余数据
            cursor.execute("SELECT cache_key, data, data_type, symbol FROM cache_entries")
            all_rows = cursor.fetchall()
            for c_key, blob, d_type, sym in all_rows:
                new_sym = sym
                if not new_sym:
                    m = re.search(r"\('(?:symbol|stock)',\s*'([^']+)'\)", c_key)
                    if m:
                        raw_s = m.group(1)
                        new_sym = (
                            raw_s[2:]
                            if len(raw_s) == 8 and raw_s[:2].lower() in ("sh", "sz", "bj")
                            else raw_s
                        )
                new_dtype = d_type
                if not new_dtype:
                    if any(k in c_key for k in ("hist", "daily", "price", "index")):
                        new_dtype = "price"
                    elif "dividend" in c_key or "fhps" in c_key:
                        new_dtype = "dividend"
                    else:
                        new_dtype = "info"

                is_pickle = bool(blob and bytes(blob[:1]) == b"\x80")
                is_oversized_daily = c_key.startswith("stock_zh_a_daily-") and len(blob) > 80_000

                if is_pickle or is_oversized_daily:
                    try:
                        df = deserialize_dataframe(blob)
                        if df is not None and not df.empty:
                            if is_oversized_daily and len(df) > 1250:
                                df = df.tail(1250).reset_index(drop=True)
                            new_blob = serialize_dataframe(df)
                            start_dt, end_dt = self.extract_date_range(df)
                            cursor.execute(
                                """
                                UPDATE cache_entries
                                SET data = ?, symbol = ?, data_type = ?, start_date = ?, end_date = ?
                                WHERE cache_key = ?
                                """,
                                (
                                    new_blob,
                                    new_sym,
                                    new_dtype,
                                    start_dt.date().isoformat() if start_dt else None,
                                    end_dt.date().isoformat() if end_dt else None,
                                    c_key,
                                ),
                            )
                            continue
                    except Exception:
                        pass

                if new_sym != sym or new_dtype != d_type:
                    cursor.execute(
                        "UPDATE cache_entries SET symbol = ?, data_type = ? WHERE cache_key = ?",
                        (new_sym, new_dtype, c_key),
                    )

            conn.commit()

            if vacuum:
                # SQLite VACUUM 需要在无未决事务时执行
                conn.execute("VACUUM")

            cursor.execute("SELECT COUNT(*) FROM cache_entries")
            remaining_count = cursor.fetchone()[0]

        size_after = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {
            "transient_deleted": transient_deleted,
            "fragment_deleted": fragment_deleted,
            "consolidated_keys": consolidated_keys,
            "expired_deleted": expired_deleted,
            "remaining_entries": remaining_count,
            "size_before_bytes": size_before,
            "size_after_bytes": size_after,
        }
    
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
