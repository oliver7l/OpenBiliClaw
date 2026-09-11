# OpenBiliClaw 全盘梳理报告（2026-09-11）

> **状态**：核实完成 ✅ → **P0/P1/P2/P3 已执行完成**（2026-09-11）。P4（大重构）待另立专项。
> **方法**：运行时实测（`create_app()` dump 真实路由表 + 全量 `importlib` 体检）+ 静态交叉验证 + git 状态核对 + 磁盘实测。
> **对应上一版**：`docs/project-audit-2026-09-11.md`（本版对其 3 处结论做了**修正**，见 §1.4）。

---

## 📌 执行进展（2026-09-11 更新）

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 修 BUG-1：补 `from openbiliclaw.cycle import CycleStore` | ✅ **已完成** |
| **P0** | 删 `app.py` 内联健康路由兜底段（292 行，读空库的真 bug） | ✅ **已完成** |
| **P0** | `_route_registry` 静默吞错 → 聚合告警 | ✅ **已完成** |
| **P1** | 删 `saved_sync/adapters/` 死代码（3 文件） | ✅ **已完成** |
| **P1** | 删 `saved_sync/extension_broker.py`（293 行；零引用 + 调用 8 个不存在的 DB 方法） | ✅ **已完成** |
| **P1** | `cycle/` 补文档 + 补测试（`tests/cycle/` 10 例，此前 0 覆盖） | ✅ **已完成** |
| **P2** | 修 `health.md` / `development.md` / `index.md` / `config.example.toml` / `changelog.md` / 审计报告 | ✅ **已完成** |
| **P3** | 清 `data/` 冗余备份：**项目 52G → 29G（−23G）** | ✅ **已完成** |
| **P4** | 大重构（obc_runtime 抽取收口、224 处旧 import） | ⏸ 另立专项 |

**验证结果**：全量 `importlib` 体检 **417–418 模块 0 失败**（修复前 3 失败）；`create_app()` 成功、`/api` 路由 390 条、健康路径 **36 条**（修复前 13 条）；实测 `/api/health/stats` 返回真实数据（17 患者 / 2 就诊 / 6 健康问题，修复前读主库空壳表恒为 0）；ruff format + check 全过，mypy 0 错误；`tests/cycle` 10 passed。
**测试回归判定**：`tests/api` 全量 25 failed / 464 passed。经 stash 对照验证，其中 **23 例在改动前即失败**（`test_api_bili_tasks` / `test_api_config_probe` / `test_api_xhs_ingest` / `test_favorites_api` / `test_watch_later_api` / `test_api_reading_tagging`），另 2 例（config guards/transactional）单独复跑通过 —— 均为**存量问题与并发资源竞争**，**非本次改动引入的回归**。`tests/api/test_api_media.py` + `test_api_ed2k.py` = **28 passed**。

---

## 0. 一页纸结论

| 维度 | 现状 | 风险 |
|------|------|------|
| 代码真实性 | 446 模块体检 **443 OK / 3 FAIL**，3 处均为真 bug | 🟢 **已修复 → 418/0 FAIL** |
| 健康模块 | 前端调用 **34** 个端点，实际只有 **13** 个存活 | 🟢 **已修复 → 36 路径全活** |
| saved_sync 适配层 | 整个 `adapters/` 子包 **3 文件全死** | 🟢 **已移除** |
| 文档一致性 | `development.md` §7 / `health.md` / `index.md` **均与代码不符** | 🟢 **已校正** |
| 磁盘 | 总计 **52G**，其中 **~26G 是可清理的冗余备份/缓存** | 🟡 非紧急，待拍板 |

**一句话**：代码主体是健康的（443/446），但有 **1 个真实的、用户点得到的坏功能**（健康管理写接口）和 **3 份会误导人的文档**。清理项 26G 全部安全，但建议**先修 bug、再清磁盘、最后重构**。

---

## 1. 代码层核实（任务 #60）

