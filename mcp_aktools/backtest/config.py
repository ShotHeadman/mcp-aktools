"""回测配置模块。

该模块定义回测配置数据类，用于存储和验证回测参数。
包括：
- 基本回测参数（股票代码、日期范围、初始资金等）
- 交易成本参数（手续费、滑点）
- 风险管理参数（止损、止盈、最大回撤熔断）
- 仓位管理参数（金字塔加仓）
"""

import json
import random
import re
from dataclasses import asdict, dataclass
from typing import Optional

from ..exceptions import ConfigurationError


# 有效的K线周期值
VALID_FREQUENCIES = {"1", "5", "15", "30", "60", "d", "w", "m"}


@dataclass
class BacktestConfig:
    """回测配置数据类。

    存储回测所需的所有配置参数，支持JSON序列化/反序列化。

    Attributes:
        stock_code: 股票代码，格式为 sh.XXXXXX 或 sz.XXXXXX
        start_date: 回测开始日期，格式为 YYYY-MM-DD
        end_date: 回测结束日期，格式为 YYYY-MM-DD
        initial_capital: 初始资金（人民币），默认 1,000,000 元
        frequency: K线周期，可选值: 1/5/15/30/60/d/w/m
        commission_rate: 手续费率，默认 0.000854 (万8.54)
        min_commission: 最低手续费（人民币），默认 5 元
        slippage_min: 滑点最小值，默认 0.0001 (0.01%)
        slippage_max: 滑点最大值，默认 0.003 (0.3%)
        stop_loss: 止损比例，如 0.05 表示 -5%，None 表示不启用
        take_profit: 止盈比例，如 0.15 表示 +15%，None 表示不启用
        max_drawdown_limit: 最大回撤熔断阈值，如 0.15 表示 15%，None 表示不启用
        enable_pyramiding: 是否启用金字塔加仓，默认 False
        max_pyramid_layers: 最大加仓层数，默认 3
        pyramid_scale_factor: 加仓比例递减因子，默认 0.5 (每层减半)
        realtime_indicator: 是否使用实时指标模式，默认 False
        signal_priority: 信号优先级，可选值: buy_first/sell_first/none
    """

    # 必填参数
    stock_code: str
    start_date: str
    end_date: str
    initial_capital: float = 1000000.0  # 默认100万人民币
    frequency: str = "d"

    # 交易成本参数
    commission_rate: float = 0.000854  # 万8.54
    min_commission: float = 5.0  # 最低手续费5元
    slippage_min: float = 0.0001
    slippage_max: float = 0.003

    # 风险管理参数（可选）
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_drawdown_limit: Optional[float] = None

    # 仓位管理参数（可选）
    enable_pyramiding: bool = False
    max_pyramid_layers: int = 3
    pyramid_scale_factor: float = 0.5

    # 交易模式参数
    # realtime_indicator: 开盘价模式
    #   - False: 收盘价模式（默认），使用收盘价计算指标，收盘时执行交易
    #   - True: 开盘价模式，用"历史收盘价+当天开盘价"实时计算指标，开盘时执行交易
    realtime_indicator: bool = False

    # 信号优先级：当同时触发买入和卖出信号时的处理方式
    # - "buy_first": 先执行买入，再执行卖出（默认行为）
    # - "sell_first": 先执行卖出，再执行买入
    # - "none": 不执行任何操作（保守策略）
    signal_priority: str = "buy_first"

    def __post_init__(self) -> None:
        """初始化后自动验证配置参数。"""
        self.validate()

    def validate(self) -> None:
        """验证配置参数有效性。

        Raises:
            ConfigurationError: 当参数无效时抛出，包含具体的错误信息
        """
        # 验证股票代码格式
        self._validate_stock_code(self.stock_code)

        # 验证日期格式
        self._validate_date_format(self.start_date, "start_date")
        self._validate_date_format(self.end_date, "end_date")

        # 验证初始资金
        self._validate_positive_capital(self.initial_capital)

        # 验证K线周期
        self._validate_frequency(self.frequency)

        # 验证滑点范围
        if self.slippage_min < 0:
            raise ConfigurationError("slippage_min", "必须为非负数")
        if self.slippage_max < 0:
            raise ConfigurationError("slippage_max", "必须为非负数")
        if self.slippage_max < self.slippage_min:
            raise ConfigurationError("slippage_max", "必须大于等于 slippage_min")

        # 验证手续费率
        if self.commission_rate < 0:
            raise ConfigurationError("commission_rate", "必须为非负数")
        if self.min_commission < 0:
            raise ConfigurationError("min_commission", "必须为非负数")

        # 验证风险管理参数
        if self.stop_loss is not None and self.stop_loss <= 0:
            raise ConfigurationError("stop_loss", "必须为正数")
        if self.take_profit is not None and self.take_profit <= 0:
            raise ConfigurationError("take_profit", "必须为正数")
        if self.max_drawdown_limit is not None and self.max_drawdown_limit <= 0:
            raise ConfigurationError("max_drawdown_limit", "必须为正数")

        # 验证金字塔加仓参数
        if self.max_pyramid_layers < 1:
            raise ConfigurationError("max_pyramid_layers", "必须至少为 1")
        if self.pyramid_scale_factor <= 0 or self.pyramid_scale_factor > 1:
            raise ConfigurationError("pyramid_scale_factor", "必须在 (0, 1] 范围内")

        # 验证信号优先级
        valid_priorities = {"buy_first", "sell_first", "none"}
        if self.signal_priority not in valid_priorities:
            raise ConfigurationError(
                "signal_priority",
                f"无效，必须为 {valid_priorities} 之一，当前值: {self.signal_priority}",
            )

    @staticmethod
    def _validate_stock_code(code: str) -> None:
        """验证股票代码格式是否为 sh.XXXXXX 或 sz.XXXXXX。

        Args:
            code: 股票代码

        Raises:
            ConfigurationError: 股票代码格式无效时抛出
        """
        pattern = r"^(sh|sz)\.\d{6}$"
        if not re.match(pattern, code):
            raise ConfigurationError(
                "stock_code",
                f"格式无效，必须为 sh.XXXXXX 或 sz.XXXXXX 格式，当前值: {code}",
            )

    @staticmethod
    def _validate_date_format(date_str: str, field_name: str) -> None:
        """验证日期格式是否为 YYYY-MM-DD。

        Args:
            date_str: 日期字符串
            field_name: 字段名称，用于错误信息

        Raises:
            ConfigurationError: 日期格式无效时抛出
        """
        pattern = r"^\d{4}-\d{2}-\d{2}$"
        if not re.match(pattern, date_str):
            raise ConfigurationError(
                field_name,
                f"格式无效，必须为 YYYY-MM-DD 格式，当前值: {date_str}",
            )

    @staticmethod
    def _validate_positive_capital(capital: float) -> None:
        """验证初始资金是否为正数。

        Args:
            capital: 初始资金

        Raises:
            ConfigurationError: 资金不为正数时抛出
        """
        if capital <= 0:
            raise ConfigurationError(
                "initial_capital",
                f"必须为正数，当前值: {capital}",
            )

    @staticmethod
    def _validate_frequency(frequency: str) -> None:
        """验证K线周期是否有效。

        Args:
            frequency: K线周期

        Raises:
            ConfigurationError: 周期无效时抛出
        """
        if frequency not in VALID_FREQUENCIES:
            raise ConfigurationError(
                "frequency",
                f"无效，必须为 {VALID_FREQUENCIES} 之一，当前值: {frequency}",
            )

    def get_random_slippage(self) -> float:
        """获取随机滑点值。

        在 [slippage_min, slippage_max] 范围内随机取值，
        用于模拟真实市场的价格波动。

        Returns:
            float: 随机滑点值
        """
        return random.uniform(self.slippage_min, self.slippage_max)

    def to_json(self) -> str:
        """序列化为JSON字符串。

        Returns:
            str: JSON格式的配置字符串
        """
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "BacktestConfig":
        """从JSON字符串反序列化。

        Args:
            json_str: JSON格式的配置字符串

        Returns:
            BacktestConfig: 配置对象

        Raises:
            ConfigurationError: JSON解析失败或参数无效时抛出
        """
        try:
            data = json.loads(json_str)
            # 创建实例时会自动调用 __post_init__ 进行验证
            return cls(**data)
        except json.JSONDecodeError as e:
            raise ConfigurationError("json", f"JSON 解析失败: {e}") from e
        except TypeError as e:
            raise ConfigurationError("json", f"配置参数错误: {e}") from e
