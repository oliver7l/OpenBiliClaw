# 求职面试备战模块（interview）

> **面试域由三个独立的子系统组成**，共用了 `/api/interview` 命名空间与同一个前端页面。
> 完整总览见 [`interview-overview.md`](./interview-overview.md)；本文档聚焦 A · 岗位备战的完整细节，
> 并在下方提供 B · C 的快速入口。

> 把外部「三层求职知识库」接入 OpenBiliClaw 的统一入口：岗位信息、全文检索、
> 真实数字、项目库、面试速记卡、全库索引、面试日志、新岗位建档，CLI 与 API 双通道。

## 概述

`interview/` 包实现求职面试备战系统。数据源是「三层求职知识库」
（默认在项目内 `<项目根>/求职知识库/`，可用 `[interview] root` 覆盖）：

| 层 | 目录 | 定位 |
|----|------|------|
| 系统层 | `_系统_知识库引擎/` | 数据表（岗位/项目/真实数字/日志/题索引/全库索引/概念）+ knowledge.db |
| 底层 | `01_原始资料库/` | 原始材料（只读保留，不删改） |
| 中层 | `02_方向知识库/` | 方法论（广告算法/推荐系统/数据科学/SQL与统计/面试方法论…） |
| 上层 | `03_岗位弹药库/` | 按岗位备战包（JD拆解/公司背景/面试攻略/预测题库/速成包） |

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 引擎 | 岗位/检索/数字/项目/速记卡/索引/日志/建档，统一返回结构化数据 | `engine.py` |
| CLI | `openbiliclaw interview` 命令组（对应原 `kb.py` 中文命令） | `cli.py` |
| API | `/api/interview/job/*` REST 接口 | `routes.py` |
| 配置 | `[interview] root` 指向知识库根目录 | `config.py` `InterviewConfig` |

设计原则：**原始材料始终保留在原目录**，引擎只读检索；仅「面试日志追加」与
「新岗位建档」两个显式操作会写入引擎数据目录（只追加、不覆盖）。

## 三子系统速览与数据流

面试域由三个**互不相关、平行演进**的子系统组成，共用 `/api/interview` 前缀与同一前端页。

| # | 子系统 | 职责 | 核心数据 | 前端标签 | 详细文档 |
|---|--------|------|----------|----------|---------|
| **A** | **岗位备战**（job） | 求职知识库 / 速记卡 / 面试日志 / 建档 / 全文检索 | `interview.db` 裸表 + `knowledge.db` | 桌面「✅待办」；移动端整个面试页 | 本文档 §A（下方） |
| **B** | **题目研习**（study） | 题库 / 今日待读 / 待看队列 / 掌握度 / 弹药库 / 反问话术 | `interview_questions.db`（iq_*）+ `interview.db`（ammo/rebuttals） | 桌面「📖今日待读 / 📋待看队列 / 📚全部题目 / 📊学习统计 / 🧨弹药库」 | [`interview-reading-tracker.md`](../interview-reading-tracker.md) |
| **C** | **面试复盘**（review） | 复盘记录 / 搜索 / 统计 | `interview.db.interview_reviews` | 桌面「📝复盘」 | [`interview-overview.md`](./interview-overview.md) §C |

### 数据层三库关系

```
┌─────────────────────────────────────────────┐
│  求职知识库/03_岗位弹药库/  （只读 Markdown） │
└──────────┬──────────────────────────────────┘
           │  import_kb_questions_to_interview_db.py
           ▼
┌──────────────────────┐     ┌────────────────────────────┐
│  data/interview.db   │     │ data/interview_questions.db │
│  interview_questions │ ←── │ iq_questions (51条)         │
│  (199条)             │  源 │ iq_queue / iq_records ...  │
│  ammo_doc/rebuttals  │     └────────────────────────────┘
│  question(25)        │
│  concept/number/...  │ ←──── 岗位弹药库 Markdown 读入
└──────────────────────┘          (B 弹药库功能)
           ▲
     InterviewEngine
     (A 岗位备战引擎)
```

