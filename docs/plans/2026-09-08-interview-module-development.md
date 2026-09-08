# interview 求职面试备战模块 · 开发文档

> 日期：2026-09-08
> 状态：v0.3.217 已交付核心实现，本文档面向开发维护与后续扩展
> 关联：`docs/modules/interview.md`（用户文档）、`docs/changelog.md` v0.3.217

---

## 1. 模块定位

把用户的「三层求职知识库」（项目内 `<项目根>/求职知识库/`，随项目迁移）
接入 OpenBiliClaw，成为可查询、可复盘、可扩展的面试备战系统模块。

**核心原则（硬约束）：**
- 原始材料（01_原始资料库 7000+ 文件）**只读、原位保留、零拷贝**；
- 数据源由 `[interview] root` 配置指向外部目录，代码内置、数据外置；
- 写入操作仅两类且均为**只追加/幂等**：`add_log`（追加面试日志）、`scaffold`（新建岗位目录）；
- 检索口径、CSV 表头、`knowledge.db` schema 与外部 `_系统_知识库引擎` 完全对齐。

## 2. 体系架构

```
┌─ OpenBiliClaw ─────────────────────────────────────────────────┐
│  CLI  openbiliclaw interview <子命令>   (typer 子命令组)        │
│  API  /api/interview/*                 (FastAPI Router)        │
│  Web  面试 tab                         (web/js/views/interview.js) │
│             │ 共用同一引擎，返回统一结构化 dict                │
│  InterviewEngine  (src/openbiliclaw/interview/engine.py)       │
│    resolve_root: [interview] root → env → DEFAULT_INTERVIEW_ROOT │
└──────────────┬─────────────────────────────────────────────────┘
               │ 只读检索 / 日志追加 / 岗位建档
┌──────────────▼─────────────────────────────────────────────────┐
│  外部求职知识库 <root>                                          │
│   _系统_知识库引擎/数据/*.csv + knowledge.db                    │
│   01_原始资料库（只读源） → 02_方向知识库 → 03_岗位弹药库        │
└────────────────────────────────────────────────────────────────┘
```

**模块结构：**

```
src/openbiliclaw/interview/
├── __init__.py     # 导出 InterviewEngine / resolve_root / 常量
├── engine.py       # InterviewEngine：全部数据访问逻辑（纯 Python，无三方依赖）
├── cli.py          # interview_app 命令组 + register(app) 注册函数
└── routes.py       # build_interview_router(root=None) → /api/interview/*
tests/test_interview_engine.py   # 14 用例（含 build_kb 临时知识库构造器）
tests/test_api_interview.py      # 9 用例
```

## 3. 数据模型（对外部引擎的契约）

引擎读取 `_系统_知识库引擎/数据/` 下 7 张 CSV 与 `knowledge.db`，表头即契约：

| 表 | 用途 | 关键列 |
|----|------|--------|
| `01_岗位表.csv` | 岗位登记 | 公司/岗位/面试时间/状态/主打方向/备战目录/简历版本/备注 |
| `02_项目表.csv` | 可讲项目 | 项目名/公司/技术栈/核心数字/来源/可讲要点 |
| `03_真实数字表.csv` | **口径权威源** | 数字/口径/公司-项目/来源/入库时间 |
| `04_面试日志.csv` | 复盘闭环 | 日期/公司/轮次/面试官角色/被问要点/复盘/复盘文档 |
| `05_面试题索引.csv` | 题库入口 | 题目/方向/公司（含"跨岗位"）/答案位置 |
| `06_全库文件索引.csv` | 索引回退 | 路径/层/子层/类型/文件名/… |
| `07_概念关系表.csv` | 概念网 | （当前引擎未消费，保留） |

`knowledge.db`：`file_index(路径,层,子层,类型,文件名,扩展名,大小KB,修改日期)`。
引擎优先查库，库缺失时回退 `06_全库文件索引.csv`。

