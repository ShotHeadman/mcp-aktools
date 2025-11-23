"""Cache strategy definitions for MCP AkTools.

This module defines cache strategies and TTL configurations for different
types of data.
"""

from typing import Dict
from enum import Enum


class CacheStrategy(Enum):
    """Cache strategy types."""
    
    # Stock data strategies
    STOCK_INFO = "stock_info"
    STOCK_PRICES = "stock_prices"
    STOCK_INDICATORS = "stock_indicators"
    
    # Crypto data strategies
    CRYPTO_PRICES = "crypto_prices"
    CRYPTO_RATIOS = "crypto_ratios"
    CRYPTO_VOLUME = "crypto_volume"
    
    # Market data strategies
    MARKET_DATA = "market_data"
    MARKET_POOL = "market_pool"
    
    # News strategies
    NEWS = "news"
    
    # Default strategy
    DEFAULT = "default"


class CacheStrategyConfig:
    """Configuration for cache strategies.
    
    This class defines TTL values and other cache parameters for different
    types of data. Values can be overridden by configuration.
    """
    
    # Default TTL values (in seconds)
    DEFAULT_MEMORY_TTL = 600  # 10 minutes
    DEFAULT_DISK_TTL = 1200   # 20 minutes
    
    # Strategy-specific TTL values
    STRATEGY_TTL: Dict[CacheStrategy, tuple[int, int]] = {
        # (memory_ttl, disk_ttl)
        CacheStrategy.STOCK_INFO: (300, 600),      # 5 min / 10 min
        CacheStrategy.STOCK_PRICES: (600, 1800),   # 10 min / 30 min
        CacheStrategy.STOCK_INDICATORS: (600, 1800),  # 10 min / 30 min
        CacheStrategy.CRYPTO_PRICES: (60, 300),    # 1 min / 5 min
        CacheStrategy.CRYPTO_RATIOS: (300, 600),   # 5 min / 10 min
        CacheStrategy.CRYPTO_VOLUME: (300, 600),   # 5 min / 10 min
        CacheStrategy.MARKET_DATA: (600, 1200),    # 10 min / 20 min
        CacheStrategy.MARKET_POOL: (300, 600),     # 5 min / 10 min
        CacheStrategy.NEWS: (600, 1800),           # 10 min / 30 min
        CacheStrategy.DEFAULT: (DEFAULT_MEMORY_TTL, DEFAULT_DISK_TTL),
    }
    
    @classmethod
    def get_ttl(cls, strategy: CacheStrategy) -> tuple[int, int]:
        """Get TTL values for a cache strategy.
        
        Args:
            strategy: The cache strategy
            
        Returns:
            Tuple of (memory_ttl, disk_ttl) in seconds
        """
        return cls.STRATEGY_TTL.get(strategy, cls.STRATEGY_TTL[CacheStrategy.DEFAULT])
    
    @classmethod
    def get_memory_ttl(cls, strategy: CacheStrategy) -> int:
        """Get memory cache TTL for a strategy.
        
        Args:
            strategy: The cache strategy
            
        Returns:
            Memory TTL in seconds
        """
        return cls.get_ttl(strategy)[0]
    
    @classmethod
    def get_disk_ttl(cls, strategy: CacheStrategy) -> int:
        """Get disk cache TTL for a strategy.
        
        Args:
            strategy: The cache strategy
            
        Returns:
            Disk TTL in seconds
        """
        return cls.get_ttl(strategy)[1]
