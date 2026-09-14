# OpenBiliClaw 全量存活审计报告

> ⚠️ **本报告已被 [`project-consolidation-2026-09-11.md`](project-consolidation-2026-09-11.md) 取代/修正。**
> 修正点：报告 §1 对 BUG-1 的结论「`/api/health` 整体未注册，路由数从 0 恢复」**是错的** ——
> 实测 `app.py` 内联的 13 条只读列表路由**是在册的**，失效的是 `health_routes.py` 的另外 55 条（写操作/高级功能）。
> 本报告保留作为审计过程记录，**结论请以 consolidation 报告为准。**

> 审计日期：2026-09-11
> 审计方式：全量只读（未修改任何文件）
> 目的：在重构前彻底搞清「哪些在用、哪些没用、哪些坏了」，避免重构出错

---

## 0. 方法论（为什么结论可信）

本次审计**放弃了纯静态分析**，因为发现静态分析会大面积误报：

| 尝试 | 结论 | 是否可信 |
|---|---|---|
| AST 静态 import 分析 | 报出 127 个"零入度"模块 | ❌ 不可信 |
| 全仓字符串引用计数 | 报出 90 个"零外部引用"模块 | ❌ 不可信 |
| **全量 import 体检（446 模块）** | 443 成功 / 3 失败 | ✅ **可信** |
| **实例化 create_app + dump 路由** | 444 条 /api 路由真实注册 | ✅ **可信** |

**为什么静态分析会错**：本项目大量使用**动态导入**——
- `_route_registry.py` 用 `__import__(f"openbiliclaw.api.{name}")` 字符串注册路由
- `runtime/*_producer.py` 由 PM2 以脚本方式直接拉起，不经 import
- `sources/url_processors/*` 用注册表模式按平台名动态加载

静态分析器看不到这些，所以把 `article_routes`、`diary_routes`、`health_routes`、`knowledge_routes`、`reading_routes`、`_delight_routes` 全误判为死代码。**实测运行时，它们全部是活的**（/api/articles 13 条、/api/diary 113 条、/api/delight 3 条等）。

> **⚠️ 更正（2026-09-14）**：上句中 `_delight_routes` 一项**不成立**。`_route_registry.py`
> 的动态导入是**显式白名单**（「K3 孤儿路由修复」清单），`_delight_routes` 从未被列入，
> 所以它确实是死模块；那「/api/delight 3 条」实际来自 `app.py` 的**内联实现**
> （`pending` / `pending-batch` / `respond`），与该模块无关。教训：**端点数量证明不了
> 某个模块已接线**——计数无法归因到模块，这正是它被误判为「活代码」而长期无人接线的
> 根因（后果见 changelog：K5 误以为其副本在模块里，删掉了 `app.py` 的
> `POST /api/delight/sent`）。该模块已删除。

---

## 1. 真实坏代码（必须修）

> **⚠️ 已修复（2026-09-11，v0.3.224）**：BUG-1 / BUG-2 / BUG-3 全部处理完毕，详见下方各条"处置结果"与 [changelog](changelog.md)。

### 🔴 BUG-1：`api/health_routes.py` — NameError 导致 55 条健康路由静默失效

> **处置结果（2026-09-11）**：✅ 已修。
> **修正**：原报告"`/api/health` 未注册 / 路由数 0"的结论**是错的** —— 实测 `app.py` 内联的 13 条只读列表路由**在册且可访问**，失效的是 `health_routes.py` 的另外 55 条（全部写操作 + stats/timeline/vitals/lab-trend/check-drug-interactions/AI 解读）。
> **根因修正**：`CycleStore` 不在 `openbiliclaw.health`（原报告第 50 行说错了），而在新增的 `openbiliclaw.cycle` 包；已补 `from openbiliclaw.cycle import CycleStore`。
> **连带修复**：`app.py` 内联的 13 条桩读的是主库已拆空的 `health_` 空壳表（0 行，真实数据在 `data/health.db` 共 17 患者），故整段（292 行）已删除，`/api/health/*` 现为单一来源 `health_routes.py`。

- **现象**：`import openbiliclaw.api.health_routes` → `NameError: name 'CycleStore' is not defined`
- **位置**：`health_routes.py:73` `_cycle_store: CycleStore | None = None`（类型注解用了 `CycleStore`，但**文件顶部从未 import 它**）
- **为何没被发现**：`_route_registry.py:80-91` 用动态 import + 宽泛 `except Exception` 兜底：
  ```python
  for _mod_name, _fn_name in [..., ("health_routes", "register_health_routes"), ...]:
      try:
          _mod = __import__(f"openbiliclaw.api.{_mod_name}", fromlist=[_fn_name])
          getattr(_mod, _fn_name)(app, ctx)
      except Exception:  # noqa: BLE001
          logger.exception("%s registration failed", _fn_name)
  ```
  注册失败只写日志，**服务照常启动**。表面一切正常，实际上该模块的全部路由缺失。
- **修复**：~~在 `health_routes.py` 顶部补 `from openbiliclaw.health import CycleStore`~~ → 实际为 `from openbiliclaw.cycle import CycleStore`（该类属 `cycle` 包，非 `health` 包）。
- **验证**：实测 `create_app()` 后健康路径 **36 条**（68 路由），`/api/health/stats` 返回真实数据。

### 🔴 BUG-2/3：`saved_sync/adapters/{bilibili,extension}.py` — 引用不存在的类

> **处置结果（2026-09-11）**：✅ 已删。该子包全仓**零外部引用**、`NativeSaveRouter()` 以无参装配、`tests/` 零覆盖 → 属重构遗留死代码（非功能缺失）。整目录（3 文件）已移入废纸篓。