**全文检索范围**：`02_方向知识库`、`03_岗位弹药库`、
`01_原始资料库/腾讯文档资料`、`01_原始资料库/解码文本`、
`01_原始资料库/工作资料_腾讯`、`01_原始资料库/工作资料_微视`、
`_系统_知识库引擎/规范`；仅 `.md/.txt`、≤2MB、
每文件首个命中行。

## 4. InterviewEngine 公开方法

| 方法 | 返回 | 说明 |
|------|------|------|
| `jobs(keyword=None)` | `list[dict]` | 岗位，按公司/岗位/主打方向过滤 |
| `search(keyword, max_hits=40)` | `list[dict{path,line,snippet}]` | 全文检索，path 相对 root |
| `numbers(keyword=None)` | `list[dict]` | 真实数字表 |
| `projects(keyword=None)` | `list[dict]` | 项目库 |
| `questions(company=None)` | `list[dict]` | 题索引（含跨岗位） |
| `directions()` | `list[dict{direction,docs}]` | 方向知识库清单 |
| `index(keyword=None, layer=None, limit=50)` | `list[dict]` | 全库索引，DB→CSV 回退 |
| `logs()` | `list[dict]` | 面试日志，倒序（最新在前） |
| `add_log(company, rnd, points)` | `int` | 追加日志（当天日期+待复盘），返回序号 |
| `speed_card(company)` | `dict{job,numbers,projects,questions,prep}` | 速记卡；`prep` = 岗位定制弹药（`03_速成包` 速记卡全文 + 题库/问答清单 + `02_面试备战资料` 清单），未登记抛 `KeyError` |
| `overview()` | `dict` | 总览（configured/jobs/projects/统计/方向/**规范清单**） |
| `scaffold(company, role)` | `dict{path,created,existing}` | 新建 01/02/03 三件套，幂等 |

模块级工具：`resolve_root(cfg_root)` 按 **配置 → 环境变量 `OPENBILICLAW_INTERVIEW_ROOT` → 默认路径** 解析。

## 5. CLI 与 API 面

### CLI（`openbiliclaw interview`）

| 命令 | 对应外部 kb.py | 说明 |
|------|---------------|------|
| `status` | `全部` | 系统总览 |
| `search <关键词> [--limit N]` | `查` | 全文检索 |
| `job <公司>` | `岗位` | 岗位信息 |
| `card <公司>` | `速记` | 速记卡 |
| `numbers [关键词]` | `数字` | 真实数字 |
| `projects [关键词]` | `项目` | 项目库 |
| `direction <方向>` | `方向` | 方向文档 |
| `index [关键词] [--layer 01/02/03]` | `索引` | 全库索引 |
| `logs` / `log <公司> <轮次> <要点>` | `记录` | 日志列表 / 追加 |
| `scaffold <公司> <岗位> [--yes]` | — | 新岗位建档（有确认） |
| `root` | — | 显示根目录及解析来源 |

注册方式：`cli.py` 主文件 `_APP_CONTEXT` 定义后插入 `from openbiliclaw.interview.cli import register; register(app)`（try/except 包裹，导入失败不阻塞主 CLI）。

### API（`/api/interview`）

| 方法 | 路径 | 行为 |
|------|------|------|
| GET | `/status` | 未配置也返回（configured=false） |
| GET | `/jobs?keyword=` / `/search?q=&limit=` / `/numbers?keyword=` / `/projects?keyword=` | 只读查询 |
| GET | `/card/{company}` | 404 未登记 |
| GET | `/directions` / `/index?keyword=&layer=` | layer 非法 422 |
| GET | `/logs` | 日志列表 |
| POST | `/logs` `{company,round,points}` | 201 追加 |
| POST | `/scaffold` `{company,role}` | 201 建档（幂等） |

注册位置：`api/app.py` `create_app` 内 travel 路由之后，配置从 `config.interview.root` 读取；
`/api/interview/*` 为顶级前缀（不被 `/web` SPA mount 覆盖）。

### 配置

```toml
[interview]
root = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/求职知识库"   # 留空走 env / 默认路径
```

`config.py` 三处改动：`InterviewConfig` dataclass（含 `root: str = ""`）、
`Config.interview` 字段、`load_config_with_diagnostics` 中 `interview_raw` 解析。

## 6. 前端 tab

- `web/js/views/interview.js`：4 个分区（岗位+速记卡 / 检索 / 数字 / 日志+追加），
  全部走 `web/js/api.js` 的 `fetchInterview*` / `postInterview*` 封装；
- `web/js/app.js`：import + TABS 数组加 `{id:"interview", label:"面试"}` + `initActiveView` 分支；
- `web/css/app.css`：`.interview-*` 样式块（复用 travel 的卡片/表格视觉体系）。

## 7. 验证与验收

**已通过：**
- `pytest tests/test_interview_engine.py tests/test_api_interview.py` → 22 passed
- `pytest tests/test_config.py` → 139 passed（配置改动无破坏）
- `ruff check` / `ruff format` → 零错误
- `mypy src/openbiliclaw/interview/` → 零错误
- `create_app` 冒烟 → 11 条 `/api/interview/*` 路由正确挂载
- CLI 冒烟 → `interview status / card 大宇` 正常输出真实知识库内容

**已知预存问题（与本模块无关，基线已存在）：**
- 全仓 `mypy src/` 在 `llm/codex_auth`、`discovery/douyin`、`llm/registry` 等路径报错
  （仓库处于重构中段，`git show HEAD` 版本同样报错）；
- `tests/test_api_app.py` 25 个失败（TestBackendAPI 9 + TestEmbeddingAndCompatProviderE2E 16），
  经临时还原 config.py 到 HEAD 验证 **同样失败**，根因是 `obc_llm/registry.py`
  访问 `config.openai` 与当前 `Config` 结构的兼容性断裂，非本次改动引入。
- 提交前建议单独修复上述基线，或在 PR 说明中标注。

**每次改动的自检清单：**
1. `ruff check src/openbiliclaw/interview/ tests/test_interview_engine.py tests/test_api_interview.py`
2. `ruff format --check` 同上
3. `mypy src/openbiliclaw/interview/`
4. `pytest tests/test_interview_engine.py tests/test_api_interview.py`
5. 若改动外部引擎契约（CSV 表头 / knowledge.db schema），同步 `build_kb` 测试构造器
6. 若新增 CLI 命令 / API 端点 / 配置项，同步 `docs/modules/interview.md`、`cli.md`、`config.md`

## 8. 后续扩展路线（建议）

| 优先级 | 方向 | 说明 |
|--------|------|------|
| P0 | LLM 能力接入 | 复用 `llm` 服务：按岗位生成模拟问答、预测题库（外部知识库已有题库但可自动化扩产） |
| P0 | 速记卡增强 | 输出前自动读 `03_速成包/速记卡`、`预测题库` 汇总（当前只读数据表） |
| P1 | 复盘闭环 | 面试后 `add_log` + 自动生成复盘文档写入岗位目录（写入需用户确认） |
| P1 | 岗位匹配评估 | 接入 `_系统_知识库引擎/规范/岗位匹配评估.md` 五维打分流程 |
| P2 | RAG 扩展 | 资料膨胀后按引擎 README 第六节加向量层；**数字答案永远以真实数字表为权威** |
| P2 | web 增强 | 检索结果命中跳转、岗位建档表单、面试倒计时提醒 |

## 9. 注意事项

- **不要在引擎里硬编码路径**：一律走 `resolve_root()` 三级解析；
- **数字守门**：任何数字查询/输出以 `03_真实数字表.csv` 为唯一权威，禁止从全文检索结果直接引用数字；
- **只追加原则**：`add_log` 不修改历史行；`scaffold` 目录已存在时不覆盖；
- **外部引擎同步**：外部 `_系统_知识库引擎` 若新增表/改字段，先改 `engine.py` 再改本文档与 `docs/modules/interview.md`；
- **CLI 中文输出**：遵循项目 Rich 风格（标题面板 / 状态块 / 卡片式），新命令保持一致。
