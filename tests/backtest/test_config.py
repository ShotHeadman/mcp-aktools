"""BacktestConfig 配置测试模块。

测试回测配置的创建、验证和序列化功能。
"""

import pytest
from mcp_aktools.backtest.config import BacktestConfig
from mcp_aktools.exceptions import ConfigurationError


class TestBacktestConfigCreation:
    """测试 BacktestConfig 创建。"""

    def test_valid_config_creation(self, valid_config):
        """测试有效配置创建成功。"""
        assert valid_config.stock_code == "sh.600000"
        assert valid_config.start_date == "2024-01-01"
        assert valid_config.end_date == "2024-06-30"
        assert valid_config.initial_capital == 1000000.0

    def test_default_values(self, valid_config):
        """测试默认值正确设置。"""
        assert valid_config.frequency == "d"
        assert valid_config.commission_rate == 0.000854
        assert valid_config.slippage_min == 0.0001
        assert valid_config.slippage_max == 0.003
        assert valid_config.enable_pyramiding is False


class TestStockCodeValidation:
    """测试股票代码验证。"""

    def test_valid_sh_stock_code(self):
        """测试有效的上海股票代码。"""
        config = BacktestConfig(
            stock_code="sh.600000",
            start_date="2024-01-01",
            end_date="2024-06-30",
        )
        assert config.stock_code == "sh.600000"

    def test_valid_sz_stock_code(self):
        """测试有效的深圳股票代码。"""
        config = BacktestConfig(
            stock_code="sz.000001",
            start_date="2024-01-01",
            end_date="2024-06-30",
        )
        assert config.stock_code == "sz.000001"

    def test_invalid_stock_code_format(self):
        """测试无效股票代码格式被拒绝。"""
        with pytest.raises(ConfigurationError) as exc_info:
            BacktestConfig(
                stock_code="600000",
                start_date="2024-01-01",
                end_date="2024-06-30",
            )
        assert "stock_code" in str(exc_info.value)

    def test_invalid_stock_code_prefix(self):
        """测试无效股票代码前缀被拒绝。"""
        with pytest.raises(ConfigurationError):
            BacktestConfig(
                stock_code="bj.600000",
                start_date="2024-01-01",
                end_date="2024-06-30",
            )


class TestDateValidation:
    """测试日期格式验证。"""

    def test_valid_date_format(self):
        """测试有效日期格式。"""
        config = BacktestConfig(
            stock_code="sh.600000",
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert config.start_date == "2024-01-01"
        assert config.end_date == "2024-12-31"

    def test_invalid_start_date_format(self):
        """测试无效开始日期格式被拒绝。"""
        with pytest.raises(ConfigurationError) as exc_info:
            BacktestConfig(
                stock_code="sh.600000",
                start_date="2024/01/01",
                end_date="2024-06-30",
            )
        assert "start_date" in str(exc_info.value)

    def test_invalid_end_date_format(self):
        """测试无效结束日期格式被拒绝。"""
        with pytest.raises(ConfigurationError) as exc_info:
            BacktestConfig(
                stock_code="sh.600000",
                start_date="2024-01-01",
                end_date="20240630",
            )
        assert "end_date" in str(exc_info.value)


class TestSlippageValidation:
    """测试滑点参数验证。"""

    def test_valid_slippage_range(self):
        """测试有效滑点范围。"""
        config = BacktestConfig(
            stock_code="sh.600000",
            start_date="2024-01-01",
            end_date="2024-06-30",
            slippage_min=0.001,
            slippage_max=0.005,
        )
        assert config.slippage_min == 0.001
        assert config.slippage_max == 0.005

    def test_slippage_max_less_than_min(self):
        """测试滑点最大值小于最小值被拒绝。"""
        with pytest.raises(ConfigurationError) as exc_info:
            BacktestConfig(
                stock_code="sh.600000",
                start_date="2024-01-01",
                end_date="2024-06-30",
                slippage_min=0.005,
                slippage_max=0.001,
            )
        assert "slippage_max" in str(exc_info.value)

    def test_negative_slippage_min(self):
        """测试负滑点最小值被拒绝。"""
        with pytest.raises(ConfigurationError):
            BacktestConfig(
                stock_code="sh.600000",
                start_date="2024-01-01",
                end_date="2024-06-30",
                slippage_min=-0.001,
            )


class TestConfigSerialization:
    """测试配置序列化。"""

    def test_to_json(self, valid_config):
        """测试序列化为 JSON。"""
        json_str = valid_config.to_json()
        assert "sh.600000" in json_str
        assert "2024-01-01" in json_str

    def test_from_json(self, valid_config):
        """测试从 JSON 反序列化。"""
        json_str = valid_config.to_json()
        restored = BacktestConfig.from_json(json_str)
        assert restored.stock_code == valid_config.stock_code
        assert restored.start_date == valid_config.start_date
        assert restored.end_date == valid_config.end_date
        assert restored.initial_capital == valid_config.initial_capital

    def test_round_trip(self, valid_config):
        """测试序列化往返一致性。"""
        json_str = valid_config.to_json()
        restored = BacktestConfig.from_json(json_str)
        # 再次序列化应该得到相同结果
        json_str2 = restored.to_json()
        assert json_str == json_str2
