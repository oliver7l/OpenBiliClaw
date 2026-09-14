# 模块梳理报告（2026-09-15）

> 目的：按模块逐个回答「**还有哪些没理清、会变成维护负担**」。
> 范围：**日记 / 旅游 / 周末活动 / 阅读库 / 面试 / 健康监控** 六个模块。
> 方法：全部来自当天**只读实测**（读代码、`git log`、`sqlite3 ?mode=ro`、curl 线上 openapi），不引用二手描述。
> 姊妹文档：全局底账见 `docs/module-cleanup-inventory-2026-09-14.md`（部署拓扑①、路由注册② 已收口）。

## 0. 六模块速览（实测）

| 模块 | 代码 | 模块文档 | 方案文档 | 测试 | 线上端点 |
|---|---|---|---|---|---|
| 日记 | `diary/` 13,837 行 / 25 文件 | ✅ `diary.md`（**部分过期**） | 🟡 `日记模块整理与导入管线方案.md`（未入库） | 有（覆盖不全） | `/api/diary/*` |
| 旅游 | `travel/` 465 行 / 2 文件 | ❌ 无 | ❌ 无 | **零** | `/api/travel/*` 8 条 |
| 周末活动 | `weekend/` 1,063 行 / 6 文件 | ✅ `weekend.md`（**两处写错**） | ❌ 无 | 有（仅 store/engine） | `/api/weekend/*` |
| 阅读库 | `reading/` `saved_sync/` `notes/` `conversation_archive/` 合计 3,688 行 | 🟡 部分（`notes.md` / `conversation_archive.md`） | ❌ 无 | 局部（saved_sync 零测试） | `/api/{reading,saved,notes,conversation-archive}/*` |
| 面试 | `interview/` 5,000 行 / 28 文件 | ✅ `interview.md` + `-overview.md`（**部分过期**） | ✅ `面试模块梳理与整合方案.md` | A 有 / **B、C 零测试** | `/api/interview/{job,study,review}/*` 64 条 |
| 健康监控 | `health/` 4,005 行 / 4 文件 | ✅ `health.md`（**多处与事实相反**） | ❌ 无 | **全模块零单测** | `/api/health/*` 35 条 |

「监控」一节按你的说法拆成两半：`health/`（健康档案，代码模块）+ `scripts/health/`（91160 挂号监控脚本，与前者无代码关系）。

---

## 1. 日记模块 `src/openbiliclaw/diary/`

### 🔴 D1. 同一张表两条写入路径，**幂等键不一样**（已亲自复核）

| 路径 | 幂等键 | 标签 | 调用方 |
|---|---|---|---|
| `diary/importer.py:69-77` | **日期 + 来源**（`source=` 参与查询） | 写死 `["乐乐","成长日记"]` | `api/diary_routes.py:249`（`/api/diary/import`） |
| `diary/sources/upsert.py:87-98` | **日期 + 归一化正文前 50 字**（不看来源） | 由参数传入 | `scripts/import_diary.py:122` |

`diary/sources/base.py:81-87` 的 docstring 自称「与历史导入策略保持一致（日期 + 内容前 50 字）」，但 `importer.py` 用的是另一套口径。**后果**：同一天两条不同来源但内容相同的笔记，走 API 会**双双入库**，走脚本会**判重跳过**；反之同来源同一天的两篇不同内容，走 API 会**丢一篇**。静默数据丢失/重复，且没有任何告警。

### 🔴 D2. 硬编码路径，绕过项目根锚点（已亲自复核）

- `diary/store.py:219,221` 用 `Path('data/diary.db')` 作兜底
- `diary/sources/_browser.py:21-23`、`scripts/import_diary.py:30,36` 自己 `parents[4]` 数层级

项目约定是走 `config._project_root()` / `load_config()`（`docs/modules/*` 与 `AGENTS.md` 都写了）。CWD ≠ 仓库根时**静默写到别处**，表现为「导入了但日记没变多」。

### 🟡 D3. 模块文档三处与代码不符（已逐条复核）

- `diary.md:364` 说信念「**已实现未接线**（`advanced_memory.py` 有完整实现与导出，无调用方），当前空转」——**「未接线」不成立**：`diary_routes.py:1224-1285` 已挂 **8 条** `/api/diary/advanced-memory/*`（overview/layers/beliefs/conflicts/build/consolidate/dream-review/search），且 `AdvancedMemoryService` 经 `openbiliclaw.diary` 包导出被调用。
  （「表里 0 行」是另一回事：`diary_beliefs` 确实 0 行，而 `diary_entries` 有 928 行——文档把「数据空」写成了「未接线」。）
