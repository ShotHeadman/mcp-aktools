"""加密货币相关工具函数模块。

此模块提供加密货币数据查询功能，包括：
- OKX 加密货币历史价格和技术指标
- OKX 杠杆多空比
- OKX 主动买卖量
- Binance AI 分析报告

所有函数都使用专门的 API 客户端，并包含完整的类型注解和文档字符串。
"""

import time
import json
import logging
from typing import Optional

import pandas as pd

from ..clients.okx import OKXClient
from ..clients.binance import BinanceClient
from ..utils.validators import Validators
from ..utils.indicators import add_technical_indicators
from ..exceptions import APIException, DataParsingException

logger = logging.getLogger(__name__)

# 全局客户端实例（延迟初始化）
_okx_client: Optional[OKXClient] = None
_binance_client: Optional[BinanceClient] = None


def _get_okx_client() -> OKXClient:
    """获取 OKX 客户端单例。
    
    Returns:
        OKXClient: OKX API 客户端实例
    """
    global _okx_client
    if _okx_client is None:
        _okx_client = OKXClient()
    return _okx_client


def _get_binance_client() -> BinanceClient:
    """获取 Binance 客户端单例。
    
    Returns:
        BinanceClient: Binance API 客户端实例
    """
    global _binance_client
    if _binance_client is None:
        _binance_client = BinanceClient()
    return _binance_client


def okx_prices(
    inst_id: str,
    bar: str = "1H",
    limit: int = 100
) -> str:
    """获取 OKX 加密货币历史价格和技术指标。
    
    从 OKX 获取加密货币的 K 线数据，包括价格、交易量和技术指标（MACD、KDJ、RSI、BOLL）。
    
    Args:
        inst_id: 产品 ID，格式: BTC-USDT
        bar: K 线时间粒度，支持: 1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/2D/3D/1W/1M/3M
             注意：分钟为小写 m，其余为大写
        limit: 返回数量，最大 300，建议最小 30
    
    Returns:
        str: CSV 格式的价格和技术指标数据
    
    Raises:
        ValidationException: 当参数验证失败时
        APIException: 当 API 请求失败时
        DataParsingException: 当数据解析失败时
    
    Example:
        >>> result = okx_prices("BTC-USDT", "1H", 100)
        >>> print(result)
        时间,开盘,收盘,最高,最低,成交量,成交额,MACD,DIF,DEA,...
    """
    logger.info(f"Fetching OKX prices: inst_id={inst_id}, bar={bar}, limit={limit}")
    
    try:
        # 验证参数
        Validators.validate_symbol(inst_id)
        Validators.validate_crypto_bar(bar)
        Validators.validate_limit(limit, min_val=1, max_val=300)
        
        # 获取客户端
        client = _get_okx_client()
        
        # 获取 K 线数据（多获取一些用于计算技术指标）
        # 技术指标需要历史数据，所以我们获取 limit + 62 条数据
        fetch_limit = max(300, limit + 62)
        df = client.get_candles(inst_id=inst_id, bar=bar, limit=fetch_limit)
        
        if df.empty:
            logger.warning(f"No data returned for {inst_id}")
            return pd.DataFrame().to_csv(index=False)
        
        # 添加技术指标
        add_technical_indicators(df, df["收盘"], df["最低"], df["最高"])
        
        # 选择要返回的列
        columns = [
            "时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额",
            "MACD", "DIF", "DEA", "KDJ.K", "KDJ.D", "KDJ.J", "RSI", "BOLL.U", "BOLL.M", "BOLL.L",
        ]
        
        # 转换为 CSV 格式
        all_rows = df.to_csv(columns=columns, index=False, float_format="%.2f").strip().split("\n")
        
        # 返回表头和最后 limit 行数据
        result = "\n".join([all_rows[0], *all_rows[-limit:]])
        
        logger.info(f"Successfully fetched {limit} price records for {inst_id}")
        return result
        
    except (APIException, DataParsingException) as e:
        logger.error(f"Error fetching OKX prices: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in okx_prices: {e}", exc_info=True)
        raise APIException("OKX", f"Unexpected error: {e}")


def okx_loan_ratios(
    symbol: str,
    period: str = "1H"
) -> str:
    """获取 OKX 加密货币杠杆多空比。
    
    获取借入计价货币与借入交易货币的累计数额比值，用于判断市场情绪。
    
    Args:
        symbol: 币种，格式: BTC 或 ETH
        period: 时间粒度，支持: 5m/1H/1D
                注意：分钟为小写 m，其余为大写
    
    Returns:
        str: CSV 格式的多空比数据
    
    Raises:
        ValidationException: 当参数验证失败时
        APIException: 当 API 请求失败时
        DataParsingException: 当数据解析失败时
    
    Example:
        >>> result = okx_loan_ratios("BTC", "1H")
        >>> print(result)
        时间,多空比
        2024-01-01 00:00:00,1.23
    """
    logger.info(f"Fetching OKX loan ratios: symbol={symbol}, period={period}")
    
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        
        # 获取客户端
        client = _get_okx_client()
        
        # 获取多空比数据
        df = client.get_loan_ratio(currency=symbol, period=period)
        
        if df.empty:
            logger.warning(f"No loan ratio data returned for {symbol}")
            return pd.DataFrame().to_csv(index=False)
        
        # 转换为 CSV 格式
        result = df.to_csv(index=False, float_format="%.2f").strip()
        
        logger.info(f"Successfully fetched {len(df)} loan ratio records for {symbol}")
        return result
        
    except (APIException, DataParsingException) as e:
        logger.error(f"Error fetching OKX loan ratios: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in okx_loan_ratios: {e}", exc_info=True)
        raise APIException("OKX", f"Unexpected error: {e}")


