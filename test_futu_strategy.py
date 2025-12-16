"""
使用 mcp-aktools 回测系统模拟富途交易逻辑

回测标的：阳光电源(sz.300274)
回测区间：2025-03-01 ~ 2025-12-12

富途量化回测官方结果（作为对比基准）：
- 策略收益率: 172.61%
- 年化收益率: 286.02%
- 最大回撤: 19.97%
- 波动率: 45.70%
- 夏普比率: 6.176

策略逻辑：
- 买入信号：KDJ.J < 10，使用50%现金买入
- 卖出信号：MACD强死叉（DIF从正变负 或 DIF > 10时的死叉）

测试两种模式：
1. 标准模式（realtime_indicator=False）：使用收盘价计算指标
2. 实时模式（realtime_indicator=True）：使用"历史收盘价+当天开盘价"计算指标
"""

import pandas as pd

from mcp_aktools.backtest.config import BacktestConfig
from mcp_aktools.backtest.engine import BacktestEngine
from mcp_aktools.backtest.strategy import Strategy
from mcp_aktools.backtest.formatter import BacktestFormatter
from mcp_aktools.models.trade import OrderType, PriceType


class FutuStrategy(Strategy):
    """模拟富途交易逻辑的策略
    
    买入条件：KDJ.J < 10
    卖出条件：MACD强死叉（DIF从正变负 或 DIF > 10时的死叉）
    
    支持两种模式：
    - 标准模式：使用收盘价计算的指标
    - 实时模式：使用"历史收盘价+当天开盘价"实时计算的指标
    """
    
    def on_bar(self, bar: pd.Series, history: pd.DataFrame) -> None:
        """每根K线的处理逻辑"""
        
        # 获取当前指标值（根据config.realtime_indicator自动选择计算方式）
        j_value = self.get_indicator('KDJ.J')
        
        # 卖出信号：MACD强死叉
        if self.has_position:
            if self.get_signal('macd', 'death_cross_strong'):
                # 卖出全部持仓
                self.sell(PriceType.OPEN, OrderType.POSITION_PERCENT, 100)
        
        # 买入信号：KDJ.J < 10
        if j_value is not None and j_value < 10:
            # 使用50%现金买入
            self.buy(PriceType.OPEN, OrderType.CASH_PERCENT, 50)


def run_backtest(realtime_mode: bool = False) -> dict:
    """运行回测
    
    Args:
        realtime_mode: 是否使用实时指标模式
        
    Returns:
        回测结果字典
    """
    # 配置回测参数
    config = BacktestConfig(
        stock_code="sz.300274",  # 阳光电源
        start_date="2025-03-01",
        end_date="2025-12-12",
        frequency="d",  # 日线
        initial_capital=1000000.0,  # 100万初始资金
        commission_rate=0.000854,  # 手续费率（与富途一致）
        min_commission=5.0,  # 最低手续费
        slippage_min=0.0,  # 无滑点（与富途一致）
        slippage_max=0.0,
        enable_pyramiding=True,  # 允许加仓
        max_pyramid_layers=50,  # 最大加仓层数
        pyramid_scale_factor=1.0,  # 加仓比例因子
        signal_priority="sell_first",  # 卖出优先
        realtime_indicator=realtime_mode,  # 实时指标模式
    )
    
    # 创建回测引擎并运行
    engine = BacktestEngine(config)
    result = engine.run(FutuStrategy)
    
    return {
        'result': result,
        'formatter': BacktestFormatter(result, use_color=True),
        'mode': '实时模式' if realtime_mode else '标准模式'
    }


def main():
    """运行回测并输出结果"""
    
    print("=" * 100)
    print("mcp-aktools 回测系统 - 模拟富途交易逻辑")
    print("=" * 100)
    
    # 运行标准模式回测
    print("\n" + "=" * 100)
    print("【标准模式】使用收盘价计算指标，信号滞后1天")
    print("=" * 100)
    standard_result = run_backtest(realtime_mode=False)
    print("\n" + standard_result['formatter'].format_summary())
    print("\n" + standard_result['formatter'].format_trades())
    
    # 运行实时模式回测
    print("\n" + "=" * 100)
    print("【实时模式】使用'历史收盘价+当天开盘价'计算指标，与富途逻辑一致")
    print("=" * 100)
    realtime_result = run_backtest(realtime_mode=True)
    print("\n" + realtime_result['formatter'].format_summary())
    print("\n" + realtime_result['formatter'].format_trades())
    
    # 输出对比分析
    print("\n" + "=" * 100)
    print("回测结果对比")
    print("=" * 100)
    
    std = standard_result['result']
    rt = realtime_result['result']
    
    print(f"""
┌───────────────────────────────────────────────────────────────────────────────┐
│                              回测结果对比                                      │
├─────────────────┬─────────────────┬─────────────────┬─────────────────────────┤
│      指标       │   富途官方      │ mcp-aktools标准 │ mcp-aktools实时         │
├─────────────────┼─────────────────┼─────────────────┼─────────────────────────┤
│   策略收益率    │    172.61%      │    {std.strategy_return*100:6.2f}%      │    {rt.strategy_return*100:6.2f}%             │
│   最大回撤      │    19.97%       │    {std.max_drawdown*100:6.2f}%      │    {rt.max_drawdown*100:6.2f}%             │
│   波动率        │    45.70%       │    {std.volatility*100:6.2f}%      │    {rt.volatility*100:6.2f}%             │
│   夏普比率      │    6.176        │    {std.sharpe_ratio:6.2f}       │    {rt.sharpe_ratio:6.2f}              │
│   交易次数      │    29           │    {std.total_trades:6d}       │    {rt.total_trades:6d}              │
└─────────────────┴─────────────────┴─────────────────┴─────────────────────────┘
""")
    
    print("""
关键差异分析：

1. 【标准模式 vs 实时模式】
   - 标准模式：使用前一天收盘价计算指标，信号滞后1天
   - 实时模式：使用"历史收盘价+当天开盘价"计算指标，与富途逻辑一致

2. 【实时模式的优势】
   - 信号判断时机与富途一致（开盘时判断）
   - 卖出时机不再滞后
   - 收益率更接近富途结果

3. 【剩余差异原因】
   - 数据源差异：akshare vs 富途数据
   - 指标计算库差异：pandas-ta vs 富途内部实现
   - 买入数量计算的细微差异

4. 【使用建议】
   - 如需复现富途交易逻辑：使用 realtime_indicator=True
   - 如需标准回测框架：使用 realtime_indicator=False（默认）
""")


if __name__ == "__main__":
    main()