- `diary.md:406` 写「有道云/WPS 首次实跑需用 `--debug-dump` 校对」，但 `scripts/import_diary.py:48-61` **没有这个参数**（`debug_dump` 只是 `sources/{youdao,wps}.py` 构造函数的入参）⇒ **文档教的操作从 CLI 根本够不到**。
- `diary.md:33` 写「支持 MindBack、有道云、苹果备忘录、WPS、乐乐成长等 **5 个来源**」，但 `sources/__init__.py:20` 的 `SOURCE_NAMES` 只有 **3 个**（`apple / youdao / wps`）——MindBack 与乐乐是另外的管线，不属于这个包。

（注：`docs/modules/diary.md` 此刻正被另一条工作线修改，以上为**当前工作树**状态。）

### 🟡 D4. 两个「洞察」服务并存，写同一张表

`diary/insights.py`（16.8KB）与 `diary/insight_engine.py`（30.7KB）都被 `diary_routes.py` 调用；后者独写 `diary_insight_patterns`（`insight_engine.py:164,487`）。另有 `self_evolution/` 下的同名 `insights.py` / `knowledge_graph.py`。同一概念三层实现，改一处容易漏另两处。

### 🟡 D5. 测试覆盖不全

`tests/diary/test_diary_sources.py` 只覆盖 `base + apple + upsert`；`sources/youdao.py`、`wps.py`、`_browser.py`（共 364 行）**零测试**；`scripts/import_diary.py` 的 CLI 与 `/api/diary/import` 路由**零测试**——而这正是 D1 出问题的两条路径。

### 🟢 D6. 遗留一次性脚本

`scripts/optimize_diary_html.py:9` 硬编码绝对路径 `/Volumes/固态硬盘1T/.../index.html`；与 `_v2` 版是同一件事的两版（v2 自述 v1 有正则陷阱），只有 `docs/references-index.md:59-60` 提到，**不知道该跑哪个**。

### 🟢 D7. 未入库（另一条工作线在改）

`diary/sources/`、`scripts/import_diary.py`、`tests/diary/test_diary_sources.py`、`docs/plans/日记模块整理与导入管线方案.md` 全是未提交状态。

**未发现死代码**：`diary/` 下各文件都经 `__init__.py` 或 routes 接线。

---

## 2. 旅游模块 `src/openbiliclaw/travel/`

### 🔴 T1. 「单一数据源」的说法在代码里不成立（已亲自复核）

- 定稿 `data/travel/新疆8天精确时间游玩计划-最终定稿.md`（34KB）**不被任何代码引用**
- 前端行程走 `GET /api/travel/itinerary` → 读 `data/travel.db`（`travel/routes.py:255-305`）
- 两处内容独立存在（md Day4 =「禾木→白哈巴」，db `trip_days` day4 =「禾木→白哈巴（核心调整日）」）
- **全仓库没有任何脚本写 `travel.db`**，也没有 md→db 生成器 ⇒ 改 md **不会**反映到前端，db 也**无法从 md 重建**

这是「改完发现页面没变」的典型配方。

### 🔴 T2. 同一个文件里两套路径策略（已亲自复核）

`travel/routes.py:258,313,369,421` 四处写 `Path("data/travel.db")`（CWD 相对）；而 md/机票路径走构造参数 `data_path`（`:47-57`，配 `[travel] data_path`）。**当前 `config.toml` 里根本没有 `[travel]` 段** ⇒ 预算/机票接口用一个机制，行程接口用另一个机制；CWD 一变，4 个接口 404，另外两个照常 200——极难排查。

### 🟡 T3. 无模块文档、零测试

`docs/modules/` 无 `travel.md`；`tests/` 无任何 travel 测试（`tests/api/test_api_route_regressions.py` 里也没有）。4 条 DB 路由 + md 解析 + baseline 告警 **零覆盖**。

### 🟡 T4. 业务常量与 md 解析写死在路由文件里

`travel/routes.py:25-44` 硬编码 `CITY_LABELS / BASELINES / AIRPORT_FEE / FUEL_FEE`；`:210,230` 用**中文字符串匹配** md 表格标题 ⇒ 改 md 标题即静默少解析。

### 🟢 T5. 前端写死「8天」

`web/js/views/travel.js:251`，天数据实来自接口——改行程天数不会跟着变。

