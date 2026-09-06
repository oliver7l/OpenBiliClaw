# 健康管理系统

> 个人与家庭医疗档案管理，参考 MediKeep (afairgiant/MediKeep)、HealthLog (MBombeck/HealthLog)、OwnHealthRecord (petrk94/ownhealthrecord) 数据模型与功能设计。

## 概述

`health/` 包实现了完整的个人健康档案管理系统，支持患者档案、就诊记录、健康问题追踪、用药管理、化验结果（含项目明细）、检查记录、过敏史、生命体征、疫苗接种、医生信息、文档附件、AI报告解读、健康时间线等十三大模块。数据存储于项目主 SQLite 数据库，表名使用 `health_` 前缀。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | 13 类医疗实体的 Pydantic 模型与枚举 | `models.py` |
| 存储层 | 13 张 SQLite 表管理、CRUD、检索、统计、时间线 | `store.py` |
| 业务层 | HealthService 封装存储，提供患者摘要、化验趋势、AI解读 | `service.py` |
| API 层 | 50+ RESTful 接口（在 `api/app.py` 中） | `api/app.py` |
| 前端页面 | 独立健康档案管理页面（12 个标签页） | `web/health/index.html` |
| 数据导入 | 用户真实看病资料批量导入脚本 | `scripts/import_health_data.py` |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 患者档案管理 | ✅ | 多成员档案（本人/配偶/子女/父母），CRUD |
| 就诊记录 | ✅ | 门诊/急诊/住院/体检/复查，支持筛选搜索 |
| 健康问题追踪 | ✅ | 疾病/健康问题状态管理（活跃/慢性/已缓解），严重程度 |
| 用药记录 | ✅ | 处方药/OTC/保健品，剂量频率，用药状态 |
| 化验结果 | ✅ | 主表+项目明细，自动标记异常（高/低/危急），历史趋势查询 |
| 检查记录 | ✅ | CT/MRI/超声/内镜/手术，需复查标记 |
| 过敏史 | ✅ | 过敏原、反应、严重程度 |
| 生命体征 | ✅ | 血压、心率、体温、体重、血氧、血糖、疼痛评分 |
| 疫苗接种 | ✅ | 疫苗名称、剂次、制造商、批号 |
| 医生信息 | ✅ | 医生姓名、职称、专科、医院、科室、联系方式、备注 |
| 文档附件 | ✅ | 看病资料原件管理（报告/处方/病历/证明/影像），分类、标签、搜索 |
| AI 报告解读 | ✅ | 利用项目 LLM 服务解读化验报告和检查报告，自动保存解读结果 |
| 健康时间线 | ✅ | 按时间聚合所有健康事件（就诊/检查/化验/用药/问题/文档/疫苗），一目了然 |
| 统计概览 | ✅ | 各模块计数、待复查提醒、活跃问题/用药 |
| 患者摘要 | ✅ | 单患者完整档案汇总（各模块数量+活跃项） |
| 化验趋势 | ✅ | 按项目名查询历史数值变化 |
| 前端页面 | ✅ | 独立 `/health` 页面，12 标签页，表单录入，AI解读按钮 |
| 数据导入 | ✅ | 脚本批量导入用户真实看病资料 |

## 数据模型

### 核心实体关系

