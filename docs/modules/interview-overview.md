# 面试域总览（interview domain）· 三子系统

> 本文件是「面试」这个**大模块**的总入口。它下面其实是**三个互不相关的子系统**，
> 共用了 `/api/interview` 命名空间与同一个前端页面。看代码前先看这张图。

| # | 子系统 | 职责 | 代码 | 数据 | 前端子标签 | 详细文档 |
|---|--------|------|------|------|-----------|---------|
| **A** | **岗位备战**（job-prep） | 读求职知识库：岗位 / 全文检索 / 真实数字 / 项目库 / 速记卡 / 面试日志 / 新岗位建档 | `interview/{engine,routes,cli}.py` | `求职知识库/` 文件 + `_系统_知识库引擎/数据/knowledge.db` + `interview.db` 裸表（`question/concept/number/project/todo/log`） | 桌面「✅待办」；**移动端整个面试页**；CLI `openbiliclaw interview` | [`interview.md`](./interview.md) |
| **B** | **题目研习**（study） | 题库 / 待看队列 / 阅读计划 / 掌握度 / 学习统计 / 反问话术 / 弹药阅读状态 | `api/_interview_routes.py` + `interview/questions/` | `data/interview_questions.db`（`iq_*`）+ 读 `03_岗位弹药库/` 文件 + `interview.db`（`ammo_doc` / `ammo_reading` / `interview_rebuttals`） | 桌面「📖今日待读 / 📋待看队列 / 📚全部题目 / 📊学习统计 / ❓反问话术 / 🧨弹药库 / 🏢公司岗位 / 📅面试安排」 | [`../interview-reading-tracker.md`](../interview-reading-tracker.md) |
| **C** | **面试复盘**（review） | 复盘记录 / 搜索 / 统计 | `interview/review_{routes,service,store,models}.py` | `interview.db`（`interview_reviews`） | 桌面「📝复盘」 | 本文件 §C |

**API 前缀**：三者都挂在 `/api/interview` 下（路径不冲突，但无法从 URL 区分）。

---

## 数据层：三个库、多套表

| 库 | 路径 | 主要表 | 归属 |
|---|---|---|---|
| 面试主库 | `data/interview.db` | `ammo_doc` / `ammo_fts` / `ammo_reading` / `interview_questions`(199) / `interview_scripts` / `interview_reviews` / `interview_rebuttals` / `interview_recordings` + 裸表 `question`(25) / `concept` / `number` / `project` / `todo` / `log` | A / C（+ B 的 ammo/rebuttals 部分） |
| 题目追踪库 | `data/interview_questions.db` | `iq_questions`(51) / `iq_queue`(48) / `iq_records` / `iq_plans` / `iq_daily` | B |
| 知识库加工层 | `_系统_知识库引擎/数据/knowledge.db` | `file_index` / `layer_stats` | A（全库索引） |

### ⚠️ 已知数据层问题（待整合，见 `docs/plans/面试模块梳理与整合方案.md`）

- **「面试题」有三套表示**：`interview.db.question`(25，文件指针表) ／ `interview.db.interview_questions`(199，从 `03` 题库 md 解析) ／ `interview_questions.db.iq_questions`(51，B 在用)。三表同名不同义、互不相通。
  - ✅ **期1 已接线**：`interview_questions`(199) 现经 `GET /api/interview/kb-questions` 暴露，在桌面「全部题目」页以「📚 岗位题库」源只读展示（按公司/分类筛选）。
- **两个同名导入脚本写向不同库**（✅ 期1 已消歧）：`interview/questions/seed_iq_questions.py`（原 `import_questions.py`，内置题→`iq_questions`）vs `scripts/import_interview_questions.py`（`03` 题库 md→`interview_questions`）。
- **`interview.db` 表命名三种风格并存**：裸名（`question`/`concept`/…）、`interview_` 前缀、`ammo_` 前缀。

---

## §C 面试复盘

- 表：`interview.db.interview_reviews`（公司 / 岗位 / 面试日期 / 轮次 / 结果 / …）
- API：`/api/interview/reviews/*`（列表 / 搜索 / 统计 / 详情 / 新建 / 删除）
- CLI：`openbiliclaw interview` 命令组中的复盘相关子命令
- 岗位级别的复盘笔记另存在 `03_岗位弹药库/{公司}-面试准备/05_面试复盘/`

---

## 相关文档

- 岗位备战明细：[`interview.md`](./interview.md)
- 题目研习明细：[`../interview-reading-tracker.md`](../interview-reading-tracker.md)
- 整合方案：[`../plans/面试模块梳理与整合方案.md`](../plans/面试模块梳理与整合方案.md)
- 职位知识库三层架构：[`../plans/求职资料库三层架构重构.md`](../plans/求职资料库三层架构重构.md)
