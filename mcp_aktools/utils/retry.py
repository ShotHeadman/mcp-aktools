"""Retry mechanism with exponential backoff for handling transient failures."""

import time
import logging
from typing import Callable, TypeVar, Optional, Type, Tuple, Any
from functools import wraps

from ..exceptions import RetryExhaustedException, RateLimitException

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry_with_backoff(
    max_attempts: int = 3,
    backoff_factor: float = 2.0,
    max_delay: int = 60,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[Exception, int, float], None]] = None
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a function with exponential backoff.
    
    The delay between retries follows the formula:
        delay = min(backoff_factor ** (attempt - 1), max_delay)
    
    For example, with backoff_factor=2.0 and max_delay=60:
        - Attempt 1: immediate
        - Attempt 2: wait 1 second (2^0)
        - Attempt 3: wait 2 seconds (2^1)
        - Attempt 4: wait 4 seconds (2^2)
        - etc., capped at max_delay
    
    Args:
        max_attempts: Maximum number of attempts (including the first try)
        backoff_factor: Multiplier for exponential backoff
        max_delay: Maximum delay between retries in seconds
        retryable_exceptions: Tuple of exception types that should trigger a retry.
                            If None, all exceptions trigger retry.
        on_retry: Optional callback function called before each retry.
                 Receives (exception, attempt_number, delay) as arguments.
    
    Returns:
        Decorated function that implements retry logic
        
    Raises:
        RetryExhaustedException: When all retry attempts are exhausted
        RateLimitException: Immediately without retry (not retryable)
        
    Example:
        @retry_with_backoff(max_attempts=3, backoff_factor=2.0)
        def fetch_data():
            return requests.get("https://api.example.com/data")
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if backoff_factor <= 0:
        raise ValueError("backoff_factor must be positive")
    if max_delay <= 0:
        raise ValueError("max_delay must be positive")
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[Exception] = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                    
                except RateLimitException:
                    # Don't retry rate limit exceptions - they should be handled
                    # by the caller with proper wait time
                    raise
                    
                except Exception as e:
                    # Check if this exception type should trigger a retry
                    if retryable_exceptions is not None:
                        if not isinstance(e, retryable_exceptions):
                            # Not a retryable exception, raise immediately
                            raise
                    
                    last_exception = e
                    
                    # If this was the last attempt, don't sleep
                    if attempt == max_attempts:
                        break
                    
                    # Calculate delay with exponential backoff
                    delay = min(
                        backoff_factor ** (attempt - 1),
                        max_delay
                    )
                    
                    # Log the retry attempt
                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed for "
                        f"{func.__name__}: {e}. Retrying in {delay:.2f}s..."
                    )
                    
                    # Call the retry callback if provided
                    if on_retry:
                        on_retry(e, attempt, delay)
                    
                    # Wait before retrying
                    time.sleep(delay)
            
            # All attempts exhausted
            operation_name = func.__name__
            logger.error(
                f"All {max_attempts} retry attempts exhausted for {operation_name}"
            )
            raise RetryExhaustedException(operation_name, max_attempts) from last_exception
        
        return wrapper
    return decorator


class RetryStrategy:
    """Configurable retry strategy for API calls.
    
    This class provides a reusable retry strategy that can be configured
    once and applied to multiple operations.
    
    Attributes:
        max_attempts: Maximum number of attempts
        backoff_factor: Multiplier for exponential backoff
        max_delay: Maximum delay between retries in seconds
        retryable_exceptions: Tuple of exception types that trigger retry
    """
    
    def __init__(
        self,
        max_attempts: int = 3,
        backoff_factor: float = 2.0,
        max_delay: int = 60,
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None
    ):
        """Initialize retry strategy.
        
        Args:
            max_attempts: Maximum number of attempts
            backoff_factor: Multiplier for exponential backoff
            max_delay: Maximum delay between retries in seconds
            retryable_exceptions: Tuple of exception types that trigger retry
        """
        self.max_attempts = max_attempts
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.retryable_exceptions = retryable_exceptions
    
    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any
    ) -> T:
        """Execute a function with retry logic.
        
        Args:
            func: Function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
        
        Returns:
            Result of the function call
            
        Raises:
            RetryExhaustedException: When all retry attempts are exhausted
        """
        last_exception: Optional[Exception] = None
        
        for attempt in range(1, self.max_attempts + 1):
            try:
                return func(*args, **kwargs)
                
            except RateLimitException:
                # Don't retry rate limit exceptions
                raise
                
            except Exception as e:
                # Check if this exception type should trigger a retry
                if self.retryable_exceptions is not None:
                    if not isinstance(e, self.retryable_exceptions):
                        raise
                
                last_exception = e
                
                # If this was the last attempt, don't sleep
                if attempt == self.max_attempts:
                    break
                
                # Calculate delay with exponential backoff
                delay = min(
                    self.backoff_factor ** (attempt - 1),
                    self.max_delay
                )
                
                # Log the retry attempt
                logger.warning(
                    f"Attempt {attempt}/{self.max_attempts} failed: {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                
                # Wait before retrying
                time.sleep(delay)
        
        # All attempts exhausted
        operation_name = func.__name__ if hasattr(func, '__name__') else 'operation'
        logger.error(
            f"All {self.max_attempts} retry attempts exhausted for {operation_name}"
        )
        raise RetryExhaustedException(
            operation_name,
            self.max_attempts
        ) from last_exception
    
    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Allow using the strategy as a decorator.
        
        Returns:
            Decorated function with retry logic
        """
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return self.execute(func, *args, **kwargs)
        return wrapper
