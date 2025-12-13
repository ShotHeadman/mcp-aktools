"""AkShare 客户端封装。

此模块封装 AkShare 库的函数调用，提供：
- 统一的缓存管理
- 错误处理和转换
- 数据解析异常处理
- 日志记录
- 重试机制
"""

import logging
import time
from typing import Any, Callable, Optional
from http.client import RemoteDisconnected
from urllib.error import URLError

import akshare as ak
import pandas as pd

from ..cache import CacheKey
from ..config import Settings, get_settings
from ..exceptions import APIException, DataParsingException, ValidationException

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
        self.max_retries = self.settings.retry_max_attempts
        self.retry_backoff_factor = self.settings.retry_backoff_factor
        self.retry_max_delay = self.settings.retry_max_delay
        
        # Initialize SQLite cache backend (Requirements 1.1, 1.2)
        self._init_sqlite_backend()
        
        logger.info("AkShare 客户端已初始化")

    def _init_sqlite_backend(self) -> None:
        """Initialize SQLite cache backend with database path from settings.
        
        This method initializes the SQLite cache backend singleton using the
        database path configured in settings. If DATABASE_URL is not set,
        a default path in the user's cache directory will be used.
        """
        try:
            db_path = self.settings.get_database_path()
            CacheKey.init_sqlite_backend(db_path)
            logger.debug(f"SQLite cache backend initialized with path: {db_path}")
        except Exception as e:
            logger.warning(f"Failed to initialize SQLite cache backend: {e}. Falling back to memory-only cache.")

    def call(
        self,
        func: Callable,
        *args: Any,
        cache_key: Optional[str] = None,
        ttl: Optional[int] = None,
        ttl2: Optional[int] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
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
            timeout: 请求超时时间（秒），如果为 None 使用 AkShare 默认值
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

        # 传递 timeout 参数到 kwargs
        if timeout is not None:
            kwargs['timeout'] = timeout

        # 初始化缓存
        cache = CacheKey.init(
            cache_key, ttl=ttl, ttl2=ttl2, maxsize=self.settings.cache_max_size
        )

        # 尝试从缓存获取
        cached_data = cache.get()
        if cached_data is not None:
            logger.debug(f"缓存命中: {cache_key}")
            return cached_data

        # 缓存未命中，调用 AkShare 函数
        logger.info(f"调用 AkShare 函数: {func.__name__}, args={args}, kwargs={kwargs}")

        # 重试机制
        last_exception = None
        for attempt in range(self.max_retries):
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
                        source=f"AkShare.{func.__name__}", message=error_msg
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

            except TimeoutError as e:
                # 超时错误，记录特定日志并进行重试
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait_time = min(
                        self.retry_backoff_factor ** attempt,
                        self.retry_max_delay
                    )
                    logger.warning(
                        f"请求超时 (尝试 {attempt + 1}/{self.max_retries}): {func.__name__}, "
                        f"timeout={timeout}, {wait_time:.1f}秒后重试..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"请求超时，达到最大重试次数 ({self.max_retries}): {func.__name__}"
                    )

            except (RemoteDisconnected, URLError, ConnectionError) as e:
                # 网络相关错误，进行重试
                last_exception = e
                if attempt < self.max_retries - 1:
                    # 指数退避策略
                    wait_time = min(
                        self.retry_backoff_factor ** attempt,
                        self.retry_max_delay
                    )
                    logger.warning(
                        f"网络错误 (尝试 {attempt + 1}/{self.max_retries}): {str(e)}, "
                        f"{wait_time:.1f}秒后重试..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"达到最大重试次数 ({self.max_retries}), 最后错误: {str(e)}"
                    )

            except KeyError as e:
                # 数据结构变化导致的 KeyError，记录特定消息并抛出 DataParsingException
                error_msg = f"数据结构可能已变化，缺少键: {e}"
                logger.error(
                    f"AkShare 函数 {func.__name__} 数据结构异常: {error_msg}",
                    exc_info=True,
                )
                raise DataParsingException(
                    source=f"AkShare.{func.__name__}",
                    message=error_msg
                )

            except ValueError as e:
                # 参数值错误，记录特定消息并抛出 ValidationException
                error_msg = f"参数值错误: {e}"
                logger.error(
                    f"AkShare 函数 {func.__name__} 参数错误: {error_msg}",
                    exc_info=True,
                )
                raise ValidationException(
                    param_name="unknown",
                    message=str(e)
                )

            except Exception as e:
                # 其他未知异常，记录完整异常详情并抛出 APIException
                error_msg = str(e)
                logger.error(
                    f"AkShare 函数调用失败: {func.__name__}, "
                    f"错误类型: {type(e).__name__}, 错误: {error_msg}",
                    exc_info=True,
                )
                raise APIException(api_name=f"AkShare.{func.__name__}", message=error_msg)

        # 所有重试都失败
        error_msg = f"网络连接失败，已重试 {self.max_retries} 次: {str(last_exception)}"
        logger.error(f"AkShare 函数调用失败: {func.__name__}, {error_msg}")
        raise APIException(api_name=f"AkShare.{func.__name__}", message=error_msg)

    def _generate_cache_key(self, func: Callable, args: tuple, kwargs: dict) -> str:
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
        self, symbol: str, market: str = "sz", ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取 A 股个股信息（东方财富），失败时尝试雪球备用接口。

        Args:
            symbol: 股票代码
            market: 市场标识 (sh/sz)，用于雪球备用接口
            ttl: 缓存 TTL（秒）

        Returns:
            股票信息 DataFrame
        """
        try:
            return self.call(
                ak.stock_individual_info_em, symbol=symbol, ttl=ttl or 43200  # 12 小时
            )
        except APIException as e:
            logger.warning(f"东财接口失败，尝试雪球备用接口: {e}")
            try:
                # 雪球接口需要格式: SH601127 或 SZ000001
                xq_symbol = f"{market.upper()}{symbol}"
                logger.info(f"尝试雪球接口，转换后的代码: {xq_symbol}")
                result = self.call(
                    ak.stock_individual_basic_info_xq, 
                    symbol=xq_symbol, 
                    ttl=ttl or 43200
                )
                if result is not None:
                    logger.info(f"雪球备用接口成功获取数据: {xq_symbol}")
                    return result
            except Exception as fallback_error:
                logger.error(f"雪球备用接口也失败: {fallback_error}")
            raise e  # 抛出原始异常

    def _check_dividend_invalidation(self, symbol: str) -> bool:
        """检查新分红并在需要时使缓存失效。
        
        此方法检查股票是否有新的除权除息日，
        如果检测到新分红则使缓存的价格数据失效。
        
        参数:
            symbol: 股票代码（如 '000001'）
            
        返回:
            如果缓存被使失效返回 True，否则返回 False。
        """
        dividend_checker = CacheKey.get_dividend_checker()
        if dividend_checker is None:
            return False
        
        try:
            if dividend_checker.should_invalidate_cache(symbol):
                logger.info(f"检测到股票 {symbol} 有新分红，正在使价格缓存失效")
                dividend_checker.invalidate_stock_cache(symbol)
                # 同时清除此股票代码的内存缓存条目
                CacheKey.clear_by_symbol(symbol)
                return True
        except Exception as e:
            logger.warning(f"检查股票 {symbol} 的分红失败: {e}")
        
        return False

    def stock_zh_a_hist(
        self,
        symbol: str,
        market: str = "sz",
        period: str = "daily",
        start_date: str = "20200101",
        end_date: str = "22220101",
        adjust: str = "",
        ttl: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """获取 A 股历史行情数据，失败时依次尝试腾讯和新浪备用接口。

        Args:
            symbol: 股票代码
            market: 市场标识 (sh/sz)，用于备用接口
            period: 周期（daily/weekly/monthly）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD）
            adjust: 复权类型（qfq/hfq/空字符串）
            ttl: 缓存 TTL（秒）

        Returns:
            历史行情 DataFrame
        """
        # 在返回缓存的价格数据前检查新分红（需求 4.1, 4.3）
        # 仅对复权价格数据（qfq/hfq）进行检查，因为原始价格不受影响
        if adjust in ("qfq", "hfq"):
            self._check_dividend_invalidation(symbol)
        
        try:
            return self.call(
                ak.stock_zh_a_hist,
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
                ttl=ttl or 3600,  # 1 小时
            )
        except APIException as e:
            # 只在 daily 周期时尝试备用接口（备用接口不支持 weekly/monthly）
            if period == "daily":
                logger.warning(f"东财历史行情接口失败，尝试备用接口: {e}")
                
                # 尝试腾讯接口
                try:
                    # 腾讯接口需要格式: sz000001 或 sh600000 (小写市场前缀)
                    tx_symbol = f"{market.lower()}{symbol}"
                    logger.info(f"尝试腾讯接口，转换后的代码: {tx_symbol}")
                    result = self.call(
                        ak.stock_zh_a_hist_tx,
                        symbol=tx_symbol,
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                        ttl=ttl or 3600,
                    )
                    if result is not None and not result.empty:
                        logger.info(f"腾讯备用接口成功获取数据: {tx_symbol}")
                        # 统一列名格式，使其与东财接口一致
                        if 'date' in result.columns:
                            result.rename(columns={'date': '日期'}, inplace=True)
                        if 'open' in result.columns:
                            result.rename(columns={
                                'open': '开盘',
                                'close': '收盘',
                                'high': '最高',
                                'low': '最低',
                                'amount': '成交量'
                            }, inplace=True)
                        # 添加缺失的列
                        if '换手率' not in result.columns:
                            result['换手率'] = None
                        if '成交额' not in result.columns:
                            result['成交额'] = None
                        if '振幅' not in result.columns:
                            result['振幅'] = None
                        if '涨跌幅' not in result.columns:
                            result['涨跌幅'] = None
                        if '涨跌额' not in result.columns:
                            result['涨跌额'] = None
                        return result
                except Exception as tx_error:
                    logger.warning(f"腾讯备用接口失败: {tx_error}")
                
                # 尝试新浪接口
                try:
                    # 新浪接口需要格式: sh600000 或 sz000001 (小写市场前缀)
                    sina_symbol = f"{market.lower()}{symbol}"
                    logger.info(f"尝试新浪接口，转换后的代码: {sina_symbol}")
                    result = self.call(
                        ak.stock_zh_a_daily,
                        symbol=sina_symbol,
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                        ttl=ttl or 3600,
                    )
                    if result is not None and not result.empty:
                        logger.info(f"新浪备用接口成功获取数据: {sina_symbol}")
                        # 统一列名格式，使其与东财接口一致
                        if 'date' in result.columns:
                            result.rename(columns={'date': '日期'}, inplace=True)
                        if 'open' in result.columns:
                            result.rename(columns={
                                'open': '开盘',
                                'close': '收盘',
                                'high': '最高',
                                'low': '最低',
                                'volume': '成交量',
                                'amount': '成交额',
                                'turnover': '换手率'
                            }, inplace=True)
                        # 添加缺失的列
                        if '振幅' not in result.columns:
                            result['振幅'] = None
                        if '涨跌幅' not in result.columns:
                            result['涨跌幅'] = None
                        if '涨跌额' not in result.columns:
                            result['涨跌额'] = None
                        return result
                except Exception as sina_error:
                    logger.error(f"新浪备用接口也失败: {sina_error}")
            
            raise e  # 所有接口都失败，抛出原始异常

    def stock_hk_hist(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "20200101",
        end_date: str = "22220101",
        adjust: str = "",
        ttl: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """获取港股历史行情数据，失败时尝试新浪备用接口。

        Args:
            symbol: 股票代码
            period: 周期（daily/weekly/monthly）
            start_date: 开始日期（YYYYMMDD）
            end_date: 结束日期（YYYYMMDD），默认 "22220101"
            adjust: 复权类型（qfq/hfq/空字符串）
            ttl: 缓存 TTL（秒）

        Returns:
            历史行情 DataFrame
        """
        try:
            return self.call(
                ak.stock_hk_hist,
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
                ttl=ttl or 3600,  # 1 小时
            )
        except APIException as e:
            # 只在 daily 周期时尝试新浪备用接口（新浪接口不支持 weekly/monthly）
            if period == "daily":
                logger.warning(f"东财港股历史行情接口失败，尝试新浪备用接口: {e}")
                try:
                    logger.info(f"尝试新浪港股接口，代码: {symbol}")
                    result = self.call(
                        ak.stock_hk_daily,
                        symbol=symbol,
                        adjust=adjust,
                        ttl=ttl or 3600,
                    )
                    if result is not None and not result.empty:
                        logger.info(f"新浪港股备用接口成功获取数据: {symbol}")
                        # 统一列名格式，使其与东财接口一致
                        if 'date' in result.columns:
                            result.rename(columns={'date': '日期'}, inplace=True)
                        if 'open' in result.columns:
                            result.rename(columns={
                                'open': '开盘',
                                'close': '收盘',
                                'high': '最高',
                                'low': '最低',
                                'volume': '成交量'
                            }, inplace=True)
                        # 添加缺失的列
                        if '成交额' not in result.columns:
                            result['成交额'] = None
                        if '换手率' not in result.columns:
                            result['换手率'] = None
                        if '振幅' not in result.columns:
                            result['振幅'] = None
                        if '涨跌幅' not in result.columns:
                            result['涨跌幅'] = None
                        if '涨跌额' not in result.columns:
                            result['涨跌额'] = None
                        
                        # 过滤日期范围（新浪接口返回所有历史数据）
                        if '日期' in result.columns:
                            result['日期'] = pd.to_datetime(result['日期'])
                            start = pd.to_datetime(start_date)
                            end = pd.to_datetime(end_date)
                            result = result[(result['日期'] >= start) & (result['日期'] <= end)]
                            result['日期'] = result['日期'].dt.strftime('%Y-%m-%d')
                        
                        return result
                except Exception as sina_error:
                    logger.error(f"新浪港股备用接口也失败: {sina_error}")
            
            raise e  # 抛出原始异常

    def stock_us_daily(
        self, symbol: str, adjust: str = "", ttl: Optional[int] = None
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
            ak.stock_us_daily, symbol=symbol, adjust=adjust, ttl=ttl or 3600  # 1 小时
        )

    def stock_info_a_code_name(
        self, ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取 A 股股票代码和名称。

        Args:
            ttl: 缓存 TTL（秒）

        Returns:
            股票代码名称 DataFrame
        """
        return self.call(ak.stock_info_a_code_name, ttl=ttl or 86400)  # 24 小时

    def stock_zt_pool_em(
        self, date: str, ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取涨停股池。

        Args:
            date: 日期（YYYYMMDD）
            ttl: 缓存 TTL（秒）

        Returns:
            涨停股池 DataFrame
        """
        return self.call(ak.stock_zt_pool_em, date=date, ttl=ttl or 1200)  # 20 分钟

    def tool_trade_date_hist_sina(
        self, ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取交易日历史数据。

        Args:
            ttl: 缓存 TTL（秒）

        Returns:
            交易日历史 DataFrame
        """
        return self.call(ak.tool_trade_date_hist_sina, ttl=ttl or 43200)  # 12 小时
