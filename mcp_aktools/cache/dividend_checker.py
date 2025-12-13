"""分红检查器，用于缓存失效处理。

此模块提供检查公司行为（分红）的功能，
当检测到新的除权除息日时，使缓存的价格数据失效。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

from mcp_aktools.cache.sqlite_backend import SQLiteCacheBackend

logger = logging.getLogger(__name__)

# 分红数据缓存的默认 TTL（24 小时，单位：秒）
DEFAULT_DIVIDEND_TTL = 86400


class DividendChecker:
    """分红检查器，用于检测除权除息事件并触发缓存失效。
    
    此类监控股票的分红事件，当检测到新的除权除息日时，
    使缓存的价格数据失效。分红数据本身以 24 小时 TTL 缓存，
    以减少 API 调用次数。
    
    属性:
        cache_backend: 用于存储分红记录的 SQLite 缓存后端。
        ttl: 分红数据缓存的 TTL，单位为秒（默认：24 小时）。
    """
    
    def __init__(
        self, 
        cache_backend: SQLiteCacheBackend, 
        ttl: int = DEFAULT_DIVIDEND_TTL
    ):
        """初始化分红检查器。
        
        参数:
            cache_backend: SQLite 缓存后端实例。
            ttl: 分红数据缓存的 TTL，单位为秒（默认：86400 = 24 小时）。
        """
        self.cache_backend = cache_backend
        self.ttl = ttl
        logger.debug(f"DividendChecker 已初始化，TTL={ttl}s")

    def _get_dividend_cache_key(self, symbol: str) -> str:
        """生成分红数据的缓存键。
        
        参数:
            symbol: 股票代码。
            
        返回:
            缓存键字符串。
        """
        return f"dividend_check:{symbol}"
    
    def _is_cache_expired(self, created_at: datetime) -> bool:
        """检查缓存的分红数据是否已过期。
        
        参数:
            created_at: 缓存条目创建时的时间戳。
            
        返回:
            如果缓存已过期返回 True，否则返回 False。
        """
        expiry_time = created_at + timedelta(seconds=self.ttl)
        return datetime.now() > expiry_time

    def get_latest_ex_dividend_date(self, symbol: str) -> Optional[datetime]:
        """获取股票的最新除权除息日。
        
        此方法首先检查缓存中的分红数据。如果缓存已过期或不存在，
        则使用 stock_history_dividend_detail(symbol, indicator='分红')
        从 AkShare API 获取最新数据。
        
        参数:
            symbol: 股票代码（如 '000001' 代表平安银行）。
            
        返回:
            最新除权除息日的 datetime 对象，如果没有分红记录则返回 None。
        """
        cache_key = self._get_dividend_cache_key(symbol)
        
        # 首先尝试从缓存获取
        cached_data = self._get_cached_dividend_data(symbol)
        if cached_data is not None:
            return cached_data
        
        # 缓存未命中或已过期，从 API 获取
        try:
            logger.info(f"正在从 API 获取股票 {symbol} 的分红数据")
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            
            if df is None or df.empty:
                logger.debug(f"未找到股票 {symbol} 的分红记录")
                # 缓存"无分红"结果以避免重复 API 调用
                self._cache_dividend_result(symbol, None)
                return None
            
            # 查找最新的除权除息日
            # 列名可能有所不同，常见名称：'除权除息日'、'除息日'
            ex_div_date_col = None
            for col in ['除权除息日', '除息日', 'ex_dividend_date']:
                if col in df.columns:
                    ex_div_date_col = col
                    break
            
            if ex_div_date_col is None:
                logger.warning(f"无法在股票 {symbol} 的分红数据中找到除权除息日列")
                logger.debug(f"可用列: {df.columns.tolist()}")
                self._cache_dividend_result(symbol, None)
                return None
            
            # 解析日期并找到最新的
            df[ex_div_date_col] = pd.to_datetime(df[ex_div_date_col], errors='coerce')
            valid_dates = df[ex_div_date_col].dropna()
            
            if valid_dates.empty:
                logger.debug(f"未找到股票 {symbol} 的有效除权除息日")
                self._cache_dividend_result(symbol, None)
                return None
            
            latest_date = valid_dates.max()
            latest_date_dt = latest_date.to_pydatetime() if hasattr(latest_date, 'to_pydatetime') else latest_date
            
            logger.debug(f"股票 {symbol} 的最新除权除息日: {latest_date_dt}")
            
            # 缓存结果
            self._cache_dividend_result(symbol, latest_date_dt)
            
            # 存储到 dividend_records 表以便追踪
            self._store_dividend_record(symbol, latest_date_dt)
            
            return latest_date_dt
            
        except Exception as e:
            logger.error(f"获取股票 {symbol} 的分红数据失败: {e}", exc_info=True)
            return None

    def _get_cached_dividend_data(self, symbol: str) -> Optional[datetime]:
        """获取未过期的缓存分红数据。
        
        参数:
            symbol: 股票代码。
            
        返回:
            如果缓存有效则返回缓存的除权除息日，否则返回 None。
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
            
            # 检查缓存是否已过期
            if self._is_cache_expired(created_at):
                logger.debug(f"股票 {symbol} 的分红缓存已过期")
                return None
            
            # 解码缓存的日期
            if data == b'none':
                return None
            
            date_str = data.decode('utf-8')
            return datetime.fromisoformat(date_str)
            
        except Exception as e:
            logger.error(f"获取股票 {symbol} 的缓存分红数据失败: {e}", exc_info=True)
            return None

    def _cache_dividend_result(self, symbol: str, ex_dividend_date: Optional[datetime]) -> None:
        """缓存分红检查结果。
        
        参数:
            symbol: 股票代码。
            ex_dividend_date: 要缓存的除权除息日，如果无分红则为 None。
        """
        cache_key = self._get_dividend_cache_key(symbol)
        
        try:
            # 将日期编码为字节
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
            logger.debug(f"已缓存股票 {symbol} 的分红结果: {ex_dividend_date}")
            
        except Exception as e:
            logger.error(f"缓存股票 {symbol} 的分红结果失败: {e}", exc_info=True)
    
    def _store_dividend_record(self, symbol: str, ex_dividend_date: datetime) -> None:
        """将分红记录存储到 dividend_records 表。
        
        参数:
            symbol: 股票代码。
            ex_dividend_date: 除权除息日。
        """
        try:
            conn = self.cache_backend._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO dividend_records (symbol, ex_dividend_date, dividend_type)
                VALUES (?, ?, ?)
            """, (symbol, ex_dividend_date.date().isoformat(), 'cash'))
            
            conn.commit()
            logger.debug(f"已存储股票 {symbol} 的分红记录: {ex_dividend_date}")
            
        except Exception as e:
            logger.error(f"存储股票 {symbol} 的分红记录失败: {e}", exc_info=True)

    def _get_stored_ex_dividend_date(self, symbol: str) -> Optional[datetime]:
        """从 dividend_records 表获取之前存储的除权除息日。
        
        参数:
            symbol: 股票代码。
            
        返回:
            存储的除权除息日，如果未找到则返回 None。
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
            logger.error(f"获取股票 {symbol} 存储的除权除息日失败: {e}", exc_info=True)
            return None
    
    def should_invalidate_cache(self, symbol: str) -> bool:
        """检查是否应因新的分红事件而使缓存失效。
        
        此方法将 API 返回的最新除权除息日与之前存储的日期进行比较。
        如果检测到新的（更近的）除权除息日，则应使缓存失效。
        
        参数:
            symbol: 股票代码。
            
        返回:
            如果应使缓存失效返回 True，否则返回 False。
        """
        try:
            # 获取之前存储的除权除息日
            stored_date = self._get_stored_ex_dividend_date(symbol)
            
            # 从 API 获取最新的除权除息日（可能使用缓存）
            latest_date = self.get_latest_ex_dividend_date(symbol)
            
            if latest_date is None:
                # 没有分红数据，无需使缓存失效
                return False
            
            if stored_date is None:
                # 首次看到分红数据，存储但不使缓存失效
                # （缓存刚刚被填充）
                return False
            
            # 比较日期 - 如果新日期更近则使缓存失效
            if latest_date > stored_date:
                logger.info(
                    f"检测到股票 {symbol} 的新除权除息日: "
                    f"{latest_date} > {stored_date}，需要使缓存失效"
                )
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查股票 {symbol} 的缓存失效状态失败: {e}", exc_info=True)
            return False
    
    def invalidate_stock_cache(self, symbol: str) -> int:
        """使股票的所有缓存价格数据失效。
        
        此方法从数据库中删除指定股票代码的所有缓存条目。
        
        参数:
            symbol: 股票代码。
            
        返回:
            删除的缓存条目数量。
        """
        try:
            deleted_count = self.cache_backend.delete_by_symbol(symbol)
            logger.info(f"已使股票 {symbol} 的 {deleted_count} 条缓存条目失效")
            return deleted_count
            
        except Exception as e:
            logger.error(f"使股票 {symbol} 的缓存失效失败: {e}", exc_info=True)
            return 0
    
    def check_and_invalidate(self, symbol: str) -> bool:
        """检查新分红并在需要时使缓存失效。
        
        这是一个便捷方法，结合了 should_invalidate_cache
        和 invalidate_stock_cache 的功能。
        
        参数:
            symbol: 股票代码。
            
        返回:
            如果缓存被使失效返回 True，否则返回 False。
        """
        if self.should_invalidate_cache(symbol):
            self.invalidate_stock_cache(symbol)
            return True
        return False
