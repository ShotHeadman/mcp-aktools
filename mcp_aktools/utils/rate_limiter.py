"""使用令牌桶算法的速率限制实现。"""

import threading
import time
from collections import deque
from typing import Optional


class RateLimiter:
    """用于控制 API 请求速率的令牌桶速率限制器。

    此实现使用滑动窗口方法和双端队列来跟踪请求时间戳。
    它确保在任何 `period` 秒窗口内不会发出超过 `calls` 个请求。

    线程安全，支持并发访问。

    Attributes:
        calls: 时间段内允许的最大调用次数
        period: 时间段（秒）
        timestamps: 请求时间戳的双端队列
        lock: 用于同步的线程锁
    """

    def __init__(self, calls: int, period: int):
        """初始化速率限制器。

        Args:
            calls: 时间段内允许的最大调用次数
            period: 时间段（秒）

        Raises:
            ValueError: 如果 calls 或 period 不是正整数
        """
        if calls <= 0:
            raise ValueError("calls 必须是正整数")
        if period <= 0:
            raise ValueError("period 必须是正整数")

        self.calls = calls
        self.period = period
        self.timestamps: deque = deque()
        self.lock = threading.Lock()

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """获取发出请求的许可。

        此方法将阻塞（如果 blocking=True）直到令牌可用，
        或立即返回（如果 blocking=False）指示是否获取了令牌。

        Args:
            blocking: 如果为 True，则阻塞直到令牌可用。
                     如果为 False，则立即返回。
            timeout: 最大等待时间（秒）（仅在 blocking=True 时使用）。
                    None 表示无限期等待。

        Returns:
            如果获取了令牌则返回 True，否则返回 False。

        Raises:
            ValueError: 如果 timeout 为负数
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout 必须为非负数")

        start_time = time.time()

        while True:
            with self.lock:
                now = time.time()

                # 移除当前窗口之外的时间戳
                while self.timestamps and self.timestamps[0] <= now - self.period:
                    self.timestamps.popleft()

                # 检查是否可以获取令牌
                if len(self.timestamps) < self.calls:
                    self.timestamps.append(now)
                    return True

            # 如果非阻塞，立即返回
            if not blocking:
                return False

            # 检查超时
            if timeout is not None and time.time() - start_time >= timeout:
                return False

            # 在重试前等待一小段时间
            # 使用小的休眠以避免忙等待
            time.sleep(0.01)

    def reset(self) -> None:
        """通过清除所有时间戳来重置速率限制器。

        这对于测试或想要重新开始时很有用。
        """
        with self.lock:
            self.timestamps.clear()

    def get_remaining(self) -> int:
        """获取当前窗口中可用的剩余调用次数。

        Returns:
            可以在不阻塞的情况下进行的调用次数
        """
        with self.lock:
            now = time.time()

            # 移除过期的时间戳
            while self.timestamps and self.timestamps[0] <= now - self.period:
                self.timestamps.popleft()

            return max(0, self.calls - len(self.timestamps))

    def get_wait_time(self) -> float:
        """获取下次调用前需要等待的时间。

        Returns:
            需要等待的时间（秒），如果可以立即调用则返回 0.0
        """
        with self.lock:
            now = time.time()

            # 移除过期的时间戳
            while self.timestamps and self.timestamps[0] <= now - self.period:
                self.timestamps.popleft()

            # 如果有容量，不需要等待
            if len(self.timestamps) < self.calls:
                return 0.0

            # 计算直到最旧时间戳过期的等待时间
            oldest = self.timestamps[0]
            wait_time = (oldest + self.period) - now
            return max(0.0, wait_time)