> 两套题表并存是设计决策，不合并：interview_questions（199条）覆盖各公司岗位预测题；
> iq_questions（51条）走独立刷题队列/掌握度追踪。各自有消费方，维持现状。

### API 前缀

| 子系统 | 正规前缀 | 兼容别名 |
|--------|----------|----------|
| A 岗位备战 | `/api/interview/job/*` | `/api/interview/*` |
| B 题目研习 | `/api/interview/study/*` | `/api/interview/*` |
| C 面试复盘 | `/api/interview/review/*` | `/api/interview/reviews/*` |

## 已实现功能（仅 A · 岗位备战）

| 功能 | 状态 | 说明 |
|------|------|------|
| 岗位总览/过滤 | ✅ | 读 `01_岗位表.csv`，支持公司/岗位/主打方向关键词过滤 |
| 全文检索 | ✅ | 三层（02/03）+ 原始资料（腾讯文档资料/解码文本/工作资料_腾讯/工作资料_微视）+ 系统层规范，每文件首个命中行，限 2MB 内文本 |
| 真实数字 | ✅ | `03_真实数字表.csv` 为权威口径源，严禁编造 |
| 项目库 | ✅ | `02_项目表.csv`，可按项目名/公司/可讲要点过滤 |
| 面试题索引 | ✅ | `05_面试题索引.csv`，支持「跨岗位」通用题 |
| 方向知识库 | ✅ | 列出 `02_方向知识库/` 全部方向及文档清单 |
| 全库文件索引 | ✅ | 优先 `knowledge.db`，缺失时回退 `06_全库文件索引.csv` |
| 面试速记卡 | ✅ | 一键组装某公司：岗位 + 核心数字 + 可讲项目 + 题库入口 + **岗位定制弹药**（`03_速成包` 速记卡全文/预测题库清单 + `02_面试备战资料` 清单） |
| 面试日志 | ✅ | 列表（最新在前）/ 追加（当天日期 + 待复盘，不修改历史） |
| 新岗位建档 | ✅ | 按统一规范创建 `01_岗位与公司信息 / 02_面试备战资料 / 03_速成包` 三件套，幂等不覆盖 |
| 系统总览 | ✅ | 岗位/项目/数字/题/日志统计 + 方向清单 + **规范清单**（岗位匹配评估/录入规范/新增岗位流程）+ 配置状态 |
| 全库索引重建 | ✅ | `rebuild_index()` 扫描三层 → 覆盖写 `06_全库文件索引.csv` + `knowledge.db`（`file_index` + `layer_stats`），不动原始文件 |
| 健康检查 | ✅ | `doctor()`：C1 题索引引用 / C2 岗位目录 / C3 日志岗位对齐 / C4 数字表完整 / C5 索引新鲜度（full）；`--fix` 自动重建过期索引 |
| CLI 命令 | ✅ | `interview search/job/card/numbers/projects/direction/index/logs/log/scaffold/status/root/doctor`；`index --rebuild` |
| API 路由 | ✅ | `GET/POST /api/interview/job/*`（见公开 API） |

## 模块结构

> 期 3「代码分层」后，`interview/` 包按 A/B/C 三子系统拆为 `job/` / `study/` / `review/`
> 三个子包；下表只列 A 相关，完整结构见 [interview-overview.md](./interview-overview.md)。

```
src/openbiliclaw/interview/
├── __init__.py           # 导出引擎公开 API
├── _paths.py             # PROJECT_ROOT：唯一项目根锚点
├── cli.py                # interview 命令组（register(app) 注册到主 CLI；跨 A + C）
└── job/                  # A · 岗位备战
    ├── engine.py         # InterviewEngine：数据表读取/检索/速记卡/日志/建档 + resolve_root
    └── routes.py         # build_interview_router → /api/interview/job/*
```

