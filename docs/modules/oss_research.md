# 模块文档：开源项目研究（oss_research）

## 概述

把「用户发给助手的开源项目链接 → 助手研究 → 结构化入库 → 前端 tab 展示」做成一个可持续闭环。
数据落在独立 SQLite 库 `12_开源项目研究/oss_research.db`（2026-09-18 自 `data/` 迁入），
不污染既有业务库（douban.db / travel.db / interview.db …）。

入口：桌面 Web UI 的「🔬 开源研究」tab（`/web/oss-research`）。

## 已实现功能

| 功能 | 说明 |
| --- | --- |
| 项目列表 | `GET /api/oss-research/projects`，支持 `?tag=` 与 `?search=` 筛选，按时间倒序 |
| 标签聚合 | `GET /api/oss-research/tags` 返回去重排序标签，供前端筛选条 |
| 项目详情 | `GET /api/oss-research/projects/{id}`，JSON 数组字段自动反序列化 |
| 新增 | `POST /api/oss-research/projects`（name 必填，列表字段接受数组或逗号字符串） |
| 更新 | `PUT /api/oss-research/projects/{id}`（仅更新传来的字段） |
| 删除 | `DELETE /api/oss-research/projects/{id}` |
| 前端展示 | 卡片网格 + 标签筛选 + 搜索 + 详情弹窗（核心能力/可迁移手法/坑/报告链接）+ 手动新增/删除 |
| 幂等回填 | `12_开源项目研究/scripts/backfill.py` 按 (owner,name) 去重写入已分析项目 |
| 报告服务 | 研究报告统一放在 `12_开源项目研究/references/` 下；API 以 `/api/oss-research/references/{path}` 与 `/api/oss-research/docs/{path}` 只读 serve（白名单目录 + 仅 .md + resolve 路径穿越防护） |

### 数据现状（2026-09-18 库目录迁移后）

库内共 95 条（其中 27 条带 `report_path`），来源五批：
1. **当日深度研究 7 个**：TraeWorkAssistant-mac / wikitok / lushu / brosis / exercise-helper / red / apple-notes-cli（各附借鉴分析报告，均在 `12_开源项目研究/references/` 下）。
2. **`12_开源项目研究/references/` 历史研究存量 13 个**（tag=`references-archive`）：求职知识库调研 11 + 深度蓝图 2（my-interview / agent-interview-hub）。
3. **`12_开源项目研究/references/` 仅克隆未精读 18 个**（tag=`references-archive`+`unstudied`）。
4. **`002-探索项目/` 各主题探索批次第三方仓库 45 个**（tag=`explore-archive`+`unstudied`）：小宇宙播客 3、微信读书 5、Telegram 3、123 云盘 6、115 网盘 8、夸克网盘 4、视频/CMS/TVBox 15（含 FongMi/TV 源码包与 JlenVideo 精简改造副本说明）、自媒体爬虫 1（MediaCrawler）。caveats 字段记录各克隆的原始位置。
5. **`12_开源项目研究/references/diary-projects` 日记类研究批次 5 个**（tag=`diary`+`explore-archive`）：Night-Journal / cube-diary / journiv-app / memex / nightly-journal（nightDiary 与 `12_开源项目研究/references/` 根目录重复，只入一条）。

注：`12_开源项目研究/references/diary-projects` 是 09-06 建的日记类项目研究批次，内含 6 个日记/记忆应用克隆；其顶层 `.git`（主仓库复制残留）已于 09-14 确认清理，目录现仅含 6 个项目本体。用户自有仓库（oliver7l / tanxue0118 名下）与自研应用（WebDAVViewer、editable-table-app、tvbox-web、wechat-tools、039 等）均不属于研究对象，未入库。

### 目录迁移（2026-09-18）

模块资产从散落的位置统一收进项目根编号目录 `12_开源项目研究/`：

| 迁移前 | 迁移后 |
| --- | --- |
| `references/`（772M 第三方克隆，67 项） | `12_开源项目研究/references/` |
| `data/oss_research.db`（+ 备份） | `12_开源项目研究/oss_research.db` |
| `scripts/oss_research/backfill.py` | `12_开源项目研究/scripts/backfill.py` |

- 代码侧仅改路径指向：`DEFAULT_DB_PATH` 与报告服务白名单 `_ALLOWED_DIRS` 的 references 根（`src/openbiliclaw/api/oss_research_routes.py`），路由前缀 / 端点均不变。
- DB 内 27 条 `report_path` 与 28 条 caveats 中的 `references/…` 引用已批量加 `12_开源项目研究/` 前缀。
- `.gitignore`：`references/`（非锚定）与 `*.db`/`*.db.bak*` 规则天然覆盖新位置；`scripts/backfill.py` 保持入库跟踪，不做整目录忽略。

## 公开 API

路由前缀 `/api/oss-research`，由 `build_oss_research_router(db_path=None)` 构建（db_path 缺省回退 `12_开源项目研究/oss_research.db`）。

可复用辅助函数（供回填脚本 / Agent 工具调用）：

```python
from openbiliclaw.api.oss_research_routes import insert_project, DEFAULT_DB_PATH

new_id = insert_project(DEFAULT_DB_PATH, {
    "name": "repo-name", "owner": "owner", "url": "https://github.com/owner/repo-name",
    "one_liner": "一句话定位", "tech_stack": ["React", "Vite"], "tags": ["reference"],
})
```

`oss_projects` 表字段：`id, name, owner, url, one_liner, purpose, tech_stack(JSON),
structure_notes, key_features(JSON), relevance_summary, reusable_techniques(JSON),
caveats, report_path, tags(JSON), created_at, updated_at`。

## 配置项

无独立配置项；库路径由 `DEFAULT_DB_PATH`（项目根 `12_开源项目研究/oss_research.db`）解析，
或 `build_oss_research_router(db_path=...)` 覆盖。

## 设计决策

- **独立库而非复用 `user_research.db`**：`user_research.db` 的 `reports` 表是纯文本 blob，
  无法满足「开源项目」结构化展示（技术栈/可迁移手法/标签筛选）。独立库隔离、便于扩展。
- **列表字段以 JSON 存储**：tech_stack / key_features / reusable_techniques / tags 用 JSON
  文本列，入库序列化、出库反序列化，前端直接渲染。
- **写入既可由 Agent 也可由前端**：Agent 研究后用 `insert_project` 入库；用户也能在 tab 内手动新增/删除，闭环不依赖单一入口。
- **遵循既有接线范式**：路由注册进 `_route_registry.py`；前端沿用 `douban-app.js` 的
  `window.initXxxPage / reloadXxxPage` + `DESKTOP_PAGE_ROUTES` 模式，新增即注册、不破坏现有页面。
