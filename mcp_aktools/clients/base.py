"""API 客户端基类，提供通用的 HTTP 请求功能。"""

import logging
from abc import ABC
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as Urllib3Retry

from ..config import Settings
from ..exceptions import APIException, RateLimitException
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class BaseAPIClient(ABC):
    """API 客户端基类，提供通用的 HTTP 请求功能。

    此基类提供：
    - 配置化的 HTTP 会话管理
    - 自动速率限制
    - 自动重试机制（使用 urllib3 的重试策略）
    - 请求日志记录

    子类应该继承此类并实现特定 API 的方法。

    Attributes:
        base_url: API 的基础 URL
        timeout: 请求超时时间（秒）
        rate_limiter: 可选的速率限制器实例
        session: 配置好的 requests Session 对象
    """

    def __init__(
        self,
        base_url: str,
        timeout: int = 20,
        rate_limiter: Optional[RateLimiter] = None,
        settings: Optional[Settings] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        """初始化 API 客户端。

        Args:
            base_url: API 的基础 URL
            timeout: 请求超时时间（秒）
            rate_limiter: 可选的速率限制器实例
            settings: 可选的配置实例（如果为 None，将使用全局配置）
            headers: 可选的默认请求头
        """
        self.base_url = base_url.rstrip("/")  # 移除尾部斜杠以保持一致性
        self.timeout = timeout
        self.rate_limiter = rate_limiter
        self.settings = settings
        self.default_headers = headers or {}

        # 创建配置好的会话
        self.session = self._create_session()

    def _create_session(self) -> requests.Session:
        """创建配置好的 requests Session。

        配置包括：
        - 重试策略（自动重试瞬时故障）
        - 默认请求头

        Returns:
            配置好的 requests.Session 对象
        """
        session = requests.Session()

        # 设置默认请求头
        if self.default_headers:
            session.headers.update(self.default_headers)

        # 配置重试策略
        # 使用 urllib3 的重试机制处理瞬时网络故障
        retry_strategy = Urllib3Retry(
            total=3,  # 最多重试 3 次
            backoff_factor=1,  # 退避因子：1, 2, 4 秒
            status_forcelist=[429, 500, 502, 503, 504],  # 这些状态码触发重试
            allowed_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
            raise_on_status=False,  # 不在重试时抛出异常
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def _make_request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> requests.Response:
        """发起 HTTP 请求。

        此方法处理：
        - 速率限制（如果配置了速率限制器）
        - URL 构建
        - 请求日志记录
        - 错误处理和异常转换

        Args:
            method: HTTP 方法（GET、POST 等）
            endpoint: API 端点（相对于 base_url）
            **kwargs: 传递给 requests 的额外参数

        Returns:
            requests.Response 对象

        Raises:
            RateLimitException: 当 API 返回 429 状态码时
            APIException: 当请求失败时
        """
        # 应用速率限制
        if self.rate_limiter:
            acquired = self.rate_limiter.acquire(blocking=True, timeout=30)
            if not acquired:
                raise RateLimitException(self.__class__.__name__, retry_after=30)

        # 构建完整 URL
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            # 端点是完整 URL
            url = endpoint
        else:
            # 端点是相对路径
            endpoint = endpoint.lstrip("/")
            url = f"{self.base_url}/{endpoint}"

        # 设置默认超时
        kwargs.setdefault("timeout", self.timeout)

        # 记录请求详情（DEBUG 级别）
        logger.debug(
            f"Making {method} request to {url} " f"(timeout={kwargs.get('timeout')}s)"
        )

        try:
            # 发起请求
            response = self.session.request(method, url, **kwargs)

            # 记录响应状态
            logger.debug(f"Received response: {response.status_code} " f"from {url}")

            # 检查 HTTP 错误
            response.raise_for_status()

            return response

        except requests.exceptions.HTTPError as e:
            # HTTP 错误（4xx, 5xx）
            status_code = e.response.status_code if e.response else None

            # 特殊处理速率限制错误
            if status_code == 429:
                retry_after = None
                if e.response and "Retry-After" in e.response.headers:
                    try:
                        retry_after = int(e.response.headers["Retry-After"])
                    except (ValueError, TypeError):
                        pass

                logger.warning(
                    f"Rate limit exceeded for {self.__class__.__name__} "
                    f"(retry after: {retry_after}s)"
                )
                raise RateLimitException(
                    self.__class__.__name__, retry_after=retry_after
                )

            # 其他 HTTP 错误
            error_msg = str(e)
            if e.response is not None:
                try:
                    # 尝试从响应中提取错误消息
                    error_data = e.response.json()
                    if isinstance(error_data, dict):
                        error_msg = (
                            error_data.get("message")
                            or error_data.get("error")
                            or error_msg
                        )
                except Exception:
                    # 如果无法解析 JSON，使用原始错误消息
                    pass

            logger.error(
                f"HTTP error for {self.__class__.__name__}: "
                f"{error_msg} (status: {status_code})"
            )
            raise APIException(
                self.__class__.__name__, error_msg, status_code=status_code
            )

        except requests.exceptions.Timeout:
            # 超时错误
            logger.error(
                f"Request timeout for {self.__class__.__name__} "
                f"after {kwargs.get('timeout')}s"
            )
            raise APIException(
                self.__class__.__name__,
                f"Request timeout after {kwargs.get('timeout')}s",
            )

        except requests.exceptions.ConnectionError as e:
            # 连接错误
            logger.error(f"Connection error for {self.__class__.__name__}: {e}")
            raise APIException(self.__class__.__name__, f"Connection error: {e}")

        except requests.exceptions.RequestException as e:
            # 其他请求异常
            logger.error(f"Request error for {self.__class__.__name__}: {e}")
            raise APIException(self.__class__.__name__, str(e))

    def get(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> requests.Response:
        """发起 GET 请求。

        Args:
            endpoint: API 端点
            params: URL 查询参数
            **kwargs: 传递给 requests 的额外参数

        Returns:
            requests.Response 对象
        """
        if params:
            kwargs["params"] = params
        return self._make_request("GET", endpoint, **kwargs)

    def post(
        self,
        endpoint: str,
        data: Optional[Any] = None,
        json: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """发起 POST 请求。

        Args:
            endpoint: API 端点
            data: 请求体数据（表单数据）
            json: 请求体数据（JSON 格式）
            **kwargs: 传递给 requests 的额外参数

        Returns:
            requests.Response 对象
        """
        if data is not None:
            kwargs["data"] = data
        if json is not None:
            kwargs["json"] = json
        return self._make_request("POST", endpoint, **kwargs)

    def put(
        self,
        endpoint: str,
        data: Optional[Any] = None,
        json: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """发起 PUT 请求。

        Args:
            endpoint: API 端点
            data: 请求体数据（表单数据）
            json: 请求体数据（JSON 格式）
            **kwargs: 传递给 requests 的额外参数

        Returns:
            requests.Response 对象
        """
        if data is not None:
            kwargs["data"] = data
        if json is not None:
            kwargs["json"] = json
        return self._make_request("PUT", endpoint, **kwargs)

    def delete(self, endpoint: str, **kwargs: Any) -> requests.Response:
        """发起 DELETE 请求。

        Args:
            endpoint: API 端点
            **kwargs: 传递给 requests 的额外参数

        Returns:
            requests.Response 对象
        """
        return self._make_request("DELETE", endpoint, **kwargs)

    def close(self) -> None:
        """关闭会话并释放资源。

        应该在不再需要客户端时调用此方法。
        """
        if self.session:
            self.session.close()
            logger.debug(f"Closed session for {self.__class__.__name__}")

    def __enter__(self):
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出，自动关闭会话。"""
        self.close()
        return False
