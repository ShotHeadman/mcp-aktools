"""MCP AkTools 的数据验证工具。

此模块提供数据验证器，用于验证从 API 获取的数据的合理性，
包括 OHLC（开盘价-最高价-最低价-收盘价）数据验证。
"""

import logging
from typing import List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class OHLCValidator:
    """OHLC 数据验证器。
    
    验证 OHLC（开盘价-最高价-最低价-收盘价）数据的合理性，
    包括：
    - 最高价 >= 最低价
    - 开盘价在最高-最低价范围内
    - 收盘价在最高-最低价范围内
    """

    # 默认列名映射
    DEFAULT_COLUMNS = {
        'open': '开盘',
        'high': '最高',
        'low': '最低',
        'close': '收盘',
    }

    @staticmethod
    def validate(
        df: pd.DataFrame,
        high_col: str = '最高',
        low_col: str = '最低',
        open_col: str = '开盘',
        close_col: str = '收盘',
    ) -> Tuple[pd.DataFrame, bool, List[str]]:
        """验证 OHLC 数据的合理性。
        
        验证每行数据是否满足以下条件：
        1. 最高价 >= 最低价
        2. 开盘价在 [最低价, 最高价] 范围内
        3. 收盘价在 [最低价, 最高价] 范围内
        
        Args:
            df: 包含 OHLC 数据的 DataFrame
            high_col: 最高价列名，默认 '最高'
            low_col: 最低价列名，默认 '最低'
            open_col: 开盘价列名，默认 '开盘'
            close_col: 收盘价列名，默认 '收盘'
            
        Returns:
            Tuple[pd.DataFrame, bool, List[str]]: 
                - DataFrame: 原始数据（不做修改）
                - bool: 数据是否全部有效（无异常）
                - List[str]: 警告消息列表
        """
        warnings: List[str] = []
        
        # 空 DataFrame 直接返回有效
        if df is None or df.empty:
            return df, True, warnings
        
        # 检查必要的列是否存在
        required_cols = [high_col, low_col, open_col, close_col]
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            warnings.append(f"缺少必要的列: {missing_cols}")
            logger.warning(f"OHLC 验证跳过: 缺少列 {missing_cols}")
            return df, False, warnings
        
        # 检查最高价 >= 最低价
        invalid_hl = df[df[high_col] < df[low_col]]
        if not invalid_hl.empty:
            warning_msg = f"发现 {len(invalid_hl)} 行最高价小于最低价"
            warnings.append(warning_msg)
            logger.warning(f"数据异常: {warning_msg}")
        
        # 检查开盘价在范围内
        invalid_open = df[(df[open_col] > df[high_col]) | (df[open_col] < df[low_col])]
        if not invalid_open.empty:
            warning_msg = f"发现 {len(invalid_open)} 行开盘价超出最高-最低价范围"
            warnings.append(warning_msg)
            logger.warning(f"数据异常: {warning_msg}")
        
        # 检查收盘价在范围内
        invalid_close = df[(df[close_col] > df[high_col]) | (df[close_col] < df[low_col])]
        if not invalid_close.empty:
            warning_msg = f"发现 {len(invalid_close)} 行收盘价超出最高-最低价范围"
            warnings.append(warning_msg)
            logger.warning(f"数据异常: {warning_msg}")
        
        is_valid = len(warnings) == 0
        return df, is_valid, warnings
