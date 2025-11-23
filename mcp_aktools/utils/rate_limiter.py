"""Rate limiting implementation using token bucket algorithm."""

import time
import threading
from collections import deque
from typing import Optional


class RateLimiter:
    """Token bucket rate limiter for controlling API request rates.
    
    This implementation uses a sliding window approach with a deque to track
    request timestamps. It ensures that no more than `calls` requests are made
    within any `period` second window.
    
    Thread-safe for concurrent access.
    
    Attributes:
        calls: Maximum number of calls allowed in the time period
        period: Time period in seconds
        timestamps: Deque of request timestamps
        lock: Thread lock for synchronization
    """
    
    def __init__(self, calls: int, period: int):
        """Initialize the rate limiter.
        
        Args:
            calls: Maximum number of calls allowed in the time period
            period: Time period in seconds
            
        Raises:
            ValueError: If calls or period are not positive integers
        """
        if calls <= 0:
            raise ValueError("calls must be a positive integer")
        if period <= 0:
            raise ValueError("period must be a positive integer")
            
        self.calls = calls
        self.period = period
        self.timestamps: deque = deque()
        self.lock = threading.Lock()
    
    def acquire(
        self,
        blocking: bool = True,
        timeout: Optional[float] = None
    ) -> bool:
        """Acquire permission to make a request.
        
        This method will block (if blocking=True) until a token is available,
        or return immediately (if blocking=False) indicating whether a token
        was acquired.
        
        Args:
            blocking: If True, block until a token is available.
                     If False, return immediately.
            timeout: Maximum time to wait in seconds (only used if blocking=True).
                    None means wait indefinitely.
        
        Returns:
            True if a token was acquired, False otherwise.
            
        Raises:
            ValueError: If timeout is negative
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
            
        start_time = time.time()
        
        while True:
            with self.lock:
                now = time.time()
                
                # Remove timestamps outside the current window
                while self.timestamps and self.timestamps[0] <= now - self.period:
                    self.timestamps.popleft()
                
                # Check if we can acquire a token
                if len(self.timestamps) < self.calls:
                    self.timestamps.append(now)
                    return True
            
            # If non-blocking, return immediately
            if not blocking:
                return False
            
            # Check timeout
            if timeout is not None and time.time() - start_time >= timeout:
                return False
            
            # Wait a short time before retrying
            # Use a small sleep to avoid busy-waiting
            time.sleep(0.01)
    
    def reset(self) -> None:
        """Reset the rate limiter by clearing all timestamps.
        
        This is useful for testing or when you want to start fresh.
        """
        with self.lock:
            self.timestamps.clear()
    
    def get_remaining(self) -> int:
        """Get the number of remaining calls available in the current window.
        
        Returns:
            Number of calls that can be made without blocking
        """
        with self.lock:
            now = time.time()
            
            # Remove expired timestamps
            while self.timestamps and self.timestamps[0] <= now - self.period:
                self.timestamps.popleft()
            
            return max(0, self.calls - len(self.timestamps))
    
    def get_wait_time(self) -> float:
        """Get the time to wait before the next call can be made.
        
        Returns:
            Time in seconds to wait, or 0.0 if a call can be made immediately
        """
        with self.lock:
            now = time.time()
            
            # Remove expired timestamps
            while self.timestamps and self.timestamps[0] <= now - self.period:
                self.timestamps.popleft()
            
            # If we have capacity, no wait needed
            if len(self.timestamps) < self.calls:
                return 0.0
            
            # Calculate wait time until oldest timestamp expires
            oldest = self.timestamps[0]
            wait_time = (oldest + self.period) - now
            return max(0.0, wait_time)
