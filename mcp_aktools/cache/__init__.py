"""Cache management module.

This package will contain the new cache manager and strategy implementations.
For backward compatibility, we re-export CacheKey from the legacy cache.py module.
"""

# The issue is that there's both mcp_aktools/cache.py and mcp_aktools/cache/ directory
# Python's import system prioritizes the package (directory) over the module (file)
# So we need to explicitly import from the parent package's cache module

# Import CacheKey from the legacy cache.py file for backward compatibility
# We use __import__ with fromlist to get the actual module, not the package
import sys
cache_module = sys.modules.get('mcp_aktools.cache')
if cache_module and hasattr(cache_module, 'CacheKey'):
    # If already imported, use it
    CacheKey = cache_module.CacheKey
else:
    # Otherwise, we need to import it differently
    # Since this __init__.py IS mcp_aktools.cache, we can't import from ourselves
    # We need to import the .py file directly
    import importlib.util
    from pathlib import Path
    
    cache_py_path = Path(__file__).parent.parent / "cache.py"
    spec = importlib.util.spec_from_file_location("_mcp_aktools_cache_legacy", cache_py_path)
    if spec and spec.loader:
        legacy_cache = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy_cache)
        CacheKey = legacy_cache.CacheKey
    else:
        # Fallback if import fails
        CacheKey = None

__all__ = ["CacheKey"]
