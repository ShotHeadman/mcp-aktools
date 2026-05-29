"""回测相关工具函数。

此模块包含所有回测相关的 MCP 工具函数，包括：
- 运行回测
- 策略对比
- 注册自定义指标
- 评估信号状态
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from ..backtest.config import BacktestConfig
from ..backtest.engine import BacktestEngine
from ..backtest.formatter import BacktestFormatter
from ..backtest.results import BacktestResult
from ..backtest.strategy import Strategy
from ..exceptions import BacktestError, ConfigurationError, StorageError
from ..models.indicator import CustomIndicator
from ..models.trade import OrderType, PriceType
from ..storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/backtest.db"


def _get_storage() -> SQLiteStorage:
    """获取 SQLite 存储实例。"""
    return SQLiteStorage(DEFAULT_DB_PATH)


def _parse_strategy_code(strategy_code: str) -> type:
    """解析策略代码字符串，返回策略类。"""
    exec_globals = {"Strategy": Strategy, "PriceType": PriceType, "OrderType": OrderType}
    exec_locals = {}
    try:
        exec(strategy_code, exec_globals, exec_locals)
        strategy_class = None
        for name, obj in exec_locals.items():
            if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                strategy_class = obj
                break
        if strategy_class is None:
            raise BacktestError("strategy_parsing", "策略代码中未找到继承自 Strategy 的类")
        return strategy_class
    except SyntaxError as e:
        raise BacktestError("strategy_parsing", f"策略代码语法错误: {e}") from e
    except Exception as e:
        raise BacktestError("strategy_parsing", f"策略代码解析失败: {e}") from e



def run_backtest(
    stock_code: str, start_date: str, end_date: str, strategy_code: str,
    initial_capital: float = 1000000.0, frequency: str = "d",
    commission_rate: float = 0.000854, slippage_min: float = 0.0001,
    slippage_max: float = 0.003, stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None, enable_pyramiding: bool = False,
    max_pyramid_layers: int = 3, pyramid_scale_factor: float = 0.5,
    realtime_indicator: bool = False, signal_priority: str = "buy_first",
    random_seed: Optional[int] = None,
) -> str:
    """运行回测工具。
    
    Args:
        stock_code: 股票代码，格式: sh.XXXXXX 或 sz.XXXXXX
        start_date: 开始日期，格式: YYYY-MM-DD
        end_date: 结束日期，格式: YYYY-MM-DD
        strategy_code: 策略代码，必须定义继承自 Strategy 的类
        initial_capital: 初始资金
        frequency: K线周期
        commission_rate: 手续费率
        slippage_min: 滑点最小值
        slippage_max: 滑点最大值
        stop_loss: 止损比例
        take_profit: 止盈比例
        enable_pyramiding: 是否启用金字塔加仓
        max_pyramid_layers: 最大加仓层数
        pyramid_scale_factor: 加仓比例递减因子
        realtime_indicator: 是否使用实时指标模式
        signal_priority: 信号优先级
        random_seed: 随机种子，设置后滑点可复现（便于复现与策略对比），默认 None

    Returns:
        格式化的回测结果字符串
    """
    try:
        logger.info(f"开始回测: 股票={stock_code}, 区间={start_date}~{end_date}")
        config = BacktestConfig(
            stock_code=stock_code, start_date=start_date, end_date=end_date,
            initial_capital=initial_capital, frequency=frequency,
            commission_rate=commission_rate, slippage_min=slippage_min,
            slippage_max=slippage_max, stop_loss=stop_loss,
            take_profit=take_profit, enable_pyramiding=enable_pyramiding,
            max_pyramid_layers=max_pyramid_layers,
            pyramid_scale_factor=pyramid_scale_factor,
            realtime_indicator=realtime_indicator, signal_priority=signal_priority,
            random_seed=random_seed,
        )
        strategy_class = _parse_strategy_code(strategy_code)
        engine = BacktestEngine(config)
        result = engine.run(strategy_class)
        storage = _get_storage()
        result_id = storage.save_backtest_result(result)
        logger.info(f"回测结果已保存，ID={result_id}")
        formatter = BacktestFormatter(result, use_color=False)
        summary = formatter.format_summary()
        summary += f"\n\n回测结果 ID: {result_id}"
        return summary
    except ConfigurationError as e:
        logger.error(f"配置错误: {e}")
        return f"配置错误: {e.message}"
    except BacktestError as e:
        logger.error(f"回测错误: {e}")
        return f"回测错误: {e.message}"
    except Exception as e:
        logger.error(f"回测失败: {e}", exc_info=True)
        return f"回测失败: {str(e)}"



def compare_strategies(result_ids: str) -> str:
    """策略对比工具。
    
    Args:
        result_ids: 逗号分隔的回测结果 ID 列表，如: 1,2,3
    
    Returns:
        格式化的对比结果字符串
    """
    try:
        id_list = [int(id_str.strip()) for id_str in result_ids.split(",")]
        if len(id_list) < 2:
            return "错误: 至少需要 2 个回测结果 ID 进行对比"
        logger.info(f"对比策略: IDs={id_list}")
        storage = _get_storage()
        results: List[BacktestResult] = []
        for result_id in id_list:
            result = storage.get_backtest_result(result_id)
            if result is None:
                return f"错误: 未找到回测结果 ID={result_id}"
            results.append(result)
        comparison = BacktestFormatter.format_comparison(results, use_color=False)
        return comparison
    except ValueError as e:
        logger.error(f"参数解析错误: {e}")
        return f"参数错误: result_ids 格式不正确，应为逗号分隔的数字，如 '1,2,3'"
    except StorageError as e:
        logger.error(f"存储错误: {e}")
        return f"存储错误: {e.message}"
    except Exception as e:
        logger.error(f"策略对比失败: {e}", exc_info=True)
        return f"策略对比失败: {str(e)}"


def register_indicator(name: str, description: str, function_code: str) -> str:
    """注册自定义指标工具。
    
    Args:
        name: 指标名称
        description: 指标描述
        function_code: 计算指标的 Python 代码，需要设置 result 变量
    
    Returns:
        注册结果字符串
    """
    import pandas as pd
    try:
        logger.info(f"注册自定义指标: name={name}")
        if not name or not name.strip():
            return "错误: 指标名称不能为空"
        name = name.strip()
        if not function_code or not function_code.strip():
            return "错误: 函数代码不能为空"
        indicator = CustomIndicator(name=name, description=description or "", function_code=function_code)
        test_df = pd.DataFrame({
            "open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5],
            "low": [0.5, 1.5, 2.5], "close": [1.2, 2.2, 3.2], "volume": [100, 200, 300],
        })
        try:
            result = indicator.calculate(test_df)
            if result is None:
                return "错误: 函数代码未设置 result 变量"
        except Exception as e:
            return f"错误: 函数代码执行失败 - {str(e)}"
        storage = _get_storage()
        storage.save_custom_indicator(indicator)
        return f"成功注册自定义指标: {name}\n描述: {description}"
    except StorageError as e:
        logger.error(f"存储错误: {e}")
        return f"存储错误: {e.message}"
    except Exception as e:
        logger.error(f"注册指标失败: {e}", exc_info=True)
        return f"注册指标失败: {str(e)}"



def evaluate_signals(stock_code: str, date: str = "") -> str:
    """评估技术信号工具。
    
    评估指定股票在特定日期的技术信号状态，包括 MACD、KDJ、RSI、BOLL 等。
    
    Args:
        stock_code: 股票代码，格式: sh.XXXXXX 或 sz.XXXXXX
        date: 评估日期，格式: YYYY-MM-DD，默认为最近交易日
    
    Returns:
        格式化的信号状态字符串
    """
    try:
        logger.info(f"评估技术信号: 股票={stock_code}, 日期={date or '最近交易日'}")
        
        # 计算日期范围（获取足够的历史数据用于计算指标）
        if date:
            end_date = datetime.strptime(date, "%Y-%m-%d")
        else:
            end_date = datetime.now()
        
        # 向前取约180个自然日的数据（确保有足够交易日用于计算指标）
        start_date = end_date - timedelta(days=180)
        
        # 创建配置
        config = BacktestConfig(
            stock_code=stock_code,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            initial_capital=1000000.0,
            frequency="d",
        )
        
        # 创建引擎并加载数据
        engine = BacktestEngine(config)
        engine.load_data()
        
        if engine.data is None or engine.data.empty:
            return f"错误: 无法获取股票 {stock_code} 的数据"
        
        # 获取最后一根K线的索引
        last_index = len(engine.data) - 1
        last_bar = engine.data.iloc[last_index]
        
        # 获取信号计算器
        signal_calc = engine.signal_calculator
        if signal_calc is None:
            return "错误: 信号计算器初始化失败"
        
        # 评估各种信号
        signals = []
        
        # MACD 信号
        macd_golden = signal_calc.macd_golden_cross(last_index)
        macd_death = signal_calc.macd_death_cross(last_index)
        macd_golden_strong = signal_calc.macd_golden_cross_strong(
            last_index, frequency=config.frequency
        )
        macd_death_strong = signal_calc.macd_death_cross_strong(
            last_index, frequency=config.frequency
        )
        
        signals.append("## MACD 信号")
        signals.append(f"- 金叉: {'✅ 是' if macd_golden else '❌ 否'}")
        signals.append(f"- 死叉: {'✅ 是' if macd_death else '❌ 否'}")
        signals.append(f"- 强金叉: {'✅ 是' if macd_golden_strong else '❌ 否'}")
        signals.append(f"- 强死叉: {'✅ 是' if macd_death_strong else '❌ 否'}")
        
        # 获取 MACD 指标值
        dif_col, dea_col = signal_calc._get_macd_columns()
        if dif_col and dea_col:
            dif_val = engine.data[dif_col].iloc[last_index]
            dea_val = engine.data[dea_col].iloc[last_index]
            signals.append(f"- DIF: {dif_val:.4f}")
            signals.append(f"- DEA: {dea_val:.4f}")
        
        # KDJ 信号
        kdj_golden = signal_calc.kdj_golden_cross(last_index)
        kdj_death = signal_calc.kdj_death_cross(last_index)
        
        signals.append("\n## KDJ 信号")
        signals.append(f"- 金叉: {'✅ 是' if kdj_golden else '❌ 否'}")
        signals.append(f"- 死叉: {'✅ 是' if kdj_death else '❌ 否'}")
        
        # 获取 KDJ 指标值
        k_col, d_col = signal_calc._get_kdj_columns()
        if k_col and d_col:
            k_val = engine.data[k_col].iloc[last_index]
            d_val = engine.data[d_col].iloc[last_index]
            signals.append(f"- K: {k_val:.2f}")
            signals.append(f"- D: {d_val:.2f}")
            if "J" in engine.data.columns:
                j_val = engine.data["J"].iloc[last_index]
                signals.append(f"- J: {j_val:.2f}")
        
        # RSI 信号
        rsi_overbought = signal_calc.rsi_overbought(last_index)
        rsi_oversold = signal_calc.rsi_oversold(last_index)
        
        signals.append("\n## RSI 信号")
        signals.append(f"- 超买 (>70): {'✅ 是' if rsi_overbought else '❌ 否'}")
        signals.append(f"- 超卖 (<30): {'✅ 是' if rsi_oversold else '❌ 否'}")
        
        # 获取 RSI 指标值
        rsi_col = signal_calc._get_rsi_column()
        if rsi_col:
            rsi_val = engine.data[rsi_col].iloc[last_index]
            signals.append(f"- RSI: {rsi_val:.2f}")
        
        # BOLL 信号
        boll_upper = signal_calc.boll_upper_break(last_index)
        boll_lower = signal_calc.boll_lower_break(last_index)
        
        signals.append("\n## BOLL 信号")
        signals.append(f"- 上轨突破: {'✅ 是' if boll_upper else '❌ 否'}")
        signals.append(f"- 下轨突破: {'✅ 是' if boll_lower else '❌ 否'}")
        
        # 获取 BOLL 指标值
        bbl_col, bbu_col = signal_calc._get_boll_columns()
        if bbl_col and bbu_col:
            bbl_val = engine.data[bbl_col].iloc[last_index]
            bbu_val = engine.data[bbu_col].iloc[last_index]
            close_val = engine.data["close"].iloc[last_index]
            signals.append(f"- 上轨: {bbu_val:.2f}")
            signals.append(f"- 收盘价: {close_val:.2f}")
            signals.append(f"- 下轨: {bbl_val:.2f}")
        
        # 添加日期信息
        eval_date = last_bar.get("date")
        if eval_date is not None:
            if hasattr(eval_date, "strftime"):
                eval_date_str = eval_date.strftime("%Y-%m-%d")
            else:
                eval_date_str = str(eval_date)
        else:
            eval_date_str = "未知"
        
        header = f"# {stock_code} 技术信号评估\n评估日期: {eval_date_str}\n\n"
        
        return header + "\n".join(signals)
        
    except ConfigurationError as e:
        logger.error(f"配置错误: {e}")
        return f"配置错误: {e.message}"
    except BacktestError as e:
        logger.error(f"回测错误: {e}")
        return f"回测错误: {e.message}"
    except Exception as e:
        logger.error(f"评估信号失败: {e}", exc_info=True)
        return f"评估信号失败: {str(e)}"
