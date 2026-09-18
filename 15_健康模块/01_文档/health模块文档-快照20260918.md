# 健康管理系统

> 个人与家庭医疗档案管理，参考 MediKeep (afairgiant/MediKeep)、HealthLog (MBombeck/HealthLog)、OwnHealthRecord (petrk94/ownhealthrecord) 数据模型与功能设计。

## 概述

`health/` 包实现了完整的个人健康档案管理系统，覆盖患者档案、就诊记录、健康问题追踪、用药管理、化验结果（含项目明细）、检查记录、医生信息、文档附件、预约复诊、服药依从性**十大实体**。数据存储于**独立子库 `data/health.db`**（db sharding P7，与主库锁域隔离），表名使用 `health_` 前缀。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | 10 类医疗实体的 Pydantic 模型与枚举 | `models.py`（816 行） |
| 存储层 | 11 张 SQLite 表管理、CRUD、检索、统计、时间线 | `store.py`（1,861 行） |
| 业务层 | HealthService 封装存储，提供患者摘要、化验趋势、依从率 | `service.py`（438 行） |
| API 层 | 25 条路径 / 54 个操作（RESTful），**单一来源** `register_health_routes(app, ctx)` | `api/health_routes.py`（888 行） |
| 周期记录 | 独立的经期/周期事件记录（`cycle_records` 表，存 `data/cycle.db`） | `cycle/store.py` |
| 前端页面 | 桌面内嵌健康档案管理页面（11 个标签页，`healthPage` 视图） | `web/desktop/assets/js/health-app.js` |
| 挂号监控 | ⚠️ `scripts/health/91160_check_slots.py` 是**挂号号源监控**，与本模块**无代码关系**，仅同名 | `scripts/health/` |

> ⚠️ **命名澄清**：精确路径 `GET /api/health` 是**系统探针**（`app.py` 内联，判断服务存活），
> 与本模块的前缀 `/api/health/*`（医疗档案）**不是同一回事**——两者只是共用前缀。
> 另：曾经文档提到的 `scripts/import_health_data.py` 批量导入脚本**从未入库**（全仓不存在），
> 不要再按该说明操作。

> **奥卡姆瘦身（2026-09-16）**：按「只留有数据的」原则删掉了 **4 个零行实体**
> ——`allergies`（过敏史）/ `vitals`（生命体征）/ `immunizations`（疫苗接种）/
> `insights`（AI 健康洞察，连同 `interpret_lab_result` / `interpret_procedure` 两个
> LLM 解读方法）——实测 `data/health.db` 这四张表都是 0 行。同时 DROP 了这四张空表
> （无损）、删掉对应端点与前端标签页，`store.py` 2,253 → 1,861 行、`models.py`
> 987 → 816 行、`service.py` 636 → 438 行、路由 68 → 54 个操作。
> **要加回来**：从 git 历史取回 `models.py` / `store.py` / `health_routes.py` 的对应段
> （DDL 会被 `_SCHEMA_SQL` 自动建表），再把 `scripts/migrate_health_db.py` 的
> `HEALTH_TABLES` 加回去即可。


> **API 单一来源说明（2026-09-11 修正）**：健康 API **只在 `api/health_routes.py` 中定义**，由 `api/_route_registry.py` 统一注册。历史上 `api/app.py` 曾内联 13 条只读列表路由作为临时兜底，且因构造 `HealthService(database=...)` 读的是主库中已拆空的 `health_` 空壳表（0 行），页面一直显示空数据 —— 该内联段已于 2026-09-11 删除。


## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 患者档案管理 | ✅ | 多成员档案（本人/配偶/子女/父母），CRUD |
| 就诊记录 | ✅ | 门诊/急诊/住院/体检/复查，支持筛选搜索 |
| 健康问题追踪 | ✅ | 疾病/健康问题状态管理（活跃/慢性/已缓解），严重程度 |
| 用药记录 | ✅ | 处方药/OTC/保健品，剂量频率，用药状态 |
| 化验结果 | ✅ | 主表+项目明细，自动标记异常（高/低/危急），历史趋势查询 |
| 检查记录 | ✅ | CT/MRI/超声/内镜/手术，需复查标记 |
| 预约/复诊 | ✅ | 预约状态与类型管理 |
| 用药日志 | ✅ | 依从性记录与 `get_medication_adherence` 依从率统计 |
| 医生信息 | ✅ | 医生姓名、职称、专科、医院、科室、联系方式、备注 |
| 文档附件 | ✅ | 看病资料原件管理（报告/处方/病历/证明/影像），分类、标签、搜索 |
| 健康时间线 | ✅ | 按时间聚合所有健康事件（就诊/检查/化验/用药/问题/文档），一目了然 |
| 统计概览 | ✅ | 各模块计数、待复查提醒、活跃问题/用药 |
| 患者摘要 | ✅ | 单患者完整档案汇总（各模块数量+活跃项） |
| 化验趋势 | ✅ | 按项目名查询历史数值变化 |
| 前端页面 | ✅ | 桌面内嵌 `healthPage`，11 标签页，表单录入 |
| 周期记录 | ✅ | 独立 `cycle/` 模块，`cycle_records` 表（日期/间隔天数/备注），存 `data/cycle.db` |
| 数据导入 | ✅ | 脚本批量导入用户真实看病资料 |

