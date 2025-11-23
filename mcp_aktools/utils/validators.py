"""MCP AkTools 的参数验证工具。

此模块为 MCP AkTools API 中使用的常见参数类型提供验证器，
包括市场类型、周期、限制、日期和符号。
"""

import re
from typing import List

from ..exceptions import ValidationException


class Validators:
    """参数验证方法集合。
    
    此类提供用于验证股票和加密货币数据查询中使用的各种参数类型的静态方法。
    """
    
    # 有效的市场类型
    VALID_MARKETS: List[str] = ["sh", "sz", "hk", "us"]
    
    # 股票数据的有效周期类型
    VALID_PERIODS: List[str] = ["daily", "weekly"]
    
    # 加密货币数据的有效K线/蜡烛图间隔
    VALID_CRYPTO_BARS: List[str] = [
        "1m", "3m", "5m", "15m", "30m",
        "1H", "2H", "4H", "6H", "12H",
        "1D", "2D", "3D",
        "1W", "1M", "3M"
    ]
    
    # 日期格式模式 (YYYYMMDD)
    DATE_PATTERN = re.compile(r"^\d{8}$")
    
    @staticmethod
    def validate_market(market: str) -> str:
        """验证市场参数。
        
        Args:
            market: 要验证的市场标识符
            
        Returns:
            验证后的市场字符串
            
        Raises:
            ValidationException: 如果市场不在 VALID_MARKETS 中
            
        Examples:
            >>> Validators.validate_market("sh")
            'sh'
            >>> Validators.validate_market("invalid")
            ValidationException: Validation error for 'market': ...
        """
        if market not in Validators.VALID_MARKETS:
            raise ValidationException(
                "market",
                f"必须是 {Validators.VALID_MARKETS} 中的一个，得到 '{market}'"
            )
        return market
    
    @staticmethod
    def validate_period(period: str) -> str:
        """验证周期参数。
        
        Args:
            period: 要验证的周期标识符
            
        Returns:
            验证后的周期字符串
            
        Raises:
            ValidationException: 如果周期不在 VALID_PERIODS 中
            
        Examples:
            >>> Validators.validate_period("daily")
            'daily'
            >>> Validators.validate_period("monthly")
            ValidationException: Validation error for 'period': ...
        """
        if period not in Validators.VALID_PERIODS:
            raise ValidationException(
                "period",
                f"必须是 {Validators.VALID_PERIODS} 中的一个，得到 '{period}'"
            )
        return period
    
    @staticmethod
    def validate_crypto_bar(bar: str) -> str:
        """验证加密货币K线/蜡烛图间隔参数。
        
        Args:
            bar: 要验证的K线间隔
            
        Returns:
            验证后的K线间隔字符串
            
        Raises:
            ValidationException: 如果K线间隔不在 VALID_CRYPTO_BARS 中
            
        Examples:
            >>> Validators.validate_crypto_bar("1H")
            '1H'
            >>> Validators.validate_crypto_bar("5s")
            ValidationException: Validation error for 'bar': ...
        """
        if bar not in Validators.VALID_CRYPTO_BARS:
            raise ValidationException(
                "bar",
                f"必须是 {Validators.VALID_CRYPTO_BARS} 中的一个，得到 '{bar}'"
            )
        return bar
    
    @staticmethod
    def validate_limit(
        limit: int,
        min_val: int = 1,
        max_val: int = 300
    ) -> int:
        """验证结果数量限制参数。
        
        Args:
            limit: 要限制的结果数量
            min_val: 允许的最小值（默认：1）
            max_val: 允许的最大值（默认：300）
            
        Returns:
            验证后的限制整数
            
        Raises:
            ValidationException: 如果限制超出允许范围
            
        Examples:
            >>> Validators.validate_limit(50)
            50
            >>> Validators.validate_limit(500)
            ValidationException: Validation error for 'limit': ...
        """
        if not min_val <= limit <= max_val:
            raise ValidationException(
                "limit",
                f"必须在 {min_val} 和 {max_val} 之间，得到 {limit}"
            )
        return limit
    
    @staticmethod
    def validate_date(date: str) -> str:
        """验证日期格式 (YYYYMMDD)。
        
        Args:
            date: 要验证的日期字符串
            
        Returns:
            验证后的日期字符串
            
        Raises:
            ValidationException: 如果日期不符合 YYYYMMDD 格式
            
        Examples:
            >>> Validators.validate_date("20240101")
            '20240101'
            >>> Validators.validate_date("2024-01-01")
            ValidationException: Validation error for 'date': ...
        """
        if not Validators.DATE_PATTERN.match(date):
            raise ValidationException(
                "date",
                f"必须为 YYYYMMDD 格式，得到 '{date}'"
            )
        return date
    
    @staticmethod
    def validate_symbol(symbol: str) -> str:
        """验证股票/加密货币符号参数。
        
        Args:
            symbol: 要验证的符号字符串
            
        Returns:
            验证后并去除空格的符号字符串
            
        Raises:
            ValidationException: 如果符号为空或仅包含空格
            
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
                "符号不能为空"
            )
        return symbol.strip()
