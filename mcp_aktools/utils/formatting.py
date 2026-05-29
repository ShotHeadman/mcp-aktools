"""MCP 工具返回值格式化辅助函数。"""

from typing import Any, Dict, List

import pandas as pd

from ..exceptions import ValidationException


VALID_RESPONSE_FORMATS = {"csv", "json"}


def validate_response_format(response_format: str, default: str = "csv") -> str:
    """验证并规范化工具返回格式。"""
    normalized = (response_format or default).strip().lower()
    if normalized not in VALID_RESPONSE_FORMATS:
        raise ValidationException(
            "response_format",
            f"必须是 {sorted(VALID_RESPONSE_FORMATS)} 中的一个，得到 '{response_format}'",
        )
    return normalized


def dataframe_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """将 DataFrame 转成 JSON 友好的记录列表。"""
    normalized = df.copy()
    for column in normalized.columns:
        if pd.api.types.is_datetime64_any_dtype(normalized[column]):
            normalized[column] = normalized[column].dt.strftime("%Y-%m-%d %H:%M:%S")

    normalized = normalized.astype(object).where(pd.notna(normalized), None)
    return normalized.to_dict(orient="records")
