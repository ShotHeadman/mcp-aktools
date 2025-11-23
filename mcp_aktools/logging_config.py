"""MCP AkTools 的日志系统配置。

此模块提供统一的日志配置和管理功能，支持：
- 基于环境变量的日志级别配置
- 可配置的日志格式
- 文件日志输出（带轮转）
- 控制台日志输出
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

from .config import Settings


class LoggingConfig:
    """日志配置管理器。
    
    此类负责配置整个应用的日志系统，包括：
    - 日志级别
    - 日志格式
    - 日志处理器（控制台、文件）
    - 日志轮转
    """
    
    # 日志级别映射
    LEVEL_MAP = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    
    # 默认日志格式
    DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
    
    def __init__(self, settings: Optional[Settings] = None):
        """初始化日志配置。
        
        Args:
            settings: 可选的配置实例（如果为 None，将使用全局配置）
        """
        if settings is None:
            from .config import get_settings
            settings = get_settings()
        
        self.settings = settings
        self._configured = False
    
    def configure(self, force: bool = False) -> None:
        """配置日志系统。
        
        此方法应该在应用启动时调用一次。
        它会根据配置设置日志级别、格式和处理器。
        
        Args:
            force: 如果为 True，即使已配置也会重新配置
        """
        if self._configured and not force:
            return
        
        # 获取根日志记录器
        root_logger = logging.getLogger()
        
        # 设置日志级别
        log_level = self._get_log_level()
        root_logger.setLevel(log_level)
        
        # 清除现有的处理器（避免重复配置）
        root_logger.handlers.clear()
        
        # 创建日志格式化器
        formatter = self._create_formatter()
        
        # 添加控制台处理器
        console_handler = self._create_console_handler(formatter)
        root_logger.addHandler(console_handler)
        
        # 添加文件处理器（如果配置了日志文件）
        if self.settings.log_file:
            file_handler = self._create_file_handler(formatter)
            if file_handler:
                root_logger.addHandler(file_handler)
        
        # 配置第三方库的日志级别（避免过多噪音）
        self._configure_third_party_loggers()
        
        self._configured = True
        
        # 记录配置完成
        logger = logging.getLogger(__name__)
        logger.info(
            f"Logging configured: level={logging.getLevelName(log_level)}, "
            f"file={'enabled' if self.settings.log_file else 'disabled'}"
        )
    
    def _get_log_level(self) -> int:
        """从配置获取日志级别。
        
        Returns:
            日志级别常量（logging.DEBUG, logging.INFO 等）
        """
        level_str = self.settings.log_level.upper()
        return self.LEVEL_MAP.get(level_str, logging.INFO)
    
    def _create_formatter(self) -> logging.Formatter:
        """创建日志格式化器。
        
        Returns:
            配置好的 logging.Formatter 对象
        """
        log_format = self.settings.log_format or self.DEFAULT_FORMAT
        return logging.Formatter(
            fmt=log_format,
            datefmt=self.DEFAULT_DATE_FORMAT
        )
    
    def _create_console_handler(
        self,
        formatter: logging.Formatter
    ) -> logging.StreamHandler:
        """创建控制台日志处理器。
        
        Args:
            formatter: 日志格式化器
            
        Returns:
            配置好的 StreamHandler 对象
        """
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        return handler
    
    def _create_file_handler(
        self,
        formatter: logging.Formatter
    ) -> Optional[logging.Handler]:
        """创建文件日志处理器（带轮转）。
        
        Args:
            formatter: 日志格式化器
            
        Returns:
            配置好的 RotatingFileHandler 对象，如果创建失败则返回 None
        """
        try:
            log_file = Path(self.settings.log_file)
            
            # 确保日志目录存在
            log_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 创建轮转文件处理器
            # 使用配置中的最大字节数和备份数量
            handler = logging.handlers.RotatingFileHandler(
                filename=str(log_file),
                maxBytes=self.settings.log_file_max_bytes,
                backupCount=self.settings.log_file_backup_count,
                encoding='utf-8'
            )
            handler.setFormatter(formatter)
            
            return handler
            
        except Exception as e:
            # 如果无法创建文件处理器，记录警告但不中断应用
            print(
                f"Warning: Failed to create file log handler: {e}",
                file=sys.stderr
            )
            return None
    
    def _configure_third_party_loggers(self) -> None:
        """配置第三方库的日志级别。
        
        某些第三方库（如 urllib3、requests）可能产生过多的日志。
        此方法将它们的日志级别设置为更高的级别以减少噪音。
        """
        # 设置 urllib3 的日志级别为 WARNING（避免过多的连接日志）
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        
        # 设置 requests 的日志级别为 WARNING
        logging.getLogger("requests").setLevel(logging.WARNING)
        
        # 设置 httpx 的日志级别为 WARNING（如果使用）
        logging.getLogger("httpx").setLevel(logging.WARNING)
        
        # 设置 httpcore 的日志级别为 WARNING（如果使用）
        logging.getLogger("httpcore").setLevel(logging.WARNING)


# 全局日志配置实例
_logging_config: Optional[LoggingConfig] = None


def setup_logging(settings: Optional[Settings] = None, force: bool = False) -> None:
    """设置日志系统。
    
    这是配置日志系统的主要入口点。
    应该在应用启动时调用一次。
    
    Args:
        settings: 可选的配置实例（如果为 None，将使用全局配置）
        force: 如果为 True，即使已配置也会重新配置
    
    Example:
        >>> from mcp_aktools.logging_config import setup_logging
        >>> setup_logging()
    """
    global _logging_config
    
    if _logging_config is None or force:
        _logging_config = LoggingConfig(settings)
    
    _logging_config.configure(force=force)


def get_logger(name: str) -> logging.Logger:
    """获取日志记录器。
    
    这是获取日志记录器的便捷函数。
    
    Args:
        name: 日志记录器名称（通常使用 __name__）
        
    Returns:
        logging.Logger 对象
    
    Example:
        >>> from mcp_aktools.logging_config import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("Application started")
    """
    return logging.getLogger(name)
