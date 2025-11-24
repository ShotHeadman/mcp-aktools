"""OKX API 客户端，用于获取加密货币数据。"""

import logging
from typing import Optional

import pandas as pd

from ..config import Settings, get_settings
from ..exceptions import APIException, DataParsingException
from ..utils.rate_limiter import RateLimiter
from .base import BaseAPIClient

logger = logging.getLogger(__name__)


class OKXClient(BaseAPIClient):
    """OKX API 客户端，用于获取加密货币市场数据。

    此客户端封装了 OKX 的公共 API 端点，包括：
    - K 线数据（历史价格）
    - 杠杆多空比
    - 主动买卖量

    所有请求都会自动应用速率限制和重试逻辑。

    Attributes:
        base_url: OKX API 的基础 URL
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
        """初始化 OKX 客户端。

        Args:
            base_url: OKX API 的基础 URL（如果为 None，从配置中读取）
            timeout: 请求超时时间（秒）
            rate_limiter: 可选的速率限制器实例
            settings: 可选的配置实例（如果为 None，使用全局配置）
        """
        # 获取配置
        if settings is None:
            settings = get_settings()

        # 使用配置中的 base_url（如果未提供）
        if base_url is None:
            base_url = settings.okx_base_url

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

        logger.info(f"Initialized OKX client with base URL: {base_url}")

    def get_candles(
        self, inst_id: str, bar: str = "1H", limit: int = 100
    ) -> pd.DataFrame:
        """获取 K 线数据（历史价格）。

        Args:
            inst_id: 产品 ID，格式: BTC-USDT
            bar: K 线时间粒度，支持: 1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/2D/3D/1W/1M/3M
                 注意：分钟为小写 m，其余为大写
            limit: 返回数量，最大 300

        Returns:
            包含 K 线数据的 DataFrame，列包括：
            时间、开盘、最高、最低、收盘、成交量、成交额、成交额USDT、K线已完结

        Raises:
            APIException: 当 API 请求失败时
            DataParsingException: 当数据解析失败时
        """
        # 标准化 bar 参数（分钟为小写 m，其余为大写）
        if not bar.endswith("m"):
            bar = bar.upper()

        # 验证 limit 范围
        if limit < 1:
            limit = 1
        elif limit > 300:
            limit = 300

        logger.debug(
            f"Fetching OKX candles: inst_id={inst_id}, bar={bar}, limit={limit}"
        )

        try:
            # 发起 API 请求
            response = self.get(
                "/api/v5/market/candles",
                params={"instId": inst_id, "bar": bar, "limit": limit},
            )

            # 解析 JSON 响应
            data = response.json()

            # 检查响应格式
            if not isinstance(data, dict):
                raise DataParsingException(
                    "OKX", f"Expected dict response, got {type(data).__name__}"
                )

            # 检查是否有错误
            if data.get("code") != "0" and "code" in data:
                error_msg = data.get("msg", "Unknown error")
                raise APIException(
                    "OKX",
                    f"API returned error: {error_msg}",
                    status_code=response.status_code,
                )

            # 提取数据
            candles_data = data.get("data", [])

            if not candles_data:
                logger.warning(f"No candle data returned for {inst_id}")
                return pd.DataFrame()

            # 创建 DataFrame
            df = pd.DataFrame(candles_data)

            # 设置列名
            df.columns = [
                "时间",
                "开盘",
                "最高",
                "最低",
                "收盘",
                "成交量",
                "成交额",
                "成交额USDT",
                "K线已完结",
            ]

            # 按时间排序
            df.sort_values("时间", inplace=True)

            # 转换数据类型
            # 先转换为数值类型，然后转换为时间戳
            df["时间"] = pd.to_datetime(
                pd.to_numeric(df["时间"], errors="coerce"), errors="coerce", unit="ms"
            )
            df["开盘"] = pd.to_numeric(df["开盘"], errors="coerce")
            df["最高"] = pd.to_numeric(df["最高"], errors="coerce")
            df["最低"] = pd.to_numeric(df["最低"], errors="coerce")
            df["收盘"] = pd.to_numeric(df["收盘"], errors="coerce")
            df["成交量"] = pd.to_numeric(df["成交量"], errors="coerce")
            df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce")

            logger.info(f"Successfully fetched {len(df)} candles for {inst_id}")

            return df

        except (APIException, DataParsingException):
            # 重新抛出我们自己的异常
            raise
        except Exception as e:
            # 捕获其他异常并转换为 DataParsingException
            logger.error(f"Error parsing OKX candle data: {e}", exc_info=True)
            raise DataParsingException("OKX", f"Failed to parse candle data: {e}")

    def get_loan_ratio(self, currency: str, period: str = "1H") -> pd.DataFrame:
        """获取杠杆多空比。

        获取借入计价货币与借入交易货币的累计数额比值。

        Args:
            currency: 币种，格式: BTC 或 ETH
            period: 时间粒度，支持: 5m/1H/1D
                   注意：分钟为小写 m，其余为大写

        Returns:
            包含多空比数据的 DataFrame，列包括：时间、多空比

        Raises:
            APIException: 当 API 请求失败时
            DataParsingException: 当数据解析失败时
        """
        logger.debug(f"Fetching OKX loan ratio: currency={currency}, period={period}")

        try:
            # 发起 API 请求
            response = self.get(
                "/api/v5/rubik/stat/margin/loan-ratio",
                params={"ccy": currency, "period": period},
            )

            # 解析 JSON 响应
            data = response.json()

            # 检查响应格式
            if not isinstance(data, dict):
                raise DataParsingException(
                    "OKX", f"Expected dict response, got {type(data).__name__}"
                )

            # 检查是否有错误
            if data.get("code") != "0" and "code" in data:
                error_msg = data.get("msg", "Unknown error")
                raise APIException(
                    "OKX",
                    f"API returned error: {error_msg}",
                    status_code=response.status_code,
                )

            # 提取数据
            ratio_data = data.get("data", [])

            if not ratio_data:
                logger.warning(f"No loan ratio data returned for {currency}")
                return pd.DataFrame()

            # 创建 DataFrame
            df = pd.DataFrame(ratio_data)

            # 设置列名
            df.columns = ["时间", "多空比"]

            # 转换数据类型
            # 先转换为数值类型，然后转换为时间戳
            df["时间"] = pd.to_datetime(
                pd.to_numeric(df["时间"], errors="coerce"), errors="coerce", unit="ms"
            )
            df["多空比"] = pd.to_numeric(df["多空比"], errors="coerce")

            logger.info(
                f"Successfully fetched {len(df)} loan ratio records for {currency}"
            )

            return df

        except (APIException, DataParsingException):
            # 重新抛出我们自己的异常
            raise
        except Exception as e:
            # 捕获其他异常并转换为 DataParsingException
            logger.error(f"Error parsing OKX loan ratio data: {e}", exc_info=True)
            raise DataParsingException("OKX", f"Failed to parse loan ratio data: {e}")

    def get_taker_volume(
        self, currency: str, period: str = "1H", inst_type: str = "SPOT"
    ) -> pd.DataFrame:
        """获取主动买卖量。

        获取主动买入和卖出的交易量数据。

        Args:
            currency: 币种，格式: BTC 或 ETH
            period: 时间粒度，支持: 5m/1H/1D
                   注意：分钟为小写 m，其余为大写
            inst_type: 产品类型，SPOT（现货）或 CONTRACTS（衍生品）

        Returns:
            包含主动买卖量数据的 DataFrame，列包括：时间、卖出量、买入量

        Raises:
            APIException: 当 API 请求失败时
            DataParsingException: 当数据解析失败时
        """
        logger.debug(
            f"Fetching OKX taker volume: currency={currency}, "
            f"period={period}, inst_type={inst_type}"
        )

        try:
            # 发起 API 请求
            response = self.get(
                "/api/v5/rubik/stat/taker-volume",
                params={"ccy": currency, "period": period, "instType": inst_type},
            )

            # 解析 JSON 响应
            data = response.json()

            # 检查响应格式
            if not isinstance(data, dict):
                raise DataParsingException(
                    "OKX", f"Expected dict response, got {type(data).__name__}"
                )

            # 检查是否有错误
            if data.get("code") != "0" and "code" in data:
                error_msg = data.get("msg", "Unknown error")
                raise APIException(
                    "OKX",
                    f"API returned error: {error_msg}",
                    status_code=response.status_code,
                )

            # 提取数据
            volume_data = data.get("data", [])

            if not volume_data:
                logger.warning(f"No taker volume data returned for {currency}")
                return pd.DataFrame()

            # 创建 DataFrame
            df = pd.DataFrame(volume_data)

            # 设置列名
            df.columns = ["时间", "卖出量", "买入量"]

            # 转换数据类型
            # 先转换为数值类型，然后转换为时间戳
            df["时间"] = pd.to_datetime(
                pd.to_numeric(df["时间"], errors="coerce"), errors="coerce", unit="ms"
            )
            df["卖出量"] = pd.to_numeric(df["卖出量"], errors="coerce")
            df["买入量"] = pd.to_numeric(df["买入量"], errors="coerce")

            logger.info(
                f"Successfully fetched {len(df)} taker volume records for {currency}"
            )

            return df

        except (APIException, DataParsingException):
            # 重新抛出我们自己的异常
            raise
        except Exception as e:
            # 捕获其他异常并转换为 DataParsingException
            logger.error(f"Error parsing OKX taker volume data: {e}", exc_info=True)
            raise DataParsingException("OKX", f"Failed to parse taker volume data: {e}")
