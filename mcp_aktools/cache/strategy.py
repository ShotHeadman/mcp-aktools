"""MCP AkTools 的缓存策略定义。

此模块定义了不同类型数据的缓存策略和 TTL 配置。
"""

from typing import Dict
from enum import Enum


class CacheStrategy(Enum):
    """缓存策略类型。"""
    
    # 股票数据策略
    STOCK_INFO = "stock_info"
    STOCK_PRICES = "stock_prices"
    STOCK_INDICATORS = "stock_indicators"
    
    # 加密货币数据策略
    CRYPTO_PRICES = "crypto_prices"
    CRYPTO_RATIOS = "crypto_ratios"
    CRYPTO_VOLUME = "crypto_volume"
    
    # 市场数据策略
    MARKET_DATA = "market_data"
    MARKET_POOL = "market_pool"
    
    # 新闻策略
    NEWS = "news"
    
    # 默认策略
    DEFAULT = "default"


class CacheStrategyConfig:
    """缓存策略配置。
    
    此类定义了不同类型数据的 TTL 值和其他缓存参数。
    这些值可以通过配置进行覆盖。
    """
    
    # 默认 TTL 值（单位：秒）
    DEFAULT_MEMORY_TTL = 600  # 10 分钟
    DEFAULT_DISK_TTL = 1200   # 20 分钟
    
    # 特定策略的 TTL 值
    STRATEGY_TTL: Dict[CacheStrategy, tuple[int, int]] = {
        # (内存TTL, 磁盘TTL)
        CacheStrategy.STOCK_INFO: (300, 600),      # 5分钟 / 10分钟
        CacheStrategy.STOCK_PRICES: (600, 1800),   # 10分钟 / 30分钟
        CacheStrategy.STOCK_INDICATORS: (600, 1800),  # 10分钟 / 30分钟
        CacheStrategy.CRYPTO_PRICES: (60, 300),    # 1分钟 / 5分钟
        CacheStrategy.CRYPTO_RATIOS: (300, 600),   # 5分钟 / 10分钟
        CacheStrategy.CRYPTO_VOLUME: (300, 600),   # 5分钟 / 10分钟
        CacheStrategy.MARKET_DATA: (600, 1200),    # 10分钟 / 20分钟
        CacheStrategy.MARKET_POOL: (300, 600),     # 5分钟 / 10分钟
        CacheStrategy.NEWS: (600, 1800),           # 10分钟 / 30分钟
        CacheStrategy.DEFAULT: (DEFAULT_MEMORY_TTL, DEFAULT_DISK_TTL),
    }
    
    @classmethod
    def get_ttl(cls, strategy: CacheStrategy) -> tuple[int, int]:
        """获取缓存策略的 TTL 值。
        
        Args:
            strategy: 缓存策略
            
        Returns:
            (内存TTL, 磁盘TTL) 的元组，单位为秒
        """
        return cls.STRATEGY_TTL.get(strategy, cls.STRATEGY_TTL[CacheStrategy.DEFAULT])
    
    @classmethod
    def get_memory_ttl(cls, strategy: CacheStrategy) -> int:
        """获取策略的内存缓存 TTL。
        
        Args:
            strategy: 缓存策略
            
        Returns:
            内存 TTL，单位为秒
        """
        return cls.get_ttl(strategy)[0]
    
    @classmethod
    def get_disk_ttl(cls, strategy: CacheStrategy) -> int:
        """获取策略的磁盘缓存 TTL。
        
        Args:
            strategy: 缓存策略
            
        Returns:
            磁盘 TTL，单位为秒
        """
        return cls.get_ttl(strategy)[1]
