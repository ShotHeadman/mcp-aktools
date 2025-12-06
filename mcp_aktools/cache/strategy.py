"""MCP AkTools 的缓存策略定义。

此模块定义了不同类型数据的缓存策略和 TTL 配置。
"""

from enum import Enum
from typing import Dict


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

    # 数据类型策略（按数据更新频率分类）
    REALTIME_DATA = "realtime_data"  # 实时数据
    DAILY_DATA = "daily_data"  # 日线数据
    HISTORICAL_DATA = "historical_data"  # 历史数据

    # 默认策略
    DEFAULT = "default"


class CacheStrategyConfig:
    """缓存策略配置。

    此类定义了不同类型数据的 TTL 值和其他缓存参数。
    这些值可以通过配置进行覆盖。
    """

    # 默认 TTL 值（单位：秒）
    DEFAULT_MEMORY_TTL = 600  # 10 分钟
    DEFAULT_DISK_TTL = 1200  # 20 分钟

    # 特定策略的 TTL 值
    STRATEGY_TTL: Dict[CacheStrategy, tuple[int, int]] = {
        # (内存TTL, 磁盘TTL)
        CacheStrategy.STOCK_INFO: (300, 600),  # 5分钟 / 10分钟
        CacheStrategy.STOCK_PRICES: (600, 1800),  # 10分钟 / 30分钟
        CacheStrategy.STOCK_INDICATORS: (600, 1800),  # 10分钟 / 30分钟
        CacheStrategy.CRYPTO_PRICES: (60, 300),  # 1分钟 / 5分钟
        CacheStrategy.CRYPTO_RATIOS: (300, 600),  # 5分钟 / 10分钟
        CacheStrategy.CRYPTO_VOLUME: (300, 600),  # 5分钟 / 10分钟
        CacheStrategy.MARKET_DATA: (600, 1200),  # 10分钟 / 20分钟
        CacheStrategy.MARKET_POOL: (300, 600),  # 5分钟 / 10分钟
        CacheStrategy.NEWS: (600, 1800),  # 10分钟 / 30分钟
        # 数据类型策略（按数据更新频率分类）
        CacheStrategy.REALTIME_DATA: (60, 300),  # 1分钟 / 5分钟
        CacheStrategy.DAILY_DATA: (3600, 7200),  # 1小时 / 2小时
        CacheStrategy.HISTORICAL_DATA: (604800, 604800),  # 7天 / 7天
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

    @classmethod
    def get_ttl_by_data_type(cls, data_type: str) -> tuple[int, int]:
        """根据数据类型获取适当的 TTL 值。

        Args:
            data_type: 数据类型，支持 "realtime"、"daily"、"historical"

        Returns:
            (内存TTL, 磁盘TTL) 的元组，单位为秒

        Raises:
            ValueError: 当数据类型不支持时
        """
        data_type_mapping = {
            "realtime": CacheStrategy.REALTIME_DATA,
            "daily": CacheStrategy.DAILY_DATA,
            "historical": CacheStrategy.HISTORICAL_DATA,
        }

        strategy = data_type_mapping.get(data_type.lower())
        if strategy is None:
            raise ValueError(
                f"不支持的数据类型: {data_type}，"
                f"支持的类型: {list(data_type_mapping.keys())}"
            )

        return cls.get_ttl(strategy)
