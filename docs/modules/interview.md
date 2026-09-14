# 求职面试备战模块（interview）

> ⚠️ **本文只描述「面试域」三子系统中的 A · 岗位备战**。三子系统总览见
> [`interview-overview.md`](./interview-overview.md)（另有 B · 题目研习、C · 面试复盘，
> 三者共用 `/api/interview` 命名空间）。

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
| API | `/api/interview/*` REST 接口 | `routes.py` |
| 配置 | `[interview] root` 指向知识库根目录 | `config.py` `InterviewConfig` |

设计原则：**原始材料始终保留在原目录**，引擎只读检索；仅「面试日志追加」与
「新岗位建档」两个显式操作会写入引擎数据目录（只追加、不覆盖）。

## 已实现功能

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
| API 路由 | ✅ | `GET/POST /api/interview/*`（见公开 API） |

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
    └── routes.py         # build_interview_router → /api/interview/*
```

旧导入路径（`interview.engine` / `interview.routes`）保留 re-export 垫片一版，下一个大版本摘除。

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
| GET | `/api/interview/status` | 系统总览（未配置也返回状态） |
| GET | `/api/interview/jobs?keyword=` | 岗位列表 |
| GET | `/api/interview/search?q=&limit=` | 全文检索（空关键词 422） |
| GET | `/api/interview/numbers?keyword=` | 真实数字表 |
| GET | `/api/interview/projects?keyword=` | 项目库 |
| GET | `/api/interview/card/{company}` | 速记卡（未登记 404） |
| GET | `/api/interview/directions` | 方向知识库清单 |
| GET | `/api/interview/index?keyword=&layer=` | 全库索引（layer 仅 01/02/03） |
| POST | `/api/interview/index/rebuild` | 重建全库索引（覆盖 06 CSV + knowledge.db） |
| GET | `/api/interview/doctor?fix=&full=` | 健康检查（C1-C5，fix 自动重建过期索引） |
| GET | `/api/interview/logs` | 面试日志（最新在前） |
| POST | `/api/interview/logs` | 追加日志 `{company, round, points}` |
| POST | `/api/interview/scaffold` | 新岗位建档 `{company, role}` |

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