旧导入路径垫片已于 2026-09-15 全部摘除（12 个，含 `api/_interview_routes.py`）；旧路径不再可用，导入请走 `interview.job / study / review`。

## 公开 API

### CLI

```bash
openbiliclaw interview status                       # 系统总览
openbiliclaw interview search <关键词> [--limit N]  # 全文检索
openbiliclaw interview job <公司>                   # 岗位信息
openbiliclaw interview card <公司>                  # 速记卡
openbiliclaw interview numbers [关键词]             # 真实数字
openbiliclaw interview projects [关键词]            # 项目库
openbiliclaw interview direction <方向>             # 方向文档
openbiliclaw interview index [关键词] [--layer 01|02|03] [--rebuild]
openbiliclaw interview doctor [--fix] [--full]      # 健康检查（C1-C5）
openbiliclaw interview logs                         # 面试日志
openbiliclaw interview log <公司> <轮次> <要点>     # 追加日志
openbiliclaw interview scaffold <公司> <岗位> [--yes]
openbiliclaw interview root                         # 显示根目录与解析来源
```

### Python 引擎

```python
from openbiliclaw.interview import InterviewEngine, resolve_root
from openbiliclaw.config import load_config

root = resolve_root(load_config().interview.root)
eng = InterviewEngine(root)

eng.overview()                       # 总览 dict
eng.jobs("大宇")                     # 岗位列表
eng.search("oCPX", max_hits=40)      # 全文检索命中
eng.numbers("ARPU")                  # 真实数字
eng.projects("微视")                 # 项目库
eng.directions()                     # 方向清单
eng.index("推荐", layer="02")        # 全库索引
eng.speed_card("大宇")               # 速记卡
eng.add_log("大宇", "负责人面", "问了 RTB 出价")   # 追加日志，返回新序号
eng.scaffold("新公司", "广告算法")   # 新岗位建档
```

### HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/interview/job/status` | 系统总览（未配置也返回状态） |
| GET | `/api/interview/job/jobs?keyword=` | 岗位列表 |
| GET | `/api/interview/job/search?q=&limit=` | 全文检索（空关键词 422） |
| GET | `/api/interview/job/numbers?keyword=` | 真实数字表 |
| GET | `/api/interview/job/projects?keyword=` | 项目库 |
| GET | `/api/interview/job/card/{company}` | 速记卡（未登记 404） |
| GET | `/api/interview/job/directions` | 方向知识库清单 |
| GET | `/api/interview/job/index?keyword=&layer=` | 全库索引（layer 仅 01/02/03） |
| POST | `/api/interview/job/index/rebuild` | 重建全库索引（覆盖 06 CSV + knowledge.db） |
| GET | `/api/interview/job/doctor?fix=&full=` | 健康检查（C1-C5，fix 自动重建过期索引） |
| GET | `/api/interview/job/logs` | 面试日志（最新在前） |
| POST | `/api/interview/job/logs` | 追加日志 `{company, round, points}` |
| POST | `/api/interview/job/scaffold` | 新岗位建档 `{company, role}` |

未配置/目录不存在时，除 `status` 外的接口返回 404（detail 说明配置方法）。

## 配置项

`config.toml` 的 `[interview]` 段：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `root` | string | `""` | 求职知识库根目录；留空依次按 `OPENBILICLAW_INTERVIEW_ROOT` 环境变量、内置默认路径（`engine.DEFAULT_INTERVIEW_ROOT`）解析 |

```toml
[interview]
# 默认 <项目根>/求职知识库/，留空走 OPENBILICLAW_INTERVIEW_ROOT / 默认路径
root = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"
```

## 设计决策

1. **数据外置、代码内置**：知识库含 7000+ 原始工作文档，复制进仓库会造成海量重复；
   模块以配置指向外部目录，原始材料零拷贝、零修改，符合「01 原始资料库只读保留」的体系约束。