def okx_taker_volume(
    symbol: str,
    period: str = "1H",
    inst_type: str = "SPOT"
) -> str:
    """获取 OKX 加密货币主动买卖情况。
    
    获取主动买入和卖出的交易量，用于分析市场买卖力量对比。
    
    Args:
        symbol: 币种，格式: BTC 或 ETH
        period: 时间粒度，支持: 5m/1H/1D
                注意：分钟为小写 m，其余为大写
        inst_type: 产品类型，SPOT（现货）或 CONTRACTS（衍生品）
    
    Returns:
        str: CSV 格式的主动买卖量数据
    
    Raises:
        ValidationException: 当参数验证失败时
        APIException: 当 API 请求失败时
        DataParsingException: 当数据解析失败时
    
    Example:
        >>> result = okx_taker_volume("BTC", "1H", "SPOT")
        >>> print(result)
        时间,卖出量,买入量
        2024-01-01 00:00:00,1000.50,1200.30
    """
    logger.info(
        f"Fetching OKX taker volume: symbol={symbol}, period={period}, inst_type={inst_type}"
    )
    
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        
        # 获取客户端
        client = _get_okx_client()
        
        # 获取主动买卖量数据
        df = client.get_taker_volume(currency=symbol, period=period, inst_type=inst_type)
        
        if df.empty:
            logger.warning(f"No taker volume data returned for {symbol}")
            return pd.DataFrame().to_csv(index=False)
        
        # 转换为 CSV 格式
        result = df.to_csv(index=False, float_format="%.2f").strip()
        
        logger.info(f"Successfully fetched {len(df)} taker volume records for {symbol}")
        return result
        
    except (APIException, DataParsingException) as e:
        logger.error(f"Error fetching OKX taker volume: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in okx_taker_volume: {e}", exc_info=True)
        raise APIException("OKX", f"Unexpected error: {e}")


def binance_ai_report(symbol: str) -> str:
    """获取 Binance 加密货币 AI 分析报告。
    
    获取币安对加密货币的 AI 分析报告，包含市场趋势、技术分析等信息。
    此工具对分析加密货币非常有用，推荐使用。
    
    Args:
        symbol: 加密货币币种，格式: BTC 或 ETH
    
    Returns:
        str: AI 分析报告文本
    
    Raises:
        ValidationException: 当参数验证失败时
        APIException: 当 API 请求失败时
        DataParsingException: 当数据解析失败时
    
    Example:
        >>> report = binance_ai_report("BTC")
        >>> print(report)
        市场概况：比特币价格持续上涨...
    """
    logger.info(f"Fetching Binance AI report: symbol={symbol}")
    
    try:
        # 验证参数
        Validators.validate_symbol(symbol)
        
        # 获取客户端
        client = _get_binance_client()
        
        # 构建请求参数
        # 注意：这里使用原始的 POST 请求方式，因为 Binance 的 AI 报告 API 不是标准的 REST API
        import requests
        
        # 获取配置
        from ..config import get_settings
        settings = get_settings()
        
        # 发起请求
        response = requests.post(
            f"{settings.binance_base_url}/bapi/bigdata/v3/friendly/bigdata/search/ai-report/report",
            json={
                'lang': 'zh-CN',
                'token': symbol,
                'symbol': f'{symbol}USDT',
                'product': 'web-spot',
                'timestamp': int(time.time() * 1000),
                'translateToken': None,
            },
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10) AppleWebKit/537.36 Chrome/139',
                'Referer': f'{settings.binance_base_url}/zh-CN/trade/{symbol}_USDT?type=spot',
                'lang': 'zh-CN',
            },
            timeout=20,
            proxies=client._get_proxies() if hasattr(client, '_get_proxies') else None
        )
        
        # 解析响应
        try:
            resp = response.json()
        except Exception:
            try:
                resp = json.loads(response.text.strip())
            except Exception:
                logger.error(f"Failed to parse Binance AI report response: {response.text}")
                return response.text
        
        # 提取报告内容
        data = resp.get('data') or {}
        report = data.get('report') or {}
        translated = report.get('translated') or report.get('original') or {}
        modules = translated.get('modules') or []
        
        # 组装报告文本
        texts = []
        for module in modules:
            if overview := module.get('overview'):
                texts.append(overview)
            for point in module.get('points', []):
                if content := point.get('content'):
                    texts.append(content)
        
        result = '\n'.join(texts)
        
        if result:
            logger.info(f"Successfully fetched AI report for {symbol}")
        else:
            logger.warning(f"Empty AI report returned for {symbol}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error fetching Binance AI report: {e}", exc_info=True)
        raise APIException("Binance", f"Failed to fetch AI report: {e}")