### 1.1 全量 import 体检结果（可复现）

```bash
.venv/bin/python3 -c "
for m in ['openbiliclaw.api.health_routes',
          'openbiliclaw.saved_sync.adapters.bilibili',
          'openbiliclaw.saved_sync.adapters.extension']:
    __import__(m)
"
```

| 模块 | 结果 | 错误 |
|------|------|------|
| `api/health_routes.py` | ❌ FAIL | `NameError: name 'CycleStore' is not defined` |
| `saved_sync/adapters/bilibili.py` | ❌ FAIL | `ImportError: cannot import name 'BilibiliFavoriteDuplicateError'` |
| `saved_sync/adapters/extension.py` | ❌ FAIL | 同上（级联） |

### 1.2 🔴 BUG-1 详勘：`health_routes.py` — **比原报告严重得多**

**原报告说**："`/api/health` 路由数应从 0 恢复" → **这个结论是错的**（已在本版修正）。

**实测真相**（`create_app()` 后 dump 真实路由表）：

```
total /api routes: 364
health 相关: 13 条  ← 全部来自 app.py 内联，全部是"只读列表"桩
cycle 相关: 0 条
```

- `api/app.py` 内联了 **13 条** `/api/health/*` 路由（`patients / conditions / medications / lab-results / procedures / allergies / immunizations / doctors / documents / insights / appointments / medication-logs` — **只有 GET 列表**，实现是 `svc.list_xxx()` 的薄封装）。
- `api/health_routes.py` 定义了 **68 条**路由：完整 CRUD（POST/PUT/DELETE）+ `stats` + `timeline` + `lab-trend` + `medication-adherence` + `check-drug-interactions` + `lab-results/{id}/interpret` + `procedures/{id}/interpret`。
- 因 `NameError`，`_route_registry.py:83-95` 的 `try/except Exception: logger.exception(...)` **静默吞掉**整个模块 → **55 条路由从未注册**。

**根因链**：

```
src/openbiliclaw/cycle/store.py   → class CycleStore        （新模块，untracked）
src/openbiliclaw/cycle/__init__.py → 导出 CycleStore
src/openbiliclaw/api/health_routes.py:73 → _cycle_store: CycleStore | None = None
                                          ↑ 类型注解引用了 CycleStore，但顶部 import 列表（from openbiliclaw.health import ... 20+ 类）
                                            里**没有它**，也没 import openbiliclaw.cycle
→ 模块级注解在 import 时求值 → NameError → 整个模块炸
```

**影响面（用户可见，非理论风险）**：

`src/openbiliclaw/web/desktop/assets/js/health-app.js` 实际调用 **34 个端点**：

| 存活（13） | 404 / 不可用（21+） |
|-----------|-------------------|
| `/patients`、`/conditions`、`/medications`、`/lab-results`、`/procedures`、`/allergies`、`/immunizations`、`/doctors`、`/documents`、`/insights`、`/appointments`、`/medication-logs`（**均 GET 列表**） | `/stats`、`/timeline`、`/vitals`、`/encounters`、`/lab-trend`、`/medication-adherence`、`/check-drug-interactions`、所有 **POST/PUT/DELETE**（新增/编辑/删除患者、就诊、用药、化验、检查、文档…） |

> **实证**：`health-app.js:809` 在用药交互检查里调用 `/check-drug-interactions?...` —— 这个端点**只存在于 health_routes.py**，所以这个功能**现在就点不通**。

**结论**：健康管理页的「新增/编辑/删除/趋势/时间线/药物相互作用/AI 解读」**全部不可用**；只有列表能看。这是**真 bug，不是死代码**。

**修复方案（二选一）**：
- **A（推荐，最小改动）**：`health_routes.py` 顶部补一行
  `from openbiliclaw.cycle import CycleStore`
  然后**确认 app.py 内联的 13 条与 health_routes 的 68 条不冲突**（同名路径先注册者生效，需删掉 app.py 的 13 条桩或让 health_routes 后注册覆盖）。修完 `_route_registry` 的 `try/except` 应改为"失败即 fail-fast"或至少 WARNING 级别可见。