---

## 3. 周末活动模块 `src/openbiliclaw/weekend/`

### 🔴 W1. 全靠 CWD 相对路径，且**跨模块读别人的库**（已亲自复核）

- `weekend/store.py:20` `DEFAULT_DB_PATH = "data/weekend.db"`
- `weekend/routes.py:48-49` `diary_db="data/diary.db"`、`douban_db="data/douban.db"`
- `weekend/cli.py:23` `_SEED_DEFAULT`；运行时循环里也写死（`runtime/_refresh_loop_supervision_mixin.py:802`）

非仓库根启动 ⇒ 日记/豆瓣读空、`spots` 为 0，仍会**照常输出「为什么适合你」**——假结果比报错更糟。

### 🟡 W2. 配置只接了一半

`WeekendConfig` 已有 `db_path / seed_path`（`config.py:775,779`），`api/_route_registry.py:342-352` 也认 `db_path`；但 `weekend/cli.py:27,119` **忽略这两个字段** ⇒ CLI 与 API 可能读写不同库。

### 🟡 W3. 文档两处写错（已亲自复核）

- `weekend.md:87` 写 `douban_wish()` 返回 `{books,movies}`，实际 `weekend/engine.py:74` 是 `{book,movie}`
- `weekend.md:42,94,149` 写时间窗「18:00–23:00」，实际默认 20 点（`engine.py:316`，且 `weekend.md:126` 自己也写 20:00）

### 🟡 W4. `routes.py` 零测试

`tests/weekend/test_weekend_module.py` 只测 `store` / `engine`，路由层（含跨库读取、seed 逻辑）零覆盖。

### 🟢 W5. `store.py:100,128` 函数内重复 import

---

## 4. 阅读库模块（边界最乱，跨 4 个包）

### 🔴 R1. 「阅读库」其实是**三套并存**（已亲自复核）

| 名字 | 表 / 库 | 行数 | 前端入口 |
|---|---|---|---|
| 「阅读库」 | `data/content.db` → `articles` | **90,182** | 桌面 `desktop/index.html:103` |
| 「已读库」 | `openbiliclaw.db` → `read_archive` | 108 | 桌面 `index.html:104` |
| 「对话归档」 | `openbiliclaw.db` → `conversation_archive` | 137 | 两侧都有 |
| 「笔记」 | `openbiliclaw.db` → `notes` | **2** | **两侧都没 UI** |

同名三个概念、三条 API、三份文档互不覆盖。**没有一处写清楚谁是谁的真值源**——这是本模块最大的维护成本。

### 🔴 R2. 稍后再看/收藏双实现且跨库（已亲自复核）

- `content.db.watch_later` / `favorites`（各 **1 行**）← 服务 `/api/watch-later`、`/api/favorites`，前端 `desktop/.../app.js:32`
- `openbiliclaw.db.saved_memberships`（**0 行**）← 服务 `/api/saved/{kind}`
- `upsert_saved_membership` **不写** legacy 表，而 `remove_saved_membership`（`saved_sync/...:207-211`）**单边清 legacy**

两表静默漂移：前端看到的与 `saved_sync` 写入的不是同一份数据。

### 🔴 R3. saved_sync 的「原生保存」是一条**不可达**路径（已亲自复核）

- `api/runtime_context.py:948` 构造 `NativeSaveRouter()`，**没有传任何 adapter**；全仓**没有任何** `router.register(...)` 调用
- 于是 `saved_sync/service.py:352` 的 `self._router.route(...)` 对所有平台必抛 `UnsupportedNativeSaveError`（`:369-379`）
- 佐证：`native_save_states` 表 **0 行**

⇒ 857 行的 `service.py` + 108 行 `router.py` 里的这条主路径在生产上从未跑通过。要么补 adapter，要么明确标死。

### 🟡 R4. 「已读库」有**两个**导入器，扫同一个目录（已亲自复核）

| 导入器 | 目标 | 调用方 |
|---|---|---|
| `scripts/content_library/import_readlib_to_db.py:28`（`READLIB = notes/已读库`） | `read_archive`（108 行） | 手动跑；`README.md:73` + `tests/test_import_readlib_feed.py` |
| `notes/service.py:125 import_from_read_archive`（同样扫 `notes/已读库` 四件套） | `notes`（**2 行**） | `api/notes_routes.py:244`、`cli/_cmd_notes.py:174` |

同一份文件被两套 schema 认知解析，长期必然分叉。建议保留 `read_archive` 一条。

