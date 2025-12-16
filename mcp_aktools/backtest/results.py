"""回测结果模块。

该模块定义回测结果数据类，用于存储：
- 交易记录
- 绩效指标
- 权益曲线
"""

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .config import BacktestConfig

from ..models.trade import TradeRecord


@dataclass
class BacktestResult:
    """回测结果数据类。

    存储单次回测的所有交易记录和绩效指标。

    Attributes:
        config: 回测配置对象
        trades: 交易记录列表
        final_capital: 最终资金
        benchmark_return: 基准收益率（买入持有策略）
        strategy_return: 策略收益率
        max_drawdown: 最大回撤
        volatility: 波动率
        sharpe_ratio: 夏普比率（使用3%无风险利率）
        win_rate: 胜率
        profit_factor: 盈亏比
        total_trades: 总交易次数
        equity_curve: 权益曲线（每日资产净值列表）
    """

    # 配置和交易记录
    config: "BacktestConfig"
    trades: List[TradeRecord] = field(default_factory=list)

    # 资金相关
    final_capital: float = 0.0

    # 绩效指标
    benchmark_return: float = 0.0
    strategy_return: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0

    # 权益曲线
    equity_curve: List[float] = field(default_factory=list)


    def get_summary(self) -> str:
        """获取回测结果摘要。

        Returns:
            格式化的回测结果摘要字符串
        """
        summary_lines = [
            "=" * 50,
            "回测结果摘要",
            "=" * 50,
            f"股票代码: {self.config.stock_code}",
            f"回测区间: {self.config.start_date} ~ {self.config.end_date}",
            f"初始资金: {self.config.initial_capital:,.2f} 元",
            f"最终资金: {self.final_capital:,.2f} 元",
            "-" * 50,
            "绩效指标:",
            f"  策略收益率: {self.strategy_return * 100:.2f}%",
            f"  基准收益率: {self.benchmark_return * 100:.2f}%",
            f"  最大回撤: {self.max_drawdown * 100:.2f}%",
            f"  波动率: {self.volatility * 100:.2f}%",
            f"  夏普比率: {self.sharpe_ratio:.2f}",
            f"  胜率: {self.win_rate * 100:.2f}%",
            f"  盈亏比: {self.profit_factor:.2f}",
            "-" * 50,
            f"总交易次数: {self.total_trades}",
            "=" * 50,
        ]
        return "\n".join(summary_lines)

    def to_json(self) -> str:
        """序列化为 JSON 字符串。

        Returns:
            JSON 格式的字符串
        """
        # 将交易记录转换为字典列表
        trades_data = []
        for trade in self.trades:
            trades_data.append({
                "timestamp": trade.timestamp,
                "action": trade.action,
                "quantity": trade.quantity,
                "price": trade.price,
                "commission": trade.commission,
                "cash_after": trade.cash_after,
                "position_after": trade.position_after,
            })

        data = {
            "config": json.loads(self.config.to_json()),
            "trades": trades_data,
            "final_capital": self.final_capital,
            "benchmark_return": self.benchmark_return,
            "strategy_return": self.strategy_return,
            "max_drawdown": self.max_drawdown,
            "volatility": self.volatility,
            "sharpe_ratio": self.sharpe_ratio,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_trades": self.total_trades,
            "equity_curve": self.equity_curve,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "BacktestResult":
        """从 JSON 字符串反序列化。

        Args:
            json_str: JSON 格式的字符串

        Returns:
            BacktestResult 实例
        """
        # 延迟导入避免循环依赖
        from .config import BacktestConfig

        data = json.loads(json_str)

        # 反序列化配置
        config = BacktestConfig.from_json(json.dumps(data["config"]))

        # 反序列化交易记录
        trades = []
        for trade_data in data.get("trades", []):
            trades.append(TradeRecord(
                timestamp=trade_data["timestamp"],
                action=trade_data["action"],
                quantity=trade_data["quantity"],
                price=trade_data["price"],
                commission=trade_data["commission"],
                cash_after=trade_data["cash_after"],
                position_after=trade_data["position_after"],
            ))

        return cls(
            config=config,
            trades=trades,
            final_capital=data.get("final_capital", 0.0),
            benchmark_return=data.get("benchmark_return", 0.0),
            strategy_return=data.get("strategy_return", 0.0),
            max_drawdown=data.get("max_drawdown", 0.0),
            volatility=data.get("volatility", 0.0),
            sharpe_ratio=data.get("sharpe_ratio", 0.0),
            win_rate=data.get("win_rate", 0.0),
            profit_factor=data.get("profit_factor", 0.0),
            total_trades=data.get("total_trades", 0),
            equity_curve=data.get("equity_curve", []),
        )