## 数据模型

### 存储位置

| 数据库 | 内容 | 连接方式 |
|--------|------|----------|
| `data/health.db` | 15 张 `health_*` 表（13 类实体 + 化验明细 `lab_components` + 用药日志 `medication_logs`） | `HealthService(db_path=...)`，独立连接 + PRAGMA（WAL / busy_timeout / synchronous） |
| `data/cycle.db` | `cycle_records`（周期事件） | `CycleStore(db_path=...)`，与 health.db 同目录，隔离锁域 |

> 主库 `data/openbiliclaw.db` 中**不再保留**任何 `health_*` 表（P7/P9 拆分时已迁出；
> **残留的 15 张空壳表已于 2026-09-15 经 `scripts/migrate_health_db.py drop` 清除**，
> drop 前的全库备份在 `data/backups/openbiliclaw_pre_p7_health_drop_*.db`）。
> 路径解析优先级：`config.storage.health_db_path` > 主库同目录 `health.db` > `data/health.db`。

### 核心实体关系

```
health_patients (患者)
  ├── health_encounters (就诊记录) 1:N
  ├── health_conditions (健康问题) 1:N
  ├── health_medications (用药记录) 1:N
  ├── health_lab_results (化验主表) 1:N
  │     └── health_lab_components (化验明细) 1:N
  ├── health_procedures (检查/手术) 1:N
  ├── health_documents (文档附件) 1:N

health_doctors (医生信息) — 独立表，可被就诊/检查引用
cycle_records (周期记录) — 独立表，不与 health_patients 关联
```

### Patient（患者档案）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 唯一 ID |
| `full_name` | str | 姓名 |
| `gender` | str | 性别 |
| `birth_date` | str \| None | 出生日期 |
| `blood_type` | str | 血型 |
| `height_cm` / `weight_kg` | float \| None | 身高/体重 |
| `phone` | str | 联系电话 |
| `relationship` | str | 与本人关系：self/spouse/child/parent/other |
| `notes` | str | 备注 |

### Encounter（就诊记录）

| 字段 | 类型 | 说明 |
|------|------|------|
| `encounter_type` | EncounterType | outpatient/emergency/inpatient/physical/follow_up/telehealth/other |
| `priority` | EncounterPriority | routine/urgent/emergency |
| `chief_complaint` | str | 主诉 |
| `present_illness` | str | 现病史 |
| `physical_exam` | str | 体格检查 |
| `diagnosis` | str | 诊断 |
| `treatment_plan` | str | 治疗计划 |
| `follow_up_instructions` | str | 随访指示 |
| `cost_yuan` | float \| None | 费用 |

### LabResult + LabTestComponent（化验结果）

化验采用主从表结构：主表记录一次化验的基本信息，明细表记录每个化验项目的数值、单位、参考范围和状态。

`LabComponentStatus` 自动标记：`normal` / `high` / `low` / `critical` / `pending`

### Procedure（检查/手术）

| 字段 | 类型 | 说明 |
|------|------|------|
| `procedure_type` | ProcedureType | imaging/lab/endoscopy/surgery/pathology/ecg/other |
| `findings` | str | 检查所见 |
| `conclusion` | str | 诊断结论 |
| `abnormal_summary` | str | 异常摘要 |
| `follow_up_recommendation` | str | 复查建议 |
| `needs_follow_up` | bool | 是否需要复查（用于待复查提醒） |

## 公开 API

### 基础路径

所有健康管理 API 以 `/api/health` 为前缀。

### 统计概览

- `GET /api/health/stats` — 全局统计（各模块计数、待复查数）

### 患者档案

- `GET /api/health/patients` — 患者列表
- `POST /api/health/patients` — 创建患者
- `GET /api/health/patients/{id}` — 患者详情（含各模块统计摘要）
- `PUT /api/health/patients/{id}` — 更新患者
- `DELETE /api/health/patients/{id}` — 删除患者

### 就诊记录

- `GET /api/health/encounters` — 列表（支持 patient_id、type、日期范围、search 筛选）
- `POST /api/health/encounters` — 创建
- `GET /api/health/encounters/{id}` — 详情
- `PUT /api/health/encounters/{id}` — 更新
- `DELETE /api/health/encounters/{id}` — 删除

### 健康问题

- `GET /api/health/conditions` — 列表（支持 patient_id、status 筛选）
- `POST /api/health/conditions` — 创建
- `GET /api/health/conditions/{id}` — 详情
- `PUT /api/health/conditions/{id}` — 更新
- `DELETE /api/health/conditions/{id}` — 删除

### 用药记录

- `GET /api/health/medications` — 列表（支持 patient_id、status 筛选）
- `POST /api/health/medications` — 创建
- `GET /api/health/medications/{id}` — 详情
- `PUT /api/health/medications/{id}` — 更新
- `DELETE /api/health/medications/{id}` — 删除

