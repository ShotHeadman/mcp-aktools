"""自定义指标模块。

该模块定义自定义指标数据类，支持用户通过 Python 代码定义技术指标。
从 models.indicator 模块重新导出 CustomIndicator 类。
"""

from mcp_aktools.models.indicator import CustomIndicator

__all__ = ["CustomIndicator"]
