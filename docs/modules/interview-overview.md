# 面试域总览（interview domain）· 三子系统

> 本文件是「面试」这个**大模块**的总入口。它下面其实是**三个互不相关的子系统**，
> 共用了 `/api/interview` 命名空间与同一个前端页面。看代码前先看这张图。
> 代码已按期 3 分层为 `interview/{job,study,review}/` 三个子包（与下表一一对应）。

| # | 子系统 | 职责 | 代码 | 数据 | 前端子标签 | 详细文档 |
|---|--------|------|------|------|-----------|---------|
| **A** | **岗位备战**（job-prep） | 读求职知识库：岗位 / 全文检索 / 真实数字 / 项目库 / 速记卡 / 面试日志 / 新岗位建档 | `interview/job/{engine,routes}.py` + `interview/cli.py` | `求职知识库/` 文件 + `_系统_知识库引擎/数据/knowledge.db` + `interview.db` 裸表（`question/concept/number/project/todo/log`） | 桌面「✅待办」；**移动端整个面试页**；CLI `openbiliclaw interview` | [`interview.md`](./interview.md) |
| **B** | **题目研习**（study） | 题库 / 待看队列 / 阅读计划 / 掌握度 / 学习统计 / 反问话术 / 弹药阅读状态 | `interview/study/{routes,store,models,cli}.py` | `data/interview_questions.db`（`iq_*`）+ 读 `03_岗位弹药库/` 文件 + `interview.db`（`ammo_doc` / `ammo_reading` / `interview_rebuttals` / `interview_questions`） | 桌面「📖今日待读 / 📋待看队列 / 📚全部题目 / 📊学习统计 / ❓反问话术 / 🧨弹药库 / 🏢公司岗位 / 📅面试安排」 | [`../interview-reading-tracker.md`](../interview-reading-tracker.md) |
| **C** | **面试复盘**（review） | 复盘记录 / 搜索 / 统计 | `interview/review/{routes,service,store,models}.py` | `interview.db`（`interview_reviews`） | 桌面「📝复盘」 | 本文件 §C |

**API 前缀**（✅ 期 2 URL 分区已完成，2026-09-14）：

| 子系统 | 正规前缀 | 兼容别名（旧） |
|---|---|---|
| A 岗位备战 | `/api/interview/job/*` | `/api/interview/*` |
| B 题目研习 | `/api/interview/study/*` | `/api/interview/*` |
| C 面试复盘 | `/api/interview/review/*` | `/api/interview/reviews/*` |

**兼容策略 = 双挂载别名**（不是 307 重定向）：三份 router 本体**不带 prefix**，注册时
`include_router` **挂两次**——新前缀进 OpenAPI，旧前缀 `include_in_schema=False`。
好处：POST/PUT/DELETE 带 body 也全兼容（重定向会让部分客户端丢 body）、不复制 handler、
摘除只需删 3 行 `include_router`。⚠️ 注意 A 的岗位列表在新前缀下是
`/api/interview/job/jobs`（唯一一处叠词，已接受）。

---

## 包结构（期 3 代码分层后）

```
src/openbiliclaw/interview/
├── _paths.py           # 唯一项目根锚点（PROJECT_ROOT）
├── cli.py              # 统一 CLI 入口（跨 A + C）
├── job/                # A 岗位备战
│   ├── engine.py
│   └── routes.py       # PREFIX /api/interview/job（+ mount_interview_router 双挂载旧前缀）
├── study/              # B 题目研习
│   ├── routes.py       # PREFIX /api/interview/study（+ register_interview_routes 双挂载旧前缀）
│   ├── store.py
│   ├── models.py
│   ├── cli.py          # python -m openbiliclaw.interview.study.cli
│   └── seed_iq_questions.py
└── review/             # C 面试复盘
    ├── routes.py       # PREFIX /api/interview/review（+ mount_review_router 兼容旧 /reviews）
    ├── service.py
    ├── store.py
    └── models.py
```

旧导入路径（`interview.engine` / `interview.routes` / `interview.review_*` /
`interview.questions.*` / `api._interview_routes`）保留 re-export 垫片一版，下一个大版本摘除。