```
health_patients (患者)
  ├── health_encounters (就诊记录) 1:N
  ├── health_conditions (健康问题) 1:N
  ├── health_medications (用药记录) 1:N
  ├── health_lab_results (化验主表) 1:N
  │     └── health_lab_components (化验明细) 1:N
  ├── health_procedures (检查/手术) 1:N
  ├── health_allergies (过敏史) 1:N
  ├── health_vitals (生命体征) 1:N
  ├── health_immunizations (疫苗接种) 1:N
  ├── health_documents (文档附件) 1:N
  └── health_insights (AI洞察) 1:N

health_doctors (医生信息) — 独立表，可被就诊/检查引用
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

### 过敏史

- `GET /api/health/allergies` — 列表
- `POST /api/health/allergies` — 创建
- `DELETE /api/health/allergies/{id}` — 删除

### 生命体征

- `GET /api/health/vitals?patient_id=` — 列表
- `POST /api/health/vitals` — 创建
- `DELETE /api/health/vitals/{id}` — 删除

### 疫苗接种

- `GET /api/health/immunizations` — 列表
- `POST /api/health/immunizations` — 创建
- `DELETE /api/health/immunizations/{id}` — 删除

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

### AI 健康洞察

- `GET /api/health/insights` — 列表（支持 patient_id、target_type、target_id 筛选）
- `POST /api/health/insights` — 创建
- `DELETE /api/health/insights/{id}` — 删除

### 健康时间线

- `GET /api/health/timeline?patient_id={id}` — 获取患者健康时间线，聚合所有类型事件

### AI 报告解读

- `POST /api/health/lab-results/{id}/interpret` — AI 解读化验报告
- `POST /api/health/procedures/{id}/interpret` — AI 解读检查报告

## 前端页面

独立页面挂载在 `/health`，与主 SPA 解耦，可直接通过 `http://localhost:port/health` 访问。

页面包含 12 个标签页：
1. **概览** — 统计卡片、活跃健康问题、当前用药、待复查提醒
2. **就诊记录** — 列表+详情弹窗+新增表单
3. **健康问题** — 状态/严重程度标签，新增表单
4. **用药记录** — 用药状态管理，新增表单
5. **化验结果** — 异常项高亮，明细表格，支持批量录入，AI解读按钮
6. **检查记录** — 需复查标记，详情弹窗，AI解读按钮
7. **过敏史** — 过敏原管理
8. **生命体征** — 血压/心率/体温/体重/血氧/血糖
9. **疫苗接种** — 接种记录
10. **健康时间线** — 按时间聚合所有健康事件，彩色图标区分类型
11. **文档资料** — 看病资料原件管理，分类/标签/搜索，卡片式展示
12. **医生信息** — 就诊医生和医疗机构管理

## 配置项

无额外配置项。数据库路径复用项目主配置 `db_path`（默认 `data/openbiliclaw.db`）。

## 设计决策

1. **参考 MediKeep 但适配 SQLite**：MediKeep 使用 PostgreSQL + SQLAlchemy，本模块适配项目的 SQLite + 原生 sqlite3 模式，参考 diary 模块的 Store 类设计。
2. **表名前缀隔离**：所有健康相关表使用 `health_` 前缀，避免与项目其他模块表冲突。
3. **化验主从表结构**：一次化验包含多个项目，采用主表+明细表设计，支持按项目查询历史趋势。
4. **独立前端页面**：与主 SPA 解耦，通过 `/health` 独立挂载，不影响现有页面路由。
5. **多患者支持**：支持家庭多成员档案管理，通过 `relationship` 字段区分。
6. **待复查提醒**：Procedure 表的 `needs_follow_up` 字段驱动概览页的待复查提醒。
7. **参考 HealthLog 的文档库设计**：文档附件模块参考 HealthLog 的 document vault 设计，支持分类、标签、搜索，存储看病资料原件的元数据和摘要。
8. **参考 OwnHealthRecord 的医生管理**：医生信息模块参考 OwnHealthRecord 的 doctors-list 设计，记录医生联系方式和就诊历史。
9. **健康时间线聚合**：时间线 API 聚合就诊、检查、化验、用药、健康问题、文档、疫苗等所有事件，按日期排序，让健康历程一目了然。
10. **AI 解读复用项目 LLM 服务**：报告解读功能复用项目已有的 LLMService，通过 `ctx.llm_service` 注入，不引入新依赖。解读结果自动保存到 `health_insights` 表，可追溯。
11. **AI 解读安全边界**：解读提示词明确要求"只能做信息整理和科普解释，不能给出确诊或治疗方案，必须建议咨询专业医生"，并在输出中包含免责声明。

## 数据导入

使用 `scripts/import_health_data.py` 可批量导入用户真实看病资料：

```bash
.venv/bin/python scripts/import_health_data.py
```

脚本会创建患者档案、就诊记录、检查记录、化验结果（含明细）、健康问题、用药记录。
