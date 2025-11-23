"""AkShare 客户端封装。

此模块封装 AkShare 库的函数调用，提供：
- 统一的缓存管理
- 错误处理和转换
- 数据解析异常处理
- 日志记录
"""

import logging
from typing import Optional, Any, Callable
import pandas as pd
import akshare as ak

from ..config import Settings, get_settings
from ..exceptions import APIException, DataParsingException
from ..cache import CacheKey

logger = logging.getLogger(__name__)


class AkShareClient:
    """AkShare 客户端封装类。
    
    此类封装 AkShare 库的函数调用，提供统一的缓存管理、
    错误处理和日志记录功能。
    
    Attributes:
        settings: 配置实例
    """
    
    def __init__(self, settings: Optional[Settings] = None):
        """初始化 AkShare 客户端。
        
        Args:
            settings: 可选的配置实例（如果为 None，将使用全局配置）
        """
        self.settings = settings or get_settings()
        logger.info("AkShare 客户端已初始化")
    
    def call(
        self,
        func: Callable,
        *args: Any,
        cache_key: Optional[str] = None,
        ttl: Optional[int] = None,
        ttl2: Optional[int] = None,
        **kwargs: Any
    ) -> Optional[pd.DataFrame]:
        """调用 AkShare 函数并处理缓存和错误。
        
        此方法封装 AkShare 函数调用，提供：
        - 自动缓存管理（内存 + 磁盘）
        - 错误处理和转换
        - 数据解析异常处理
        - 请求日志记录
        
        Args:
            func: 要调用的 AkShare 函数
            *args: 传递给函数的位置参数
            cache_key: 可选的缓存键（如果为 None，将自动生成）
            ttl: 内存缓存 TTL（秒），如果为 None 使用配置值
            ttl2: 磁盘缓存 TTL（秒），如果为 None 使用 ttl * 2
            **kwargs: 传递给函数的关键字参数
        
        Returns:
            pandas DataFrame 或 None（如果调用失败）
            
        Raises:
            APIException: 当 AkShare 函数调用失败时
            DataParsingException: 当返回的数据无法解析时
        """
        # 生成缓存键
        if cache_key is None:
            cache_key = self._generate_cache_key(func, args, kwargs)
        
        # 设置 TTL 值
        if ttl is None:
            ttl = self.settings.cache_memory_ttl
        if ttl2 is None:
            ttl2 = ttl * 2
        
        # 初始化缓存
        cache = CacheKey.init(
            cache_key,
            ttl=ttl,
            ttl2=ttl2,
            maxsize=self.settings.cache_max_size
        )
        
        # 尝试从缓存获取
        cached_data = cache.get()
        if cached_data is not None:
            logger.debug(f"缓存命中: {cache_key}")
            return cached_data
        
        # 缓存未命中，调用 AkShare 函数
        logger.info(f"调用 AkShare 函数: {func.__name__}, args={args}, kwargs={kwargs}")
        
        try:
            # 调用 AkShare 函数
            result = func(*args, **kwargs)
            
            # 验证返回结果
            if result is None:
                logger.warning(f"AkShare 函数 {func.__name__} 返回 None")
                return None
            
            # 验证返回的是 DataFrame
            if not isinstance(result, pd.DataFrame):
                error_msg = f"期望返回 pandas.DataFrame，但得到 {type(result).__name__}"
                logger.error(f"数据解析错误: {error_msg}")
                raise DataParsingException(
                    source=f"AkShare.{func.__name__}",
                    message=error_msg
                )
            
            # 检查 DataFrame 是否为空
            if result.empty:
                logger.warning(f"AkShare 函数 {func.__name__} 返回空 DataFrame")
                return result
            
            # 存入缓存
            cache.set(result)
            logger.debug(f"数据已缓存: {cache_key}")
            
            return result
            
        except DataParsingException:
            # 重新抛出数据解析异常
            raise
            
        except Exception as e:
            # 捕获所有其他异常并转换为 APIException
            error_msg = str(e)
            logger.error(
                f"AkShare 函数调用失败: {func.__name__}, "
                f"错误: {error_msg}",
                exc_info=True
            )
            raise APIException(
                api_name=f"AkShare.{func.__name__}",
                message=error_msg
            )
    
    def _generate_cache_key(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict
    ) -> str:
        """生成缓存键。
        
        缓存键包含函数名和所有参数，以确保唯一性。
        
        Args:
            func: AkShare 函数
            args: 位置参数
            kwargs: 关键字参数
        
        Returns:
            生成的缓存键字符串
        """
        # 构建缓存键：函数名-参数
        key_parts = [func.__name__]
        
        if args:
            key_parts.append(str(args))
        
        if kwargs:
            # 对 kwargs 排序以确保一致性
            sorted_kwargs = sorted(kwargs.items())
            key_parts.append(str(sorted_kwargs))
        
        cache_key = "-".join(key_parts)
        return cache_key
    
    # 便捷方法：常用的 AkShare 函数封装
    
    def stock_individual_info_em(
        self,
        symbol: str,
        ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取 A 股个股信息（东方财富）。
        
        Args:
            symbol: 股票代码
            ttl: 缓存 TTL（秒）
        
        Returns:
            股票信息 DataFrame
        """
        return self.call(
            ak.stock_individual_info_em,
            symbol=symbol,
            ttl=ttl or 43200  # 12 小时
        )
    
    def stock_zh_a_hist(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "20200101",
        end_date: str = "20500101",
        adjust: str = "",
        ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取 A 股历史行情数据。
        
        Args:
            symbol: 股票代码
            period: 周期（daily/weekly/monthly）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            adjust: 复权类型（qfq/hfq/空字符串）
            ttl: 缓存 TTL（秒）
        
        Returns:
            历史行情 DataFrame
        """
        return self.call(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            ttl=ttl or 3600  # 1 小时
        )
    
    def stock_hk_hist(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "20200101",
        end_date: str = "20500101",
        adjust: str = "",
        ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取港股历史行情数据。
        
        Args:
            symbol: 股票代码
            period: 周期（daily/weekly）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            adjust: 复权类型（qfq/hfq/空字符串）
            ttl: 缓存 TTL（秒）
        
        Returns:
            历史行情 DataFrame
        """
        return self.call(
            ak.stock_hk_hist,
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            ttl=ttl or 3600  # 1 小时
        )
    
    def stock_us_daily(
        self,
        symbol: str,
        adjust: str = "",
        ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取美股日线数据。
        
        Args:
            symbol: 股票代码
            adjust: 复权类型（qfq/hfq/空字符串）
            ttl: 缓存 TTL（秒）
        
        Returns:
            日线数据 DataFrame
        """
        return self.call(
            ak.stock_us_daily,
            symbol=symbol,
            adjust=adjust,
            ttl=ttl or 3600  # 1 小时
        )
    
    def stock_info_a_code_name(
        self,
        ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取 A 股股票代码和名称。
        
        Args:
            ttl: 缓存 TTL（秒）
        
        Returns:
            股票代码名称 DataFrame
        """
        return self.call(
            ak.stock_info_a_code_name,
            ttl=ttl or 86400  # 24 小时
        )
    
    def stock_zt_pool_em(
        self,
        date: str,
        ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取涨停股池。
        
        Args:
            date: 日期（YYYYMMDD）
            ttl: 缓存 TTL（秒）
        
        Returns:
            涨停股池 DataFrame
        """
        return self.call(
            ak.stock_zt_pool_em,
            date=date,
            ttl=ttl or 1200  # 20 分钟
        )
    
    def tool_trade_date_hist_sina(
        self,
        ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取交易日历史数据。
        
        Args:
            ttl: 缓存 TTL（秒）
        
        Returns:
            交易日历史 DataFrame
        """
        return self.call(
            ak.tool_trade_date_hist_sina,
            ttl=ttl or 43200  # 12 小时
        )
