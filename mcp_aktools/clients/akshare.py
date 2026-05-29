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
        timeout: Optional[float] = None,
        enable_range_completion: bool = True,
        **kwargs: Any,
    ) -> Optional[pd.DataFrame]:
        """调用 AkShare 函数并处理缓存和错误。

        此方法封装 AkShare 函数调用，提供：
        - 自动缓存管理（内存 + 磁盘）
        - 智能范围补全（可选）
        - 错误处理和转换
        - 数据解析异常处理
        - 请求日志记录

        Args:
            func: 要调用的 AkShare 函数
            *args: 传递给函数的位置参数
            cache_key: 可选的缓存键（如果为 None，将自动生成）
            ttl: 内存缓存 TTL（秒），如果为 None 使用配置值
            timeout: 请求超时时间（秒），如果为 None 使用 AkShare 默认值
            enable_range_completion: 是否启用范围补全功能（默认 True）
            **kwargs: 传递给函数的关键字参数

        Returns:
            pandas DataFrame 或 None（如果调用失败）

        Raises:
            APIException: 当 AkShare 函数调用失败时
            DataParsingException: 当返回的数据无法解析时
            
        需求: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5
        """
        # 生成缓存键
        if cache_key is None:
            cache_key = self._generate_cache_key(func, args, kwargs)

        # 设置 TTL 值
        if ttl is None:
            ttl = self.settings.cache_memory_ttl

        # 传递 timeout 参数到 kwargs
        if timeout is not None:
            kwargs['timeout'] = timeout

        # 检查是否启用范围补全且有日期参数
        has_date_params = 'start_date' in kwargs and 'end_date' in kwargs
        use_range_completion = enable_range_completion and has_date_params

        if use_range_completion:
            # 使用智能范围补全逻辑
            # 需求 6.1: 记录范围补全启用日志
            logger.debug(
                "启用范围补全: key=%s, 函数=%s, 范围=[%s, %s]",
                cache_key, func.__name__, kwargs['start_date'], kwargs['end_date']
            )

            # 检查缓存范围
            range_check = self._check_cache_range(
                cache_key,
                kwargs['start_date'],
                kwargs['end_date']
            )

            status = range_check['status']

            # 需求 2.4: 完全命中时直接返回缓存数据
            if status == 'full_hit':
                # 需求 6.1: 记录完全命中日志
                logger.info(
                    "范围完全命中，直接返回缓存数据: key=%s, 行数=%d",
                    cache_key, len(range_check['cached_data'])
                )
                return range_check['cached_data']
            
            # 需求 2.5: 缓存不存在或部分命中，需要补全
            if status == 'miss':
                # 需求 6.1: 记录缓存未命中日志
                logger.info(
                    "缓存未命中，获取完整范围数据: key=%s, 范围=[%s, %s]",
                    cache_key, kwargs['start_date'], kwargs['end_date']
                )
                # 缓存不存在，使用原有逻辑获取完整数据
                try:
                    result = self._fetch_data_with_retry(func, args, kwargs)

                    if result is not None and not result.empty:
                        # 提取日期范围并存入缓存
                        from ..cache.sqlite_backend import SQLiteCacheBackend
                        from ..cache.serialization import serialize_dataframe

                        start_date, end_date = SQLiteCacheBackend.extract_date_range(result)

                        # 使用 set_with_range() 存储数据和日期范围
                        backend = CacheKey.get_sqlite_backend()
                        if backend is not None and start_date is not None and end_date is not None:
                            try:
                                serialized = serialize_dataframe(result)
                                backend.set_with_range(
                                    cache_key,
                                    serialized,
                                    start_date=start_date,
                                    end_date=end_date,
                                    data_type='dataframe',
                                    symbol=kwargs.get('symbol')
                                )
                                logger.debug(
                                    "数据已缓存（带范围）: key=%s, 范围=[%s, %s], 行数=%d",
                                    cache_key, start_date, end_date, len(result)
                                )
                            except Exception as cache_error:
                                # 需求 5.3, 6.4: 缓存失败不影响数据返回
                                logger.warning(
                                    "缓存数据失败: %s, 继续返回数据",
                                    cache_error, exc_info=True
                                )

                        # 同时更新内存缓存（SQLite 已经通过 set_with_range 设置）
                        try:
                            cache = CacheKey.init(
                                cache_key, ttl=ttl, maxsize=self.settings.cache_max_size
                            )
                            # 只设置内存缓存，不重复写入 SQLite
                            cache.cache1[cache_key] = result
                            logger.debug("缓存已设置（仅内存）: %s (ttl=%ds)", cache_key, ttl)
                        except Exception as mem_cache_error:
                            # 需求 5.3, 6.4: 内存缓存失败不影响数据返回
                            logger.warning(
                                "内存缓存失败: %s, 继续返回数据",
                                mem_cache_error
                            )

                    return result

                except Exception as e:
                    # 需求 5.4, 6.4: API 调用失败的错误处理
                    logger.error(
                        "获取完整范围数据失败: key=%s, 错误=%s",
                        cache_key, e, exc_info=True
                    )
                    raise
            
            elif status == 'partial_hit':
                # 需求 2.1, 2.2, 2.3: 部分命中，需要补全缺失范围
                # 需求 6.1: 记录部分命中日志
                logger.info(
                    "缓存部分命中，补全缺失数据段: key=%s, 缺失段数=%d",
                    cache_key, len(range_check['missing_ranges'])
                )

                cached_df = range_check['cached_data']
                missing_ranges = range_check['missing_ranges']

                # 需求 3.1, 3.2: 获取缺失的数据段
                try:
                    # 从 kwargs 中移除 start_date 和 end_date，因为我们会为每个范围单独设置
                    fetch_kwargs = {k: v for k, v in kwargs.items()
                                   if k not in ['start_date', 'end_date']}

                    new_dfs = self._fetch_missing_ranges(func, missing_ranges, **fetch_kwargs)

                    # 需求 3.4, 3.5: 合并缓存数据和新数据
                    merged_df = self._merge_dataframes(cached_df, new_dfs, missing_ranges)

                    # 提取合并后数据的日期范围
                    from ..cache.sqlite_backend import SQLiteCacheBackend
                    from ..cache.serialization import serialize_dataframe

                    start_date, end_date = SQLiteCacheBackend.extract_date_range(merged_df)

                    # 使用 set_with_range() 更新缓存
                    backend = CacheKey.get_sqlite_backend()
                    if backend is not None and start_date is not None and end_date is not None:
                        try:
                            serialized = serialize_dataframe(merged_df)
                            backend.set_with_range(
                                cache_key,
                                serialized,
                                start_date=start_date,
                                end_date=end_date,
                                data_type='dataframe',
                                symbol=kwargs.get('symbol')
                            )
                            # 需求 6.1: 记录缓存更新日志
                            logger.info(
                                "缓存已更新（带范围）: key=%s, 范围=[%s, %s], 行数=%d",
                                cache_key, start_date, end_date, len(merged_df)
                            )
                        except Exception as cache_error:
                            # 需求 5.3, 6.4: 缓存更新失败不影响数据返回
                            logger.warning(
                                "更新缓存失败: %s, 继续返回合并后的数据",
                                cache_error, exc_info=True
                            )

                    # 同时更新内存缓存（SQLite 已经通过 set_with_range 设置）
                    try:
                        cache = CacheKey.init(
                            cache_key, ttl=ttl, maxsize=self.settings.cache_max_size
                        )
                        # 只设置内存缓存，不重复写入 SQLite
                        cache.cache1[cache_key] = merged_df
                        logger.debug("缓存已设置（仅内存）: %s (ttl=%ds)", cache_key, ttl)
                    except Exception as mem_cache_error:
                        # 需求 5.3, 6.4: 内存缓存失败不影响数据返回
                        logger.warning(
                            "内存缓存失败: %s, 继续返回数据",
                            mem_cache_error
                        )

                    return merged_df

                except Exception as e:
                    # 需求 5.4: API 失败时的降级处理
                    # 需求 6.4: 记录详细的错误信息和上下文
                    logger.error(
                        "补全数据失败: %s, 错误类型=%s, key=%s",
                        e, type(e).__name__, cache_key,
                        exc_info=True
                    )
                    if cached_df is not None and not cached_df.empty:
                        # 需求 5.4: 降级返回缓存数据
                        logger.warning(
                            "降级返回缓存数据: key=%s, 行数=%d",
                            cache_key, len(cached_df)
                        )
                        return cached_df
                    else:
                        # 如果连缓存数据都没有，重新抛出异常
                        logger.error("无缓存数据可用，抛出异常")
                        raise
        
        # 禁用范围补全或没有日期参数，使用原有的简单缓存逻辑
        logger.debug(f"使用简单缓存逻辑: {cache_key}")
        
        # 初始化缓存
        cache = CacheKey.init(
            cache_key, ttl=ttl, maxsize=self.settings.cache_max_size
        )

        # 尝试从缓存获取
        cached_data = cache.get()
        if cached_data is not None:
            logger.debug(f"缓存命中: {cache_key}")
            return cached_data

        # 缓存未命中，调用 AkShare 函数
        result = self._fetch_data_with_retry(func, args, kwargs)
        
        if result is not None:
            # 存入缓存
            cache.set(result)
            logger.debug(f"数据已缓存: {cache_key}")
        
        return result

    def _fetch_data_with_retry(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict
    ) -> Optional[pd.DataFrame]:
        """使用重试机制调用 AkShare 函数获取数据。
        
        此方法封装了原有的重试逻辑，供 call() 方法内部使用。
        
        参数:
            func: 要调用的 AkShare 函数
            args: 位置参数
            kwargs: 关键字参数
            
        返回:
            pandas DataFrame 或 None（如果调用失败）
            
        抛出:
            APIException: 当 AkShare 函数调用失败时
            DataParsingException: 当返回的数据无法解析时
        """
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
                        f"timeout={kwargs.get('timeout')}, {wait_time:.1f}秒后重试..."
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
        如果检测到新分红则仅使复权价格数据（qfq/hfq）缓存失效，
        保留原始价格数据（adjust=''）的缓存。

        参数:
            symbol: 股票代码（如 '000001'）

        返回:
            如果缓存被使失效返回 True，否则返回 False。

        需求: 4.1, 4.3, 6.5
        """
        dividend_checker = CacheKey.get_dividend_checker()
        if dividend_checker is None:
            return False

        try:
            if dividend_checker.should_invalidate_cache(symbol):
                # 需求 6.5: 记录失效原因
                logger.info(
                    "检测到股票 %s 有新分红事件，正在使复权价格缓存失效 (qfq/hfq)，保留原始价格数据",
                    symbol
                )
                # 仅删除复权数据缓存（qfq/hfq），保留原始价格数据（需求 4.1, 4.3）
                cache_backend = CacheKey.get_sqlite_backend()
                if cache_backend is not None:
                    deleted_count = cache_backend.delete_adjusted_price_cache(symbol)
                    # 需求 6.5: 记录失效日志（失效原因、影响的条目数）
                    logger.info(
                        "缓存失效完成: 股票=%s, 原因=新分红事件, "
                        "失效类型=复权价格(qfq/hfq), 影响条目数=%d",
                        symbol, deleted_count
                    )
                else:
                    # 需求 6.4: 记录错误情况
                    logger.warning(
                        "SQLite 后端未初始化，无法使股票 %s 的缓存失效",
                        symbol
                    )

                # 同时清除此股票代码的内存缓存条目（仅复权数据）
                # 注意：CacheKey.clear_by_symbol 会清除所有该股票的内存缓存
                # 这是可以接受的，因为内存缓存会在下次访问时重新填充
                CacheKey.clear_by_symbol(symbol)
                logger.debug("已清除股票 %s 的内存缓存", symbol)
                return True
        except Exception as e:
            # 需求 6.4: 记录详细的错误信息和上下文
            logger.warning(
                "检查股票 %s 的分红失败: %s, 错误类型=%s",
                symbol, str(e), type(e).__name__,
                exc_info=True
            )

        return False

    def _fetch_missing_ranges(
        self,
        func: Callable,
        missing_ranges: list,
        **kwargs
    ) -> list[pd.DataFrame]:
        """获取缺失的数据段。

        此方法遍历缺失范围列表，为每个缺失范围调用 AkShare 函数。
        如果某个范围的 API 调用失败，会记录错误但不中断处理，
        继续尝试获取其他范围的数据。

        参数:
            func: AkShare 函数
            missing_ranges: 缺失范围列表，格式:
                [{'start': str, 'end': str, 'position': 'before'|'after'|'full'}]
            **kwargs: 传递给函数的其他参数（不包括 start_date 和 end_date）

        返回:
            DataFrame 列表，每个对应一个成功获取的缺失范围

        需求: 3.1, 3.2, 5.4, 6.2, 6.4
        """
        fetched_dfs = []

        # 需求 6.2: 记录补全开始日志
        logger.info(
            "开始数据补全: 函数=%s, 缺失段数=%d",
            func.__name__, len(missing_ranges)
        )

        for idx, range_info in enumerate(missing_ranges, 1):
            start = range_info['start']
            end = range_info['end']
            position = range_info['position']

            # 需求 6.2: 记录补全范围详情
            logger.info(
                "正在获取缺失数据段 [%d/%d]: position=%s, 范围=[%s, %s]",
                idx, len(missing_ranges), position, start, end
            )

            try:
                # 创建新的 kwargs 副本，添加 start_date 和 end_date
                range_kwargs = kwargs.copy()
                range_kwargs['start_date'] = start
                range_kwargs['end_date'] = end

                # 调用 AkShare 函数获取数据
                # 注意：这里直接调用函数，不使用 self.call() 以避免缓存
                # 因为我们稍后会将合并后的完整数据存入缓存
                df = func(**range_kwargs)

                if df is not None and not df.empty:
                    fetched_dfs.append({
                        'data': df,
                        'position': position,
                        'start': start,
                        'end': end
                    })
                    # 需求 6.2: 记录补全成功日志（补全范围、获取行数）
                    logger.info(
                        "成功获取缺失数据段 [%d/%d]: position=%s, 行数=%d, 范围=[%s, %s]",
                        idx, len(missing_ranges), position, len(df), start, end
                    )
                else:
                    # 需求 6.4: 记录空结果警告
                    logger.warning(
                        "缺失数据段返回空结果 [%d/%d]: position=%s, 范围=[%s, %s]",
                        idx, len(missing_ranges), position, start, end
                    )

            except Exception as e:
                # 需求 3.2, 5.4: 处理 API 调用失败的情况（记录错误但不中断）
                # 需求 6.4: 记录详细的错误信息和上下文
                logger.error(
                    "获取缺失数据段失败 [%d/%d]: position=%s, 范围=[%s, %s], "
                    "错误类型=%s, 错误信息=%s",
                    idx, len(missing_ranges), position, start, end,
                    type(e).__name__, str(e),
                    exc_info=True
                )
                # 继续处理下一个范围，不中断整个流程

        # 需求 6.2: 记录补全完成日志
        success_count = len(fetched_dfs)
        total_count = len(missing_ranges)
        total_rows = sum(len(item['data']) for item in fetched_dfs)

        if success_count == total_count:
            logger.info(
                "数据补全完成: 成功获取 %d/%d 个数据段, 总行数=%d",
                success_count, total_count, total_rows
            )
        else:
            # 需求 6.4: 记录部分失败的情况
            logger.warning(
                "数据补全部分成功: 成功获取 %d/%d 个数据段, 失败 %d 个, 总行数=%d",
                success_count, total_count, total_count - success_count, total_rows
            )

        return fetched_dfs

    def _standardize_date_column(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 DataFrame 的日期列名为 '日期'。
        
        参数:
            df: 要标准化的 DataFrame
            
        返回:
            标准化后的 DataFrame（原地修改）
        """
        date_column_names = ['date', '时间', 'datetime', '交易日期', 'trade_date']
        
        for col_name in date_column_names:
            if col_name in df.columns:
                df.rename(columns={col_name: '日期'}, inplace=True)
                logger.debug(f"标准化日期列名: {col_name} -> 日期")
                break
        
        return df

    def _merge_dataframes(
        self,
        cached_df: Optional[pd.DataFrame],
        new_dfs: list,
        missing_ranges: list
    ) -> pd.DataFrame:
        """合并缓存数据和新获取的数据。

        此方法根据缺失范围的位置信息，将新获取的数据与缓存数据合并。
        合并后的数据会按日期排序，并去除重复的日期记录。

        参数:
            cached_df: 缓存的 DataFrame（可能为 None）
            new_dfs: 新获取的 DataFrame 列表，每个元素是包含以下键的字典:
                - 'data': DataFrame
                - 'position': 'before'|'after'|'full'
                - 'start': 起始日期字符串
                - 'end': 结束日期字符串
            missing_ranges: 缺失范围列表（用于确定插入位置）

        返回:
            合并后的完整 DataFrame

        需求: 3.4, 3.5, 5.1, 5.2, 5.3, 5.5
        """
        try:
            # 需求 5.3: 处理空补全结果（返回原缓存数据）
            if not new_dfs:
                logger.debug("补全数据为空，返回原缓存数据")
                return cached_df if cached_df is not None else pd.DataFrame()

            # 需求 6.3: 记录合并前的数据量
            cached_count = len(cached_df) if cached_df is not None and not cached_df.empty else 0
            new_count = sum(len(item['data']) for item in new_dfs)
            logger.info(
                "开始数据合并: 缓存行数=%d, 新数据段数=%d, 新数据总行数=%d",
                cached_count, len(new_dfs), new_count
            )

            # 需求 5.5: 在合并前标准化所有 DataFrame 的列名
            # 这样可以避免合并后出现多个日期列
            for item in new_dfs:
                try:
                    self._standardize_date_column(item['data'])
                except Exception as e:
                    # 需求 5.3, 6.4: 记录列名标准化错误但继续处理
                    logger.warning(
                        "标准化新数据列名失败: %s, position=%s, 继续处理",
                        e, item.get('position', 'unknown')
                    )

            if cached_df is not None and not cached_df.empty:
                # 创建副本以避免修改原始缓存数据
                try:
                    cached_df = cached_df.copy()
                    self._standardize_date_column(cached_df)
                except Exception as e:
                    # 需求 5.3, 6.4: 记录缓存数据处理错误
                    logger.warning("标准化缓存数据列名失败: %s, 继续处理", e)

            # 提取所有新获取的 DataFrame
            new_dataframes = [item['data'] for item in new_dfs]

            # 如果没有缓存数据，直接合并所有新数据
            if cached_df is None or cached_df.empty:
                logger.debug("缓存数据为空，直接合并所有新数据")
                if len(new_dataframes) == 1:
                    merged = new_dataframes[0].copy()
                else:
                    merged = pd.concat(new_dataframes, ignore_index=True)
            else:
                # 需求 3.4: 根据 position 确定插入位置并合并
                dfs_to_merge = []

                # 按位置组织数据：before 数据 + 缓存数据 + after 数据
                before_dfs = [item['data'] for item in new_dfs if item['position'] == 'before']
                after_dfs = [item['data'] for item in new_dfs if item['position'] == 'after']
                full_dfs = [item['data'] for item in new_dfs if item['position'] == 'full']

                # 如果有 full position，说明缓存不存在或完全不匹配，直接使用新数据
                if full_dfs:
                    logger.debug("检测到 full position，使用完整新数据")
                    dfs_to_merge = full_dfs
                else:
                    # 按顺序添加：before -> cached -> after
                    if before_dfs:
                        dfs_to_merge.extend(before_dfs)
                        logger.debug("添加 %d 个 before 数据段", len(before_dfs))

                    dfs_to_merge.append(cached_df)
                    logger.debug("添加缓存数据")

                    if after_dfs:
                        dfs_to_merge.extend(after_dfs)
                        logger.debug("添加 %d 个 after 数据段", len(after_dfs))

                # 使用 pd.concat() 合并所有 DataFrame
                logger.debug("正在合并 %d 个 DataFrame", len(dfs_to_merge))
                try:
                    merged = pd.concat(dfs_to_merge, ignore_index=True)
                except Exception as e:
                    # 需求 5.3: 内存不足或合并失败时的处理
                    logger.error(
                        "数据合并失败: %s, 尝试返回缓存数据作为降级处理",
                        e, exc_info=True
                    )
                    # 需求 5.4: 降级返回缓存数据
                    if cached_df is not None and not cached_df.empty:
                        logger.warning("合并失败，降级返回原缓存数据")
                        return cached_df
                    else:
                        raise

            # 识别日期列
            date_column_names = ['日期', 'date', '时间', 'datetime', '交易日期', 'trade_date']
            date_col = None
            for col_name in date_column_names:
                if col_name in merged.columns:
                    date_col = col_name
                    break

            # 如果找到日期列，进行排序和去重
            if date_col:
                # 确保日期列是 datetime 类型
                if merged[date_col].dtype != 'datetime64[ns]':
                    logger.debug("转换日期列为 datetime 类型")
                    try:
                        merged[date_col] = pd.to_datetime(merged[date_col], errors='coerce')
                    except Exception as e:
                        # 需求 5.3, 6.4: 记录日期转换错误
                        logger.warning("日期列类型转换失败: %s, 继续处理", e)

                # 需求 5.2: 按日期列排序
                logger.debug("按日期排序数据")
                try:
                    merged = merged.sort_values(by=date_col, ascending=True)
                except Exception as e:
                    # 需求 5.3, 6.4: 记录排序错误但继续处理
                    logger.warning("数据排序失败: %s, 继续处理", e)

                # 需求 5.1: 使用 drop_duplicates() 去除重复日期（保留最后出现的）
                before_count = len(merged)
                try:
                    merged = merged.drop_duplicates(subset=[date_col], keep='last')
                    after_count = len(merged)

                    if before_count != after_count:
                        # 需求 5.3, 6.3: 记录数据合并冲突（重复日期）
                        logger.warning(
                            "数据合并冲突: 检测到 %d 条重复日期记录，优先使用新数据 (keep='last')",
                            before_count - after_count
                        )
                        logger.info(
                            "去除重复日期: %d -> %d 行 (删除 %d 条重复记录)",
                            before_count, after_count, before_count - after_count
                        )
                except Exception as e:
                    # 需求 5.3, 6.4: 记录去重错误
                    logger.warning("去除重复日期失败: %s, 继续处理", e)

                # 重置索引
                try:
                    merged = merged.reset_index(drop=True)
                except Exception as e:
                    logger.warning("重置索引失败: %s, 继续处理", e)
            else:
                logger.warning("未找到日期列，无法排序和去重")

            # 需求 3.5, 6.3: 记录合并后的日志
            final_count = len(merged)
            logger.info(
                "数据合并完成: 缓存行数=%d, 新数据行数=%d, 合并后行数=%d",
                cached_count, new_count, final_count
            )

            return merged

        except Exception as e:
            # 需求 5.3, 5.4, 6.4: 最外层错误处理 - 记录详细错误并尝试降级
            logger.error(
                "数据合并过程发生严重错误: %s, 缓存数据可用: %s",
                e, cached_df is not None and not cached_df.empty,
                exc_info=True
            )
            # 需求 5.4: API 失败时的降级处理 - 返回缓存数据
            if cached_df is not None and not cached_df.empty:
                logger.warning("合并失败，降级返回原缓存数据")
                return cached_df
            else:
                # 如果连缓存数据都没有，返回空 DataFrame
                logger.error("合并失败且无缓存数据可用，返回空 DataFrame")
                return pd.DataFrame()

    def _check_cache_range(
        self,
        cache_key: str,
        requested_start: str,
        requested_end: str
    ) -> dict:
        """检查缓存范围并确定需要补全的数据段。

        此方法实现智能缓存范围检查逻辑，比较请求的时间范围与缓存的时间范围，
        识别需要从 API 获取的缺失数据段。

        参数:
            cache_key: 缓存键
            requested_start: 请求的起始日期 (YYYYMMDD 格式)
            requested_end: 请求的结束日期 (YYYYMMDD 格式)

        返回:
            包含以下键的字典:
            - 'status': 'full_hit' | 'partial_hit' | 'miss'
            - 'cached_data': 缓存的 DataFrame（如果存在）
            - 'cached_start': 缓存的起始日期 (datetime)
            - 'cached_end': 缓存的结束日期 (datetime)
            - 'missing_ranges': 需要获取的时间范围列表
                格式: [{'start': str, 'end': str, 'position': 'before'|'after'}]

        需求: 2.1, 2.2, 2.3, 2.4, 2.5
        """
        from datetime import datetime, timedelta
        from ..cache.serialization import deserialize_dataframe

        # 解析请求的日期范围 (YYYYMMDD -> datetime)
        try:
            req_start = datetime.strptime(requested_start, '%Y%m%d')
            req_end = datetime.strptime(requested_end, '%Y%m%d')
        except ValueError as e:
            logger.error(f"解析请求日期失败: start={requested_start}, end={requested_end}, 错误: {e}")
            # 返回 miss 状态，让调用者获取完整范围
            return {
                'status': 'miss',
                'cached_data': None,
                'cached_start': None,
                'cached_end': None,
                'missing_ranges': [{
                    'start': requested_start,
                    'end': requested_end,
                    'position': 'full'
                }]
            }

        # 从缓存后端获取缓存数据和日期范围
        backend = CacheKey.get_sqlite_backend()
        if backend is None:
            logger.debug("SQLite 后端未初始化，返回 miss 状态")
            return {
                'status': 'miss',
                'cached_data': None,
                'cached_start': None,
                'cached_end': None,
                'missing_ranges': [{
                    'start': requested_start,
                    'end': requested_end,
                    'position': 'full'
                }]
            }

        # 获取缓存数据及其时间范围
        cache_result = backend.get_with_range(cache_key)

        # 需求 2.5: 缓存不存在时的完整获取
        if cache_result is None:
            logger.debug(f"缓存不存在: {cache_key}，需要获取完整范围")
            return {
                'status': 'miss',
                'cached_data': None,
                'cached_start': None,
                'cached_end': None,
                'missing_ranges': [{
                    'start': requested_start,
                    'end': requested_end,
                    'position': 'full'
                }]
            }

        # 解包缓存结果
        cached_bytes, cached_start, cached_end = cache_result

        # 如果缓存数据存在但没有日期范围元数据，返回 miss
        if cached_start is None or cached_end is None:
            logger.debug(f"缓存存在但无日期范围元数据: {cache_key}，返回 miss")
            return {
                'status': 'miss',
                'cached_data': None,
                'cached_start': None,
                'cached_end': None,
                'missing_ranges': [{
                    'start': requested_start,
                    'end': requested_end,
                    'position': 'full'
                }]
            }

        # 反序列化缓存数据
        try:
            cached_df = deserialize_dataframe(cached_bytes)
        except Exception as e:
            logger.error(f"反序列化缓存数据失败: {e}")
            return {
                'status': 'miss',
                'cached_data': None,
                'cached_start': None,
                'cached_end': None,
                'missing_ranges': [{
                    'start': requested_start,
                    'end': requested_end,
                    'position': 'full'
                }]
            }

        # 比较请求范围与缓存范围
        missing_ranges = []

        # 需求 2.2: 请求的起始日期早于缓存起始日期
        if req_start < cached_start:
            # 需要补全早期数据
            # 结束日期是缓存起始日期的前一天
            before_end = (cached_start - timedelta(days=1)).strftime('%Y%m%d')
            missing_ranges.append({
                'start': requested_start,
                'end': before_end,
                'position': 'before'
            })
            # 需求 6.1: 记录缺失范围详情
            logger.debug(
                "需要补全早期数据: %s 到 %s (缺失 %d 天)",
                requested_start, before_end,
                (cached_start - req_start).days
            )

        # 需求 2.3: 请求的结束日期晚于缓存结束日期
        if req_end > cached_end:
            # 需要补全最新数据
            # 起始日期是缓存结束日期的后一天
            after_start = (cached_end + timedelta(days=1)).strftime('%Y%m%d')
            missing_ranges.append({
                'start': after_start,
                'end': requested_end,
                'position': 'after'
            })
            # 需求 6.1: 记录缺失范围详情
            logger.debug(
                "需要补全最新数据: %s 到 %s (缺失 %d 天)",
                after_start, requested_end,
                (req_end - cached_end).days
            )

        # 确定状态
        if not missing_ranges:
            # 需求 2.4: 请求范围完全包含在缓存范围内
            status = 'full_hit'
            # 需求 6.1: 记录范围检查日志（命中状态、请求范围、缓存范围）
            logger.info(
                "缓存范围检查 - 完全命中: key=%s, 请求范围=[%s, %s], 缓存范围=[%s, %s]",
                cache_key, requested_start, requested_end,
                cached_start.strftime('%Y%m%d'), cached_end.strftime('%Y%m%d')
            )
        else:
            # 需求 2.1, 2.2, 2.3: 部分命中，需要补全
            status = 'partial_hit'
            # 需求 6.1: 记录范围检查日志（命中状态、请求范围、缓存范围）
            logger.info(
                "缓存范围检查 - 部分命中: key=%s, 请求范围=[%s, %s], "
                "缓存范围=[%s, %s], 缺失段数=%d",
                cache_key, requested_start, requested_end,
                cached_start.strftime('%Y%m%d'), cached_end.strftime('%Y%m%d'),
                len(missing_ranges)
            )

        return {
            'status': status,
            'cached_data': cached_df,
            'cached_start': cached_start,
            'cached_end': cached_end,
            'missing_ranges': missing_ranges
        }

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
