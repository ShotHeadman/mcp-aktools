"""MCP AkTools 的配置管理。"""

import os
from pathlib import Path
from typing import Optional

from pydantic import ConfigDict, field_validator
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
    newsnow_base_url: Optional[str] = None
    newsnow_channels: str = "wallstreetcn-quick,cls-telegraph,jin10"

    # 缓存配置
    cache_memory_ttl: int = 600
    cache_disk_ttl: int = 1200
    cache_max_size: int = 100

    # SQLite 数据库缓存配置
    database_url: Optional[str] = None

    # 日志配置
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: Optional[str] = None
    log_file_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    log_file_backup_count: int = 5

    # API 速率限制配置
    rate_limit_calls: int = 10
    rate_limit_period: int = 1

    # 重试与数据源熔断配置
    retry_max_attempts: int = 3
    retry_backoff_factor: float = 2.0
    retry_max_delay: int = 60
    eastmoney_cooldown_seconds: int = 300
    eastmoney_max_retries: int = 1

    # 交易规则配置
    trading_rule_file: Optional[str] = None

    # 用户代码执行配置（回测策略、自定义指标）
    enable_user_code_execution: bool = False
    user_code_max_chars: int = 20000
    user_code_allowed_imports: str = ""

    @field_validator("database_url", mode="before")
    @classmethod
    def set_default_database_url(cls, v: Optional[str]) -> Optional[str]:
        """Set default database URL if not provided.
        
        Uses the user's cache directory as the default location.
        """
        if v is not None:
            return v
        # Return None here, actual default will be computed in get_database_path()
        return None

    def get_database_path(self) -> str:
        """Get the database file path.
        
        If database_url is set, returns that path.
        Otherwise, prefers the project data/stock_data.db directory if present,
        or falls back to the user's cache directory.
        
        Returns:
            str: The database file path
        """
        if self.database_url:
            return self.database_url

        project_data_dir = Path(__file__).resolve().parent.parent / "data"
        if project_data_dir.exists() and os.access(project_data_dir, os.W_OK):
            return str(project_data_dir / "stock_data.db")
        
        # Use user's cache directory as fallback
        if os.name == "nt":
            cache_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        elif os.name == "posix" and os.uname().sysname == "Darwin":
            cache_dir = Path.home() / "Library" / "Caches"
        else:
            cache_dir = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        
        db_dir = cache_dir / "mcp-aktools"
        db_path = db_dir / "cache.db"
        
        return str(db_path)

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


def get_trading_rules() -> Optional[str]:
    """读取交易规则文件内容。

    Returns:
        Optional[str]: 规则文件内容，如果文件不存在或读取失败则返回 None
    """
    settings = get_settings()
    if not settings.trading_rule_file:
        return None

    try:
        import os

        rule_path = settings.trading_rule_file
        # 支持相对路径和绝对路径
        if not os.path.isabs(rule_path):
            rule_path = os.path.abspath(rule_path)

        if not os.path.exists(rule_path):
            return None

        with open(rule_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None
