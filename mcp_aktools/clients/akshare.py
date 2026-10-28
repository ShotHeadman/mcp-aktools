"""AkShare 客户端封装。

此模块封装 AkShare 库的函数调用，提供：
- 统一的缓存管理
- 错误处理和转换
- 数据解析异常处理
- 日志记录
- 重试机制
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from http.client import RemoteDisconnected
from urllib.error import URLError
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
import requests

from ..cache import CacheKey
from ..config import Settings, get_settings
from ..exceptions import APIException, DataParsingException, ValidationException
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class AkShareClient:
    """AkShare 客户端封装类。

    此类封装 AkShare 库的函数调用，提供统一的缓存管理、
    数据源熔断降级、速率限制、错误处理和日志记录功能。

    Attributes:
        settings: 配置实例
    """

    # 数据源级熔断器状态: {source_name: {"failures": int, "cooldown_until": float}}
    _source_circuit: Dict[str, Dict[str, Any]] = {}
    _http_session: Optional[requests.Session] = None

    def __init__(self, settings: Optional[Settings] = None):
        """初始化 AkShare 客户端。

        Args:
            settings: 可选的配置实例（如果为 None，将使用全局配置）
        """
        self.settings = settings or get_settings()
        self.max_retries = self.settings.retry_max_attempts
        self.retry_backoff_factor = self.settings.retry_backoff_factor
        self.retry_max_delay = self.settings.retry_max_delay
        self.rate_limiter = RateLimiter(
            calls=max(1, self.settings.rate_limit_calls),
            period=max(1, self.settings.rate_limit_period),
        )
        
        # Initialize SQLite cache backend (Requirements 1.1, 1.2)
        self._init_sqlite_backend()
        
        logger.info("AkShare 客户端已初始化")

    @classmethod
    def get_http_session(cls) -> requests.Session:
        """获取共享的带连接池 HTTP Session（忽略系统代理以保证稳定性）。"""
        if cls._http_session is None:
            session = requests.Session()
            session.trust_env = False
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                )
            })
            cls._http_session = session
        return cls._http_session

    @classmethod
    def reset_circuit_breaker(cls, source: Optional[str] = None) -> None:
        """重置数据源熔断器状态。"""
        if source:
            cls._source_circuit.pop(source, None)
        else:
            cls._source_circuit.clear()

    @staticmethod
    def _infer_data_source(func_or_name: Any) -> str:
        """根据函数或函数名推断上游数据源。"""
        if callable(func_or_name):
            name = getattr(func_or_name, "__name__", str(func_or_name)).lower()
        else:
            name = str(func_or_name).lower()
        if (
            name.endswith("_em")
            or name in {
                "stock_zh_a_hist",
                "stock_hk_hist",
                "stock_individual_fund_flow",
                "macro_china_market_margin_sh",
                "macro_china_market_margin_sz",
            }
        ):
            return "eastmoney"
        if "_tx" in name:
            return "tencent"
        if "_sina" in name or name in {
            "stock_zh_a_daily",
            "stock_zh_a_minute",
            "stock_hk_daily",
            "stock_us_daily",
            "stock_zh_a_spot",
            "stock_hk_spot",
            "stock_zh_index_daily",
        }:
            return "sina"
        if "_ths" in name or name.startswith("stock_fund_flow_"):
            return "ths"
        if "_xq" in name:
            return "xueqiu"
        if "_cninfo" in name:
            return "cninfo"
        return "other"

    def _is_source_open(self, source: str) -> bool:
        """检查数据源是否处于熔断开启（冷却跳过）状态。"""
        state = self._source_circuit.get(source)
        if not state:
            return False
        cooldown_until = state.get("cooldown_until", 0.0)
        if time.time() < cooldown_until:
            return True
        return False

    def _record_source_failure(self, source: str, error: Exception) -> None:
        """记录数据源失败并在达到阈值时触发熔断冷却。"""
        if source != "eastmoney":
            return
        state = self._source_circuit.setdefault(
            source, {"failures": 0, "cooldown_until": 0.0}
        )
        state["failures"] = state.get("failures", 0) + 1
        cooldown = getattr(self.settings, "eastmoney_cooldown_seconds", 300)
        # 东方财富一旦出现连接断开/重置或连续2次失败，立即进入冷却期以加速备用源切换
        is_conn_drop = isinstance(
            error,
            (
                RemoteDisconnected,
                ConnectionError,
                URLError,
                TimeoutError,
                requests.exceptions.RequestException,
            ),
        )
        if is_conn_drop or state["failures"] >= 2:
            state["cooldown_until"] = time.time() + cooldown
            logger.warning(
                "数据源 %s 触发限流/连接异常 (%s)，进入 %ds 熔断冷却期",
                source,
                type(error).__name__,
                cooldown,
            )

    def _record_source_success(self, source: str) -> None:
        """重置数据源失败计数。"""
        if source in self._source_circuit:
            self._source_circuit[source] = {"failures": 0, "cooldown_until": 0.0}

    @staticmethod
    def _extract_symbol_from_kwargs(kwargs: dict) -> Optional[str]:
        """从调用参数中提取标准化股票代码（去除 sh/sz/bj 前缀）。"""
        raw = kwargs.get("symbol") or kwargs.get("stock")
        if not raw or not isinstance(raw, str):
            return None
        s = raw.strip()
        if len(s) == 8 and s[:2].lower() in ("sh", "sz", "bj") and s[2:].isdigit():
            return s[2:]
        return s

    @staticmethod
    def _slice_dataframe_by_date(
        df: pd.DataFrame, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """按请求的 [start_date, end_date] 裁剪 DataFrame。"""
        if df is None or df.empty:
            return df
        date_col = next(
            (
                c
                for c in ("日期", "date", "时间", "datetime", "交易日期", "trade_date")
                if c in df.columns
            ),
            None,
        )
        if date_col is None:
            return df
        try:
            start_ts = pd.Timestamp(start_date)
            end_ts = pd.Timestamp(end_date)
            dates = pd.to_datetime(df[date_col], errors="coerce")
            mask = dates.between(start_ts, end_ts)
            return df.loc[mask].reset_index(drop=True)
        except Exception:
            return df

    def _init_sqlite_backend(self) -> None:
        """Initialize SQLite cache backend with database path from settings.
        
        This method initializes the SQLite cache backend singleton using the
        database path configured in settings. If DATABASE_URL is not set,
        a default path in the user's cache directory will be used.
        """
        try:
            db_path = self.settings.get_database_path()
            CacheKey.init_sqlite_backend(db_path, disk_ttl=self.settings.cache_disk_ttl)
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
        use_disk_cache: bool = True,
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
            use_disk_cache: 是否读写磁盘缓存；分钟行情快照仅使用内存 TTL
            **kwargs: 传递给函数的关键字参数

        Returns:
            pandas DataFrame 或 None（如果调用失败）

        Raises:
            APIException: 当 AkShare 函数调用失败时
            DataParsingException: 当返回的数据无法解析时
            
        需求: 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 3.5
        """
        if "use_range_completion" in kwargs:
            enable_range_completion = bool(kwargs.pop("use_range_completion"))

        # 范围补全以日为单位；分钟行情不能按天补全或永久缓存。
        date_params = (kwargs.get('start_date'), kwargs.get('end_date'))
        has_date_params = all(
            isinstance(value, str) and len(value) == 8 and value.isdigit()
            for value in date_params
        )
        use_range_completion = enable_range_completion and use_disk_cache and has_date_params
        if use_range_completion:
            today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
            kwargs['end_date'] = min(kwargs['end_date'], today)
            if kwargs['start_date'] > kwargs['end_date']:
                return pd.DataFrame()

        # 自动键包含请求区间，内存命中可直接复用；自定义键仍需检查覆盖范围。
        automatic_cache_key = cache_key is None
        mem_cache_key = cache_key or self._generate_cache_key(
            func, args, kwargs, exclude_keys={"timeout"}
        )
        # 范围补全在 SQLite 中使用不含 start_date/end_date 的规范化键名，以真正复用并增量补全同一标的缓存
        range_cache_key = cache_key or self._generate_cache_key(
            func, args, kwargs, exclude_keys={"start_date", "end_date", "timeout"}
        )

        symbol = self._extract_symbol_from_kwargs(kwargs)
        data_type = self._infer_data_type(func.__name__)

        # 设置 TTL 值
        if ttl is None:
            ttl = self.settings.cache_memory_ttl

        # 传递 timeout 参数到 kwargs
        if timeout is not None:
            kwargs['timeout'] = timeout

        if use_range_completion:
            cache = CacheKey.init(
                range_cache_key,
                ttl=ttl,
                maxsize=self.settings.cache_max_size,
                symbol=symbol,
                data_type=data_type,
            )
            if automatic_cache_key:
                cached_data = cache.cache1.get(mem_cache_key)
                if cached_data is not None:
                    return cached_data.copy()

            # 使用智能范围补全逻辑
            logger.debug(
                "启用范围补全: key=%s, 函数=%s, 范围=[%s, %s]",
                range_cache_key, func.__name__, kwargs['start_date'], kwargs['end_date']
            )

            # 检查缓存范围
            range_check = self._check_cache_range(
                range_cache_key,
                kwargs['start_date'],
                kwargs['end_date'],
                refresh_last_bar=data_type == 'price',
            )

            status = range_check['status']

            # 需求 2.4: 完全命中时按请求区间切片并返回
            if status == 'full_hit':
                sliced_df = self._slice_dataframe_by_date(
                    range_check['cached_data'], kwargs['start_date'], kwargs['end_date']
                )
                logger.info(
                    "范围完全命中，直接返回缓存数据: key=%s, 切片行数=%d",
                    range_cache_key, len(sliced_df)
                )
                cache.cache1[mem_cache_key] = sliced_df.copy()
                return sliced_df
            
            # 需求 2.5: 缓存不存在，获取完整数据
            if status == 'miss':
                logger.info(
                    "缓存未命中，获取完整范围数据: key=%s, 范围=[%s, %s]",
                    range_cache_key, kwargs['start_date'], kwargs['end_date']
                )
                try:
                    result = self._fetch_data_with_retry(func, args, kwargs)

                    if result is not None and not result.empty:
                        from ..cache.sqlite_backend import SQLiteCacheBackend
                        from ..cache.serialization import serialize_dataframe

                        start_date, end_date = SQLiteCacheBackend.extract_date_range(result)

                        backend = CacheKey.get_sqlite_backend()
                        if backend is not None and start_date is not None and end_date is not None:
                            try:
                                serialized = serialize_dataframe(result)
                                backend.set_with_range(
                                    range_cache_key,
                                    serialized,
                                    start_date=start_date,
                                    end_date=end_date,
                                    data_type=data_type,
                                    symbol=symbol,
                                )
                                logger.debug(
                                    "数据已缓存（带范围）: key=%s, 范围=[%s, %s], 行数=%d",
                                    range_cache_key, start_date, end_date, len(result)
                                )
                            except Exception as cache_error:
                                logger.warning(
                                    "缓存数据失败: %s, 继续返回数据",
                                    cache_error, exc_info=True
                                )

                        try:
                            sliced_result = self._slice_dataframe_by_date(
                                result, kwargs['start_date'], kwargs['end_date']
                            )
                            cache.cache1[mem_cache_key] = sliced_result.copy()
                            return sliced_result
                        except Exception as mem_cache_error:
                            logger.warning(
                                "内存缓存失败: %s, 继续返回数据",
                                mem_cache_error
                            )

                    return result

                except Exception as e:
                    logger.error(
                        "获取完整范围数据失败: key=%s, 错误=%s",
                        range_cache_key, e, exc_info=True
                    )
                    raise
            
            elif status == 'partial_hit':
                logger.info(
                    "缓存部分命中，补全缺失数据段: key=%s, 缺失段数=%d",
                    range_cache_key, len(range_check['missing_ranges'])
                )

                cached_df = range_check['cached_data']
                missing_ranges = range_check['missing_ranges']

                try:
                    fetch_kwargs = {k: v for k, v in kwargs.items()
                                   if k not in ['start_date', 'end_date']}

                    new_dfs = self._fetch_missing_ranges(func, missing_ranges, **fetch_kwargs)

                    if not new_dfs:
                        return self._slice_dataframe_by_date(
                            cached_df.copy(), kwargs['start_date'], kwargs['end_date']
                        )

                    merged_df = self._merge_dataframes(cached_df, new_dfs, missing_ranges)

                    from ..cache.sqlite_backend import SQLiteCacheBackend
                    from ..cache.serialization import serialize_dataframe

                    start_date, end_date = SQLiteCacheBackend.extract_date_range(merged_df)

                    backend = CacheKey.get_sqlite_backend()
                    if backend is not None and start_date is not None and end_date is not None:
                        try:
                            serialized = serialize_dataframe(merged_df)
                            backend.set_with_range(
                                range_cache_key,
                                serialized,
                                start_date=start_date,
                                end_date=end_date,
                                data_type=data_type,
                                symbol=symbol,
                            )
                            logger.info(
                                "缓存已更新（带范围）: key=%s, 范围=[%s, %s], 行数=%d",
                                range_cache_key, start_date, end_date, len(merged_df)
                            )
                        except Exception as cache_error:
                            logger.warning(
                                "更新缓存失败: %s, 继续返回合并后的数据",
                                cache_error, exc_info=True
                            )

                    sliced_merged = self._slice_dataframe_by_date(
                        merged_df, kwargs['start_date'], kwargs['end_date']
                    )
                    try:
                        cache.cache1[mem_cache_key] = sliced_merged.copy()
                    except Exception as mem_cache_error:
                        logger.warning(
                            "内存缓存失败: %s, 继续返回数据",
                            mem_cache_error
                        )

                    return sliced_merged

                except Exception as e:
                    logger.error(
                        "补全数据失败: %s, 错误类型=%s, key=%s",
                        e, type(e).__name__, range_cache_key,
                        exc_info=True
                    )
                    if cached_df is not None and not cached_df.empty:
                        logger.warning(
                            "降级返回缓存数据: key=%s, 行数=%d",
                            range_cache_key, len(cached_df)
                        )
                        return self._slice_dataframe_by_date(
                            cached_df, kwargs['start_date'], kwargs['end_date']
                        )
                    else:
                        logger.error("无缓存数据可用，抛出异常")
                        raise
        
        # 禁用范围补全或没有日期参数，使用简单缓存逻辑
        cache_key = mem_cache_key
        logger.debug(f"使用简单缓存逻辑: {cache_key}")
        
        cache = CacheKey.init(
            cache_key,
            ttl=ttl,
            maxsize=self.settings.cache_max_size,
            symbol=symbol,
            data_type=data_type,
        )

        cached_data = cache.get() if use_disk_cache else cache.cache1.get(cache_key)
        if cached_data is not None:
            logger.debug(f"缓存命中: {cache_key}")
            return cached_data.copy()

        result = self._fetch_data_with_retry(func, args, kwargs)
        
        if result is not None:
            if use_disk_cache:
                cache.set(result.copy())
            else:
                cache.cache1[cache_key] = result.copy()
            logger.debug(f"数据已缓存: {cache_key}")
        
        return result

    def _fetch_data_with_retry(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict
    ) -> Optional[pd.DataFrame]:
        """使用重试、速率限制和数据源熔断机制调用 AkShare 函数。"""
        source = self._infer_data_source(func.__name__)
        if self._is_source_open(source):
            msg = f"数据源 {source} ({func.__name__}) 处于限流熔断冷却期，直接跳过"
            logger.warning(msg)
            raise APIException(api_name=f"AkShare.{func.__name__}", message=msg)

        logger.info(f"调用 AkShare 函数: {func.__name__}, args={args}, kwargs={kwargs}")

        max_attempts = (
            min(self.max_retries, max(1, getattr(self.settings, "eastmoney_max_retries", 1)))
            if source == "eastmoney"
            else self.max_retries
        )

        last_exception = None
        for attempt in range(max_attempts):
            try:
                if self.rate_limiter:
                    self.rate_limiter.acquire(blocking=True, timeout=10)

                result = func(*args, **kwargs)
                self._record_source_success(source)

                if result is None:
                    logger.warning(f"AkShare 函数 {func.__name__} 返回 None")
                    return None

                if not isinstance(result, pd.DataFrame):
                    error_msg = f"期望返回 pandas.DataFrame，但得到 {type(result).__name__}"
                    logger.error(f"数据解析错误: {error_msg}")
                    raise DataParsingException(
                        source=f"AkShare.{func.__name__}", message=error_msg
                    )

                if result.empty:
                    logger.warning(f"AkShare 函数 {func.__name__} 返回空 DataFrame")
                    return result

                return result

            except DataParsingException:
                raise

            except TimeoutError as e:
                last_exception = e
                self._record_source_failure(source, e)
                if attempt < max_attempts - 1 and not self._is_source_open(source):
                    wait_time = min(
                        self.retry_backoff_factor ** attempt,
                        self.retry_max_delay
                    )
                    logger.warning(
                        f"请求超时 (尝试 {attempt + 1}/{max_attempts}): {func.__name__}, "
                        f"timeout={kwargs.get('timeout')}, {wait_time:.1f}秒后重试..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"请求超时，停止重试 ({attempt + 1}/{max_attempts}): {func.__name__}"
                    )
                    break

            except (RemoteDisconnected, URLError, ConnectionError, requests.exceptions.RequestException) as e:
                last_exception = e
                self._record_source_failure(source, e)
                if attempt < max_attempts - 1 and not self._is_source_open(source):
                    wait_time = min(
                        self.retry_backoff_factor ** attempt,
                        self.retry_max_delay
                    )
                    logger.warning(
                        f"网络错误 (尝试 {attempt + 1}/{max_attempts}): {str(e)}, "
                        f"{wait_time:.1f}秒后重试..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        f"停止重试 ({attempt + 1}/{max_attempts}), 最后错误: {str(e)}"
                    )
                    break

            except KeyError as e:
                self._record_source_failure(source, e)
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
                self._record_source_failure(source, e)
                error_msg = str(e)
                logger.error(
                    f"AkShare 函数调用失败: {func.__name__}, "
                    f"错误类型: {type(e).__name__}, 错误: {error_msg}",
                    exc_info=True,
                )
                raise APIException(api_name=f"AkShare.{func.__name__}", message=error_msg)

        error_msg = f"网络连接失败，已尝试 {max_attempts} 次: {str(last_exception)}"
        logger.error(f"AkShare 函数调用失败: {func.__name__}, {error_msg}")
        raise APIException(api_name=f"AkShare.{func.__name__}", message=error_msg)

    def _generate_cache_key(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict,
        exclude_keys: Optional[set] = None,
    ) -> str:
        """生成缓存键。

        Args:
            func: AkShare 函数
            args: 位置参数
            kwargs: 关键字参数
            exclude_keys: 需要从缓存键中排除的参数名集合（如 start_date, end_date, timeout）

        Returns:
            生成的缓存键字符串
        """
        key_parts = [func.__name__]

        if args:
            key_parts.append(str(args))

        if kwargs:
            filtered = (
                {k: v for k, v in kwargs.items() if k not in exclude_keys}
                if exclude_keys
                else kwargs
            )
            if filtered:
                sorted_kwargs = sorted(filtered.items())
                key_parts.append(str(sorted_kwargs))

        cache_key = "-".join(key_parts)
        return cache_key

    def _infer_data_type(self, func_name: str) -> str:
        """根据 AkShare 函数名推断缓存数据类型。"""
        normalized = func_name.lower()
        if any(token in normalized for token in ("hist", "daily", "price", "candle")):
            return "price"
        if "dividend" in normalized:
            return "dividend"
        if "info" in normalized or "profile" in normalized:
            return "info"
        return "dataframe"

    # 便捷方法：常用的 AkShare 函数封装

    def stock_individual_info_em(
        self, symbol: str, market: str = "sz", ttl: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """获取 A 股个股信息（优先雪球接口，再尝试巨潮）。

        Args:
            symbol: 股票代码
            market: 市场标识 (sh/sz)
            ttl: 缓存 TTL（秒）

        Returns:
            股票信息 DataFrame
        """
        try:
            xq_symbol = f"{market.upper()}{symbol}"
            logger.info(f"使用雪球接口获取股票信息: {xq_symbol}")
            result = self.call(
                ak.stock_individual_basic_info_xq, 
                symbol=xq_symbol, 
                ttl=ttl or 43200
            )
            if result is not None and not result.empty:
                return result
        except Exception as e:
            logger.warning(f"雪球接口失败，尝试巨潮接口: {e}")

        try:
            return self.call(ak.stock_profile_cninfo, symbol=symbol, ttl=ttl or 43200)
        except Exception as fallback_error:
            logger.error(f"巨潮接口也失败: {fallback_error}")

        try:
            return self.call(ak.stock_individual_info_em, symbol=symbol, ttl=ttl or 43200)
        except Exception as e:
            raise e

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

                # 调用 AkShare 函数获取数据（复用带重试与熔断机制的内部方法）
                df = self._fetch_data_with_retry(func, (), range_kwargs)

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
                    merged = merged.sort_values(by=date_col, ascending=True, kind="stable")
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
        requested_end: str,
        refresh_last_bar: bool = False,
    ) -> dict:
        """检查缓存范围并确定需要补全的数据段。

        此方法实现智能缓存范围检查逻辑，比较请求的时间范围与缓存的时间范围，
        识别需要从 API 获取的缺失数据段。

        参数:
            cache_key: 缓存键
            requested_start: 请求的起始日期 (YYYYMMDD 格式)
            requested_end: 请求的结束日期 (YYYYMMDD 格式)
            refresh_last_bar: 内存 TTL 到期后重取尾部 K 线，覆盖盘中值和最终收盘值

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
        if req_end > cached_end or (refresh_last_bar and req_end == cached_end):
            # 价格数据重叠获取最后一根 K 线，合并时保留新值。
            # 只从次日补全会让当天首次缓存的盘中价格永久保留下来。
            after_date = cached_end if refresh_last_bar else cached_end + timedelta(days=1)
            after_start = max(req_start, after_date).strftime('%Y%m%d')
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
        """获取 A 股日、周、月行情；各数据源统一获取日线后聚合。

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
        if period not in {"daily", "weekly", "monthly"}:
            raise ValidationException("period", "仅支持 daily、weekly、monthly；分钟行情请使用分钟接口")

        # 从周期起点加载，避免第一根周/月 K 线缺少开盘价或成交量。
        fetch_start = pd.Timestamp(start_date)
        if period != "daily":
            frequency = "W-SUN" if period == "weekly" else "M"
            fetch_start = fetch_start.to_period(frequency).start_time

        if adjust in ("qfq", "hfq"):
            self._check_dividend_invalidation(symbol)

        full_symbol = f"{market.lower()}{symbol}"
        is_etf = (
            (market.lower() == "sh" and symbol.startswith(("51", "56", "58")))
            or (market.lower() == "sz" and symbol.startswith("159"))
        )
        if is_etf:
            sources = (
                (ak.stock_zh_a_hist_tx, {"symbol": full_symbol}),
                (ak.fund_etf_hist_sina, {"symbol": full_symbol}),
                (ak.fund_etf_hist_em, {"symbol": symbol, "period": "daily"}),
            )
        else:
            sources = (
                (ak.stock_zh_a_hist_tx, {"symbol": full_symbol}),
                (ak.stock_zh_a_daily, {"symbol": full_symbol}),
                (ak.stock_zh_a_hist, {"symbol": symbol, "period": "daily"}),
            )
        last_error = None
        for func, source_kwargs in sources:
            try:
                if func is ak.fund_etf_hist_sina:
                    result = self.call(
                        func,
                        **source_kwargs,
                        ttl=ttl if ttl is not None else 3600,
                        enable_range_completion=False,
                    )
                else:
                    result = self.call(
                        func,
                        **source_kwargs,
                        start_date=fetch_start.strftime("%Y%m%d"),
                        end_date=end_date,
                        adjust=adjust,
                        ttl=ttl if ttl is not None else 3600,
                    )
                if result is not None and not result.empty:
                    result = self._resample_daily_bars(result, period, start_date, end_date)
                    if not result.empty:
                        return result
            except Exception as error:
                last_error = error
                logger.warning("%s 历史行情获取失败: %s", func.__name__, error)

        if last_error is not None:
            raise APIException("AkShare.stock_zh_a_hist", str(last_error)) from last_error
        return None

    @staticmethod
    def _resample_daily_bars(
        df: pd.DataFrame,
        period: str,
        start_date: str = "19900101",
        end_date: str = "22220101",
    ) -> pd.DataFrame:
        """按实际交易日期聚合 OHLCV，日期使用该周期最后一个已有交易日。"""
        date_col = next((col for col in ("date", "日期") if col in df.columns), None)
        if date_col is None:
            raise DataParsingException("AkShare.history", "行情缺少日期列")

        result = df.copy()
        result[date_col] = pd.to_datetime(result[date_col], errors="coerce")
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        frequency = "W-SUN" if period == "weekly" else "M"
        fetch_start = start if period == "daily" else start.to_period(frequency).start_time
        result = result[result[date_col].between(fetch_start, end)]
        result = result.drop_duplicates(date_col, keep="last").sort_values(date_col)

        if period != "daily" and not result.empty:
            aggregations: dict[str, Any] = {date_col: "last"}
            for aliases, operation in (
                (("open", "开盘"), "first"),
                (("close", "收盘"), "last"),
                (("high", "最高"), "max"),
                (("low", "最低"), "min"),
            ):
                column = next((col for col in aliases if col in result.columns), None)
                if column is None:
                    raise DataParsingException("AkShare.history", f"行情缺少 {aliases[0]} 列")
                result[column] = pd.to_numeric(result[column], errors="coerce")
                aggregations[column] = operation
            for column in ("volume", "成交量", "amount", "成交额"):
                if column in result.columns:
                    result[column] = pd.to_numeric(result[column], errors="coerce")
                    aggregations[column] = lambda values: values.sum(min_count=1)

            result = result.groupby(result[date_col].dt.to_period(frequency)).agg(aggregations)

        return result[result[date_col].between(start, end)].reset_index(drop=True)

    def stock_zh_a_hist_min(
        self,
        symbol: str,
        market: str = "sz",
        period: str = "5",
        start_date: str = "20200101",
        end_date: str = "22220101",
        adjust: str = "",
        ttl: int = 300,
    ) -> Optional[pd.DataFrame]:
        """获取指定区间的分钟行情，优先新浪、备用东财，不回退到日线。"""
        if period not in {"1", "5", "15", "30", "60"}:
            raise ValidationException("period", "分钟周期仅支持 1、5、15、30、60")
        if market not in {"sh", "sz"}:
            raise ValidationException("market", "分钟行情仅支持 sh、sz")
        if adjust not in {"", "qfq", "hfq"} or (period == "1" and adjust):
            raise ValidationException("adjust", "仅支持不复权、qfq、hfq；1 分钟行情仅支持不复权")

        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        if len(end_date) in (8, 10):
            end += pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
        now = pd.Timestamp(datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None))
        end = min(end, now)
        if start > end:
            return pd.DataFrame()

        sources = (
            (ak.stock_zh_a_minute, {"symbol": f"{market.lower()}{symbol}"}),
            (ak.stock_zh_a_hist_min_em, {
                "symbol": symbol,
                "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            }),
        )
        last_error = None
        for func, source_kwargs in sources:
            try:
                result = self.call(
                    func, **source_kwargs, period=period, adjust=adjust, ttl=ttl,
                    enable_range_completion=False, use_disk_cache=False,
                )
                if result is None or result.empty:
                    continue
                result = result.rename(columns={
                    "day": "date", "时间": "date", "日期": "date",
                    "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
                    "成交量": "volume", "成交额": "amount",
                }).copy()
                result["date"] = pd.to_datetime(result["date"], errors="coerce")
                for column in ("open", "close", "high", "low", "volume", "amount"):
                    if column in result.columns:
                        result[column] = pd.to_numeric(result[column], errors="coerce")
                result = result[result["date"].between(start, end)]
                if not result.empty:
                    return result.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
            except Exception as error:
                last_error = error
                logger.warning("%s 分钟行情获取失败: %s", func.__name__, error)

        if last_error is not None:
            raise APIException("AkShare.stock_zh_a_hist_min", str(last_error)) from last_error
        return None

    def stock_hk_hist(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "20200101",
        end_date: str = "22220101",
        adjust: str = "",
        ttl: Optional[int] = None,
    ) -> Optional[pd.DataFrame]:
        """获取港股历史行情数据，优先新浪日线聚合，东财作为备选。

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
        if period not in {"daily", "weekly", "monthly"}:
            raise ValidationException("period", "仅支持 daily、weekly、monthly")

        fetch_start = pd.Timestamp(start_date)
        if period != "daily":
            frequency = "W-SUN" if period == "weekly" else "M"
            fetch_start = fetch_start.to_period(frequency).start_time

        last_error = None

        # 优先使用稳定的新浪港股日线接口并支持周/月线重采样
        try:
            sina_df = self.call(
                ak.stock_hk_daily,
                symbol=symbol,
                adjust=adjust,
                ttl=ttl or 3600,
                enable_range_completion=False,
            )
            if sina_df is not None and not sina_df.empty:
                normalized = sina_df.rename(
                    columns={
                        "date": "日期",
                        "open": "开盘",
                        "close": "收盘",
                        "high": "最高",
                        "low": "最低",
                        "volume": "成交量",
                    }
                ).copy()
                resampled = self._resample_daily_bars(
                    normalized, period, start_date, end_date
                )
                if not resampled.empty:
                    if "日期" in resampled.columns:
                        resampled["日期"] = pd.to_datetime(resampled["日期"]).dt.strftime(
                            "%Y-%m-%d"
                        )
                    return resampled
        except Exception as sina_err:
            last_error = sina_err
            logger.warning(f"新浪港股历史行情接口失败，尝试东财接口: {sina_err}")

        try:
            em_df = self.call(
                ak.stock_hk_hist,
                symbol=symbol,
                period="daily",
                start_date=fetch_start.strftime("%Y%m%d"),
                end_date=end_date,
                adjust=adjust,
                ttl=ttl or 3600,
            )
            if em_df is not None and not em_df.empty:
                resampled = self._resample_daily_bars(em_df, period, start_date, end_date)
                if not resampled.empty:
                    return resampled
        except Exception as em_err:
            last_error = em_err
            logger.error(f"东财港股历史行情接口也失败: {em_err}")

        if last_error is not None:
            raise APIException("AkShare.stock_hk_hist", str(last_error)) from last_error
        return None

    def fetch_tencent_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """通过腾讯极速行情接口 (qt.gtimg.cn) 批量获取单只或多只股票/ETF/指数五档盘口及估值快照。

        Args:
            symbols: 标准化带前缀代码列表，如 ['sh600519', 'sz000001', 'sh510300']

        Returns:
            解析后的字典列表
        """
        if not symbols:
            return []

        normalized = []
        for sym in symbols:
            s = str(sym).strip().lower()
            if not s:
                continue
            if "." in s:
                parts = s.split(".", 1)
                if parts[0] in ("sh", "sz", "bj", "hk"):
                    s = f"{parts[0]}{parts[1]}"
                elif parts[1] in ("sh", "sz", "bj", "hk"):
                    s = f"{parts[1]}{parts[0]}"
            elif not s.startswith(("sh", "sz", "bj", "hk")):
                if len(s) == 5 and s.isdigit():
                    s = f"hk{s}"
                elif s.startswith(("5", "6", "9")):
                    s = f"sh{s}"
                elif s.startswith(("4", "8")):
                    s = f"bj{s}"
                else:
                    s = f"sz{s}"
            normalized.append(s)

        if not normalized:
            return []

        url = f"http://qt.gtimg.cn/q={','.join(normalized)}"
        session = self.get_http_session()
        resp = session.get(url, timeout=6)
        resp.raise_for_status()
        content = resp.content.decode("gbk", errors="ignore")

        def _to_float(val: str) -> Optional[float]:
            try:
                return float(val) if val and val.strip() != "" else None
            except (ValueError, TypeError):
                return None

        results: List[Dict[str, Any]] = []
        for line in content.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            lhs, rhs = line.split("=", 1)
            raw_key = lhs.replace("v_", "").strip()
            body = rhs.strip().strip('"')
            parts = body.split("~")
            if len(parts) < 45:
                continue

            bid_levels = []
            ask_levels = []
            for i in range(5):
                bp = _to_float(parts[9 + i * 2])
                bv = _to_float(parts[10 + i * 2])
                ap = _to_float(parts[19 + i * 2])
                av = _to_float(parts[20 + i * 2])
                if bp is not None:
                    bid_levels.append({"level": i + 1, "price": bp, "volume_lots": int(bv or 0)})
                if ap is not None:
                    ask_levels.append({"level": i + 1, "price": ap, "volume_lots": int(av or 0)})

            results.append({
                "full_symbol": raw_key,
                "symbol": parts[2],
                "name": parts[1],
                "price": _to_float(parts[3]),
                "prev_close": _to_float(parts[4]),
                "open": _to_float(parts[5]),
                "high": _to_float(parts[33]),
                "low": _to_float(parts[34]),
                "change": _to_float(parts[31]),
                "pct_change": _to_float(parts[32]),
                "amplitude_pct": _to_float(parts[43]),
                "volume_lots": _to_float(parts[6]),
                "amount_wan": _to_float(parts[37]),
                "turnover_pct": _to_float(parts[38]),
                "volume_ratio": _to_float(parts[49]) if len(parts) > 49 else None,
                "outer_vol_lots": _to_float(parts[7]),
                "inner_vol_lots": _to_float(parts[8]),
                "vwap": _to_float(parts[51]) if len(parts) > 51 else None,
                "pe_ttm": _to_float(parts[39]),
                "pe_dynamic": _to_float(parts[52]) if len(parts) > 52 else None,
                "pe_static": _to_float(parts[53]) if len(parts) > 53 else None,
                "pb": _to_float(parts[46]) if len(parts) > 46 else None,
                "float_market_cap_yi": _to_float(parts[44]),
                "total_market_cap_yi": _to_float(parts[45]),
                "limit_up": _to_float(parts[47]) if len(parts) > 47 else None,
                "limit_down": _to_float(parts[48]) if len(parts) > 48 else None,
                "timestamp": parts[30],
                "bids": bid_levels,
                "asks": ask_levels,
            })

        return results

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