### 化验结果

- `GET /api/health/lab-results` — 列表（支持 patient_id、search 筛选）
- `POST /api/health/lab-results` — 创建（支持 components 明细一并写入）
- `GET /api/health/lab-results/{id}` — 详情（含全部项目明细）
- `PUT /api/health/lab-results/{id}` — 更新
- `DELETE /api/health/lab-results/{id}` — 删除
- `GET /api/health/lab-trend?patient_id=&test_name=` — 某项目历史趋势

### 检查记录

- `GET /api/health/procedures` — 列表（支持 patient_id、type、needs_follow_up 筛选）
- `POST /api/health/procedures` — 创建
- `GET /api/health/procedures/{id}` — 详情
- `PUT /api/health/procedures/{id}` — 更新
- `DELETE /api/health/procedures/{id}` — 删除

### 医生信息

- `GET /api/health/doctors` — 列表（支持 specialty、search 筛选）
- `POST /api/health/doctors` — 创建
- `GET /api/health/doctors/{id}` — 详情
- `PUT /api/health/doctors/{id}` — 更新
- `DELETE /api/health/doctors/{id}` — 删除

### 文档附件

- `GET /api/health/documents` — 列表（支持 patient_id、document_type、encounter_id、search、分页）
- `POST /api/health/documents` — 创建
- `GET /api/health/documents/{id}` — 详情
- `PUT /api/health/documents/{id}` — 更新
- `DELETE /api/health/documents/{id}` — 删除

### 健康时间线

- `GET /api/health/timeline?patient_id={id}` — 获取患者健康时间线，聚合所有类型事件

## 前端页面

桌面端**内嵌视图** `healthPage`（顶栏「❤ 健康」tab 进入），与专题/媒体/ed2k 同款统一三栏布局；`health-app.js` 顶部常量 `const API = '/api/health'`，全部请求走该前缀。

页面包含 11 个标签页（2026-09-16 奥卡姆剃刀瘦身：删掉过敏史/生命体征/疫苗接种三个
空实体标签与 AI 解读按钮，后面加了数据再恢复）：
1. **概览** — 统计卡片、活跃健康问题、当前用药、待复查提醒
2. **就诊记录** — 列表+详情弹窗+新增表单
3. **健康问题** — 状态/严重程度标签，新增表单
4. **用药记录** — 用药状态管理，新增表单
5. **化验结果** — 异常项高亮，明细表格，支持批量录入
6. **检查记录** — 需复查标记，详情弹窗
7. **健康时间线** — 按时间聚合所有健康事件，彩色图标区分类型
8. **文档资料** — 看病资料原件管理，分类/标签/搜索，卡片式展示
9. **医生信息** — 就诊医生和医疗机构管理
10. **预约/复诊** — 预约状态与类型管理
11. **用药日志** — 依从性记录与依从率

## 配置项

| 字段 | 默认 | 说明 |
|------|------|------|
| `storage.health_db_path` | `data/health.db` | 健康子库路径（`config.py` 中 `StorageConfig.health_db_path`） |

`data/cycle.db` 与 `health.db` 同目录解析（`config.storage.health_db_path` 同级），无独立配置项。

## 设计决策

1. **参考 MediKeep 但适配 SQLite**：MediKeep 使用 PostgreSQL + SQLAlchemy，本模块适配项目的 SQLite + 原生 sqlite3 模式，参考 diary 模块的 Store 类设计。
2. **表名前缀隔离**：所有健康相关表使用 `health_` 前缀，避免与项目其他模块表冲突。
3. **化验主从表结构**：一次化验包含多个项目，采用主表+明细表设计，支持按项目查询历史趋势。
4. **独立前端页面**：与主 SPA 解耦，通过 `/health` 独立挂载，不影响现有页面路由。
5. **多患者支持**：支持家庭多成员档案管理，通过 `relationship` 字段区分。
6. **待复查提醒**：Procedure 表的 `needs_follow_up` 字段驱动概览页的待复查提醒。
7. **参考 HealthLog 的文档库设计**：文档附件模块参考 HealthLog 的 document vault 设计，支持分类、标签、搜索，存储看病资料原件的元数据和摘要。
8. **参考 OwnHealthRecord 的医生管理**：医生信息模块参考 OwnHealthRecord 的 doctors-list 设计，记录医生联系方式和就诊历史。
9. **健康时间线聚合**：时间线 API 聚合就诊、检查、化验、用药、健康问题、文档、预约等事件，按日期排序，让健康历程一目了然。（2026-09-16 瘦身时移除了「疫苗/体征」两个来源。）

## 数据导入

> ⚠️ **本节曾被本文误导**：早先版本教用户运行 `scripts/import_health_data.py`，
> 但该脚本**从未存在于仓库中**（2026-09-15 全仓核实）。如需批量导入真实看病资料，
> 直接调用 `HealthService` 的各 `create_*` 方法（见 `store.py`），或另写一次性脚本。