---

## 数据层：三个库、多套表

| 库 | 路径 | 主要表 | 归属 |
|---|---|---|---|
| 面试主库 | `data/interview.db` | `ammo_doc` / `ammo_fts` / `ammo_reading` / `interview_questions`(199) / `interview_scripts` / `interview_reviews` / `interview_rebuttals` / `interview_recordings` + 裸表 `question`(25) / `concept` / `number` / `project` / `todo` / `log` | A / C（+ B 的 ammo/rebuttals 部分） |
| 题目追踪库 | `data/interview_questions.db` | `iq_questions`(51) / `iq_queue`(48) / `iq_records` / `iq_plans` / `iq_daily` | B |
| 知识库加工层 | `_系统_知识库引擎/数据/knowledge.db` | `file_index` / `layer_stats` | A（全库索引） |

### ⚠️ 已知数据层问题（待整合，见 `docs/plans/面试模块梳理与整合方案.md`）

- **「面试题」有三套表示**：`interview.db.question`(25，文件指针表) ／ `interview.db.interview_questions`(199，从 `03` 题库 md 解析) ／ `interview_questions.db.iq_questions`(51，B 在用)。三表同名不同义、互不相通。
  - ✅ **期1 已接线**：`interview_questions`(199) 现经 `GET /api/interview/study/kb-questions` 暴露，在桌面「全部题目」页以「📚 岗位题库」源只读展示（按公司/分类筛选）。
- **两个同名导入脚本写向不同库**（✅ 期1 已消歧）：`interview/study/seed_iq_questions.py`（原 `import_questions.py`，内置题→`iq_questions`）vs `scripts/import_interview_questions.py`（`03` 题库 md→`interview_questions`）。
- **`interview.db` 表命名三种风格并存**：裸名（`question`/`concept`/…）、`interview_` 前缀、`ammo_` 前缀。

---

## §C 面试复盘

- 表：`interview.db.interview_reviews`（公司 / 岗位 / 面试日期 / 轮次 / 结果 / …）
- API：`/api/interview/review/*`（列表 / 搜索 / 统计 / 详情 / 新建 / 更新 / 删除）；旧 `/api/interview/reviews/*` 仍兼容
- CLI：`openbiliclaw interview` 命令组中的复盘相关子命令
- 岗位级别的复盘笔记另存在 `03_岗位弹药库/{公司}-面试准备/05_面试复盘/`

---

## §D 前端（桌面端 `/web`）

面试页的 10 个子标签按三子系统分为三段（**期 4 前端分组**，纯视觉，不改行为）：

| 段 | 子标签 | 子系统 / API 前缀 |
|---|---|---|
| **备战** | 面试安排 · 待办 · 公司岗位 | A `/api/interview/job` |
| **研习** | 今日待读 · 待看队列 · 全部题目 · 弹药库 · 反问话术 · 学习统计 | B `/api/interview/study` |
| **复盘** | 复盘 | C `/api/interview/review` |

- 涉及文件：`web/desktop/index.html`（`#interviewSubtabbar` 内三个 `.page-subtab-group`）、
  `web/desktop/assets/css/app.css`（`.page-subtab-group` / `.page-subtab-group-label`）、
  `web/desktop/assets/js/interview.js`（切换逻辑，未改）。
- 主 web SPA（`web/js/views/interview.js`，8 个标签）是 A 域工作台的细分，不涉及三子系统混装，未参与分组。
- 调试入口：`http://127.0.0.1:8420/web`（静态资源按请求读盘，改前端无需重启服务）。

---

## 相关文档

- 岗位备战明细：[`interview.md`](./interview.md)
- 题目研习明细：[`../interview-reading-tracker.md`](../interview-reading-tracker.md)
- 整合方案：[`../plans/面试模块梳理与整合方案.md`](../plans/面试模块梳理与整合方案.md)
- 职位知识库三层架构：[`../plans/求职资料库三层架构重构.md`](../plans/求职资料库三层架构重构.md)
