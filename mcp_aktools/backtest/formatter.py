"""结果格式化模块。

该模块实现回测结果格式化器，用于：
- 格式化单个回测结果摘要
- 格式化多个回测结果对比
- 格式化交易记录列表
"""

import os
import re
import unicodedata
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .results import BacktestResult


def _str_width(s: str) -> int:
    """计算字符串的显示宽度（考虑中文字符占2个宽度）。
    
    Args:
        s: 输入字符串
        
    Returns:
        字符串的显示宽度
    """
    width = 0
    # 移除ANSI颜色代码
    s = re.sub(r'\033\[[0-9;]*m', '', s)
    for char in s:
        if unicodedata.east_asian_width(char) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def _pad_str(s: str, width: int, align: str = 'left') -> str:
    """填充字符串到指定宽度（考虑中文字符）。
    
    Args:
        s: 输入字符串
        width: 目标宽度
        align: 对齐方式 ('left', 'right', 'center')
        
    Returns:
        填充后的字符串
    """
    current_width = _str_width(s)
    padding = width - current_width
    if padding <= 0:
        return s
    if align == 'right':
        return ' ' * padding + s
    elif align == 'center':
        left = padding // 2
        right = padding - left
        return ' ' * left + s + ' ' * right
    else:  # left
        return s + ' ' * padding


