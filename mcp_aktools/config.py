"""Configuration management for MCP AkTools."""

import os
from typing import Optional
from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """System configuration loaded from environment variables and .env file."""
    
    # Pydantic configuration using ConfigDict (Pydantic V2 style)
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Server configuration
    transport: str = "stdio"
    host: str = "0.0.0.0"
    port: int = 80
    
    # API configuration
    okx_base_url: str = "https://www.okx.com"
    binance_base_url: str = "https://www.binance.com"
    newsnow_base_url: Optional[str] = None
    newsnow_channels: str = "wallstreetcn-quick,cls-telegraph,jin10"
    
    # Proxy configuration
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None
    no_proxy: Optional[str] = None
    
    # Cache configuration
    cache_memory_ttl: int = 600
    cache_disk_ttl: int = 1200
    cache_max_size: int = 100
    
    # Logging configuration
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: Optional[str] = None
    
    # API rate limiting configuration
    rate_limit_calls: int = 10
    rate_limit_period: int = 1
    
    # Retry configuration
    retry_max_attempts: int = 3
    retry_backoff_factor: float = 2.0
    retry_max_delay: int = 60


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance (singleton pattern).
    
    Returns:
        Settings: The global settings instance
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """Reload settings from environment variables and .env file.
    
    This is useful for testing or when configuration changes at runtime.
    
    Returns:
        Settings: The newly loaded settings instance
    """
    global _settings
    _settings = Settings()
    return _settings
