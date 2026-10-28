"""新闻资讯工具函数。

此模块包含所有新闻相关的 MCP 工具函数，包括：
- 股票相关新闻
- 全球财经快讯
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
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
    """根据股票代码获取近期相关新闻及公司公告。

    优先组合巨潮资讯公司公告、财联社电报匹配与东方财富个股新闻，确保东财限流时仍能稳定返回。

    Args:
        symbol: 股票代码
        limit: 返回的新闻数量

    Returns:
        str: 新闻内容字符串，每条新闻一行，或 "Not Found for {symbol}"
    """
    try:
        symbol = Validators.validate_symbol(symbol)
        Validators.validate_limit(limit, min_val=1, max_val=100)

        logger.info(f"获取新闻: symbol={symbol}, limit={limit}")
        client = get_akshare_client()
        collected_news: List[str] = []

        # 1. 尝试东方财富个股新闻（若未处于熔断冷却期）
        if not client._is_source_open("eastmoney"):
            try:
                news_df = _stock_news_em(symbol, limit=max(20, int(limit)))
                if news_df is not None and not news_df.empty:
                    for v in news_df.to_dict(orient="records"):
                        if isinstance(v, dict) and v.get("新闻内容"):
                            date_prefix = f"[{v.get('date')}] " if v.get("date") else ""
                            collected_news.append(f"{date_prefix}{v['新闻内容']}")
            except Exception as em_err:
                client._record_source_failure("eastmoney", em_err)
                logger.warning(f"东财新闻接口失败，切换至巨潮公告/财联社备用源: {em_err}")

        # 2. 如果数量不足，补充巨潮资讯官方公告 (CNInfo)
        if len(collected_news) < limit and symbol.isdigit() and len(symbol) == 6:
            try:
                end_dt = datetime.now()
                start_dt = end_dt - timedelta(days=180)
                cninfo_df = client.call(
                    ak.stock_zh_a_disclosure_report_cninfo,
                    symbol=symbol,
                    start_date=start_dt.strftime("%Y%m%d"),
                    end_date=end_dt.strftime("%Y%m%d"),
                    ttl=3600,
                    enable_range_completion=False,
                )
                if cninfo_df is not None and not cninfo_df.empty:
                    for _, row in cninfo_df.head(int(limit)).iterrows():
                        title = str(row.get("公告标题", "")).strip()
                        pub_date = str(row.get("公告时间", "")).strip()
                        if title:
                            collected_news.append(f"[{pub_date} 公告] {title}")
            except Exception as cn_err:
                logger.warning(f"巨潮公告备用接口失败: {cn_err}")

        # 3. 尝试从财联社全球电报中检索关联快讯
        if len(collected_news) < limit:
            try:
                cls_df = client.call(
                    ak.stock_info_global_cls, symbol="全部", ttl=600, use_disk_cache=False
                )
                if cls_df is not None and not cls_df.empty:
                    text_cols = [c for c in ("内容", "标题", "content", "title") if c in cls_df.columns]
                    if text_cols:
                        col = text_cols[0]
                        matched = cls_df[cls_df[col].astype(str).str.contains(symbol, na=False)]
                        for item in matched[col].head(int(limit)).tolist():
                            collected_news.append(str(item).strip())
            except Exception as cls_err:
                logger.debug(f"财联社电报检索失败: {cls_err}")

        deduped = list(dict.fromkeys(n for n in collected_news if n))
        if not deduped:
            logger.warning(f"未找到 {symbol} 的有效新闻内容")
            return f"Not Found for {symbol}"

        return "\n".join(deduped[: int(limit)])

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
    - 财联社 7x24 全球电报
    - NewsNow 财经频道（如果配置）

    Returns:
        str: 新闻内容字符串，每条新闻一行
    """
    try:
        logger.info("获取全球财经快讯")

        news_lines: List[str] = []
        client = get_akshare_client()

        # 1. 尝试从新浪获取全球财经快讯
        try:
            sina_df = client.call(ak.stock_info_global_sina, ttl=300, use_disk_cache=False)

            if sina_df is not None and not sina_df.empty:
                csv_str = sina_df.to_csv(index=False, float_format="%.2f").strip()
                csv_str = csv_str.replace(datetime.now().strftime("%Y-%m-%d "), "")
                news_lines.extend(csv_str.split("\n"))
                logger.debug(f"从新浪获取了 {len(news_lines)} 条新闻")
        except Exception as e:
            logger.warning(f"从新浪获取全球财经快讯失败: {e}")

        # 2. 尝试从财联社获取电报快讯
        try:
            cls_df = client.call(
                ak.stock_info_global_cls, symbol="全部", ttl=300, use_disk_cache=False
            )
            if cls_df is not None and not cls_df.empty:
                for _, row in cls_df.head(20).iterrows():
                    title = str(row.get("标题", "") or "").strip()
                    content = str(row.get("内容", "") or "").strip()
                    pub_time = str(row.get("发布时间", "") or "").strip()
                    text = content or title
                    if text:
                        news_lines.append(f"{pub_time},{text}" if pub_time else text)
        except Exception as e:
            logger.warning(f"从财联社获取快讯失败: {e}")

        # 3. 尝试从 NewsNow 获取新闻
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
    """从东方财富获取股票新闻（使用连接池与动态 callback）。"""
    try:
        cache_key = f"stock_news_em-{symbol}-{limit}"

        from ..cache import CacheKey

        cache = CacheKey.init(cache_key, ttl=1800, symbol=symbol, data_type="info")
        cached_data = cache.cache1.get(cache_key)

        if cached_data is not None:
            logger.debug(f"新闻缓存命中: {cache_key}")
            return cached_data

        rand_digits = "".join(str(random.randint(0, 9)) for _ in range(20))
        cbk = f"jQuery3510{rand_digits}_{int(time.time() * 1000)}"
        url = "https://search-api-web.eastmoney.com/search/jsonp"

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
                            "pageSize": min(max(limit, 10), 50),
                            "preTag": "<em>",
                            "postTag": "</em>",
                        }
                    },
                },
                ensure_ascii=False,
            ),
        }

        logger.debug(f"请求东方财富新闻 API: symbol={symbol}")
        session = AkShareClient.get_http_session()
        response = session.get(url, headers=headers, params=params, timeout=8)
        response.raise_for_status()

        text = response.text.replace(cbk, "").strip().strip("()")
        data = json.loads(text) or {}

        articles = data.get("result", {}).get("cmsArticleWebOld") or []

        if not articles:
            logger.warning(f"东方财富未返回 {symbol} 的新闻数据")
            return None

        df = pd.DataFrame(articles)

        if "date" in df.columns:
            df.sort_values("date", ascending=False, inplace=True)

        df = df.head(limit)

        if "content" in df.columns:
            df["新闻内容"] = df["content"].str.replace(r"</?em>", "", regex=True)

        cache.cache1[cache_key] = df
        logger.debug(f"新闻数据已缓存（内存）: {cache_key}")

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
