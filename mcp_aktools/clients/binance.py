"""Binance API 客户端，用于获取加密货币数据。"""

import logging
from typing import Any, Dict, Optional

from ..config import Settings, get_settings
from ..exceptions import APIException, DataParsingException
from ..utils.rate_limiter import RateLimiter
from .base import BaseAPIClient

logger = logging.getLogger(__name__)


class BinanceClient(BaseAPIClient):
    """Binance API 客户端，用于获取加密货币市场数据。

    此客户端封装了 Binance 的公共 API 端点，包括：
    - AI 分析报告

    所有请求都会自动应用速率限制和重试逻辑。

    Attributes:
        base_url: Binance API 的基础 URL
        timeout: 请求超时时间（秒）
        rate_limiter: 速率限制器实例
        session: HTTP 会话对象
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: int = 20,
        rate_limiter: Optional[RateLimiter] = None,
        settings: Optional[Settings] = None,
    ):
        """初始化 Binance 客户端。

        Args:
            base_url: Binance API 的基础 URL（如果为 None，从配置中读取）
            timeout: 请求超时时间（秒）
            rate_limiter: 可选的速率限制器实例
            settings: 可选的配置实例（如果为 None，使用全局配置）
        """
        # 获取配置
        if settings is None:
            settings = get_settings()

        # 使用配置中的 base_url（如果未提供）
        if base_url is None:
            base_url = settings.binance_base_url

        # 如果未提供速率限制器，创建一个默认的
        if rate_limiter is None:
            rate_limiter = RateLimiter(
                calls=settings.rate_limit_calls, period=settings.rate_limit_period
            )

        # 设置默认请求头
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10) AppleWebKit/537.36 Chrome/139"
        }

        # 调用父类初始化
        super().__init__(
            base_url=base_url,
            timeout=timeout,
            rate_limiter=rate_limiter,
            settings=settings,
            headers=headers,
        )

        logger.info(f"Initialized Binance client with base URL: {base_url}")

    def get_ai_report(self, symbol: str, timeframe: str = "1d") -> Dict[str, Any]:
        """获取加密货币 AI 分析报告。

        获取 Binance 提供的 AI 分析报告，包含市场趋势、技术分析等信息。

        Args:
            symbol: 交易对符号，格式: BTCUSDT
            timeframe: 时间周期，支持: 1h/4h/1d/1w

        Returns:
            包含 AI 分析报告的字典，包括：
            - symbol: 交易对符号
            - timeframe: 时间周期
            - analysis: 分析内容
            - timestamp: 报告时间戳

        Raises:
            APIException: 当 API 请求失败时
            DataParsingException: 当数据解析失败时
        """
        logger.debug(
            f"Fetching Binance AI report: symbol={symbol}, timeframe={timeframe}"
        )

        try:
            # 发起 API 请求
            # 注意：这是一个示例端点，实际的 Binance AI 报告 API 端点可能不同
            # 需要根据实际的 Binance API 文档进行调整
            response = self.get(
                "/api/v3/ai/report",
                params={"symbol": symbol.upper(), "interval": timeframe},
            )

            # 解析 JSON 响应
            data = response.json()

            # 检查响应格式
            if not isinstance(data, dict):
                raise DataParsingException(
                    "Binance", f"Expected dict response, got {type(data).__name__}"
                )

            # 检查是否有错误
            if "code" in data and data.get("code") != 200:
                error_msg = data.get("msg", "Unknown error")
                raise APIException(
                    "Binance",
                    f"API returned error: {error_msg}",
                    status_code=data.get("code"),
                )

            logger.info(f"Successfully fetched AI report for {symbol}")

            return data

        except (APIException, DataParsingException):
            # 重新抛出我们自己的异常
            raise
        except Exception as e:
            # 捕获其他异常并转换为 DataParsingException
            logger.error(f"Error parsing Binance AI report data: {e}", exc_info=True)
            raise DataParsingException(
                "Binance", f"Failed to parse AI report data: {e}"
            )
