# 缓存模块（storage/cache.py）

## 概述

统一两级缓存层：内存 L1 + 磁盘 L2（diskcache）。为统计类、计算密集型接口提供高性能缓存，支持持久化（进程重启后缓存不失效）。

## 架构设计

```
┌─────────────────────────────────────────┐
│           TwoLevelCache                  │
│                                          │
│  ┌──────────────┐  ┌────────────────┐  │
│  │  L1 内存缓存  │  │  L2 磁盘缓存    │  │
│  │  (LRU)       │  │  (diskcache)   │  │
│  │  微秒级访问   │  │  毫秒级访问     │  │
│  │  1000 条容量  │  │  1GB 容量       │  │
│  │  重启后失效   │  │  持久化，重启有效 │  │
│  └──────────────┘  └────────────────┘  │
│                                          │
│  读取顺序：L1 → L2 → 源数据              │
│  写入顺序：同时写 L1 和 L2                │
│  L2 命中时回填到 L1                       │
└─────────────────────────────────────────┘
```

## 已实现功能

| 功能 | 说明 |
|------|------|
| 两级缓存 | L1 内存（LRU）+ L2 磁盘（diskcache） |
| TTL 过期 | 支持秒级过期时间，0 表示不过期 |
| 命名空间 | 支持按命名空间批量失效缓存 |
| 装饰器用法 | `@cache.cached(ttl=300, namespace="xxx")` |
| 线程安全 | 所有操作都有锁保护 |
| 缓存统计 | 命中率、L1/L2 命中数、miss 数 |
| 持久化 | L2 磁盘缓存持久化，进程重启后不失效 |

## 公开 API

### 获取全局缓存实例

```python
from openbiliclaw.storage.cache import get_cache

cache = get_cache()  # 默认缓存目录：data/cache
```

### 基础用法

```python
# 设置缓存（ttl=300 秒）
cache.set("key", value, ttl=300, namespace="diary")

# 获取缓存
value = cache.get("key", namespace="diary", default=None)

# 删除缓存
cache.delete("key", namespace="diary")

# 失效整个命名空间
cache.invalidate_namespace("diary")

# 清空所有缓存
cache.clear()
```

### 装饰器用法

```python
@cache.cached(ttl=300, namespace="diary")
def expensive_function(arg1, arg2):
    # 耗时计算
    return result
```

### 缓存统计

```python
stats = cache.get_stats()
# {
#     "l1_size": 100,
#     "l2_size": 500,
#     "hits_l1": 1000,
#     "hits_l2": 200,
#     "misses": 100,
#     "sets": 300,
#     "hit_rate": "92.3%"
# }
```

## 配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| cache_dir | `data/cache` | 磁盘缓存目录 |
| l1_max_size | 1000 | L1 内存缓存最大条目数 |
| l2_size_limit | 1GB | L2 磁盘缓存最大字节数 |

## 设计决策

### 为什么用 diskcache 而不是 Redis？

1. **零依赖**：diskcache 是纯 Python 库，pip install 即可，不需要额外服务
2. **单机够用**：OpenBiliClaw 是单机本地应用，不需要分布式缓存
3. **性能足够**：diskcache 毫秒级访问，对于本地应用完全够用
4. **持久化**：SQLite 后端，进程重启后缓存不失效

### 为什么需要两级缓存？

1. **L1 内存缓存**：微秒级访问，用于热点数据，但容量有限且重启后失效
2. **L2 磁盘缓存**：毫秒级访问，容量大且持久化，用于重启后恢复
3. **L2 命中回填 L1**：磁盘缓存命中后自动写入内存缓存，后续访问更快

### 为什么用命名空间而不是全局清空？

1. **粒度更细**：可以只失效某个模块的缓存，不影响其他模块
2. **性能更好**：避免写操作后清空所有缓存，导致缓存雪崩
3. **易于管理**：按模块/功能划分命名空间，便于维护

## 性能数据

| 场景 | 响应时间 | 提升倍数 |
|------|---------|---------|
| 冷启动（无缓存） | 9.0s | 1x |
| 重启后 L2 磁盘缓存命中 | 2.6s | 3.5x |
| L1 内存缓存命中 | 4.5ms | 2000x |

## 相关文件

- `src/openbiliclaw/storage/cache.py` — 缓存层实现
- `src/openbiliclaw/api/app.py` — API 缓存中间件（使用统一缓存层）
- `data/cache/` — 磁盘缓存目录（运行时生成）