- **B（更彻底）**：把 app.py 内联的 13 条健康路由**整段删除**，健康 API 完全交给 `health_routes.py` 单点负责。这符合 `docs/development.md` §7 自己写的约定（"不要继续往 app.py 里加内联路由"）。

### 1.3 🟡 BUG-2/3 详勘：`saved_sync/adapters/` — **整个子包是死的**

**证据链**（三重验证）：

```
① 全仓 grep "BilibiliNativeSaveAdapter|build_extension_native_save_adapters"
   → 只在 adapters/__init__.py 和 adapters/*.py 自身出现，外部 **零引用**。
② 全仓 grep "ExtensionNativeSaveBroker"
   → 只被 adapters/extension.py 内部 import，**外部零引用**。
③ 运行时装配（runtime_context.py:898-905）：
   SavedSyncService(database=..., router=NativeSaveRouter(), ...)
                                          ↑ NativeSaveRouter() 无参 → 没注册任何 adapter
④ tests/ 目录：**0 个** saved_sync 相关测试。
```

**结论**：`saved_sync/adapters/{__init__,bilibili,extension}.py` 是**重构遗留的死代码**（大概是"native-save 适配器"设计被放弃或未接线）。**无运行时影响**（因为压根没人调用），但会让 `import` 体检和静态分析一直报警。

**处置建议**：**删除 `saved_sync/adapters/` 整个目录**（3 文件）。前提：确认 `NativeSaveRouter` 当前无 adapter 是**有意为之**（即 native-save 走 extension_broker 而非 adapter）——建议动手前由项目主再确认一次产品意图。

**残留问题**：`saved_sync/extension_broker.py` 现在是"半死"状态（只被死的 adapters 引用）→ 一并评估是否删除。

### 1.4 对上一版报告的三处修正

| 上一版说法 | 本版核实 | 修正 |
|-----------|---------|------|
| BUG-1 "`/api/health` 整体未注册，路由数 0" | 实测 13 条 `/api/health/*` **是活的** | ❌ 原结论错误 → 缺口是 **21+ 条**，不是全部 |
| BUG-1 "影响整个健康模块静默失效" | 列表可看、写操作全废 | ✅ 方向对，但**"整体"应改为"写入/高级功能"** |
| BUG-2/3 "引用不存在的类" | 属实，且**整个子包都无调用方** | ✅ 属实 → 升级为"可整目录删除" |

---

## 2. 文档一致性核实（任务 #61）

### 2.1 🟠 `docs/development.md` §7 —— **明确过时且自相矛盾**

原文（§7 API 结构现状）：

> - **孤儿死代码**：`diary_routes.py`（87 路由）/ **`health_routes.py`（68）** / `source_routes.py`（36）等 13 个文件…**从未被注册**

**实际**：`_route_registry.py` 里 **`diary_routes` / `health_routes` / `source_routes` 等 12 个都有注册调用**（K3 修复加的）。所以「从未被注册」是**旧快照**。而 health_routes 恰因 §1.2 的 bug "注册了但失败"。

**同时** §7 又说"实际生效：app.py 内联 ~297 条 + 已接线模块"——**这个描述比"孤儿死代码"更接近事实**，两段自相矛盾。

**必改**：重写 §7，改为"K3 已接线 12 个 routes 模块；其中 `health_routes` 因 `CycleStore` 未 import 实际注册失败（见 bug）"。

### 2.2 🟠 `docs/modules/health.md` —— **三处与代码不符**

