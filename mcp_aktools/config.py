"""MCP AkTools 的配置管理。"""

import os
import urllib.request
from typing import Dict, Optional

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
    log_file_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    log_file_backup_count: int = 5

    # API 速率限制配置
    rate_limit_calls: int = 10
    rate_limit_period: int = 1

    # 重试配置
    retry_max_attempts: int = 3
    retry_backoff_factor: float = 2.0
    retry_max_delay: int = 60

    def __init__(self, **kwargs):
        """初始化配置，支持从多个环境变量源读取代理配置。"""
        super().__init__(**kwargs)
        # 从环境变量读取代理配置（支持大小写）
        self._load_proxy_from_env()

    def _load_proxy_from_env(self) -> None:
        """从环境变量加载代理配置。

        支持以下环境变量（按优先级顺序）：
        - HTTP_PROXY / http_proxy
        - HTTPS_PROXY / https_proxy
        - NO_PROXY / no_proxy

        如果未设置显式代理，尝试检测系统代理。
        """
        # HTTP 代理：优先使用大写，然后小写
        if self.http_proxy is None:
            self.http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get(
                "http_proxy"
            )

        # HTTPS 代理：优先使用大写，然后小写
        if self.https_proxy is None:
            self.https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get(
                "https_proxy"
            )

        # NO_PROXY：优先使用大写，然后小写
        if self.no_proxy is None:
            self.no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")

        # 如果没有显式配置代理，尝试检测系统代理
        if self.http_proxy is None and self.https_proxy is None:
            self._detect_system_proxy()

    def _detect_system_proxy(self) -> None:
        """检测系统代理设置。

        使用 urllib 的 ProxyHandler 来检测系统代理配置。
        """
        try:
            # 获取系统代理处理器
            proxy_handler = urllib.request.ProxyHandler()
            proxies = proxy_handler.proxies

            # 设置检测到的代理
            if "http" in proxies and self.http_proxy is None:
                self.http_proxy = proxies["http"]

            if "https" in proxies and self.https_proxy is None:
                self.https_proxy = proxies["https"]
        except Exception:
            # 系统代理检测失败时静默失败
            pass

    def get_proxies(self) -> Optional[Dict[str, str]]:
        """获取用于 requests 库的代理字典。

        Returns:
            Optional[Dict[str, str]]: 代理字典，格式为 {"http": "...", "https": "..."}
                                      如果没有配置代理则返回 None
        """
        proxies = {}

        if self.http_proxy:
            proxies["http"] = self.http_proxy

        if self.https_proxy:
            proxies["https"] = self.https_proxy

        return proxies if proxies else None

    def should_bypass_proxy(self, url: str) -> bool:
        """检查给定的 URL 是否应该绕过代理。

        根据 NO_PROXY 环境变量判断。NO_PROXY 可以包含：
        - 域名：example.com
        - 带端口的域名：example.com:8080
        - IP 地址：192.168.1.1
        - CIDR 表示法：192.168.0.0/16
        - 通配符：*.example.com
        - 多个条目用逗号分隔

        Args:
            url: 要检查的 URL

        Returns:
            bool: 如果应该绕过代理返回 True，否则返回 False
        """
        if not self.no_proxy:
            return False

        # 解析 URL 获取主机名
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname or parsed.netloc

        if not hostname:
            return False

        # 解析 NO_PROXY 列表
        no_proxy_list = [item.strip() for item in self.no_proxy.split(",")]

        for pattern in no_proxy_list:
            if not pattern:
                continue

            # 处理通配符
            if pattern.startswith("*."):
                # *.example.com 匹配 sub.example.com
                domain = pattern[2:]
                if hostname.endswith(domain) or hostname == domain:
                    return True
            elif pattern.startswith("."):
                # .example.com 匹配 sub.example.com
                domain = pattern[1:]
                if hostname.endswith(domain):
                    return True
            else:
                # 精确匹配或后缀匹配
                if hostname == pattern or hostname.endswith("." + pattern):
                    return True

        return False

    def get_proxy_for_url(self, url: str) -> Optional[Dict[str, str]]:
        """获取特定 URL 的代理配置，考虑 NO_PROXY 设置。

        Args:
            url: 目标 URL

        Returns:
            Optional[Dict[str, str]]: 代理字典，如果应该绕过代理则返回 None
        """
        if self.should_bypass_proxy(url):
            return None

        return self.get_proxies()


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