- **现象**：`ImportError: cannot import name 'BilibiliFavoriteDuplicateError' from 'openbiliclaw.bilibili.api'`
- **证据**：全仓（src + packages）grep `class BilibiliFavoriteDuplicateError` → **零结果**，该类已被删除
- **影响**：这两个适配器彻底无法加载。~~需确认 `saved_sync` 是否仍走这条路径~~ → 已确认无人走（`NativeSaveRouter()` 无参装配），删除无影响。

---

## 2. 配置与运行状态漂移

### 🟡 DRIFT-1：PM2 配置严重滞后

| 项 | ecosystem.config.json | PM2 实际运行 |
|---|---|---|
| 进程数 | **9 个** | **22 个** |
| 缺失项 | — | `bili-favorites`、`douyin-favorites`、`douyin-likes`、`hupu-feed`、`hupu-bxj`、`toutiao-feed`、`x-favorites`、`xhs-favorites`、`zhihu-favorites`、`xiaoyuzhou-favorites`、`v2ex-rss`、`inbox-merger`、`bili-favorites` |

**风险**：这 13 个进程不在版本控制里。一旦机器重启/PM2 重装/换机部署，这些采集进程**不会被恢复**，且没人知道要手动补哪些。

**修复**：从 `pm2 jlist` 导出真实配置，回写 `ecosystem.config.json`。

### 🟡 DRIFT-2：SQLite 锁竞争

- **现象**：`/tmp/openbiliclaw-error.log` 大量 `sqlite3.OperationalError: database is locked`（栈：`sources/yt_tasks.py:271` `BEGIN IMMEDIATE`）
- **根因**：22 个进程并发写同一批 `.db`（`openbiliclaw.db`、`content.db`、`pool.db` 等），SQLite 单写者模型扛不住
- **旁证**：`openbiliclaw-api` PM2 重启 **484 次**，`toutiao-feed` 重启 **174 次**
- **修复方向**（择一）：① 统一 WAL + busy_timeout 调大；② 关键写路径接入已有的 `rate_limit_guard` / 任务队列串行化；③ 长期按域拆库。

---

## 3. 可安全清理（约 26 GB）

| # | 目标 | 大小 | 说明 |
|---|---|---|---|
| 1 | `data/_archive/backups_20260910/` 旧批次 | ~12 GB | 17 个 openbiliclaw.db 历史快照，保留最近 2-3 个即可 |
| 2 | `data/openbiliclaw.db.bak-pre-*` | 5.9 GB | pre-events / pre-interview / pre-pool 各 1.6G + wal |
| 3 | `data/openbiliclaw.db.backup-2026*` | 1.05 GB | 09-07 旧备份 |
| 4 | 根目录 `embedding_cache.db` | 12 KB | 空壳残留（真库 1.29G 在 `data/`，MD5 不同） |
| 5 | `data/hiser.db` | 0 字节 | 空文件（项目真实名是 hister） |
| 6 | `data/database.db` | 0 字节 | 空文件 |
| 7 | `data/_archive/_dead_shells_20260909/` | 0 字节 | 空目录 |

---

## 4. 代码结构：抽取式重构未收尾

### 现状

| 包 | 状态 | 体量 |
|---|---|---|
| `obc_llm` | ✅ 完成 | 18 文件 |
| `obc_soul` | ✅ 完成 | 28 文件 / 13.9K 行 |
| `obc_discovery` | ✅ 完成 | 22 文件 |
| `obc_runtime` | ❌ **未创建** | 计划中（`docs/module-extraction-plan.md` §5） |

- `src/openbiliclaw/{llm,soul,discovery}/` 仍留 **37 个兼容垫片**（3 行 re-export / 13 行 `sys.modules` 别名）
- **224 处**代码仍走旧 import 路径，未迁移到新包

### 上帝文件

| 文件 | 行数 |
|---|---|
| `cli/__init__.py` | **8,795** |
| `api/app.py` | **6,933** |
| `recommendation/engine.py` | 3,066 |
| `api/source_routes.py` | 3,040 |
| `config.py` | 2,682 |

### 质量门禁（AGENTS.md 要求全绿，当前未达标）

- `ruff check src/`：**279 个问题**（115 行超长 + 66 导入未排序；**125 个可 `--fix` 自动修**）
- `mypy src/`：**73 个错误 / 40 文件**（最集中 `api/_interview_routes.py` 10 个）

---

## 5. 建议的重构顺序（先安全、后激进）

### 阶段 0：清理与止损（低风险，可立即做）
1. 清理 §3 的 26G 冗余
2. 修 BUG-1（补 import）——最小改动，恢复健康模块
3. 确认 BUG-2/3 的两个适配器是死是活，决定修或删

### 阶段 1：配置对齐（低风险）
4. 从 `pm2 jlist` 重建 `ecosystem.config.json`，纳入全部 22 个进程
5. `ruff --fix` 自动修 125 个问题 + 跑 `pytest` 验证

### 阶段 2：结构整理（中风险，需谨慎）
6. 收尾 `obc_runtime` 抽取（若决定继续）
7. 迁移 224 处 import → 删除 37 个垫片
8. 修 mypy 73 个错误

### 阶段 3：深度重构（高风险，最后做）
9. 拆 `cli/__init__.py`（8.8K 行）
10. 拆 `api/app.py`（6.9K 行）
11. 解决 SQLite 锁竞争

---

## 6. 待用户决策

- [ ] 阶段 0 的清理是否执行？（26G）
- [ ] 3 处坏代码：BUG-1 修、BUG-2/3 修还是删？
- [ ] `obc_runtime` 抽取是否继续？
- [ ] 重构优先级：先清理止损，还是先动代码？
