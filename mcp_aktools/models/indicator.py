"""指标相关模型模块。

该模块定义技术指标相关的数据类，包括：
- CustomIndicator: 自定义指标
"""

from dataclasses import dataclass
import json

import pandas as pd


@dataclass
class CustomIndicator:
    """自定义指标数据类。
    
    用户可以通过 Python 代码定义自己的技术指标。
    
    Attributes:
        name: 指标名称
        description: 指标描述
        function_code: 计算指标的 Python 代码
    """
    name: str
    description: str
    function_code: str
    
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """计算指标值。
        
        使用 exec() 执行用户定义的代码来计算指标。
        
        Args:
            df: 包含K线数据的 DataFrame
            
        Returns:
            计算得到的指标值 Series
            
        Raises:
            Exception: 当指标计算失败时
        """
        # 创建执行环境
        local_vars = {"df": df, "pd": pd, "result": None}
        
        try:
            # 执行用户代码
            exec(self.function_code, {"pd": pd}, local_vars)
            result = local_vars.get("result")
            
            if result is None:
                # 如果没有显式设置 result，返回 NaN Series
                return pd.Series([float("nan")] * len(df), index=df.index)
            
            return result
        except Exception:
            # 计算失败时返回 NaN Series
            return pd.Series([float("nan")] * len(df), index=df.index)
    
    def to_json(self) -> str:
        """序列化为 JSON 字符串。
        
        Returns:
            JSON 格式的字符串
        """
        return json.dumps({
            "name": self.name,
            "description": self.description,
            "function_code": self.function_code,
        }, ensure_ascii=False)
    
    @classmethod
    def from_json(cls, json_str: str) -> "CustomIndicator":
        """从 JSON 字符串反序列化。
        
        Args:
            json_str: JSON 格式的字符串
            
        Returns:
            CustomIndicator 实例
        """
        data = json.loads(json_str)
        return cls(
            name=data["name"],
            description=data["description"],
            function_code=data["function_code"],
        )