### 🟡 R5. conversation_archive 写路径不唯一（潜伏雷）

`scripts/content_library/sync_library_to_db.py:147` 按 `md_file` 幂等；`api/conversation_archive_routes.py:106-118` 却按 `seq` 写入且**不写 v2 列** ⇒ 该行 `md_file=''`，`/{id}/raw-md` 必 404，且此后 sync 再也匹配不上。当前 137 行 `md_file` 恰好全非空，所以还没爆。

### 🟡 R6. 其他

- v2 五列的 `ALTER` 逻辑两处复制：`sync_library_to_db.py:44-55` vs `conversation_archive/store.py:132-142`
- 硬编码路径：`conversation_archive_routes.py:18` 用 `parents[3]`；`library_routes.py:104` 兜底 `db_path="data/content.db"`
- `/api/reading/*` 端点分裂在两个文件：`reading_routes.py`（建议/简报/统计/标签/相似）+ `saved_sync_routes.py:235-320`（items/count/sources/tags/status/search）
- 你记忆里的 `notes.db` / `conversation_archive.db` / `reading*.db` **都不存在**——数据全在 `openbiliclaw.db` + `content.db`（边界因此更难辨认）

### 🟡 R7. 测试缺口

`saved_sync` **零测试**；`/api/reading/{items,count,sources,tags,status,search}` 六端点零测试；已读库导入无测试。

### 🟢 R8. 孤儿前端资产

`web/js/views/library.js`（149 行，导出 `initContentLibraryView`）无人引用；`desktop/assets/js/saved-sync-core.js` 未被 `desktop/index.html` 加载。另有 `notes` 2110 行代码 + 1217 行测试 对 2 行生产数据，投入产出失衡。

**前端不一致**：阅读库/已读库只有桌面有；移动 `/m` 只有 `watchLater/favorites/conversation`；`/api/notes` 两侧都没有 UI。

---

## 5. 面试模块 `src/openbiliclaw/interview/`（期0–期4 重构后）

### 🔴 I1. B 子系统的 DB 路径**不走配置**（已亲自复核）

`study/routes.py:30-33` 把 `DB_PATH / INTERVIEW_DB_PATH / APPLICATION_DB_PATH / AMMO_DIR` 写死为模块常量（基于 `_paths.PROJECT_ROOT`）；而 A（`job/routes.py:214-216`）与 C（`review/routes.py:49-51`）都读 `settings.storage.*`。

`config.py:640-646` 的 `StorageConfig` 里有 `interview_db_path` / `health_db_path` / `knowledge_db_path`…**但根本没有 `interview_questions_db_path`**。⇒ 改数据目录时 B 必漏，静默读写错库（今天恰好默认值相同所以没暴露）。

### 🔴 I2. B、C 两个子系统**零测试**（已亲自复核）

`tests/` 中搜不到 `InterviewQuestionStore` / `InterviewReviewStore` / `InterviewReviewService`，也没有 `/api/interview/study|review` 的用例。只有 A 有 `tests/interview/test_interview_engine.py`(20) 与 `tests/api/test_api_interview.py`(14)。⇒ **B 24 条 + C 10 条端点、两层 store/service 完全没有回归网**（重构期3 刚动过它们的文件布局）。

### 🟡 I3. 12 个兼容垫片全是死代码（已亲自复核）

`interview/{routes,engine}.py`、`review_{models,routes,service,store}.py`、`questions/{__init__,cli,models,store,seed_iq_questions}.py`、`api/_interview_routes.py`。

实测：**全仓没有任何代码 import 这些旧路径**（`_route_registry.py:292` 直连 `interview.study.routes`；旧路径唯一的真实导入是 `interview/_paths`，那是正经模块）。它们只为「旧导入路径保活」而存在——而这是个人项目，外部消费者几乎不存在。

注意：`tests/api/test_api_route_regressions.py` 里的白名单**正在主动保活** `_interview_routes`（上一轮我加的，当时理由是「期3 有意留」）。**建议连同白名单条目一起摘**。

### 🟡 I4. 路径约定被绕过

`study/cli.py:39`、`study/seed_iq_questions.py:17` 仍用 `Path(__file__).resolve().parents[4]`——而 `interview/_paths.py:16` 正是为了消灭这种 `parents[N]` 而建的。再挪一层目录就静默错位。

### 🟡 I5. 文档与代码不符