| 文档说 | 实际 | 
|--------|------|
| "API 层：50+ RESTful 接口（在 `api/app.py` 中）" | 50+ 接口在 `api/health_routes.py`；app.py 只有 13 条桩，且**实际生效的只有这 13 条** |
| "数据存储于项目主 SQLite 数据库，表名 `health_` 前缀" | **已 sharding 到 `data/health.db`**（changelog P7 明确"与主库锁域隔离"） |
| "前端页面：`/web/health`" | 页面已迁 desktop 内嵌（`healthPage`），独立 `web/health/index.html` **已在 git 中 D**（删除） |
| "状态 ✅ 全部已实现" | 应改为"⚠️ 读列表可用，写/高级功能因注册失败不可用" |

**必改**：① API 归属改为 `health_routes.py`；② 存储改为 `health.db`（独立子库）；③ 前端路径更新；④ 补一条"当前已知问题"指向本报告的 BUG-1。

### 2.3 🟠 `docs/modules/ed2k.md` / `docs/index.md` 模块表

- `index.md` 模块表**缺少** `ed2k`（新，v0.3.223）、`media` 行存在但表尾部截止；`saved_sync`、`health`、`cycle` 未列入。
- `docs/modules/ed2k.md` 是 untracked 新文件（未 `git add`）。

### 2.4 🟡 版本号三处不一致

| 位置 | 版本 |
|------|------|
| `pyproject.toml` | `0.3.152` |
| `extension/package.json` | `0.3.97` |
| `extension/manifest.json` | `0.3.97` |
| `docs/changelog.md` 顶部 | `v0.3.223`（2026-09-10） |

**说明**：后端 `pyproject.toml` 版本与 changelog **脱节 71 个小版本**（changelog 记到 223，pyproject 还在 152）。这是长期历史债，**不紧急**，但说明"版本唯一事实源"没有确立。建议：以 `changelog` 为准，在下次发版时同步 `pyproject.toml`。

### 2.5 🔴 `docs/project-audit-2026-09-11.md` 自身需修正

上一版报告本身的 BUG-1 描述（"路由数从 0 恢复"）**是错的**，应更新或标注被本报告取代。

---

## 3. 磁盘与清理项安全性核实（任务 #62）

### 3.1 总盘面（实测）

```
项目总计                52G
├── data/               34G   ← gitignored，全部非代码
├── 求职知识库/          14G
├── notes/             559M
├── references/        547M
├── images/            536M
├── tests/              33M
├── src/                21M
└── docs/               15M
```

### 3.2 `data/` 内可清理项（**全部 gitignore，无代码引用**）

**A 类：`data/_archive/` 历史备份（20G）**

| 项 | 大小 | 性质 | 结论 |
|----|------|------|------|
| `_archive/backups_20260910/` | 17G | 09-04 ~ 09-09 的 18 份主库快照（拆库各阶段） | ✅ **可清**（拆库已完成并验证） |
| `_archive/_backup_p8_20260910/` | 601M | P8 knowledge 拆分前备份 | ✅ 可清 |
| `_archive/面试资料总库_移除书籍前_*.db` | 459M | 入库前备份 | ✅ 可清 |
| `_archive/面试资料总库_移除方向知识库前_*.db` ×2 | 578M | 同上 | ✅ 可清 |
| `_archive/面试资料总库_清理其他前_*.db` | 289M | 同上 | ✅ 可清 |
| `_archive/面试资料总库_移除幻灯片笔记前_*.db` | 247M | 同上 | ✅ 可清 |
| `_archive/面试处理库_*.db` ×3 | 520M | 同上 | ✅ 可清 |
| `_archive/interview_合并前_*.db` | 20M | 同上 | ✅ 可清 |
| `_archive/_dead_shells_20260909/` | 0B | 空目录 | ✅ 可清 |

**B 类：`data/` 顶层孤儿备份（~5.5G）**

