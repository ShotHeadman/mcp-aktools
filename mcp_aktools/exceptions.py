"""MCP AkTools 的自定义异常类。"""

from typing import Optional


class MCPAkToolsException(Exception):
    """所有 MCP AkTools 异常的基类。"""

    pass


class APIException(MCPAkToolsException):
    """外部 API 调用失败时抛出的异常。

    Attributes:
        api_name: 失败的 API 名称
        message: 描述失败的错误消息
        status_code: HTTP 状态码（如果适用）
    """

    def __init__(self, api_name: str, message: str, status_code: Optional[int] = None):
        """初始化 APIException。

        Args:
            api_name: 失败的 API 名称
            message: 描述失败的错误消息
            status_code: HTTP 状态码（如果适用）
        """
        self.api_name = api_name
        self.message = message
        self.status_code = status_code

        error_msg = f"{api_name} API 错误: {message}"
        if status_code is not None:
            error_msg += f" (状态: {status_code})"

        super().__init__(error_msg)


class DataParsingException(MCPAkToolsException):
    """数据解析失败时抛出的异常。

    Attributes:
        source: 解析失败的数据源
        message: 描述解析失败的错误消息
    """

    def __init__(self, source: str, message: str):
        """初始化 DataParsingException。

        Args:
            source: 解析失败的数据源
            message: 描述解析失败的错误消息
        """
        self.source = source
        self.message = message
        super().__init__(f"来自 {source} 的数据解析错误: {message}")


class ValidationException(MCPAkToolsException):
    """参数验证失败时抛出的异常。

    Attributes:
        param_name: 验证失败的参数名称
        message: 描述验证失败的错误消息
    """

    def __init__(self, param_name: str, message: str):
        """初始化 ValidationException。

        Args:
            param_name: 验证失败的参数名称
            message: 描述验证失败的错误消息
        """
        self.param_name = param_name
        self.message = message
        super().__init__(f"'{param_name}' 的验证错误: {message}")


class RateLimitException(MCPAkToolsException):
    """API 速率限制被超过时抛出的异常。

    Attributes:
        api_name: 限制请求的 API 名称
        retry_after: 重试前需要等待的秒数（如果 API 提供）
    """

    def __init__(self, api_name: str, retry_after: Optional[int] = None):
        """初始化 RateLimitException。

        Args:
            api_name: 限制请求的 API 名称
            retry_after: 重试前需要等待的秒数
        """
        self.api_name = api_name
        self.retry_after = retry_after

        msg = f"{api_name} 的速率限制已超过"
        if retry_after is not None:
            msg += f"，请在 {retry_after} 秒后重试"

        super().__init__(msg)


class RetryExhaustedException(MCPAkToolsException):
    """重试尝试用尽时抛出的异常。

    Attributes:
        operation: 失败的操作名称
        attempts: 进行的重试尝试次数
    """

    def __init__(self, operation: str, attempts: int):
        """初始化 RetryExhaustedException。

        Args:
            operation: 失败的操作名称
            attempts: 进行的重试尝试次数
        """
        self.operation = operation
        self.attempts = attempts
        super().__init__(f"{operation} 的重试在 {attempts} 次尝试后已用尽")


class CacheException(MCPAkToolsException):
    """缓存操作失败时抛出的异常。

    此异常通常被记录但不抛出，以避免
    中断正常操作，因为缓存用于性能优化。

    Attributes:
        operation: 失败的缓存操作（get、set、delete 等）
        message: 描述失败的错误消息
    """

    def __init__(self, operation: str, message: str):
        """初始化 CacheException。

        Args:
            operation: 失败的缓存操作
            message: 描述失败的错误消息
        """
        self.operation = operation
        self.message = message
        super().__init__(f"缓存 {operation} 失败: {message}")