class BacktestFormatter:
    """回测结果格式化器。
    
    提供回测结果的格式化输出功能，包括：
    - 颜色标注（正值绿色，负值红色）
    - 数值格式化（百分比、货币）
    - 摘要和交易记录格式化
    - 多策略对比格式化
    """

    # ANSI 颜色代码
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def __init__(
        self, 
        result: Optional["BacktestResult"] = None, 
        use_color: bool = True
    ):
        """初始化格式化器。
        
        Args:
            result: 回测结果对象（可选）
            use_color: 是否使用颜色输出
        """
        self.result = result
        self.use_color = use_color and self._supports_color()

    @staticmethod
    def _supports_color() -> bool:
        """检查终端是否支持颜色输出。
        
        Returns:
            是否支持颜色
        """
        if not hasattr(os, "isatty") or not os.isatty(1):
            return False
        term = os.environ.get("TERM", "")
        if term == "dumb":
            return False
        return True

    def colorize(self, value: float, format_type: str = "percent") -> str:
        """为数值添加颜色标注。
        
        Args:
            value: 数值
            format_type: 格式类型 ('percent', 'currency', 'number')
            
        Returns:
            带颜色的格式化字符串
        """
        if format_type == "percent":
            formatted = self.format_percent(value)
        elif format_type == "currency":
            formatted = self.format_currency(value)
        else:
            formatted = f"{value:.2f}"

        if not self.use_color:
            return formatted

        if value > 0:
            return f"{self.GREEN}{formatted}{self.RESET}"
        if value < 0:
            return f"{self.RED}{formatted}{self.RESET}"
        return formatted

    @staticmethod
    def format_percent(value: float) -> str:
        """格式化百分比数值。
        
        Args:
            value: 小数形式的百分比值
            
        Returns:
            格式化的百分比字符串
        """
        return f"{value * 100:.2f}%"

    @staticmethod
    def format_currency(value: float) -> str:
        """格式化货币数值。
        
        Args:
            value: 货币数值
            
        Returns:
            格式化的货币字符串
        """
        return f"{value:,.2f}"

    def format_summary(self) -> str:
        """格式化单个回测结果摘要。
        
        Returns:
            格式化的摘要字符串
        """
        if self.result is None:
            return "无回测结果"

        r = self.result
        lines = []

        # 标题
        title = f"回测结果: {r.config.stock_code}"
        if self.use_color:
            title = f"{self.BOLD}{title}{self.RESET}"
        lines.append(title)
        lines.append("=" * 50)

        # 基本信息
        lines.append(f"回测区间: {r.config.start_date} ~ {r.config.end_date}")
        lines.append(f"K线周期: {r.config.frequency}")
        lines.append(f"初始资金: {self.format_currency(r.config.initial_capital)}")
        lines.append(f"最终资金: {self.format_currency(r.final_capital)}")
        lines.append("")

        # 收益指标
        lines.append("收益指标:")
        excess_return = r.strategy_return - r.benchmark_return
        lines.append(f"  基准收益率: {self.colorize(r.benchmark_return, 'percent')}")
        lines.append(f"  策略收益率: {self.colorize(r.strategy_return, 'percent')}")
        lines.append(f"  超额收益:   {self.colorize(excess_return, 'percent')}")
        lines.append("")

        # 风险指标
        lines.append("风险指标:")
        lines.append(f"  最大回撤: {self.colorize(-r.max_drawdown, 'percent')}")
        lines.append(f"  波动率:   {self.format_percent(r.volatility)}")
        lines.append(f"  夏普比率: {r.sharpe_ratio:.2f}")
        lines.append("")

        # 交易统计
        lines.append("交易统计:")
        lines.append(f"  总交易次数: {r.total_trades}")
        lines.append(f"  胜率:       {self.format_percent(r.win_rate)}")
        lines.append(f"  盈亏比:     {r.profit_factor:.2f}")

        return "\n".join(lines)

    def format_trades(self) -> str:
        """格式化交易记录列表。
        
        Returns:
            格式化的交易记录字符串
        """
        if self.result is None or not self.result.trades:
            return "无交易记录"

        lines = []

        # 标题
        title = "交易记录"
        if self.use_color:
            title = f"{self.BOLD}{title}{self.RESET}"
        lines.append(title)

        # 定义列宽（考虑中文字符）
        col_widths = {
            'time': 20,
            'action': 6,
            'quantity': 10,
            'price': 10,
            'amount': 14,
            'commission': 10,
            'cash': 14,
        }

        # 表头
        headers = ['时间', '操作', '数量', '价格', '成交金额', '手续费', '现金余额']
        header_line = (
            _pad_str(headers[0], col_widths['time']) +
            _pad_str(headers[1], col_widths['action']) +
            _pad_str(headers[2], col_widths['quantity'], 'right') +
            _pad_str(headers[3], col_widths['price'], 'right') +
            _pad_str(headers[4], col_widths['amount'], 'right') +
            _pad_str(headers[5], col_widths['commission'], 'right') +
            _pad_str(headers[6], col_widths['cash'], 'right')
        )
        lines.append(header_line)

        # 分隔线
        total_width = sum(col_widths.values())
        lines.append("-" * total_width)

        # 交易记录
        for trade in self.result.trades:
            action = trade.action
            if self.use_color:
                if action == "BUY":
                    action = f"{self.RED}{action}{self.RESET}"
                else:
                    action = f"{self.GREEN}{action}{self.RESET}"

            amount = trade.quantity * trade.price
            row = (
                _pad_str(trade.timestamp, col_widths['time']) +
                _pad_str(action, col_widths['action']) +
                _pad_str(f"{trade.quantity:,}", col_widths['quantity'], 'right') +
                _pad_str(f"{trade.price:.2f}", col_widths['price'], 'right') +
                _pad_str(f"{amount:,.2f}", col_widths['amount'], 'right') +
                _pad_str(f"{trade.commission:.2f}", col_widths['commission'], 'right') +
                _pad_str(f"{trade.cash_after:,.2f}", col_widths['cash'], 'right')
            )
            lines.append(row)

        return "\n".join(lines)

    @classmethod
    def format_comparison(
        cls,
        results: List["BacktestResult"],
        use_color: bool = True
    ) -> str:
        """格式化多个回测结果对比。
        
        根据结果数量和终端宽度自动选择水平或垂直布局。
        
        Args:
            results: 回测结果列表
            use_color: 是否使用颜色输出
            
        Returns:
            格式化的对比字符串
        """
        if not results:
            return "无对比结果"

        formatter = cls(use_color=use_color)

        # 获取终端宽度
        try:
            terminal_width = os.get_terminal_size().columns
        except OSError:
            terminal_width = 80

        # 根据结果数量和终端宽度选择布局
        if len(results) > 3 or terminal_width < 100:
            return cls._format_comparison_vertical(results, formatter)
        return cls._format_comparison_horizontal(results, formatter)

    @classmethod
    def _format_comparison_horizontal(
        cls,
        results: List["BacktestResult"],
        formatter: "BacktestFormatter"
    ) -> str:
        """水平布局的策略对比。
        
        Args:
            results: 回测结果列表
            formatter: 格式化器实例
            
        Returns:
            格式化的对比字符串
        """
        lines = []

        title = "策略对比"
        if formatter.use_color:
            title = f"{cls.BOLD}{title}{cls.RESET}"
        lines.append(title)

        # 列宽
        label_width = 12
        value_width = 16

        # 表头
        header = _pad_str("指标", label_width)
        for i in range(len(results)):
            header += _pad_str(f"策略{i+1}", value_width, 'right')
        lines.append(header)
        lines.append("-" * (label_width + value_width * len(results)))

        # 数据行
        metrics = [
            ("股票代码", lambda r: r.config.stock_code),
            ("回测区间", lambda r: f"{r.config.start_date[:7]}~"),
            ("初始资金", lambda r: formatter.format_currency(r.config.initial_capital)),
            ("最终资金", lambda r: formatter.format_currency(r.final_capital)),
            ("基准收益率", lambda r: formatter.format_percent(r.benchmark_return)),
            ("策略收益率", lambda r: formatter.format_percent(r.strategy_return)),
            ("最大回撤", lambda r: formatter.format_percent(r.max_drawdown)),
            ("夏普比率", lambda r: f"{r.sharpe_ratio:.2f}"),
            ("胜率", lambda r: formatter.format_percent(r.win_rate)),
            ("交易次数", lambda r: str(r.total_trades)),
        ]

        for name, getter in metrics:
            row = _pad_str(name, label_width)
            for r in results:
                row += _pad_str(getter(r), value_width, 'right')
            lines.append(row)

        return "\n".join(lines)

    @classmethod
    def _format_comparison_vertical(
        cls,
        results: List["BacktestResult"],
        formatter: "BacktestFormatter"
    ) -> str:
        """垂直布局的策略对比。
        
        Args:
            results: 回测结果列表
            formatter: 格式化器实例
            
        Returns:
            格式化的对比字符串
        """
        lines = []

        for i, r in enumerate(results):
            if i > 0:
                lines.append("")

            title = f"策略 {i + 1}: {r.config.stock_code}"
            if formatter.use_color:
                title = f"{cls.BOLD}{title}{cls.RESET}"
            lines.append(title)
            lines.append("-" * 40)

            lines.append(f"  回测区间:   {r.config.start_date} ~ {r.config.end_date}")
            lines.append(f"  初始资金:   {formatter.format_currency(r.config.initial_capital)}")
            lines.append(f"  最终资金:   {formatter.format_currency(r.final_capital)}")
            lines.append(f"  基准收益率: {formatter.colorize(r.benchmark_return, 'percent')}")
            lines.append(f"  策略收益率: {formatter.colorize(r.strategy_return, 'percent')}")
            lines.append(f"  最大回撤:   {formatter.colorize(-r.max_drawdown, 'percent')}")
            lines.append(f"  夏普比率:   {r.sharpe_ratio:.2f}")
            lines.append(f"  胜率:       {formatter.format_percent(r.win_rate)}")
            lines.append(f"  交易次数:   {r.total_trades}")

        return "\n".join(lines)
