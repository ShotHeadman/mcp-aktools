"""DataFrame 序列化工具，用于缓存存储。

此模块提供使用 Parquet 格式序列化和反序列化 pandas DataFrame 的函数，
以便在 SQLite 缓存后端中高效存储。

选择 Parquet 格式的原因：
- 保留列类型和数据完整性
- 高压缩效率
- 快速读写性能
"""

import io
import logging
from typing import Union

import pandas as pd

logger = logging.getLogger(__name__)


class CacheSerializationError(Exception):
    """序列化或反序列化失败时抛出的异常。"""
    pass


def serialize_dataframe(df: pd.DataFrame) -> bytes:
    """使用 Parquet 格式将 DataFrame 序列化为字节。
    
    如果 Parquet 序列化失败（例如由于 pyarrow 兼容性问题），
    则回退到使用 pickle 格式。
    
    参数:
        df: 要序列化的 pandas DataFrame。
        
    返回:
        序列化后的字节数据。
        
    异常:
        CacheSerializationError: 序列化失败时抛出。
        
    示例:
        >>> df = pd.DataFrame({'a': [1, 2, 3], 'b': ['x', 'y', 'z']})
        >>> data = serialize_dataframe(df)
        >>> isinstance(data, bytes)
        True
    """
    if not isinstance(df, pd.DataFrame):
        raise CacheSerializationError(
            f"期望 pandas DataFrame，实际得到 {type(df).__name__}"
        )
    
    # 首先尝试使用 Parquet 格式
    try:
        buffer = io.BytesIO()
        df.to_parquet(buffer, engine='pyarrow', compression='snappy')
        return buffer.getvalue()
    except ImportError as e:
        logger.warning("pyarrow 不可用，回退到 pickle 格式")
    except Exception as e:
        # 如果是 pyarrow 兼容性问题，回退到 pickle
        logger.warning("Parquet 序列化失败 (%s)，回退到 pickle 格式", str(e))
    
    # 回退到 pickle 格式
    try:
        import pickle
        buffer = io.BytesIO()
        pickle.dump(df, buffer, protocol=pickle.HIGHEST_PROTOCOL)
        return buffer.getvalue()
    except Exception as e:
        logger.error("序列化 DataFrame 失败: %s", e, exc_info=True)
        raise CacheSerializationError(f"序列化 DataFrame 失败: {e}") from e


def deserialize_dataframe(data: Union[bytes, memoryview]) -> pd.DataFrame:
    """将字节反序列化为 DataFrame。
    
    首先尝试使用 Parquet 格式，如果失败则尝试 pickle 格式。
    
    参数:
        data: 序列化后的字节数据或 memoryview。
        
    返回:
        反序列化后的 pandas DataFrame。
        
    异常:
        CacheSerializationError: 反序列化失败时抛出。
        
    示例:
        >>> df = pd.DataFrame({'a': [1, 2, 3], 'b': ['x', 'y', 'z']})
        >>> data = serialize_dataframe(df)
        >>> restored = deserialize_dataframe(data)
        >>> df.equals(restored)
        True
    """
    if data is None:
        raise CacheSerializationError("无法反序列化 None 数据")
    
    if not isinstance(data, (bytes, memoryview)):
        raise CacheSerializationError(
            f"期望 bytes 或 memoryview，实际得到 {type(data).__name__}"
        )
    
    # 如有必要，将 memoryview 转换为 bytes
    if isinstance(data, memoryview):
        data = bytes(data)
    
    # 首先尝试 Parquet 格式
    try:
        buffer = io.BytesIO(data)
        return pd.read_parquet(buffer, engine='pyarrow')
    except Exception:
        # 如果 Parquet 失败，尝试 pickle 格式
        pass
    
    # 回退到 pickle 格式
    try:
        import pickle
        buffer = io.BytesIO(data)
        return pickle.load(buffer)
    except Exception as e:
        logger.error("反序列化 DataFrame 失败: %s", e, exc_info=True)
        raise CacheSerializationError(f"反序列化 DataFrame 失败: {e}") from e
