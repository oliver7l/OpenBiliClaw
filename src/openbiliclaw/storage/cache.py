"""统一缓存层：内存 L1 + 磁盘 L2（diskcache）。

两级缓存设计：
- L1 内存缓存：速度极快（微秒级），容量有限，进程重启后失效
- L2 磁盘缓存（diskcache）：速度快（毫秒级），容量大，持久化，进程重启后不失效

读取顺序：L1 → L2 → 源数据
写入顺序：同时写 L1 和 L2

使用示例：
    from openbiliclaw.storage.cache import get_cache

    cache = get_cache()

    # 基础用法
    cache.set("key", value, ttl=300)  # 5分钟
    value = cache.get("key")

    # 装饰器用法
    @cache.cached(ttl=300, namespace="diary")
    def expensive_function(arg1, arg2):
        ...

    # 手动失效
    cache.invalidate("key")
    cache.invalidate_namespace("diary")  # 失效整个命名空间
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 全局缓存实例
_cache_instance: "TwoLevelCache | None" = None
_cache_lock = threading.Lock()


class LRUCache:
    """简单的 LRU 内存缓存（L1 层）。

    线程安全，支持 TTL。
    """

    def __init__(self, max_size: int = 1000):
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """获取缓存值，过期返回 None。"""
        with self._lock:
            if key not in self._cache:
                return None
            value, expires_at = self._cache[key]
            if expires_at > 0 and time.time() > expires_at:
                del self._cache[key]
                return None
            # 移动到末尾（最近使用）
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: float = 0) -> None:
        """设置缓存值，ttl=0 表示不过期。"""
        with self._lock:
            expires_at = time.time() + ttl if ttl > 0 else 0
            self._cache[key] = (value, expires_at)
            self._cache.move_to_end(key)
            # 超过容量时淘汰最旧的
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        """删除缓存值，返回是否存在。"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """清空所有缓存。"""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


class TwoLevelCache:
    """两级缓存：内存 L1 + 磁盘 L2。

    特性：
    - L1 内存缓存：微秒级访问，容量 1000 条，进程重启后失效
    - L2 磁盘缓存（diskcache）：毫秒级访问，持久化，容量大
    - 支持 TTL（秒）
    - 支持命名空间（用于批量失效）
    - 线程安全
    """

    def __init__(
        self,
        cache_dir: str | Path,
        l1_max_size: int = 1000,
        l2_size_limit: int = 1 * 1024 * 1024 * 1024,  # 1GB
    ):
        """初始化两级缓存。

        Args:
            cache_dir: 磁盘缓存目录
            l1_max_size: L1 内存缓存最大条目数
            l2_size_limit: L2 磁盘缓存最大字节数
        """
        import diskcache

        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # L1 内存缓存
        self._l1 = LRUCache(max_size=l1_max_size)

        # L2 磁盘缓存
        self._l2 = diskcache.Cache(
            str(self._cache_dir),
            size_limit=l2_size_limit,
        )

        self._lock = threading.Lock()
        self._stats = {"hits_l1": 0, "hits_l2": 0, "misses": 0, "sets": 0}

        logger.info(
            "TwoLevelCache initialized: dir=%s, l1_max=%d, l2_limit=%dMB",
            self._cache_dir,
            l1_max_size,
            l2_size_limit // (1024 * 1024),
        )

    def _make_key(self, key: str, namespace: str | None = None) -> str:
        """生成带命名空间的缓存键。"""
        if namespace:
            return f"{namespace}:{key}"
        return key

    def get(self, key: str, namespace: str | None = None, default: Any = None) -> Any:
        """获取缓存值。

        读取顺序：L1 → L2 → default
        L2 命中时会回填到 L1。
        """
        full_key = self._make_key(key, namespace)

        # 先查 L1
        value = self._l1.get(full_key)
        if value is not None:
            self._stats["hits_l1"] += 1
            return value

        # 再查 L2
        try:
            value = self._l2.get(full_key, default=None)
            if value is not None:
                # 回填到 L1
                self._l1.set(full_key, value)
                self._stats["hits_l2"] += 1
                return value
        except Exception as e:
            logger.warning("L2 cache get failed: %s", e)

        self._stats["misses"] += 1
        return default

    def set(self, key: str, value: Any, ttl: float = 0, namespace: str | None = None) -> None:
        """设置缓存值。

        同时写入 L1 和 L2。

        Args:
            key: 缓存键
            value: 缓存值（必须可 JSON 序列化）
            ttl: 过期时间（秒），0 表示不过期
            namespace: 命名空间
        """
        full_key = self._make_key(key, namespace)

        # 写入 L1
        self._l1.set(full_key, value, ttl=ttl)

        # 写入 L2
        try:
            expire = ttl if ttl > 0 else None
            self._l2.set(full_key, value, expire=expire)
            self._stats["sets"] += 1
        except Exception as e:
            logger.warning("L2 cache set failed: %s", e)

    def delete(self, key: str, namespace: str | None = None) -> bool:
        """删除缓存值。"""
        full_key = self._make_key(key, namespace)
        l1_deleted = self._l1.delete(full_key)
        try:
            l2_deleted = self._l2.delete(full_key)
        except Exception as e:
            logger.warning("L2 cache delete failed: %s", e)
            l2_deleted = False
        return l1_deleted or l2_deleted

    def invalidate_namespace(self, namespace: str) -> int:
        """失效整个命名空间的缓存。

        遍历 L2 中所有以 namespace: 开头的键并删除。
        返回删除的条目数。
        """
        count = 0
        prefix = f"{namespace}:"

        # 清理 L1（遍历所有键，删除匹配的）
        # 注意：L1 是 OrderedDict，遍历时不能修改，所以先收集再删除
        keys_to_delete = []
        for k in self._l1._cache:
            if k.startswith(prefix):
                keys_to_delete.append(k)
        for k in keys_to_delete:
            self._l1.delete(k)
            count += 1

        # 清理 L2
        try:
            for key in list(self._l2.iterkeys()):
                if isinstance(key, str) and key.startswith(prefix):
                    self._l2.delete(key)
                    count += 1
        except Exception as e:
            logger.warning("L2 cache invalidate_namespace failed: %s", e)

        logger.info("Invalidated namespace '%s': %d entries", namespace, count)
        return count

    def clear(self) -> None:
        """清空所有缓存（L1 + L2）。"""
        self._l1.clear()
        try:
            self._l2.clear()
        except Exception as e:
            logger.warning("L2 cache clear failed: %s", e)
        logger.info("Cache cleared")

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计信息。"""
        total = self._stats["hits_l1"] + self._stats["hits_l2"] + self._stats["misses"]
        hit_rate = (
            (self._stats["hits_l1"] + self._stats["hits_l2"]) / total * 100
            if total > 0
            else 0
        )
        return {
            "l1_size": len(self._l1),
            "l2_size": len(self._l2),
            "hits_l1": self._stats["hits_l1"],
            "hits_l2": self._stats["hits_l2"],
            "misses": self._stats["misses"],
            "sets": self._stats["sets"],
            "hit_rate": f"{hit_rate:.1f}%",
        }

    def cached(
        self,
        ttl: float = 300,
        namespace: str | None = None,
        key_func: Callable[..., str] | None = None,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """缓存装饰器。

        Args:
            ttl: 过期时间（秒）
            namespace: 命名空间
            key_func: 自定义缓存键生成函数，默认用参数的 hash

        示例：
            @cache.cached(ttl=300, namespace="diary")
            def get_diary_stats(diary_id: int):
                ...
        """

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args: Any, **kwargs: Any) -> T:
                # 生成缓存键
                if key_func:
                    cache_key = key_func(*args, **kwargs)
                else:
                    key_data = json.dumps(
                        {"args": [str(a) for a in args], "kwargs": sorted(kwargs.items())},
                        sort_keys=True,
                        default=str,
                    )
                    cache_key = hashlib.md5(key_data.encode()).hexdigest()[:16]

                # 尝试获取缓存
                cached_value = self.get(cache_key, namespace=namespace)
                if cached_value is not None:
                    return cached_value

                # 执行函数
                result = func(*args, **kwargs)

                # 写入缓存
                self.set(cache_key, result, ttl=ttl, namespace=namespace)

                return result

            wrapper.__name__ = func.__name__
            wrapper.__doc__ = func.__doc__
            return wrapper

        return decorator

    def close(self) -> None:
        """关闭缓存（主要是关闭 L2 diskcache）。"""
        try:
            self._l2.close()
        except Exception:
            pass


def get_cache(
    cache_dir: str | Path | None = None,
    l1_max_size: int = 1000,
) -> TwoLevelCache:
    """获取全局缓存单例。

    Args:
        cache_dir: 缓存目录，默认 data/cache
        l1_max_size: L1 内存缓存最大条目数

    Returns:
        TwoLevelCache 实例
    """
    global _cache_instance

    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                if cache_dir is None:
                    # 默认缓存目录：项目根目录/data/cache
                    project_root = Path(__file__).parent.parent.parent.parent
                    cache_dir = project_root / "data" / "cache"

                _cache_instance = TwoLevelCache(
                    cache_dir=cache_dir,
                    l1_max_size=l1_max_size,
                )

    return _cache_instance
