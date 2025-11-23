"""MCP AkTools 的配置管理。"""

import os
from typing import Optional
from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """从环境变量和 .env 文件加载的系统配置。"""
    
    # 使用 ConfigDict 的 Pydantic 配置（Pydantic V2 风格）
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # 服务器配置
    transport: str = "stdio"
    host: str = "0.0.0.0"
    port: int = 80
    
    # API 配置
    okx_base_url: str = "https://www.okx.com"
    binance_base_url: str = "https://www.binance.com"
    newsnow_base_url: Optional[str] = None
    newsnow_channels: str = "wallstreetcn-quick,cls-telegraph,jin10"
    
    # 代理配置
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    no_proxy: Optional[str] = None
    
    # 缓存配置
    cache_memory_ttl: int = 600
    cache_disk_ttl: int = 1200
    cache_max_size: int = 100
    
    # 日志配置
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: Optional[str] = None
    
    # API 速率限制配置
    rate_limit_calls: int = 10
    rate_limit_period: int = 1
    
    # 重试配置
    retry_max_attempts: int = 3
    retry_backoff_factor: float = 2.0
    retry_max_delay: int = 60


# 全局设置实例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取全局设置实例（单例模式）。
    
    Returns:
        Settings: 全局设置实例
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """从环境变量和 .env 文件重新加载设置。
    
    这对于测试或运行时配置更改很有用。
    
    Returns:
        Settings: 新加载的设置实例
    """
    global _settings
    _settings = Settings()
    return _settings
