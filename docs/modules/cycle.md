# 周期记录模块（Cycle）

> 独立小模块：记录一次周期事件（日期、间隔天数、备注），并提供简单的频率/间隔统计。

## 概述

`cycle/` 是 2026-09 新增的独立小模块，用于记录周期性事件（典型场景：经期/生理周期追踪）。设计上**只做一件事**：把"某天发生了一次周期事件"落库，并据此推算间隔天数与简单统计。它不依赖项目主库、不依赖 LLM、不注入推荐流。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | 周期记录的 Pydantic/数据结构定义 | `models.py` |
| 存储层 | `CycleStore`，独立 SQLite（`data/cycle.db`）CRUD + 频率统计 | `store.py` |
| 包导出 | 对外仅暴露 `CycleStore` | `__init__.py` |

> **与其他模块的关系**：`CycleStore` 被 `api/health_routes.py` 以懒加载方式引用（`_get_cycle_store()`），与健康子库同目录、同锁域隔离策略；周期表**不与 `health_patients` 关联**，是独立的记录域。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 周期事件落库 | ✅ | 记录事件日期（`dt`，唯一）、距上次间隔天数（`interval_days`）、备注（`note`） |
| 自动计算间隔 | ✅ | 插入时按上一条记录推算 `interval_days` |
| 频率/间隔统计 | ✅ | 基于历史记录给出简单统计 |
| 独立锁域 | ✅ | 数据存 `data/cycle.db`，与 main / health 库隔离，避免写锁竞争 |

## 公开 API

### Python

```python
from openbiliclaw.cycle import CycleStore

store = CycleStore(db_path="data/cycle.db")  # 默认 "data/cycle.db"
```

### 数据表

```sql
CREATE TABLE IF NOT EXISTS cycle_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    dt             TEXT NOT NULL UNIQUE,   -- 事件日期 YYYY-MM-DD
    interval_days  INTEGER,                -- 距上次天数（可为空）
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
```

### 连接约定

遵循项目统一约定（与 health/diary 等一致）：

```python
sqlite3.connect(db_path, timeout=30, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=5000")
conn.execute("PRAGMA synchronous=NORMAL")
```

## 配置项

| 字段 | 默认 | 说明 |
|------|------|------|
| （无独立配置） | — | 路径由 `api/health_routes._get_cycle_store()` 解析：`config.storage.health_db_path` 同目录的 `cycle.db`，回退到主库同目录，再回退到 `data/cycle.db` |

## 设计决策

1. **拆成独立小模块而非塞进 health**：周期记录与医疗档案是不同语义域（记录型 vs 档案型），拆开后 `health/` 不必为一个 4 列小表膨胀，也避免健康库路由因它而耦合。
2. **独立 db 文件**：复用 db sharding 的"锁域隔离"经验，`cycle.db` 独立于 `main` 与 `health.db`，写入互不阻塞。
3. **`dt` 唯一约束**：一天只允许一条周期记录，天然幂等（重复插入由 SQLite 约束拒绝）。
4. **只暴露 `CycleStore`**：`__init__.py` 仅 re-export 一个类，保持模块边界最小。

## 已知问题

- **历史事故（已修）**：`api/health_routes.py` 早期在类型注解中直接引用 `CycleStore` 却**未 import**，导致该模块整体 `NameError` 导入失败、55 条健康路由长期未注册（见 [changelog v0.3.224](../changelog.md)）。教训：新模块被跨模块引用时，务必同步 import；路由注册失败必须可见（`_route_registry` 已改为聚合告警）。
- 模块目前**无独立 API 路由与独立测试**，仅作为 `CycleStore` 被 health_routes 复用。若后续要做独立的周期页面，需补 `cycle/routes.py` + `tests/cycle/`。