- `interview.md:24,26,63,64` 的组件表/目录树仍把引擎与路由写作 `engine.py` / `routes.py`（实际主体已迁到 `job/engine.py`、`job/routes.py`，顶层这两个文件现在是 8 行的垫片）
- `interview.md:67` 只提了 `interview.engine` / `interview.routes` **两个**垫片，实际有 **12 个**（还含 `review_*.py` 四个与 `questions/*` 五个）
- `docs/interview-reading-tracker.md:122` 仍在教 `python -m ...interview.questions.cli` 旧路径

### 🟢 I6. 其他

移动端缺 B/C 视图（`interview-overview.md:10` 已如实标注）；`interview/__init__.py:31-46` 只导 A+C 不导 B，包级 API 不对称。

**未发现问题**：三库边界清楚（与 `interview-overview.md:56-70` 一致）；期2 双挂载实现干净（三份 router 都无 prefix）；前端无旧前缀残留。

---

## 6. 健康监控模块 `src/openbiliclaw/health/`

先说清一件事：**「健康档案」与「挂号监控」是两件不相干的东西**。

- `health/` 包 = 个人/家庭医疗档案（35 条 `/api/health/*` 端点，数据在 `data/health.db`）
- `scripts/health/91160_check_slots.py` = 挂号号源监控（只 print JSON，硬编码医院/医生 ID，凭证在 `~/.workbuddy/91160-monitor/`，与 `health/` 包**无任何代码关系**，也无 cron/pm2 托管）

两件事共用一个「health」命名，本身就值得在文档里点明。

### 🔴 H1. 时间线分页**静默丢数据**（已亲自复核）

`health/store.py:1699-1884 get_timeline()`：对**每一个**来源表各自执行 `LIMIT ? OFFSET ?`（`:1712` 等 8 处），把结果全部 append 进同一个 list，最后 `events.sort(...)` 后 `return events[:limit]`（`:1884`）。

问题有两层：
1. `offset` 是**逐表**应用的，不是全局分页 ⇒ `GET /api/health/timeline?offset=N`（`health_routes.py:863-869`）拿到的不是第 N 页
2. 末尾 `[:limit]` 会在合并排序后再次截断 ⇒ **每个来源各取 limit 条**的目的被抹掉，且被截掉的条目静默消失

零测试覆盖，所以从没被发现。今天数据量小（`health_encounters` 2 行）还不显形。

### 🔴 H2. 主库里 15 张空壳表，而文档说「已清理」（已亲自复核）

```
data/openbiliclaw.db  → 15 张 health_* 表，全部 0 行
data/health.db        → 同名 15 张表，有真实数据（health_patients=17 / encounters=2 / conditions=6 / medications=1）
```

`docs/modules/health.md:55` 写「残留空壳表已清理」——**与事实相反**；`scripts/migrate_health_db.py:126-134` 的 `drop` 步骤从未执行。

风险点：`health/store.py:440` 的 `HealthStore(database=...)` 仍可直接写主库，误用即**双写**。

### 🟡 H3. `store.py` 该拆

单类 `HealthStore` 覆盖 `:431-2242`（≈1812 行 / 89KB），~100 个方法覆盖 15 类实体，零分层。最长三个：`get_timeline`（`:1699`，186 行）、`get_medication_adherence`（`:2082`，118 行）、`check_drug_interactions`（`:2200`，43 行）。每加一类实体都往同一文件堆。

### 🟡 H4. 全模块零单测（已亲自复核）

`tests/` 里搜不到 `HealthService` / `HealthStore`，也没有 `tests/health/`。注意别被假信号骗：tests 里 33 处 `api/health` 全是**系统探针** `/api/health`（`app.py:1774`）的测试，与医疗档案无关。⇒ 4005 行零覆盖。

### 🟡 H5. 命名冲突：`/api/health` 有两层含义

`/api/health`（系统探针，`app.py:1774`，`app.py:1317` 还拿它做运维判定）与 `/api/health/*`（医疗档案，35 条）同名；前端 `health-app.js:6` 又用 `/api/health` 当医疗基址。运行时不冲突，但语义长期混淆。

### 🟡 H6. 文档漂移（已亲自复核）

- `health.md:17,272-275` 教你跑 `scripts/import_health_data.py` —— **该脚本不存在**
- `health/__init__.py:9` 仍写「存储于主 SQLite 数据库」，与 `health.md:7` 自相矛盾（实为 `health.db`）
- `health.md:14` 的「68 条」是装饰器数，唯一路径实为 **35**
- `health.md:12`「13 张表」实为 **15**（漏 `appointments`、`medication_logs`；「时间线辅助表」不存在）

