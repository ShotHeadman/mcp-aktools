"""市场数据相关工具函数模块。

此模块提供市场数据查询功能，包括：
- 当前时间及交易日信息
- A股涨停股池
- A股强势股池
- A股龙虎榜统计
- A股板块资金流

所有函数都使用 AkShare 客户端，并包含完整的类型注解和文档字符串。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
from pydantic import Field

from ..clients.akshare import AkShareClient
from ..config import get_settings
from ..exceptions import ValidationException
from ..utils.validators import Validators

logger = logging.getLogger(__name__)

# 全局客户端实例（延迟初始化）
_akshare_client: Optional[AkShareClient] = None


def _get_akshare_client() -> AkShareClient:
    """获取 AkShare 客户端单例。

    Returns:
        AkShareClient: AkShare 客户端实例
    """
    global _akshare_client
    if _akshare_client is None:
        _akshare_client = AkShareClient(get_settings())
    return _akshare_client


def get_current_time() -> str:
    """获取当前系统时间及A股交易日信息。

    建议在调用其他需要日期参数的工具前使用该工具，以获取最近的交易日信息。

    Returns:
        str: 包含当前时间和最近交易日的字符串

    Example:
        >>> result = get_current_time()
        >>> print(result)
        当前时间: 2024-01-15T10:30:00, 星期一, 最近交易日有: 2024-01-12,2024-01-15,2024-01-16
    """
    logger.info("获取当前时间及交易日信息")

    try:
        now = datetime.now()
        week = "日一二三四五六日"[now.isoweekday()]
        texts = [f"当前时间: {now.isoformat()}, 星期{week}"]

        # 获取交易日历史数据
        client = _get_akshare_client()
        dfs = client.call(ak.tool_trade_date_hist_sina, ttl=43200)

        if dfs is not None and not dfs.empty:
            # 获取前后5天的交易日
            start = now.date() - timedelta(days=5)
            ended = now.date() + timedelta(days=5)
            dates = [
                d.strftime("%Y-%m-%d") for d in dfs["trade_date"] if start <= d <= ended
            ]
            texts.append(f", 最近交易日有: {','.join(dates)}")

        result = "".join(texts)
        logger.info(f"成功获取时间信息: {result}")
        return result

    except Exception as e:
        logger.error(f"获取时间信息失败: {e}", exc_info=True)
        # 即使失败也返回基本时间信息
        now = datetime.now()
        week = "日一二三四五六日"[now.isoweekday()]
        return f"当前时间: {now.isoformat()}, 星期{week}"


def _convert_index_symbol(symbol: str) -> str:
    """将指数代码转换为新浪接口所需的格式（内部辅助函数）。

    转换规则:
    - 以 "399" 开头 → "sz" 前缀（深证指数）
    - 以 "000" 开头且长度为6 → "sh" 前缀（上证指数）
    - 其他 → "sh" 前缀

    Args:
        symbol: 原始指数代码，如 "000001", "399001", "000300"

    Returns:
        str: 转换后的指数代码，如 "sh000001", "sz399001", "sh000300"

    Example:
        >>> _convert_index_symbol("000001")
        'sh000001'
        >>> _convert_index_symbol("399001")
        'sz399001'
        >>> _convert_index_symbol("000300")
        'sh000300'
    """
    symbol = symbol.strip()
    
    # 如果已经有前缀，直接返回
    if symbol.startswith("sh") or symbol.startswith("sz"):
        return symbol
    
    # 以 "399" 开头的是深证指数
    if symbol.startswith("399"):
        return f"sz{symbol}"
    
    # 其他情况（包括 "000" 开头的上证指数）使用 "sh" 前缀
    return f"sh{symbol}"


def _recent_trade_date() -> datetime.date:
    """获取最近的交易日（内部辅助函数）。

    Returns:
        datetime.date: 最近的交易日日期
    """
    now = datetime.now().date()

    try:
        client = _get_akshare_client()
        dfs = client.call(ak.tool_trade_date_hist_sina, ttl=43200)

        if dfs is None or dfs.empty:
            logger.warning("无法获取交易日历史数据，使用当前日期")
            return now

        # 按日期降序排序
        dfs.sort_values("trade_date", ascending=False, inplace=True)

        # 找到最近的交易日（不晚于今天）
        for d in dfs["trade_date"]:
            if d <= now:
                logger.debug(f"最近交易日: {d}")
                return d

        # 如果没有找到，返回当前日期
        return now

    except Exception as e:
        logger.error(f"获取最近交易日失败: {e}", exc_info=True)
        return now


def stock_zt_pool_em(
    date: str = Field(
        "", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"
    ),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
) -> str:
    """获取中国A股市场(上证、深证)的所有涨停股票。

    Args:
        date: 交易日日期（可选），默认为最近的交易日，格式: YYYYMMDD
        limit: 返回数量，范围 30-100

    Returns:
        str: CSV 格式的涨停股池数据

    Raises:
        ValidationException: 当参数验证失败时

    Example:
        >>> result = stock_zt_pool_em("20240115", 50)
        >>> print(result)
        共100只涨停股
        代码,名称,涨跌幅,最新价,成交额,...
    """
    logger.info(f"获取涨停股池: date={date}, limit={limit}")

    try:
        # 验证参数
        Validators.validate_limit(limit, min_val=30, max_val=100)

        # 如果没有指定日期，使用最近的交易日
        if not date:
            date = _recent_trade_date().strftime("%Y%m%d")
            logger.debug(f"使用最近交易日: {date}")
        else:
            # 验证日期格式
            Validators.validate_date(date)

        # 获取涨停股池数据（历史日期永久落盘缓存，减少东财重复请求）
        client = _get_akshare_client()
        is_historical = date < datetime.now().strftime("%Y%m%d")
        pool_ttl = 86400 * 30 if is_historical else 1200
        dfs = None
        source_name = "东方财富"

        if not client._is_source_open("eastmoney"):
            try:
                dfs = client.call(
                    ak.stock_zt_pool_em,
                    date=date,
                    ttl=pool_ttl,
                    use_disk_cache=is_historical,
                )
            except Exception as e:
                logger.warning("东财涨停池接口失败: %s", e)

        if dfs is None or dfs.empty:
            # 当日/最近交易日东财失败时，使用新浪全市场实时行情筛选涨停股作为降级兜底
            recent_date = _recent_trade_date().strftime("%Y%m%d")
            if date == recent_date or date == datetime.now().strftime("%Y%m%d"):
                try:
                    spot_df = client.call(ak.stock_zh_a_spot, ttl=120, use_disk_cache=False)
                    if spot_df is not None and not spot_df.empty and "涨跌幅" in spot_df.columns:
                        code_series = spot_df["代码"].astype(str)
                        name_series = spot_df["名称"].astype(str)
                        pct_series = pd.to_numeric(spot_df["涨跌幅"], errors="coerce").fillna(0.0)
                        is_20pct = code_series.str.contains(r"^(?:sh|sz)?(?:30|68)", regex=True)
                        is_st = name_series.str.upper().str.contains("ST", na=False)
                        zt_mask = (
                            (is_20pct & (pct_series >= 19.8))
                            | (is_st & (pct_series >= 4.8))
                            | (~is_20pct & ~is_st & (pct_series >= 9.8))
                        )
                        dfs = spot_df[zt_mask].copy()
                        source_name = "新浪财经(实时行情降级筛选)"
                except Exception as fallback_err:
                    logger.warning("新浪涨停股降级筛选失败: %s", fallback_err)

        if dfs is None or dfs.empty:
            logger.warning(f"未找到涨停股池数据: date={date}")
            return f"未找到 {date} 的涨停股池数据"

        cnt = len(dfs)
        if "成交额" in dfs.columns:
            dfs.sort_values("成交额", ascending=False, inplace=True)

        dfs = dfs.head(int(limit))
        desc = f"交易日: {date}，来源: {source_name}\n共{cnt}只涨停股\n"
        result = desc + dfs.to_csv(index=False, float_format="%.2f").strip()
        logger.info(f"成功获取 {len(dfs)} 只涨停股数据")
        return result

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取涨停股池失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_zt_pool_strong_em(
    date: str = Field(
        "", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"
    ),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
) -> str:
    """获取中国A股市场(上证、深证)的强势股池数据。

    强势股池包含连续涨停、快速涨停等强势股票。
    """
    logger.info(f"获取强势股池: date={date}, limit={limit}")

    try:
        Validators.validate_limit(limit, min_val=30, max_val=100)

        if not date:
            date = _recent_trade_date().strftime("%Y%m%d")
        else:
            Validators.validate_date(date)

        client = _get_akshare_client()
        is_historical = date < datetime.now().strftime("%Y%m%d")
        pool_ttl = 86400 * 30 if is_historical else 1200
        try:
            dfs = client.call(
                ak.stock_zt_pool_strong_em,
                date=date,
                ttl=pool_ttl,
                use_disk_cache=is_historical,
            )
        except Exception as e:
            logger.warning("东财强势股池接口失败: %s", e)
            return f"获取 {date} 的强势股池失败: {e}"

        if dfs is None or dfs.empty:
            logger.warning(f"未找到强势股池数据: date={date}")
            return f"未找到 {date} 的强势股池数据"

        dfs = dfs.head(int(limit))
        result = f"交易日: {date}，来源: 东方财富\n" + dfs.to_csv(index=False, float_format="%.2f").strip()
        logger.info(f"成功获取 {len(dfs)} 只强势股数据")
        return result

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取强势股池失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_lhb_ggtj_sina(
    days: str = Field("5", description="统计最近天数，仅支持: [5/10/30/60]"),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
) -> str:
    """获取中国A股市场(上证、深证)的龙虎榜个股上榜统计数据。

    龙虎榜显示异常波动股票的机构和游资买卖情况。

    Args:
        days: 统计最近天数，仅支持: 5/10/30/60
        limit: 返回数量，范围 30-100

    Returns:
        str: CSV 格式的龙虎榜统计数据

    Raises:
        ValidationException: 当参数验证失败时

    Example:
        >>> result = stock_lhb_ggtj_sina("5", 50)
        >>> print(result)
        股票代码,股票名称,上榜次数,累计买入额,累计卖出额,...
    """
    logger.info(f"获取龙虎榜统计: days={days}, limit={limit}")

    try:
        # 验证参数
        if days not in ["5", "10", "30", "60"]:
            raise ValidationException(
                "days", f"必须是 [5/10/30/60] 之一，得到 '{days}'"
            )
        Validators.validate_limit(limit, min_val=30, max_val=100)

        # 获取龙虎榜数据
        client = _get_akshare_client()
        dfs = client.call(ak.stock_lhb_ggtj_sina, symbol=days, ttl=3600)

        if dfs is None or dfs.empty:
            logger.warning(f"未找到龙虎榜数据: days={days}")
            return f"未找到最近 {days} 天的龙虎榜数据"

        # 限制返回数量
        dfs = dfs.head(int(limit))

        # 转换为 CSV 格式
        result = dfs.to_csv(index=False, float_format="%.2f").strip()

        logger.info(f"成功获取 {len(dfs)} 条龙虎榜数据")
        return result

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取龙虎榜数据失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_zh_a_spot_em(
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
) -> str:
    """获取 A 股实时行情数据。

    从新浪财经获取全市场 A 股实时行情数据，包括股票代码、名称、
    最新价、涨跌幅、成交量、成交额等信息。

    注意：此函数使用新浪数据源 (ak.stock_zh_a_spot) 替代被拦截的东方财富接口。

    Args:
        limit: 返回数量，范围 30-100

    Returns:
        str: CSV 格式的实时行情数据

    Raises:
        ValidationException: 当参数验证失败时

    Example:
        >>> result = stock_zh_a_spot_em(50)
        >>> print(result)
        代码,名称,最新价,涨跌幅,成交量,成交额
        000001,平安银行,10.50,1.23,1234567,12345678.00
    """
    logger.info(f"获取A股实时行情: limit={limit}")

    try:
        # 验证参数
        Validators.validate_limit(limit, min_val=30, max_val=100)

        # 获取实时行情数据，使用新浪接口替代被拦截的东财接口
        # 使用 60 秒 TTL 缓存
        client = _get_akshare_client()
        dfs = client.call(ak.stock_zh_a_spot, ttl=60)

        if dfs is None or dfs.empty:
            logger.warning("未获取到A股实时行情数据")
            return "获取实时行情数据失败"

        # 新浪接口返回列: 代码, 名称, 最新价, 涨跌额, 涨跌幅, 买入, 卖出, 昨收, 今开, 最高, 最低, 成交量, 成交额
        # 目标列: 代码, 名称, 最新价, 涨跌幅, 涨跌额, 成交量, 成交额, 最高, 最低, 今开, 昨收
        # 注意：新浪接口不提供 振幅 和 换手率 列
        target_columns = ['代码', '名称', '最新价', '涨跌幅', '涨跌额', '成交量', '成交额', '最高', '最低', '今开', '昨收']
        
        # 只选择存在的列
        available_columns = [col for col in target_columns if col in dfs.columns]
        if not available_columns:
            logger.error(f"实时行情数据列名不匹配，实际列名: {list(dfs.columns)}")
            return "数据格式异常，列名不匹配"

        result_df = dfs[available_columns].head(int(limit))

        # 转换为 CSV 格式
        result = result_df.to_csv(index=False, float_format="%.2f").strip()

        logger.info(f"成功获取 {len(result_df)} 条A股实时行情数据")
        return result

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取A股实时行情失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def margin_trading(
    limit: int = Field(30, description="返回数量(int)，默认30条", strict=False),
) -> str:
    """获取中国A股市场融资融券数据（综合上海和深圳两市）。

    从东方财富获取上海和深圳两市的融资融券历史数据，包括融资买入额、
    融资余额、融券余量、融券余额、融资融券余额等信息。

    AkShare 接口:
    - ak.macro_china_market_margin_sh() - 上海市场融资融券数据
    - ak.macro_china_market_margin_sz() - 深圳市场融资融券数据

    Args:
        limit: 返回记录数量，默认30条

    Returns:
        str: CSV 格式的融资融券数据，包含两市综合数据

    Example:
        >>> result = margin_trading(30)
        >>> print(result)
        日期,上海融资余额,上海融券余额,上海融资融券余额,深圳融资余额,深圳融券余额,深圳融资融券余额,两市融资融券余额
    """
    logger.info(f"获取融资融券数据: limit={limit}")

    try:
        client = _get_akshare_client()

        # 获取上海市场融资融券数据
        sh_df = client.call(ak.macro_china_market_margin_sh, ttl=3600)
        # 获取深圳市场融资融券数据
        sz_df = client.call(ak.macro_china_market_margin_sz, ttl=3600)

        if (sh_df is None or sh_df.empty) and (sz_df is None or sz_df.empty):
            logger.warning("未获取到融资融券数据")
            return "Not Found: 未获取到融资融券数据"

        # 处理上海数据
        if sh_df is not None and not sh_df.empty:
            sh_df = sh_df.rename(columns={
                "融资买入额": "上海融资买入额",
                "融资余额": "上海融资余额",
                "融券余量": "上海融券余量",
                "融券余额": "上海融券余额",
                "融券卖出量": "上海融券卖出量",
                "融资融券余额": "上海融资融券余额"
            })
            sh_df["日期"] = pd.to_datetime(sh_df["日期"]).dt.strftime("%Y-%m-%d")
            sh_df = sh_df.sort_values("日期", ascending=False)

        # 处理深圳数据
        if sz_df is not None and not sz_df.empty:
            sz_df = sz_df.rename(columns={
                "融资买入额": "深圳融资买入额",
                "融资余额": "深圳融资余额",
                "融券余量": "深圳融券余量",
                "融券余额": "深圳融券余额",
                "融券卖出量": "深圳融券卖出量",
                "融资融券余额": "深圳融资融券余额"
            })
            sz_df["日期"] = pd.to_datetime(sz_df["日期"]).dt.strftime("%Y-%m-%d")
            sz_df = sz_df.sort_values("日期", ascending=False)

        # 合并两市数据
        if sh_df is not None and not sh_df.empty and sz_df is not None and not sz_df.empty:
            # 按日期合并
            merged_df = pd.merge(sh_df, sz_df, on="日期", how="outer")
            merged_df = merged_df.sort_values("日期", ascending=False)
            
            # 计算两市合计
            merged_df["两市融资融券余额"] = (
                merged_df["上海融资融券余额"].fillna(0) + 
                merged_df["深圳融资融券余额"].fillna(0)
            )
            
            # 选择关键列
            columns = [
                "日期",
                "上海融资余额", "上海融券余额", "上海融资融券余额",
                "深圳融资余额", "深圳融券余额", "深圳融资融券余额",
                "两市融资融券余额"
            ]
            available_columns = [col for col in columns if col in merged_df.columns]
            result_df = merged_df[available_columns]
        elif sh_df is not None and not sh_df.empty:
            result_df = sh_df
        else:
            result_df = sz_df

        # 限制返回数量
        result_df = result_df.head(int(limit))

        # 转换为 CSV 格式
        result = result_df.to_csv(index=False, float_format="%.2f").strip()

        logger.info(f"成功获取 {len(result_df)} 条融资融券数据")
        return result

    except Exception as e:
        logger.error(f"获取融资融券数据失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def index_zh_a_hist(
    symbol: str = Field(description="指数代码，如 000001(上证指数), 399001(深证成指), 000300(沪深300)"),
    period: str = Field("daily", description="周期，支持: daily(日线), weekly(周线), monthly(月线)"),
    limit: int = Field(30, description="返回数量(int)", strict=False),
) -> str:
    """获取A股指数历史行情数据。

    从新浪财经获取A股指数的历史行情数据，包括开盘价、收盘价、最高价、
    最低价、成交量等信息，并支持重采样为周线/月线以及计算技术指标。

    AkShare 接口: ak.stock_zh_index_daily(symbol)

    常用指数代码:
    - 000001: 上证指数
    - 399001: 深证成指
    - 399006: 创业板指
    - 000300: 沪深300
    - 000016: 上证50
    - 000905: 中证500

    Args:
        symbol: 指数代码（不需要市场标识）
        period: 周期，支持 daily(日线), weekly(周线), monthly(月线)
        limit: 返回记录数量，默认30条

    Returns:
        str: CSV 格式的指数历史行情数据，包含技术指标
    """
    if hasattr(period, "default"):
        period = period.default
    if hasattr(limit, "default"):
        limit = limit.default

    logger.info(f"获取指数历史行情: symbol={symbol}, period={period}, limit={limit}")

    try:
        Validators.validate_period(period)

        # 转换指数代码格式
        converted_symbol = _convert_index_symbol(symbol)
        logger.debug(f"指数代码转换: {symbol} -> {converted_symbol}")

        # 获取指数历史数据，使用新浪接口替代被拦截的东财接口
        client = _get_akshare_client()
        dfs = client.call(
            ak.stock_zh_index_daily,
            symbol=converted_symbol,
            ttl=1200,
        )

        if dfs is None or dfs.empty:
            logger.warning(f"未找到指数历史数据: symbol={symbol} (converted: {converted_symbol})")
            return f"Not Found for {symbol}"

        # 新浪接口返回列: date, open, high, low, close, volume
        # 目标列: 日期, 开盘, 最高, 最低, 收盘, 成交量
        column_mapping = {
            "date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
        }

        # 重命名列
        dfs = dfs.rename(columns=column_mapping)

        # 确保日期列是字符串格式
        if "日期" in dfs.columns:
            dfs["日期"] = pd.to_datetime(dfs["日期"]).dt.strftime("%Y-%m-%d")

        # 支持周线与月线重采样
        if period in ("weekly", "monthly"):
            dfs = AkShareClient._resample_daily_bars(dfs, period)
            if "日期" in dfs.columns:
                dfs["日期"] = pd.to_datetime(dfs["日期"]).dt.strftime("%Y-%m-%d")
        else:
            dfs = dfs.sort_values("日期", ascending=True).reset_index(drop=True)
            if "收盘" in dfs.columns and "涨跌幅" not in dfs.columns:
                prev_close = dfs["收盘"].shift(1)
                dfs["涨跌额"] = (dfs["收盘"] - prev_close).round(2)
                dfs["涨跌幅"] = ((dfs["收盘"] - prev_close) / prev_close * 100).round(2)

        # 添加技术指标（需要先按日期升序排列计算，然后再降序）
        dfs_for_calc = dfs.sort_values("日期", ascending=True).copy()

        # 导入技术指标计算函数
        from ..utils.indicators import add_technical_indicators

        try:
            add_technical_indicators(
                dfs_for_calc,
                close=dfs_for_calc["收盘"],
                low=dfs_for_calc["最低"],
                high=dfs_for_calc["最高"],
            )
        except Exception as e:
            logger.warning(f"计算技术指标失败（可忽略）: {e}")

        dfs = dfs_for_calc.sort_values("日期", ascending=False)

        # 限制返回数量
        dfs = dfs.head(int(limit))

        # 转换为 CSV 格式
        result = dfs.to_csv(index=False, float_format="%.2f").strip()

        logger.info(f"成功获取 {len(dfs)} 条指数历史数据")
        return result

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取指数历史行情失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_individual_fund_flow(
    symbol: str = Field(description="股票代码，如 600519, 000001"),
    market: str = Field("sh", description="股票市场，仅支持: sh(上证), sz(深证)"),
    limit: int = Field(30, description="返回最近N个交易日的数据，默认30条", strict=False),
) -> str:
    """获取个股资金流向历史详情数据。

    从东方财富获取单个股票的历史资金流向数据，包括主力净流入、超大单净流入、
    大单净流入、中单净流入、小单净流入等信息。当东财限流时，自动降级至同花顺个股资金流或分笔大单统计。

    AkShare 接口: ak.stock_individual_fund_flow(stock, market)

    Args:
        symbol: 股票代码（如 600519, 000001）
        market: 股票市场，"sh" 表示上证，"sz" 表示深证
        limit: 返回最近N个交易日的数据，默认30条

    Returns:
        str: CSV 格式的个股资金流向历史数据
    """
    if hasattr(limit, "default"):
        limit = limit.default

    logger.info(f"获取个股资金流向: symbol={symbol}, market={market}, limit={limit}")

    try:
        symbol, market = _normalize_a_stock_symbol_market(symbol, market)

        # 验证市场参数
        valid_markets = ["sh", "sz"]
        if market not in valid_markets:
            raise ValidationException(
                "market", f"必须是 {valid_markets} 之一，得到 '{market}'"
            )

        # 验证股票代码
        if not symbol or not symbol.strip():
            raise ValidationException("symbol", "股票代码不能为空")

        # 获取个股资金流向数据
        client = _get_akshare_client()
        dfs = None
        last_error: Optional[Exception] = None

        if not client._is_source_open("eastmoney"):
            try:
                dfs = client.call(
                    ak.stock_individual_fund_flow,
                    stock=symbol,
                    market=market,
                    ttl=1200,
                )
            except Exception as e:
                last_error = e
                logger.warning("东财个股资金流接口失败 (%s): %s", symbol, e)

        if dfs is not None and not dfs.empty:
            # 按日期降序排序并截断
            if "日期" in dfs.columns:
                dfs = dfs.sort_values("日期", ascending=False)
            dfs = dfs.head(int(limit))
            result = dfs.to_csv(index=False, float_format="%.2f").strip()
            logger.info(f"成功获取 {len(dfs)} 条个股资金流向数据")
            return result

        if last_error is not None:
            fallback = _stock_fund_flow_fallback(symbol=symbol, error=last_error)
            if fallback is not None:
                return fallback
            return f"获取失败: 数据源暂不可用 ({type(last_error).__name__})"

        logger.warning(f"未找到个股资金流向数据: symbol={symbol}, market={market}")
        return f"Not Found for {symbol}"

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取个股资金流向失败: {e}", exc_info=True)
        fallback = _stock_fund_flow_fallback(symbol=symbol, error=e)
        if fallback is not None:
            return fallback
        return f"获取失败: 数据源暂不可用 ({type(e).__name__})"


def _normalize_a_stock_symbol_market(symbol: str, market: object) -> tuple[str, str]:
    """Normalize A-share symbol/market inputs for tool and direct function calls."""
    symbol = Validators.validate_symbol(str(symbol)).lower()

    if symbol.startswith(("sh", "sz")) and len(symbol) >= 8:
        return symbol[2:], symbol[:2]

    if "." in symbol:
        code, suffix = symbol.split(".", 1)
        if suffix in {"sh", "sz"}:
            return code, suffix

    if isinstance(market, str):
        normalized_market = market.strip().lower()
    else:
        normalized_market = ""

    if normalized_market not in {"sh", "sz"}:
        normalized_market = "sh" if symbol.startswith(("5", "6", "9")) else "sz"

    return symbol, normalized_market


def _stock_fund_flow_fallback(symbol: str, error: Exception) -> Optional[str]:
    """Return THS fund-flow or intraday tick-flow summary when Eastmoney source fails."""
    # 1. 优先尝试同花顺个股资金流排行汇总 (即时/3日/5日/10日)
    try:
        client = _get_akshare_client()
        ths_rows = []
        for period_label in ("即时", "3日排行", "5日排行", "10日排行"):
            try:
                ths_df = client.call(
                    ak.stock_fund_flow_individual,
                    symbol=period_label,
                    ttl=600,
                    use_disk_cache=False,
                )
                if ths_df is not None and not ths_df.empty and "股票代码" in ths_df.columns:
                    matched = ths_df[
                        ths_df["股票代码"].astype(str).str.zfill(6) == str(symbol).zfill(6)
                    ].copy()
                    if not matched.empty:
                        matched.insert(0, "统计周期", period_label)
                        ths_rows.append(matched)
            except Exception:
                continue
        if ths_rows:
            merged_ths = pd.concat(ths_rows, ignore_index=True)
            return (
                f"# 个股资金流向降级结果 (股票代码: {symbol}，来源: 同花顺)\n\n"
                + merged_ths.to_csv(index=False).strip()
            )
    except Exception as ths_err:
        logger.debug("同花顺个股资金流降级查询未命中: %s", ths_err)

    # 2. 降级到腾讯分笔成交买卖盘统计
    try:
        from .stock import stock_tick_data

        tick_summary = stock_tick_data(symbol=symbol, filter_type="summary", min_volume=500)
        if tick_summary.startswith("获取失败") or tick_summary.startswith("Not Found"):
            return None
        return (
            f"# 个股资金流向降级结果 (股票代码: {symbol})\n\n"
            "历史资金流向数据源暂不可用，已返回最近交易日分笔成交买卖盘统计，"
            "可作为当日资金流强弱的替代参考。\n\n"
            f"原始错误类型: {type(error).__name__}\n\n"
            f"{tick_summary}"
        )
    except Exception as fallback_error:
        logger.warning("个股资金流向降级查询失败: %s", fallback_error, exc_info=True)
        return None


def stock_zt_pool_zbgc_em(
    date: str = Field(
        "", description="交易日日期(可选)，默认为最近的交易日，格式: 20251231"
    ),
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
) -> str:
    """获取 A 股炸板股池数据。

    从东方财富获取炸板股池数据，包含曾经涨停但后来打开涨停的股票。
    炸板股池数据包括股票代码、名称、涨跌幅、炸板次数、涨停统计等信息。

    注意：该接口只能获取最近 30 个交易日的数据。

    Args:
        date: 交易日日期（可选），默认为最近的交易日，格式: YYYYMMDD
        limit: 返回数量，范围 30-100

    Returns:
        str: CSV 格式的炸板股池数据

    Raises:
        ValidationException: 当参数验证失败时

    Example:
        >>> result = stock_zt_pool_zbgc_em("20240115", 50)
        >>> print(result)
        共10只炸板股
        代码,名称,涨跌幅,最新价,炸板次数,...
    """
    logger.info(f"获取炸板股池: date={date}, limit={limit}")

    try:
        # 验证参数
        Validators.validate_limit(limit, min_val=30, max_val=100)

        # 如果没有指定日期，使用最近的交易日
        if not date:
            date = _recent_trade_date().strftime("%Y%m%d")
            logger.debug(f"使用最近交易日: {date}")
        else:
            # 验证日期格式
            Validators.validate_date(date)

        # 获取炸板股池数据
        client = _get_akshare_client()
        is_historical = date < datetime.now().strftime("%Y%m%d")
        pool_ttl = 86400 * 30 if is_historical else 1200
        try:
            dfs = client.call(
                ak.stock_zt_pool_zbgc_em,
                date=date,
                ttl=pool_ttl,
                use_disk_cache=is_historical,
            )
        except Exception as e:
            logger.warning("东财炸板池接口失败: %s", e)
            return f"获取 {date} 的炸板股池失败: {e}"

        if dfs is None or dfs.empty:
            logger.warning(f"未找到炸板股池数据: date={date}")
            return f"未找到 {date} 的炸板股池数据"

        cnt = len(dfs)
        if "成交额" in dfs.columns:
            dfs.sort_values("成交额", ascending=False, inplace=True)

        dfs = dfs.head(int(limit))
        desc = f"交易日: {date}，来源: 东方财富\n共{cnt}只炸板股\n"
        result = desc + dfs.to_csv(index=False, float_format="%.2f").strip()
        logger.info(f"成功获取 {len(dfs)} 只炸板股数据")
        return result

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取炸板股池失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_sector_fund_flow_rank(
    sector_type: str = Field(
        "industry",
        description="板块类型: industry(行业板块资金流), concept(概念板块资金流), ths_summary(同花顺行业总览), sina_sector(新浪行业行情)",
    ),
    period: str = Field(
        "即时",
        description="统计周期: 即时, 3日排行, 5日排行, 10日排行, 20日排行",
    ),
    limit: int = Field(30, description="返回数量(int, 5-100)", strict=False),
) -> str:
    """获取A股行业板块与概念板块资金流向及涨跌排行榜（多源防封版）。

    优先使用同花顺(10jqka)与新浪财经(Sina)板块资金流及行情接口，规避东方财富高频限流；
    当需要时自动多源降级，包含板块涨跌幅、资金净流入、领涨股等信息。
    """
    if hasattr(sector_type, "default"):
        sector_type = sector_type.default
    if hasattr(period, "default"):
        period = period.default
    if hasattr(limit, "default"):
        limit = limit.default

    sector_type = str(sector_type or "industry").strip().lower()
    period = str(period or "即时").strip()
    limit_val = max(5, min(int(limit), 100))

    valid_periods = {"即时", "3日排行", "5日排行", "10日排行", "20日排行"}
    if period not in valid_periods:
        period = "即时"

    client = _get_akshare_client()
    dfs = None
    source_label = ""

    # 1. 根据请求类型优先走同花顺/新浪（高可用免东财限流）
    if sector_type == "concept":
        try:
            dfs = client.call(ak.stock_fund_flow_concept, symbol=period, ttl=300, use_disk_cache=False)
            source_label = f"同花顺概念资金流({period})"
        except Exception as e:
            logger.warning("同花顺概念资金流获取失败: %s", e)
    elif sector_type == "ths_summary":
        try:
            dfs = client.call(ak.stock_board_industry_summary_ths, ttl=300, use_disk_cache=False)
            source_label = "同花顺行业板块总览"
        except Exception as e:
            logger.warning("同花顺行业板块总览获取失败: %s", e)
    elif sector_type == "sina_sector":
        try:
            dfs = client.call(ak.stock_sector_spot, indicator="新浪行业", ttl=300, use_disk_cache=False)
            source_label = "新浪行业板块行情"
        except Exception as e:
            logger.warning("新浪行业板块行情获取失败: %s", e)
    else:
        # 默认 industry
        try:
            dfs = client.call(ak.stock_fund_flow_industry, symbol=period, ttl=300, use_disk_cache=False)
            source_label = f"同花顺行业资金流({period})"
        except Exception as e:
            logger.warning("同花顺行业资金流获取失败: %s", e)

    # 2. 降级备选链：同花顺行业总览 -> 新浪行业热点 -> 东方财富板块资金流
    if dfs is None or dfs.empty:
        try:
            dfs = client.call(ak.stock_board_industry_summary_ths, ttl=300, use_disk_cache=False)
            source_label = "同花顺行业板块总览(降级)"
        except Exception as e:
            logger.warning("同花顺行业总览降级失败: %s", e)

    if dfs is None or dfs.empty:
        try:
            dfs = client.call(ak.stock_sector_spot, indicator="新浪行业", ttl=300, use_disk_cache=False)
            if dfs is not None and not dfs.empty and "涨跌幅" in dfs.columns:
                dfs["涨跌幅"] = pd.to_numeric(dfs["涨跌幅"], errors="coerce")
                dfs = dfs.sort_values("涨跌幅", ascending=False)
            source_label = "新浪行业板块行情(降级)"
        except Exception as e:
            logger.warning("新浪行业板块行情降级失败: %s", e)

    if (dfs is None or dfs.empty) and not client._is_source_open("eastmoney"):
        try:
            em_sector = "概念资金流" if sector_type == "concept" else "行业资金流"
            em_indicator = "今日" if period == "即时" else ("5日" if "5" in period else "10日")
            dfs = client.call(
                ak.stock_sector_fund_flow_rank,
                indicator=em_indicator,
                sector_type=em_sector,
                ttl=300,
                use_disk_cache=False,
            )
            source_label = f"东方财富{em_sector}({em_indicator})"
        except Exception as e:
            logger.warning("东方财富板块资金流降级失败: %s", e)

    if dfs is None or dfs.empty:
        return "获取失败: 板块资金流数据源暂不可用"

    dfs = dfs.head(limit_val)
    header = f"# A股板块资金流与热点排行 (来源: {source_label}，共返回 {len(dfs)} 条)\n"
    return header + dfs.to_csv(index=False, float_format="%.2f").strip()

