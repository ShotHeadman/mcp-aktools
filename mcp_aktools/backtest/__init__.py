"""回测模块。

该模块提供量化回测系统的核心功能，包括：
- 回测配置管理
- 回测引擎
- 策略基类
- 信号计算
- 自定义指标
- 结果分析和格式化

注意：各组件将在后续任务中实现，当前为占位导入。
"""

# 导出列表（组件实现后将自动可用）
__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "Strategy",
    "SignalCalculator",
    "CustomIndicator",
    "BacktestResult",
    "BacktestFormatter",
]

# 延迟导入，避免在组件未实现时报错
def __getattr__(name: str):
    """延迟导入机制，支持按需加载组件。"""
    if name == "BacktestConfig":
        from mcp_aktools.backtest.config import BacktestConfig
        return BacktestConfig
    elif name == "BacktestEngine":
        from mcp_aktools.backtest.engine import BacktestEngine
        return BacktestEngine
    elif name == "Strategy":
        from mcp_aktools.backtest.strategy import Strategy
        return Strategy
    elif name == "SignalCalculator":
        from mcp_aktools.backtest.signals import SignalCalculator
        return SignalCalculator
    elif name == "CustomIndicator":
        from mcp_aktools.backtest.indicators import CustomIndicator
        return CustomIndicator
    elif name == "BacktestResult":
        from mcp_aktools.backtest.results import BacktestResult
        return BacktestResult
    elif name == "BacktestFormatter":
        from mcp_aktools.backtest.formatter import BacktestFormatter
        return BacktestFormatter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
