"""新闻资讯工具函数。

此模块包含所有新闻相关的 MCP 工具函数，包括：
- 股票相关新闻
- 全球财经快讯
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Optional

import akshare as ak
import pandas as pd
import requests
from pydantic import Field

from ..clients.akshare import AkShareClient
from ..config import get_settings
from ..exceptions import APIException, ValidationException
from ..utils.validators import Validators

logger = logging.getLogger(__name__)

# 初始化客户端
_akshare_client: Optional[AkShareClient] = None

# User-Agent 用于 HTTP 请求
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10) AppleWebKit/537.36 Chrome/139"


def get_akshare_client() -> AkShareClient:
    """获取 AkShare 客户端单例。"""
    global _akshare_client
    if _akshare_client is None:
        _akshare_client = AkShareClient(get_settings())
    return _akshare_client


def stock_news(
    symbol: str = Field(description="股票代码"),
    limit: int = Field(15, description="返回数量(int)", strict=False),
) -> str:
    """根据股票代码获取近期相关新闻。

    Args:
        symbol: 股票代码
        limit: 返回的新闻数量

    Returns:
        str: 新闻内容字符串，每条新闻一行，或 "Not Found for {symbol}"
    """
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        Validators.validate_limit(limit, min_val=1, max_val=100)

        logger.info(f"获取新闻: symbol={symbol}, limit={limit}")

        # 获取新闻数据
        news_df = _stock_news_em(symbol, limit=20)

        if news_df is None or news_df.empty:
            logger.warning(f"未找到 {symbol} 的新闻")
            return f"Not Found for {symbol}"

        # 提取新闻内容并去重
        news_list = list(
            dict.fromkeys(
                [
                    v["新闻内容"]
                    for v in news_df.to_dict(orient="records")
                    if isinstance(v, dict) and "新闻内容" in v
                ]
            )
        )

        if not news_list:
            logger.warning(f"未找到 {symbol} 的有效新闻内容")
            return f"Not Found for {symbol}"

        # 返回指定数量的新闻
        return "\n".join(news_list[0:limit])

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取新闻失败: {e}", exc_info=True)
        return f"获取新闻失败: {str(e)}"


def stock_news_global() -> str:
    """获取最新的全球财经快讯。

    此函数从多个来源获取全球财经新闻：
    - 新浪全球财经快讯
    - NewsNow 财经频道（如果配置）

    Returns:
        str: 新闻内容字符串，每条新闻一行
    """
    try:
        logger.info("获取全球财经快讯")

        news_lines: List[str] = []

        # 尝试从新浪获取全球财经快讯
        try:
            client = get_akshare_client()
            sina_df = client.call(ak.stock_info_global_sina, ttl=600)  # 10 分钟缓存

            if sina_df is not None and not sina_df.empty:
                # 转换为 CSV 格式并清理
                csv_str = sina_df.to_csv(index=False, float_format="%.2f").strip()
                # 移除今天的日期前缀以简化输出
                csv_str = csv_str.replace(datetime.now().strftime("%Y-%m-%d "), "")
                news_lines.extend(csv_str.split("\n"))
                logger.debug(f"从新浪获取了 {len(news_lines)} 条新闻")
        except Exception as e:
            logger.warning(f"从新浪获取全球财经快讯失败: {e}")

        # 尝试从 NewsNow 获取新闻
        try:
            newsnow_items = _newsnow_news()
            if newsnow_items:
                news_lines.extend(newsnow_items)
                logger.debug(f"从 NewsNow 获取了 {len(newsnow_items)} 条新闻")
        except Exception as e:
            logger.warning(f"从 NewsNow 获取新闻失败: {e}")

        if not news_lines:
            logger.warning("未能获取任何全球财经快讯")
            return "暂无全球财经快讯"

        return "\n".join(news_lines)

    except Exception as e:
        logger.error(f"获取全球财经快讯失败: {e}", exc_info=True)
        return f"获取全球财经快讯失败: {str(e)}"


# 辅助函数


def _stock_news_em(symbol: str, limit: int = 20) -> Optional[pd.DataFrame]:
    """从东方财富获取股票新闻。

    此函数通过东方财富的搜索 API 获取指定股票的相关新闻。

    Args:
        symbol: 股票代码
        limit: 返回的新闻数量

    Returns:
        pd.DataFrame: 包含新闻数据的 DataFrame，或 None

    Raises:
        APIException: 当 API 调用失败时
    """
    try:
        # 构建缓存键
        cache_key = f"stock_news_em-{symbol}-{limit}"

        # 尝试从缓存获取
        from ..cache import CacheKey

        cache = CacheKey.init(cache_key, ttl=3600)
        cached_data = cache.get()

        if cached_data is not None:
            logger.debug(f"新闻缓存命中: {cache_key}")
            return cached_data

        # 构建请求参数
        cbk = "jQuery351013927587392975826_1763361926020"
        url = "http://search-api-web.eastmoney.com/search/jsonp"

        headers = {
            "User-Agent": USER_AGENT,
            "Referer": f"https://so.eastmoney.com/news/s?keyword={symbol}",
        }

        params = {
            "cb": cbk,
            "param": json.dumps(
                {
                    "uid": "",
                    "keyword": symbol,
                    "type": ["cmsArticleWebOld"],
                    "client": "web",
                    "clientType": "web",
                    "clientVersion": "curr",
                    "param": {
                        "cmsArticleWebOld": {
                            "searchScope": "default",
                            "sort": "default",
                            "pageIndex": 1,
                            "pageSize": 10,
                            "preTag": "<em>",
                            "postTag": "</em>",
                        }
                    },
                },
                ensure_ascii=False,
            ),
        }

        # 发起请求
        logger.debug(f"请求东方财富新闻 API: symbol={symbol}")
        response = requests.get(url, headers=headers, params=params, timeout=20)
        response.raise_for_status()

        # 解析响应
        text = response.text.replace(cbk, "").strip().strip("()")
        data = json.loads(text) or {}

        # 提取新闻数据
        articles = data.get("result", {}).get("cmsArticleWebOld") or []

        if not articles:
            logger.warning(f"东方财富未返回 {symbol} 的新闻数据")
            return None

        # 转换为 DataFrame
        df = pd.DataFrame(articles)

        # 按日期排序
        if "date" in df.columns:
            df.sort_values("date", ascending=False, inplace=True)

        # 限制数量
        df = df.head(limit)

        # 清理新闻内容（移除 HTML 标签）
        if "content" in df.columns:
            df["新闻内容"] = df["content"].str.replace(r"</?em>", "", regex=True)

        # 存入缓存
        cache.set(df)
        logger.debug(f"新闻数据已缓存: {cache_key}")

        return df

    except requests.exceptions.RequestException as e:
        logger.error(f"请求东方财富新闻 API 失败: {e}")
        raise APIException(api_name="EastMoney.NewsSearch", message=str(e))
    except json.JSONDecodeError as e:
        logger.error(f"解析东方财富新闻响应失败: {e}")
        raise APIException(
            api_name="EastMoney.NewsSearch", message=f"JSON 解析失败: {str(e)}"
        )
    except Exception as e:
        logger.error(f"获取东方财富新闻失败: {e}", exc_info=True)
        raise APIException(api_name="EastMoney.NewsSearch", message=str(e))


def _newsnow_news(channels: Optional[List[str]] = None) -> List[str]:
    """从 NewsNow 获取财经新闻。

    此函数从配置的 NewsNow 服务获取财经新闻。
    需要配置 NEWSNOW_BASE_URL 环境变量。

    Args:
        channels: 新闻频道列表，如果为 None 则从环境变量读取

    Returns:
        List[str]: 新闻标题列表
    """
    # 检查是否配置了 NewsNow 基础 URL
    base_url = os.getenv("NEWSNOW_BASE_URL")
    if not base_url:
        logger.debug("未配置 NEWSNOW_BASE_URL，跳过 NewsNow 新闻获取")
        return []

    try:
        # 获取频道配置
        if channels is None:
            channels_str = (
                os.getenv("NEWSNOW_CHANNELS")
                or "wallstreetcn-quick,cls-telegraph,jin10"
            )
            channels = channels_str.split(",")
        elif isinstance(channels, str):
            channels = channels.split(",")

        logger.debug(f"从 NewsNow 获取新闻，频道: {channels}")

        # 发起请求
        url = f"{base_url}/api/s/entire"
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": base_url,
        }
        payload = {"sources": channels}

        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()

        # 解析响应
        data = response.json() or []

        # 提取新闻
        news_items: List[str] = []
        for item in data:
            items = item.get("items", [])[0:15]
            for v in items:
                title = v.get("title", "")
                extra = v.get("extra") or {}
                hover = extra.get("hover") or title
                info = extra.get("info") or ""

                # 组合新闻内容
                news_text = f"{hover} {info}".strip().replace("\n", " ")
                if news_text:
                    news_items.append(news_text)

        logger.debug(f"从 NewsNow 获取了 {len(news_items)} 条新闻")
        return news_items

    except requests.exceptions.RequestException as e:
        logger.warning(f"请求 NewsNow API 失败: {e}")
        return []
    except json.JSONDecodeError as e:
        logger.warning(f"解析 NewsNow 响应失败: {e}")
        return []
    except Exception as e:
        logger.warning(f"从 NewsNow 获取新闻失败: {e}")
        return []