**未发现问题**：表前缀隔离与 `health.db` 落库正确（`config.py:643` + `health_routes.py:57-68` 优先级链完整）；`app.py` 已无内联健康路由。

---

## 7. 跨模块的共性问题（一次看清）

| 共性问题 | 命中模块 | 说明 |
|---|---|---|
| **CWD 相对路径 / `parents[N]`** | 日记、旅游、周末、阅读库、面试 B | 同一个 bug 家族，项目已有 `config._project_root()` 与 `interview/_paths.py` 两个现成工具，只是没被统一采用 |
| **模块文档与代码脱节** | 日记、周末、面试、健康（+ 健康最严重，5 处） | 文档写的表数/键名/脚本/端点都对不上 |
| **关键路径零测试** | 旅游（全零）、健康（全零）、面试 B/C（全零）、阅读库 saved_sync（全零） | 合计 ≈ 9,100 行代码无回归网 |
| **同一件事两套实现** | 日记（两条导入路径）、阅读库（稍后读双写 + 已读库双导入器）、健康（主库/子库同名表） | 都是「静默不一致」而非「报错」 |
| **兼容垫片没人摘** | 面试 12 个（本期）、`api/_interview_routes` | 原本约定「下个大版本摘」，但没有机制保证 |
| **命名复用造成语义混淆** | `/api/health` 两种含义、三个「阅读库」、`health` 包 vs 挂号监控脚本 | 读代码的人要额外花时间才不搞混 |

## 8. 建议处理顺序（按「风险 vs 收益」排）

| 批次 | 内容 | 风险 | 为什么排这里 |
|---|---|---|---|
| **③** | **质量门禁**：mypy 55 → 0、ruff 3 → 0 | 低 | 上一次盘点已定；纯机械，且是 AGENTS.md 要求 |
| **④** | **健康模块三修**：H1 时间线分页（真 bug）+ H2 空壳表清理 + H6 文档更正 | 低 | H1 是会静默丢数据的真缺陷，H2 只是删表，两者都不动业务逻辑 |
| **⑤** | **路径统一**：六模块的 CWD 相对路径全部改走 `config._project_root()` / `load_config()` | 低-中 | 一次消灭 20+ 处同类隐患；建议配一条「禁 `parents[N]` / 禁 `Path("data/...")`」的回归测试 |
| **⑥** | **旅游模块补齐**：T1 定 md↔db 真值源（并写一个 md→db 生成器）+ T3 模块文档 + 基础测试 | 中 | 需要你先拍板「谁是真值源」 |
| **⑦** | **阅读库术语与真值源定案**：R1 三套并存写清边界 + R2 稍后读定一真值源 + R3 saved_sync 决定「补 adapter」还是「标死删除」 | 中 | 涉及数据迁移，必须先定语义 |
| **⑧** | **面试 B/C 补测 + 摘 12 个垫片**（含改掉测试白名单） | 中 | 摘垫片是删代码，先补测再删更稳 |
| **⑨** | **日记 D1 统一幂等键** + D2 路径 + D3 文档更正 | 中 | D1 会改变导入行为，需先确认「按来源去重」还是「按内容去重」才是你要的 |
| **⑩** | **健康 `store.py` 拆分**（H3）、周末 W1-W5 收尾 | 中 | 体量工作，收益是后续可维护性 |

## 9. 需要你先拍板的四件事

1. **旅游**：md 定稿与 `travel.db`，哪个是**唯一真值源**？（我倾向「md 是源、db 是派生」，但要你确认，因为可能有手工在 db 里改过的数据）
2. **阅读库**：稍后再看/收藏，真值源定 `content.db`（前端在用、有数据）还是 `openbiliclaw.db.saved_memberships`（设计更完整、但 0 行）？
3. **saved_sync 原生保存**：**补 adapter 让它活**，还是**确认废弃、删掉这条路径**（含 857 行 service 里的相关部分）？
4. **面试 12 个垫片**：现在摘，还是留到下一个版本？（我的建议：现在摘，因为它同时让「`*_routes.py` 必须贡献端点」那条回归测试要写白名单例外——垫片留着就是长期噪音）

> 本文只做诊断，**未改任何代码/数据**。所有 🔴 结论均已亲自复核（读源码 / `sqlite3 ?mode=ro` / `curl openapi.json`）。
