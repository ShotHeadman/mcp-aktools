"""DataFrame serialization utilities for cache storage.

This module provides functions to serialize and deserialize pandas DataFrames
using Parquet format for efficient storage in the SQLite cache backend.

Parquet format is chosen because:
- Preserves column types and data integrity
- High compression efficiency
- Fast read/write performance
"""

import io
import logging
from typing import Union

import pandas as pd

logger = logging.getLogger(__name__)


class CacheSerializationError(Exception):
    """Exception raised when serialization or deserialization fails."""
    pass


def serialize_dataframe(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to bytes using Parquet format.
    
    Args:
        df: The pandas DataFrame to serialize.
        
    Returns:
        Serialized data as bytes.
        
    Raises:
        CacheSerializationError: If serialization fails.
        
    Example:
        >>> df = pd.DataFrame({'a': [1, 2, 3], 'b': ['x', 'y', 'z']})
        >>> data = serialize_dataframe(df)
        >>> isinstance(data, bytes)
        True
    """
    if not isinstance(df, pd.DataFrame):
        raise CacheSerializationError(
            f"Expected pandas DataFrame, got {type(df).__name__}"
        )
    
    try:
        buffer = io.BytesIO()
        df.to_parquet(buffer, engine='pyarrow', compression='snappy')
        return buffer.getvalue()
    except ImportError as e:
        logger.error("pyarrow is required for DataFrame serialization")
        raise CacheSerializationError(
            "pyarrow is required for DataFrame serialization. "
            "Install it with: pip install pyarrow"
        ) from e
    except Exception as e:
        logger.error(f"Failed to serialize DataFrame: {e}", exc_info=True)
        raise CacheSerializationError(f"Failed to serialize DataFrame: {e}") from e


def deserialize_dataframe(data: Union[bytes, memoryview]) -> pd.DataFrame:
    """Deserialize bytes to a DataFrame using Parquet format.
    
    Args:
        data: Serialized data as bytes or memoryview.
        
    Returns:
        The deserialized pandas DataFrame.
        
    Raises:
        CacheSerializationError: If deserialization fails.
        
    Example:
        >>> df = pd.DataFrame({'a': [1, 2, 3], 'b': ['x', 'y', 'z']})
        >>> data = serialize_dataframe(df)
        >>> restored = deserialize_dataframe(data)
        >>> df.equals(restored)
        True
    """
    if data is None:
        raise CacheSerializationError("Cannot deserialize None data")
    
    if not isinstance(data, (bytes, memoryview)):
        raise CacheSerializationError(
            f"Expected bytes or memoryview, got {type(data).__name__}"
        )
    
    try:
        # Convert memoryview to bytes if necessary
        if isinstance(data, memoryview):
            data = bytes(data)
        
        buffer = io.BytesIO(data)
        return pd.read_parquet(buffer, engine='pyarrow')
    except ImportError as e:
        logger.error("pyarrow is required for DataFrame deserialization")
        raise CacheSerializationError(
            "pyarrow is required for DataFrame deserialization. "
            "Install it with: pip install pyarrow"
        ) from e
    except Exception as e:
        logger.error(f"Failed to deserialize DataFrame: {e}", exc_info=True)
        raise CacheSerializationError(f"Failed to deserialize DataFrame: {e}") from e