2. **结构化数据表优先，不做 RAG**：简历/项目数字是精确事实，
   `03_真实数字表.csv` 作为权威口径源（查询即精确命中、可审计），沿用原 kb.py 的取舍，
   RAG 作为可选扩展层保留（见知识库引擎 README 第六节）。
3. **复用而非重写**：引擎把 `scripts/kb.py` 的检索逻辑规范化（去掉硬编码路径、
   改为可配置 root、统一返回结构化 dict），CLI/API 共用同一引擎，避免双份逻辑漂移。
4. **只追加写入**：`add_log` 追加当天日期行（状态「待复盘」）、`scaffold` 幂等建目录，
   均不覆盖/不删除既有文件；其余全部为只读操作。
5. **双入口一致**：CLI 与 API 共用 `InterviewEngine`，同一份输出结构，
   前端 tab 与命令行行为一致，便于后续扩展（如 LLM 生成面试题、复盘话术）。

## 与外部知识库的关系

- 检索范围、CSV 表头、`knowledge.db` schema 与 `_系统_知识库引擎` 完全对齐；
  若外部知识库结构调整（新增表/改字段），优先同步 `engine.py` 的解析逻辑与本文档。
- 外部 `kb.py` 仍可独立使用；`openbiliclaw interview` 提供等价且配置化的入口。

## 面试安排面板（前端契约）