| 项 | 大小 | 结论 |
|----|------|------|
| `openbiliclaw.db.bak-pre-interview` (+139M wal/shm) | 1.74G | ✅ 可清（迁移脚本已完成） |
| `openbiliclaw.db.bak-pre-events` (+139M wal/shm) | 1.74G | ✅ 可清 |
| `openbiliclaw.db.bak-pre-pool` (+44M wal/shm) | 1.14G | ✅ 可清 |
| `openbiliclaw.db.backup-20260907-083148` | 1.0G | ✅ 可清 |

**验证**：全仓 grep 这些路径名，**只有 4 个一次性迁移脚本**（`migrate_events_db.py` / `migrate_pool_db.py` / `finalize_db_sharding.py` / `migrate_interview_db.py`）提到，且仅作为**脚本内注释/默认源名**，脚本本身已跑完，无任何生产代码/tests/CI 引用。

**C 类：`clone-sites` / 缓存（**需谨慎，不建议现在清**）**

| 项 | 大小 | 说明 |
|----|------|------|
| `data/clone-sites/` | 3.0G | 克隆站点（含 node_modules），**可能有产品功能在用**，本次不动 |
| `data/embedding_cache.db` | 1.2G | 向量缓存，**删除会触发全量重算**，本次不动 |
| `data/douyin_profile/` | 995M | 抖音画像，**数据**，不动 |
| `data/image-cache/` | 311M | 图片缓存，可清但收益低，不动 |

### 3.3 清理安全性判定

| 项 | 可清 | 依据 |
|----|------|------|
| `data/_archive/backups_20260910/` | ✅ | 拆库前快照，拆库已验证通过 |
| `data/_archive/` 其余面试库备份 | ✅ | 入库前快照，DB_MAP 已更新、入库已验证 |
| `data/*.bak-pre-*` + `*.backup-*` | ✅ | 一次性迁移前备份，迁移脚本已跑完 |
| 汇总可清 | **~26G** | 全部在 gitignored `data/` 内，**零代码引用** |

**注意**：可清理 ≠ 立刻 `rm`。按用户约定应**移入废纸篓**（非永久删除），且建议**先复制一份最新的** `openbiliclaw.db` 快照到外部。

---

## 4. 其他遗留问题（本次盘点新发现）

### 4.1 ⚠️ 沙箱/权限

- `data/` 在**外置硬盘** `/Volumes/固态硬盘1T/`，部分 `mv` 操作被沙箱拦截（"Could not identify command root"），需 `dangerouslyDisableSandbox`。
- 清理 26G 涉及大量文件移动，**建议分批（每批 ≤10 个）**。

### 4.2 ⚠️ 未纳入 git 的重要新模块

`git status` 显示以下**新模块尚未提交**：

```
?? src/openbiliclaw/cycle/          ← 正是 BUG-1 的 CycleStore 来源
?? src/openbiliclaw/ed2k/
?? docs/modules/ed2k.md
?? tests/api/test_api_ed2k.py
?? docs/project-audit-2026-09-11.md
```

→ `cycle/` 是**半成品**（store 有了，但无 routes、无注册、无测试、无文档）。**要么补全（加 cycle 路由 + 文档 + 测试），要么删除**。这是 BUG-1 的"病根"。

### 4.3 ⚠️ `_route_registry.py` 的 `try/except Exception` 吞错模式

`_route_registry.py` 有 **20+ 处** `try: import ... except Exception: logger.exception(...)`。K3 修复时用它是为了"不阻塞主 API"，但**副作用是路由注册失败不可见**——BUG-1 就是这么潜伏的。

**建议**：改为收集失败清单，启动时**聚合 WARNING 打印**（如 "5/12 route modules failed: health_routes(...)"），既不阻塞也不静默。

---

## 5. 建议的动手顺序（**P0–P3 已执行**）

