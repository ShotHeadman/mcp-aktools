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

        # 获取涨停股池数据
        client = _get_akshare_client()
        dfs = client.call(ak.stock_zt_pool_em, date=date, ttl=1200)

        if dfs is None or dfs.empty:
            logger.warning(f"未找到涨停股池数据: date={date}")
            return f"未找到 {date} 的涨停股池数据"

        cnt = len(dfs)

        # 删除不需要的列
        try:
            dfs.drop(
                columns=["序号", "流通市值", "总市值"], inplace=True, errors="ignore"
            )
        except Exception as e:
            logger.debug(f"删除列时出错（可忽略）: {e}")

        # 按成交额降序排序
        dfs.sort_values("成交额", ascending=False, inplace=True)

        # 限制返回数量
        dfs = dfs.head(int(limit))

        # 转换为 CSV 格式
        desc = f"共{cnt}只涨停股\n"
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

    Args:
        date: 交易日日期（可选），默认为最近的交易日，格式: YYYYMMDD
        limit: 返回数量，范围 30-100

    Returns:
        str: CSV 格式的强势股池数据

    Raises:
        ValidationException: 当参数验证失败时

    Example:
        >>> result = stock_zt_pool_strong_em("20240115", 50)
        >>> print(result)
        代码,名称,涨跌幅,最新价,成交额,...
    """
    logger.info(f"获取强势股池: date={date}, limit={limit}")

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

        # 获取强势股池数据
        client = _get_akshare_client()
        dfs = client.call(ak.stock_zt_pool_strong_em, date=date, ttl=1200)

        if dfs is None or dfs.empty:
            logger.warning(f"未找到强势股池数据: date={date}")
            return f"未找到 {date} 的强势股池数据"

        # 删除不需要的列
        try:
            dfs.drop(
                columns=["序号", "流通市值", "总市值"], inplace=True, errors="ignore"
            )
        except Exception as e:
            logger.debug(f"删除列时出错（可忽略）: {e}")

        # 按成交额降序排序
        dfs.sort_values("成交额", ascending=False, inplace=True)

        # 限制返回数量
        dfs = dfs.head(int(limit))

        # 转换为 CSV 格式
        result = dfs.to_csv(index=False, float_format="%.2f").strip()

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


def stock_sector_fund_flow_rank(
    days: str = Field(
        "今日",
        description="天数，仅支持: {'今日','5日','10日'}，如果需要获取今日数据，请确保是交易日",
    ),
    cate: str = Field(
        "行业资金流", description="仅支持: {'行业资金流','概念资金流','地域资金流'}"
    ),
) -> str:
    """获取中国A股市场(上证、深证)的行业资金流向数据。

    显示各行业/概念/地域的资金流入流出情况。

    Args:
        days: 天数，仅支持: 今日/5日/10日
              注意：如果需要获取今日数据，请确保是交易日
        cate: 分类，仅支持: 行业资金流/概念资金流/地域资金流

    Returns:
        str: CSV 格式的资金流向数据（包含涨幅最高和最低的各20个）

    Raises:
        ValidationException: 当参数验证失败时

    Example:
        >>> result = stock_sector_fund_flow_rank("今日", "行业资金流")
        >>> print(result)
        序号,名称,今日涨跌幅,今日主力净流入,今日超大单净流入,...
    """
    logger.info(f"获取板块资金流: days={days}, cate={cate}")

    try:
        # 验证参数
        valid_days = ["今日", "5日", "10日"]
        valid_cate = ["行业资金流", "概念资金流", "地域资金流"]

        if days not in valid_days:
            raise ValidationException(
                "days", f"必须是 {valid_days} 之一，得到 '{days}'"
            )

        if cate not in valid_cate:
            raise ValidationException(
                "cate", f"必须是 {valid_cate} 之一，得到 '{cate}'"
            )

        # 获取资金流数据
        client = _get_akshare_client()
        dfs = client.call(
            ak.stock_sector_fund_flow_rank, indicator=days, sector_type=cate, ttl=1200
        )

        if dfs is None or dfs.empty:
            logger.warning(f"未找到资金流数据: days={days}, cate={cate}")
            return "获取数据失败"

        # 按今日涨跌幅排序
        try:
            dfs.sort_values("今日涨跌幅", ascending=False, inplace=True)
            dfs.drop(columns=["序号"], inplace=True, errors="ignore")
        except Exception as e:
            logger.debug(f"排序或删除列时出错（可忽略）: {e}")

        # 返回涨幅最高和最低的各20个
        try:
            result_df = pd.concat([dfs.head(20), dfs.tail(20)])
            result = result_df.to_csv(index=False, float_format="%.2f").strip()

            logger.info(f"成功获取 {len(result_df)} 条资金流数据")
            return result

        except Exception as exc:
            logger.error(f"处理资金流数据失败: {exc}", exc_info=True)
            return str(exc)

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取资金流数据失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"


def stock_zh_a_spot_em(
    limit: int = Field(50, description="返回数量(int,30-100)", strict=False),
) -> str:
    """获取 A 股实时行情数据。

    从东方财富获取全市场 A 股实时行情数据，包括股票代码、名称、
    最新价、涨跌幅、成交量、成交额等信息。

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

        # 获取实时行情数据，使用 60 秒 TTL 缓存
        client = _get_akshare_client()
        dfs = client.call(ak.stock_zh_a_spot_em, ttl=60)

        if dfs is None or dfs.empty:
            logger.warning("未获取到A股实时行情数据")
            return "获取实时行情数据失败"

        # 选择关键列（根据东财接口返回的列名）
        # 东财接口返回列: 序号, 代码, 名称, 最新价, 涨跌幅, 涨跌额, 成交量, 成交额, 振幅, 最高, 最低, 今开, 昨收, 量比, 换手率, 市盈率-动态, 市净率, 总市值, 流通市值, 涨速, 5分钟涨跌, 60日涨跌幅, 年初至今涨跌幅
        columns = ['代码', '名称', '最新价', '涨跌幅', '涨跌额', '成交量', '成交额', '振幅', '最高', '最低', '今开', '昨收', '换手率']
        
        # 只选择存在的列
        available_columns = [col for col in columns if col in dfs.columns]
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

        # 获取炸板股池数据，使用 1200 秒 TTL 缓存
        client = _get_akshare_client()
        dfs = client.call(ak.stock_zt_pool_zbgc_em, date=date, ttl=1200)

        if dfs is None or dfs.empty:
            logger.warning(f"未找到炸板股池数据: date={date}")
            return f"未找到 {date} 的炸板股池数据"

        cnt = len(dfs)

        # 删除不需要的列
        try:
            dfs.drop(
                columns=["序号", "流通市值", "总市值"], inplace=True, errors="ignore"
            )
        except Exception as e:
            logger.debug(f"删除列时出错（可忽略）: {e}")

        # 按成交额降序排序
        dfs.sort_values("成交额", ascending=False, inplace=True)

        # 限制返回数量
        dfs = dfs.head(int(limit))

        # 转换为 CSV 格式
        desc = f"共{cnt}只炸板股\n"
        result = desc + dfs.to_csv(index=False, float_format="%.2f").strip()

        logger.info(f"成功获取 {len(dfs)} 只炸板股数据")
        return result

    except ValidationException as e:
        logger.error(f"参数验证失败: {e}")
        return f"参数错误: {e.message}"
    except Exception as e:
        logger.error(f"获取炸板股池失败: {e}", exc_info=True)
        return f"获取失败: {str(e)}"
