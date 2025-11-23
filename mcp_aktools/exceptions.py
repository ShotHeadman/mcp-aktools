"""Custom exception classes for MCP AkTools."""

from typing import Optional


class MCPAkToolsException(Exception):
    """Base exception class for all MCP AkTools exceptions."""
    pass


class APIException(MCPAkToolsException):
    """Exception raised when an external API call fails.
    
    Attributes:
        api_name: Name of the API that failed
        message: Error message describing the failure
        status_code: HTTP status code if applicable
    """
    
    def __init__(
        self,
        api_name: str,
        message: str,
        status_code: Optional[int] = None
    ):
        """Initialize APIException.
        
        Args:
            api_name: Name of the API that failed
            message: Error message describing the failure
            status_code: HTTP status code if applicable
        """
        self.api_name = api_name
        self.message = message
        self.status_code = status_code
        
        error_msg = f"{api_name} API Error: {message}"
        if status_code is not None:
            error_msg += f" (Status: {status_code})"
        
        super().__init__(error_msg)


class DataParsingException(MCPAkToolsException):
    """Exception raised when data parsing fails.
    
    Attributes:
        source: Source of the data that failed to parse
        message: Error message describing the parsing failure
    """
    
    def __init__(self, source: str, message: str):
        """Initialize DataParsingException.
        
        Args:
            source: Source of the data that failed to parse
            message: Error message describing the parsing failure
        """
        self.source = source
        self.message = message
        super().__init__(f"Data parsing error from {source}: {message}")


class ValidationException(MCPAkToolsException):
    """Exception raised when parameter validation fails.
    
    Attributes:
        param_name: Name of the parameter that failed validation
        message: Error message describing the validation failure
    """
    
    def __init__(self, param_name: str, message: str):
        """Initialize ValidationException.
        
        Args:
            param_name: Name of the parameter that failed validation
            message: Error message describing the validation failure
        """
        self.param_name = param_name
        self.message = message
        super().__init__(f"Validation error for '{param_name}': {message}")


class RateLimitException(MCPAkToolsException):
    """Exception raised when API rate limit is exceeded.
    
    Attributes:
        api_name: Name of the API that rate limited the request
        retry_after: Number of seconds to wait before retrying (if provided by API)
    """
    
    def __init__(self, api_name: str, retry_after: Optional[int] = None):
        """Initialize RateLimitException.
        
        Args:
            api_name: Name of the API that rate limited the request
            retry_after: Number of seconds to wait before retrying
        """
        self.api_name = api_name
        self.retry_after = retry_after
        
        msg = f"Rate limit exceeded for {api_name}"
        if retry_after is not None:
            msg += f", retry after {retry_after} seconds"
        
        super().__init__(msg)


class RetryExhaustedException(MCPAkToolsException):
    """Exception raised when retry attempts are exhausted.
    
    Attributes:
        operation: Name of the operation that failed
        attempts: Number of retry attempts made
    """
    
    def __init__(self, operation: str, attempts: int):
        """Initialize RetryExhaustedException.
        
        Args:
            operation: Name of the operation that failed
            attempts: Number of retry attempts made
        """
        self.operation = operation
        self.attempts = attempts
        super().__init__(
            f"Retry exhausted for {operation} after {attempts} attempts"
        )


class CacheException(MCPAkToolsException):
    """Exception raised when cache operations fail.
    
    This exception is typically logged but not raised to avoid
    disrupting normal operations since cache is for performance optimization.
    
    Attributes:
        operation: Cache operation that failed (get, set, delete, etc.)
        message: Error message describing the failure
    """
    
    def __init__(self, operation: str, message: str):
        """Initialize CacheException.
        
        Args:
            operation: Cache operation that failed
            message: Error message describing the failure
        """
        self.operation = operation
        self.message = message
        super().__init__(f"Cache {operation} failed: {message}")
