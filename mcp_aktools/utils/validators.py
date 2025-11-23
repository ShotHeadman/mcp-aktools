"""Parameter validation utilities for MCP AkTools.

This module provides validators for common parameter types used across
the MCP AkTools API, including market types, periods, limits, dates, and symbols.
"""

import re
from typing import List

from ..exceptions import ValidationException


class Validators:
    """Collection of parameter validation methods.
    
    This class provides static methods for validating various parameter types
    used in stock and cryptocurrency data queries.
    """
    
    # Valid market types
    VALID_MARKETS: List[str] = ["sh", "sz", "hk", "us"]
    
    # Valid period types for stock data
    VALID_PERIODS: List[str] = ["daily", "weekly"]
    
    # Valid bar/candlestick intervals for cryptocurrency data
    VALID_CRYPTO_BARS: List[str] = [
        "1m", "3m", "5m", "15m", "30m",
        "1H", "2H", "4H", "6H", "12H",
        "1D", "2D", "3D",
        "1W", "1M", "3M"
    ]
    
    # Date format pattern (YYYYMMDD)
    DATE_PATTERN = re.compile(r"^\d{8}$")
    
    @staticmethod
    def validate_market(market: str) -> str:
        """Validate market parameter.
        
        Args:
            market: Market identifier to validate
            
        Returns:
            The validated market string
            
        Raises:
            ValidationException: If market is not in VALID_MARKETS
            
        Examples:
            >>> Validators.validate_market("sh")
            'sh'
            >>> Validators.validate_market("invalid")
            ValidationException: Validation error for 'market': ...
        """
        if market not in Validators.VALID_MARKETS:
            raise ValidationException(
                "market",
                f"Must be one of {Validators.VALID_MARKETS}, got '{market}'"
            )
        return market
    
    @staticmethod
    def validate_period(period: str) -> str:
        """Validate period parameter.
        
        Args:
            period: Period identifier to validate
            
        Returns:
            The validated period string
            
        Raises:
            ValidationException: If period is not in VALID_PERIODS
            
        Examples:
            >>> Validators.validate_period("daily")
            'daily'
            >>> Validators.validate_period("monthly")
            ValidationException: Validation error for 'period': ...
        """
        if period not in Validators.VALID_PERIODS:
            raise ValidationException(
                "period",
                f"Must be one of {Validators.VALID_PERIODS}, got '{period}'"
            )
        return period
    
    @staticmethod
    def validate_crypto_bar(bar: str) -> str:
        """Validate cryptocurrency bar/candlestick interval parameter.
        
        Args:
            bar: Bar interval to validate
            
        Returns:
            The validated bar string
            
        Raises:
            ValidationException: If bar is not in VALID_CRYPTO_BARS
            
        Examples:
            >>> Validators.validate_crypto_bar("1H")
            '1H'
            >>> Validators.validate_crypto_bar("5s")
            ValidationException: Validation error for 'bar': ...
        """
        if bar not in Validators.VALID_CRYPTO_BARS:
            raise ValidationException(
                "bar",
                f"Must be one of {Validators.VALID_CRYPTO_BARS}, got '{bar}'"
            )
        return bar
    
    @staticmethod
    def validate_limit(
        limit: int,
        min_val: int = 1,
        max_val: int = 300
    ) -> int:
        """Validate limit parameter for result count restrictions.
        
        Args:
            limit: Number of results to limit to
            min_val: Minimum allowed value (default: 1)
            max_val: Maximum allowed value (default: 300)
            
        Returns:
            The validated limit integer
            
        Raises:
            ValidationException: If limit is outside the allowed range
            
        Examples:
            >>> Validators.validate_limit(50)
            50
            >>> Validators.validate_limit(500)
            ValidationException: Validation error for 'limit': ...
        """
        if not min_val <= limit <= max_val:
            raise ValidationException(
                "limit",
                f"Must be between {min_val} and {max_val}, got {limit}"
            )
        return limit
    
    @staticmethod
    def validate_date(date: str) -> str:
        """Validate date format (YYYYMMDD).
        
        Args:
            date: Date string to validate
            
        Returns:
            The validated date string
            
        Raises:
            ValidationException: If date doesn't match YYYYMMDD format
            
        Examples:
            >>> Validators.validate_date("20240101")
            '20240101'
            >>> Validators.validate_date("2024-01-01")
            ValidationException: Validation error for 'date': ...
        """
        if not Validators.DATE_PATTERN.match(date):
            raise ValidationException(
                "date",
                f"Must be in YYYYMMDD format, got '{date}'"
            )
        return date
    
    @staticmethod
    def validate_symbol(symbol: str) -> str:
        """Validate stock/crypto symbol parameter.
        
        Args:
            symbol: Symbol string to validate
            
        Returns:
            The validated and trimmed symbol string
            
        Raises:
            ValidationException: If symbol is empty or contains only whitespace
            
        Examples:
            >>> Validators.validate_symbol("AAPL")
            'AAPL'
            >>> Validators.validate_symbol("  TSLA  ")
            'TSLA'
            >>> Validators.validate_symbol("   ")
            ValidationException: Validation error for 'symbol': ...
        """
        if not symbol or not symbol.strip():
            raise ValidationException(
                "symbol",
                "Symbol cannot be empty"
            )
        return symbol.strip()