桌面端「面试备战中心 → 📅 面试安排」的数据来自投递域 `resume.db.applications`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/interview/study/schedule` | 面试安排列表（`upcoming` / `history` 二分；旧前缀 `/api/interview/schedule` 为双挂载别名） |
| PATCH | `/api/interview/study/schedule/{application_id}/time` | **改面试时间的唯一写入口**（见下「时间真值」） |

**响应字段**（除 `upcoming/history` 的行数组外）：

| 字段 | 说明 |
|------|------|
| `total` / `upcoming_count` | 总岗位数 / 待进行场次 |
| `stage_counts` | 阶段分布（`{待面: 3, 面试中: 5, …}`），供前端渲染筛选小标签 |
| `structured` | `applications` 是否已跑迁移 001（含结构化列）；老库为 `false`，前端自动隐藏阶段相关 UI |
| `time_conflicts` | 面试时间双写告警（见下）。非空 = 有人绕开写入口直接改了一边 |

`upcoming` / `history` 的行在原始列之外附加 `id`、`interview_start_at`、
`round_note`、`stage`、`is_upcoming`。**「是否待进行」一律由后端 `is_upcoming` 判定**
（阶段枚举 + 结构化时间），前端不得复用自由文本 `status` 自己判断——
`status` 的真实值形如 `'已确认参加(9/17周四 19:00 视频面)'`，硬匹配必然漏判。

### 职责划分

| 层 | 文件 | 职责 |
|----|------|------|
| 纯视图模型 | `web/desktop/assets/js/interview-schedule-view.js` | 日期切分、倒计时、阶段分组，**无 DOM 无 HTML**；UMD 包装，浏览器挂 `window.OBCScheduleView`，node 可直接 `require` |
| 渲染 | `web/desktop/assets/js/interview.js` | 只把 VM 拼成 HTML（`renderScheduleHtml`），视图模型缺失时降级为只列公司/岗位 |

抽成独立模块的原因：日期切分逻辑埋在 IIFE 里时只能靠 grep 源码"测试"，
那类断言没有鉴别力。现在 `tests/desktop/test_interview_schedule_view_model.py`
用 node 真跑真断言，并且能注入「今天」保证倒计时判定确定性。

### 两条硬约束

1. **日期只信 `interview_start_at`**（ISO 前缀，可字符串比较），为空才回退去
   `interview_at` 抠 `YYYY-MM-DD`；**绝不用空白切分**——`'2026-08下旬~09初'`、
   `'未约面'` 这类值切出来是垃圾（曾把 day 渲染成「下旬~09初」）。
   抠不到就显示「待定」，不猜。
2. **`interview_at` 与 `status` 保留原样透出**，不丢信息、不参与判定——
   它们是历史自由文本，清洗只在 `scripts/interview_module_optimize.py` 里做。

### 时间真值：applications 唯一，待办派生

面试时间曾经两处各写各的（`applications.interview_start_at` 与
`interview.db.todo.due_date`），深圳灵动改期后出现过
「applications 写 9/17、待办停在 9/16」的漂移。现规则（迁移 002，
`scripts/interview_time_decouple.py`）：

| 位置 | 角色 |
|------|------|
| `applications.interview_start_at` | **唯一真值**（ISO，可排序） |
| `applications.interview_at` | 派生：时间 + 场次备注（兼容旧读方） |
| `todo.due_date`（`kind='interview'`） | 派生：改期时由写入口同步 |
| `todo.due_date`（`kind='followup'`） | 自主：跟进类待办（如「谈薪 R3 电话 9/18」）不随面试时间动 |

`kind` 列由迁移 002 补（默认 `followup`）；判定「面试时间类」的依据是
标题含 `HH:MM` 且 `due_date` 与该公司面试日期一致。**改期一律走
`PATCH /schedule/{application_id}/time`**，不要分别 UPDATE 两张表。

## 变更记录

### 2026-09-15 排期字段结构化 + 三处缺陷修复

后端补齐了上面契约缺失的实现部分（此前前端视图模型与测试已就位，后端未落地）。

**迁移 001 —— `scripts/interview_module_optimize.py`**（幂等，`--apply` 落库）

给 `resume.db.applications` 新增三列，**不动原列**（向后兼容）：

| 新列 | 说明 |
|------|------|
| `interview_start_at` | ISO 前缀时间 `YYYY-MM-DD[ HH:MM]`，可字符串比较排序 |
| `round_note` | 场次备注，如 `HR面` / `二面+HR面(一次性走完)` / `视频面试` |
| `stage` | 阶段枚举：候选 / 已投递 / 待面 / 面试中 / 谈薪中 / 已结束 / 已终止 |

解析不了的（`2026-08下旬~09初`、`未约面`）留空，原值仍在 `interview_at`，不丢信息。

**修复的三个 bug**

1. **`is_upcoming` 恒为 False**（真实故障）
   旧实现 `status in ("待面", "进行中")` 硬匹配，而 status 真实值形如
   `'已确认参加(9/17周四 19:00 视频面)'`，永远命不中 → upcoming 永远为空。
   改为 `stage` 枚举判断。修复实测：**upcoming 从 0 → 4**。
2. **阶段误判**：`'已结束(输给内转,HR留门:新增HC可直接推进谈薪)'` 里"谈薪"
   是条件句，旧规则顺序让它先命中 → 误判「谈薪中」。终态规则已提到最前。
3. **排序失效**：`ORDER BY interview_at DESC` 排的是自由文本。改用
   `interview_start_at`，无时间的恒沉底。

排序规则：upcoming 升序（最近的在前），history 降序。
**活跃阶段（待面/面试中/谈薪中）但没有具体时间的岗位仍计入 upcoming 并沉底**——
它需要留在视野里提醒约时间，而不是被归入 history 消失。

回归测试：`tests/interview/test_schedule_stage.py`（13 条）。
两条核心断言已验证在修复前会失败（非恒绿）：
`is_upcoming` 旧逻辑得 False；`_fallback_stage` 旧顺序得「谈薪中」。

### 2026-09-15 录音索引复活 —— `scripts/interview_scan_recordings.py`

`interview_recordings` 此前是**零引用死表**（全树无任何代码读写），导致：

- 比亚迪 HR 面录音卡在 `transcribing` 5 天无人发现（真实原因：转写产出是 0 字节空文件）
- 文件系统里 3 个音频 + 3 份转写从未被索引

处理：

- 新增端点 `GET/POST /api/interview/review/recordings`、`PATCH .../recordings/{id}`
  （必须定义在 `/{review_id}` 之前，否则被路径参数吃掉）
- 列表返回 `stuck_count`，显式暴露「未转写完成」的条目便于巡检
- 扫描脚本：自动关联同目录转写文件（紧凑日期 `20260915` 与横线日期都要比），
  跳过 `-part2` / `-16k` 等衍生版本，按 `audio_path` 幂等去重，默认 dry-run
- 修掉 GoodLuckStudio 的路径漂移：`05_面试记录` 早已改名为 `05_面试复盘`，
  库里路径未同步 → 一条已转写完成的录音被误判为"文件不存在"

⚠️ 改弹药库目录名时，务必同步脚本里的 `RECORD_DIR_HINTS` 常量。

### 2026-09-15 `/scripts` 接口语义

- 传 `company` 时默认会带上 `company='通用'` 的话术（刻意设计），
  此前无法关闭 → 新增 `include_common=false`
- 旧版 `total=len(rows)` 是 **LIMIT 之后**的条数，被截断时恰好等于 limit，
  看起来正常却与库内实际数不符（拼多多 55 + 通用 56 = 111，limit=100 时显示 100，
  极易误判"已同步完整"）→ 新增 `matched_total` 与 `truncated`

### 2026-09-15 待办覆盖度

18 个在跟岗位此前只有 7 个有待办。已给 5 个在跟但零待办的岗位补跟进项
（待确认 / 万联易达 / 大宇无限 / 字节 / GoodLuckStudio），pending 达 13 条。

### 2026-09-15 面试时间去双写（迁移 002）

`scripts/interview_time_decouple.py`（幂等，`--apply` 落库，先备份到
`.bak/interview_time_decouple_<时间戳>/`）：

1. 给 `interview.db.todo` 补 `kind` 列（`interview` / `followup`，默认 `followup`）；
2. 标注现存面试时间类待办 → 本次标了 4 条（#3 万声音乐二面、#9 HungryStudio、
   #10 深圳灵动、#15 待确认 9/16），0 条需对齐；
3. 新增 `PATCH /api/interview/study/schedule/{id}/time` 作为改期唯一写入口，
   写 `interview_start_at` 并派生 `interview_at` + 该公司 `kind='interview'`
   的 pending 待办 `due_date`；`GET /schedule` 增加 `time_conflicts` 告警
   与行 `id`（前端调改期需要）。

回归：`tests/interview/test_interview_time_decouple.py`（13 条）。鉴别力已验证：
摘掉同步调用 / 把冲突检测置空后，3 条断言立刻变红。

### 2026-09-15 学习追踪空转修复

症结：`iq_queue` 48 条、`iq_records` 2 条、`iq_daily` 0 行 —— 建了队列
从不消费、打卡从不记账。两处根因：

1. **`get_today_queue` 完全不看队列**：旧实现只从 `iq_questions` 全表按
   （掌握度, 难度）挑 `daily_target` 条，48 条排队题永远推不出去。
   改为**队列优先**（先取已到期/无排期的队列题，不足再从全表补齐；
   全表补齐时排除「已排队但未到期」的题，否则 `planned_date` 形同虚设）。
2. **`iq_daily` 无写入方**：`mark_read` 只写 `iq_records`，`/progress`
   恒返回空数组。现在打卡自动累加当日进度（`_bump_daily`，复用同一连接
   避免嵌套连接撞写锁），无活跃计划时静默跳过。

`GET /today` 新增 `queue_remaining` / `today_read` / `from_queue_count`
与行级 `from_queue`，让「今天还差几题、队列还剩多少」可见。
线上实测：今日 5 题全部来自队列（此前 0 条）。

回归：`tests/interview/test_study_queue_daily.py`（9 条）。鉴别力已验证：
退回旧实现后 4 条断言变红。

