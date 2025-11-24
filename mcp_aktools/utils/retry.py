"""用于处理瞬时故障的指数退避重试机制。"""

import logging
import time
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

from ..exceptions import RateLimitException, RetryExhaustedException

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with_backoff(
    max_attempts: int = 3,
    backoff_factor: float = 2.0,
    max_delay: int = 60,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[Exception, int, float], None]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """使用指数退避重试函数的装饰器。

    重试之间的延迟遵循以下公式：
        delay = min(backoff_factor ** (attempt - 1), max_delay)

    例如，当 backoff_factor=2.0 且 max_delay=60 时：
        - 尝试 1：立即执行
        - 尝试 2：等待 1 秒 (2^0)
        - 尝试 3：等待 2 秒 (2^1)
        - 尝试 4：等待 4 秒 (2^2)
        - 以此类推，上限为 max_delay

    Args:
        max_attempts: 最大尝试次数（包括第一次尝试）
        backoff_factor: 指数退避的乘数
        max_delay: 重试之间的最大延迟（秒）
        retryable_exceptions: 应触发重试的异常类型元组。
                            如果为 None，则所有异常都会触发重试。
        on_retry: 可选的回调函数，在每次重试前调用。
                 接收 (exception, attempt_number, delay) 作为参数。

    Returns:
        实现重试逻辑的装饰函数

    Raises:
        RetryExhaustedException: 当所有重试尝试都用尽时
        RateLimitException: 立即抛出，不重试（不可重试）

    Example:
        @retry_with_backoff(max_attempts=3, backoff_factor=2.0)
        def fetch_data():
            return requests.get("https://api.example.com/data")
    """
    if max_attempts < 1:
        raise ValueError("max_attempts 必须至少为 1")
    if backoff_factor <= 0:
        raise ValueError("backoff_factor 必须为正数")
    if max_delay <= 0:
        raise ValueError("max_delay 必须为正数")

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except RateLimitException:
                    # 不重试速率限制异常 - 应由调用者使用适当的等待时间处理
                    raise

                except Exception as e:
                    # 检查此异常类型是否应触发重试
                    if retryable_exceptions is not None:
                        if not isinstance(e, retryable_exceptions):
                            # 不是可重试的异常，立即抛出
                            raise

                    last_exception = e

                    # 如果这是最后一次尝试，不要休眠
                    if attempt == max_attempts:
                        break

                    # 使用指数退避计算延迟
                    delay = min(backoff_factor ** (attempt - 1), max_delay)

                    # 记录重试尝试
                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed for "
                        f"{func.__name__}: {e}. Retrying in {delay:.2f}s..."
                    )

                    # 如果提供了重试回调，则调用它
                    if on_retry:
                        on_retry(e, attempt, delay)

                    # 在重试前等待
                    time.sleep(delay)

            # 所有尝试都已用尽
            operation_name = func.__name__
            logger.error(
                f"All {max_attempts} retry attempts exhausted for {operation_name}"
            )
            raise RetryExhaustedException(
                operation_name, max_attempts
            ) from last_exception

        return wrapper

    return decorator


class RetryStrategy:
    """API 调用的可配置重试策略。

    此类提供了一个可重用的重试策略，可以配置一次并应用于多个操作。

    Attributes:
        max_attempts: 最大尝试次数
        backoff_factor: 指数退避的乘数
        max_delay: 重试之间的最大延迟（秒）
        retryable_exceptions: 触发重试的异常类型元组
    """

    def __init__(
        self,
        max_attempts: int = 3,
        backoff_factor: float = 2.0,
        max_delay: int = 60,
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    ):
        """初始化重试策略。

        Args:
            max_attempts: 最大尝试次数
            backoff_factor: 指数退避的乘数
            max_delay: 重试之间的最大延迟（秒）
            retryable_exceptions: 触发重试的异常类型元组
        """
        self.max_attempts = max_attempts
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.retryable_exceptions = retryable_exceptions

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """使用重试逻辑执行函数。

        Args:
            func: 要执行的函数
            *args: 函数的位置参数
            **kwargs: 函数的关键字参数

        Returns:
            函数调用的结果

        Raises:
            RetryExhaustedException: 当所有重试尝试都用尽时
        """
        last_exception: Optional[Exception] = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                return func(*args, **kwargs)

            except RateLimitException:
                # 不重试速率限制异常
                raise

            except Exception as e:
                # 检查此异常类型是否应触发重试
                if self.retryable_exceptions is not None:
                    if not isinstance(e, self.retryable_exceptions):
                        raise

                last_exception = e

                # 如果这是最后一次尝试，不要休眠
                if attempt == self.max_attempts:
                    break

                # 使用指数退避计算延迟
                delay = min(self.backoff_factor ** (attempt - 1), self.max_delay)

                # 记录重试尝试
                logger.warning(
                    f"Attempt {attempt}/{self.max_attempts} failed: {e}. "
                    f"Retrying in {delay:.2f}s..."
                )

                # 在重试前等待
                time.sleep(delay)

        # 所有尝试都已用尽
        operation_name = func.__name__ if hasattr(func, "__name__") else "operation"
        logger.error(
            f"All {self.max_attempts} retry attempts exhausted for {operation_name}"
        )
        raise RetryExhaustedException(
            operation_name, self.max_attempts
        ) from last_exception

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """允许将策略用作装饰器。

        Returns:
            带有重试逻辑的装饰函数
        """

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return self.execute(func, *args, **kwargs)

        return wrapper