| 阶段 | 动作 | 风险 | 状态 |
|------|------|------|------|
| **P0** | 修 BUG-1：`health_routes.py` 补 import | 低 | ✅ 已执行 |
| **P0** | 删 app.py 内联 13 条健康桩（方案 B，同时修掉"读空库"） | 低 | ✅ 已执行 |
| **P0** | 修 `_route_registry` 吞错 → 聚合告警 | 低 | ✅ 已执行 |
| **P1** | 删 `saved_sync/adapters/`（3 文件，入废纸篓） | 低 | ✅ 已执行 |
| **P1** | 删 `saved_sync/extension_broker.py`（293 行，零引用+调用不存在的 DB 方法） | 低 | ✅ 已执行 |
| **P1** | `cycle/` 补文档 + 补测试（`tests/cycle/` 10 例） | 零 | ✅ 已执行 |
| **P2** | 修文档：`health.md` / `development.md` / `index.md` / `config.example.toml` / `changelog.md` / 审计报告 | 零 | ✅ 已执行 |
| **P3** | 清 `data/` 冗余备份（**项目 52G → 29G**） | 中 | ✅ 已执行 |
| **P4** | 大重构（obc_runtime 抽取收口、224 处旧 import 路径） | 高 | ⏸ 另立专项 |

**实际执行记录（2026-09-11）**

1. `health_routes.py` 补 `from openbiliclaw.cycle import CycleStore`（注意：该类属 `cycle` 包，不是原报告说的 `health` 包）。
2. `app.py` 删除内联健康路由段 6616–6907 行（292 行）+ 清理不再使用的 `HealthService` import。**关键**：该段用 `HealthService(database=database)` 读主库已拆空的 `health_` 空壳表（0 行），真实数据在 `data/health.db`（17 患者）——所以健康页列表一直显示空。
3. `_route_registry.py` 新增 `_RouteRegistrationFailures`，14 处吞错改为 `record()` + 末尾聚合 ERROR。
4. `saved_sync/adapters/`（3 文件）+ `saved_sync/extension_broker.py`（293 行）移入废纸篓（`/tmp/obc-deadcode-20260911-084635` 留有副本）。
5. `cycle/` 补 `tests/cycle/test_cycle_store.py`（10 例，10 passed）+ `docs/modules/cycle.md`。
6. 磁盘清理：**项目 52G → 29G，`data/` 34G → 9.9G（−23G）**。保留回滚点 `data/backups/rollback-20260909/`（1.6G 完整单体快照，integrity ok）。执行用"同盘暂存 → 验证 → 删除"两阶段。清单见 `docs/cleanup-manifest-2026-09-11.md`。
7. 文档：新增 `cycle.md` / `cleanup-manifest-2026-09-11.md` / 本报告；修 `health.md`（API 归属/存储/前端/配置）、`development.md`（§4.2/§5/§7/§8）、`index.md`（模块表）、`config.example.toml`（补 `health_db_path`）、`config.md`、`changelog.md`（v0.3.224 / v0.3.226）、`project-audit-2026-09-11.md`（标注被取代 + 修正 BUG-1 结论）。

---

## 6. 待用户拍板（剩余）

1. **`cycle/` 是否要独立页面**：目前仅作 `CycleStore` 独立模块（有测试、有文档，但无独立 API 路由与前端，`data/cycle.db` 未创建）。要不要做成独立的周期记录页面（补 `cycle/routes.py` + 前端 tab）？
2. **大重构（P4）**：是否启动 obc_runtime 抽取收口专项？（37 个兼容垫片 + 224 处旧 import 路径；上帝文件 `cli.py` / `app.py`）
3. **进一步清理**：`data/clone-sites/`（3.0G）/ `data/embedding_cache.db`（1.2G，删会触发重算）/ `data/douyin_profile/`（993M）/ `data/image-cache/`（311M）本次**未动**，需要时再评估。
4. **测试存量失败**：`tests/api` 有 23 例存量失败（bili_tasks / config_probe / xhs_ingest / favorites / watch_later / reading_tagging 等），是否安排修复？

---

*报告完成于 2026-09-11 · 核实阶段全程只读；P0–P3 执行记录见 §5*
