import logging
import pathlib
import sys

import diskcache
from cachetools import TTLCache

logger = logging.getLogger(__name__)


class CacheKey:
    ALL: dict = {}

    def __init__(self, key, ttl=600, ttl2=None, maxsize=100):
        self.key = key
        self.ttl = ttl
        self.ttl2 = ttl2 or (ttl * 2)
        self.cache1 = TTLCache(maxsize=maxsize, ttl=ttl)
        self.cache2 = diskcache.Cache(self.get_cache_dir())

    @staticmethod
    def init(key, ttl=600, ttl2=None, maxsize=100):
        if key in CacheKey.ALL:
            return CacheKey.ALL[key]
        cache = CacheKey(key, ttl, ttl2, maxsize)
        return CacheKey.ALL.setdefault(key, cache)

    def get(self):
        try:
            value = self.cache1[self.key]
            logger.debug(f"Cache hit (memory): {self.key}")
            return value
        except KeyError:
            pass

        value = self.cache2.get(self.key)
        if value is not None:
            logger.debug(f"Cache hit (disk): {self.key}")
            # 回填到内存缓存
            self.cache1[self.key] = value
        else:
            logger.debug(f"Cache miss: {self.key}")

        return value

    def set(self, val):
        try:
            self.cache1[self.key] = val
            self.cache2.set(self.key, val, expire=self.ttl2)
            logger.debug(
                f"Cache set: {self.key} (ttl={self.ttl}s, disk_ttl={self.ttl2}s)"
            )
        except Exception as e:
            # 缓存写入失败不应该中断操作
            logger.error(f"Cache write failed for {self.key}: {e}", exc_info=True)
        return val

    def delete(self):
        try:
            self.cache1.pop(self.key, None)
            self.cache2.delete(self.key)
            logger.debug(f"Cache deleted: {self.key}")
        except Exception as e:
            logger.error(f"Cache delete failed for {self.key}: {e}", exc_info=True)

    def get_cache_dir(self):
        home = pathlib.Path.home()
        name = __package__
        if sys.platform == "win32":
            return home / "AppData" / "Local" / "Cache" / name
        return home / ".cache" / name
