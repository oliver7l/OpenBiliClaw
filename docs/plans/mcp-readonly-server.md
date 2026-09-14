# OpenBiliClaw MCP 只读服务 — 完整开发方案

> 灵感来源：`AllenBall/brosis` 的「把本地数据变 AI 上下文」机制（MCP 只读服务 + grant 细粒度授权 + 混合检索）。
> 范式来源：本项目已有的零依赖 MCP server 范本 `mule-cli/mcp/server.py`（纯 stdlib 手写 stdio JSON-RPC）。
> 状态：**方案阶段，待确认后实施**。

---

## 0. 这个东西是干啥的（一句话 + 场景）

**把 OpenBiliClaw 本地那 20+ 个 SQLite 库 / 内容库，变成一个「只读、受控、可被任意 MCP 客户端调用」的 typed 工具集。**

今天你问我「我面试题库里推荐算法相关的有哪些」「新疆行程酒店定了几家」「那 282 份简历里字节定制版在哪」，我得现写脚本 / 用 bash 查库。装上这个 MCP server 后，**我（或 Claude Code / Cursor / Codex / WorkBuddy）直接调一个 typed 工具**就能拿到结构化结果，秒回、不破坏数据、按你的授权范围访问。

它本质是 brosis 思路在你项目上的落地：**本地数据 → AI 上下文**，但三个硬约束对齐你的隐私与工程偏好：

- **只读**：永远不开写连接，查询被 SQL 白名单双保险拦截。
- **授权**：默认全拒，按需逐库/逐表开启（grant）。
- **不离机**：stdio 本地进程，数据不出本机（和 brosis「never leaves machine」一致）。

---

## 1. 架构总览

```
┌─────────────────────────┐         stdio (newline JSON-RPC 2.0)
│  MCP 客户端              │  ◄──────►  ┌──────────────────────────────────┐
│  (Claude Desktop /       │            │  obc-mcp/server.py (stdlib only) │
│   Codex / Cursor /       │            │  ├─ initialize / tools/list       │
│   Claude Code /          │            │  ├─ tools/call ──► dispatch        │
│   WorkBuddy Connector)   │            │  ├─ GRANT 检查 (grants.toml)        │
└─────────────────────────┘            │  ├─ READ-ONLY 包装 (mode=ro)        │
                                        │  └─ 领域工具 (catalog)             │
                                        │        │                           │
                                        │        ▼                           │
                                        │  data/*.db (只读连接)             │
                                        │  notes/阅读收藏库/ (md)           │
                                        └──────────────────────────────────┘
```

- **传输**：手写 stdio + JSON-RPC 2.0（与 `mule-cli/mcp/server.py` 的 `handle()`/`send()`/`main()` 一模一样），不引 `mcp` SDK，纯 `json`+`sys`+`sqlite3` stdlib。
- **进程模型**：每条客户端连接 = 一个本地 `python3 server.py` 子进程；无网络端口、无常驻服务，关掉客户端即停。
- **复用**：直接 clone `mule-cli/mcp/` 的 `server.py` 骨架 → 改名为 `obc-mcp`，替换 `TOOLS` 与 `call_tool`。

---

## 2. 目录布局（镜像 mule-cli/mcp/）

```
mcp/                                  # 仓库根新建（与 mule-cli/mcp 平级范式一致）
  server.py                           # stdio 循环 + 工具分发（零依赖）
  catalog.py                          # 数据源注册表：db 路径 / 表 / FTS表 / 工具构造器
  grants.toml                         # 授权白名单（默认全拒，逐库开启）★安全核心
  manifest.json                       # 供 .mcpb 打包 / WorkBuddy 连接器注册
  README.md                           # 各客户端配置（Claude Desktop/Codex/Cursor/Claude Code/WorkBuddy）
  tools/                              # 按域拆分的工具实现（可选，或先全放 catalog.py）
    __init__.py
    interview.py
    resume.py
    content.py
    travel.py
    diary.py
    knowledge.py
    oss.py
    conversation.py
    generic.py                        # obc_query 通用只读查询（带 SQL 白名单）
  redact.py                           # 结果脱敏（Phase 2，复用 brosis 思路）
tests/
  test_mcp_server.py                  # initialize/tools/list/各工具只读性/grant 拒绝
scripts/mcp/
  smoke_test.py                       # 管道 initialize+tools/list 的冒烟（镜像 mule README）
```

> 说明：Phase 0 可先把所有工具塞进 `catalog.py` 单文件（最贴近 mule 范本）；库多了再拆 `tools/`。

---

## 3. 数据源目录（基于实查的 `data/*.db`）

| DB 文件 | 高价值表（行数） | 拟暴露工具 | 默认授权 |
|---|---|---|---|
| `interview.db` | interview_questions(199) / ammo_doc(105) / interview_reviews(3) / resumes(29) / job_positions(71) / kb_documents(696) / slide(435) / concept(33) | `obc_interview_questions` `obc_interview_ammo` `obc_interview_reviews` `obc_interview_resumes` `obc_interview_jobs` `obc_interview_kb` `obc_interview_slides` | 开 |
| `resume.db` | resume_files(282, sha256 去重) | `obc_resume_find`（按 sha256/文件名/标签） | 开 |
| `content.db` | articles(89975)+articles_fts / article_tldrs(1073) / article_entities(1226) | `obc_content_search` `obc_content_tldr` `obc_content_entities` | 开 |
| `openbiliclaw.db` | conversation_archive(124)+fts / read_archive(108)+fts / reading_schedule(501) / zhihu_tasks(1398) / xhs_tasks(189) / bili_tasks(354) | `obc_conversation_search` `obc_read_archive` `obc_reading_schedule` `obc_tasks` | 开 |
| `travel.db` | trips(1) / trip_days(8) / trip_flights(8) / trip_hotels(7) / trip_members(11) / trip_expenses(10) / trip_checklist(22) | `obc_travel_show`（新疆 8 天行程全景） | 开 |
| `diary.db` | diary_entries(925) / diary_timeline_cards(4861) / diary_lessons(448) / diary_open_loops(123) | `obc_diary_search` `obc_diary_lessons` `obc_diary_open_loops` | 开 |
| `knowledge.db` | knowledge_cards(988) / topics(8) / topic_items(2690) / entities(774) | `obc_knowledge_cards` `obc_knowledge_topics` | 开 |
| `oss_research.db` | oss_projects(4) | `obc_oss_projects`（列表/详情） | 开 |
| `douban.db` | douban_items(1410) | `obc_douban_search` | 开 |
| `discovery.db` | discovery_candidates(30255) / discovery_keywords(43690) | `obc_discovery_candidates` | 开 |
| `chat_analysis.db` | chat_sessions(801) / chat_messages(3.6M) / chat_insights(20) | `obc_chat_search` `obc_chat_insights`（强制 LIMIT 上限） | 关（含私聊，需显式开） |
| `events.db` | events(231073) | `obc_events`（强制 LIMIT） | 关 |
| `health.db` | 16 表（PII） | `obc_health_*`（Phase 2） | **默认拒**（PII，需显式开 + 脱敏） |
| `knowledge_audit.db` | audit_issues(1M) / article_quality_scores(87k) | 内部用，不暴露 | 拒 |
| `article_rag.db` / `embedding_cache.db` | chunks(31854) / embedding_cache(44934) | 语义检索源（Phase 2） | 拒（内部） |

**敏感表硬拒绝（deny-list，永不暴露）**：`auth_state`、任意 config/密码表、`schema_version`、`self_evolution_state`、`*_fts*` 内部影子表（自动跳过）、`health_*`（除非显式 grant + 脱敏）。

---

## 4. 安全模型（brosis 精髓）

### 4.1 只读强制（两层）
1. 连接串强制只读：`sqlite3.connect(f"file:{path}?mode=ro", uri=True)` + `PRAGMA query_only=ON`。
2. 通用 `obc_query` 工具对 SQL 做正则白名单：仅允许 `SELECT` / `WITH ... SELECT`，拒绝 `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/REPLACE/TRUNCATE/VACUUM/PRAGMA`（写类 PRAGMA）。**即使第 1 层被意外绕过，第 2 层兜底。**

### 4.2 授权（grant，默认全拒）
- `grants.toml` 结构：
  ```toml
  [default]
  enabled = false            # 默认拒绝一切

  [databases.interview]
  enabled = true
  tables = ["*"]             # 或显式表名白名单

  [databases.resume]
  enabled = true

  [databases.chat_analysis]
  enabled = false            # 含私聊，默认关

  [databases.health]
  enabled = false
  require_redaction = true   # 若开启则强制脱敏
  ```
- `tools/list` 只广播**已授权** DB 的工具；调用时再查一次 grant，未授权返回 `isError` 提示「未授权，请在 grants.toml 开启」。

### 4.3 审计（brosis 式可追溯）
- 每次 `tools/call` 写一行本地审计日志（`mcp/audit.log`）：时间戳 + 工具名 + 目标 DB + 参数摘要（**不记返回值、不记 PII**）。用户可随时回看「AI 查了我哪些数据」。

### 4.4 脱敏（Phase 2，复用 brosis 思路）
- `redact.py`：对结果做规则脱敏（邮箱 / 手机号 / 身份证 / API token / 银行卡 Luhn 校验，对应 brosis `Redaction.swift` 的 33 条测试向量思路）。仅当 `require_redaction=true` 的 DB 开启时启用。

---

## 5. 混合检索（brosis 核心算法，分两期）

### Phase 1 — 词法检索（FTS5，零依赖、离线）
- 凡有 `*_fts` 表的域（`articles` / `diary_fts5` / `conversation_archive_fts` / `read_archive_fts` / `chat_messages_fts` / `interview_questions_fts` 等），`obc_*_search` 直接 `MATCH ? ORDER BY rank`。
- 无 FTS 的域退化为 `LIKE` 兜底。

### Phase 2 — 语义 + RRF 融合（sqlite-vec）
- 复用项目已有 `embedding_cache.db` / `article_rag.db` 的向量；语义检索经 `sqlite3.load_extension('vec0')` + 复用 `obc_llm` 的 embedding 服务生成 query 向量。
- **RRF 融合**（brosis `Retrieval+Support.swift`）：`score = Σ 1/(k+rank)`，`k=60`；词法 rank 与语义 rank 融合后重排。
- 提供 `obc_*_search_fused` 工具。Phase 2 才引入 `load_extension`，Phase 1 严格零依赖。

---

## 6. 客户端接入（直接复用 mule 的 manifest 范式）

`manifest.json` 字段与 WorkBuddy 连接器 schema 同构（`server.type=python` + `mcp_config.command/args`），因此**既能给 Claude/Cursor 用，也能注册成 WorkBuddy 连接器**。

各客户端启动命令统一为：
```bash
python3 /ABSOLUTE/PATH/TO/mcp/server.py
```
- **Claude Desktop**：`claude_desktop_config.json` → `mcpServers.obc`
- **Codex CLI**：`~/.codex/config.toml` → `[mcp_servers.obc]`
- **Claude Code**：`claude mcp add obc -- python3 /abs/server.py`
- **WorkBuddy**：连接器配置填 `mcp_config`（command+args），或 .mcpb 一键装。

---

## 7. 实施分期

### Phase 0 — 骨架跑通（最小可用，约 1 个文件 + 3 工具）
- 复制 `mule-cli/mcp/server.py` → `mcp/server.py`，改名 `obc-mcp`。
- 建 `grants.toml`（默认拒，开 `interview`/`resume`/`oss_research` 三个做示范）。
- 实现 3 个 proof-of-concept 工具：`obc_oss_projects`（列表/详情，接刚建的库）、`obc_resume_find`、`obc_interview_questions`。
- `manifest.json` + `README.md`（各客户端配置）+ `scripts/mcp/smoke_test.py`。
- **验收**：`smoke_test.py` 通过（initialize → tools/list 返回 3 工具 → 调一个返回 JSON）；`obc_query` 对 INSERT 返回拒绝；未授权 DB 调用返回 isError。

### Phase 1 — 全量目录（高价值库铺满）
- 在 `catalog.py` 注册 §3 中「默认开」的全部 DB 与工具（interview / resume / content / conversation / travel / diary / knowledge / douban / discovery / oss）。
- 补齐只读双保险 + 审计日志 + `tools/list` 按 grant 广播。
- 每个工具强制 `LIMIT` 上限（防 `chat_messages` 3.6M 行拖垮）。
- **验收**：`tests/test_mcp_server.py` 覆盖各工具只读性 + grant 拒绝 + 行数上限。

### Phase 2 — 智能化（可选，按需）
- FTS5 词法检索（Phase 1 已含基础版）→ 升级为语义 + RRF 融合（sqlite-vec）。
- `health` 等 PII 库在 `require_redaction` 下开放 + `redact.py` 脱敏。
- 交互式 grant（brosis 式：客户端弹窗逐库授权）替代静态 toml（看需求）。

---

## 8. 测试与文档

- **测试**：`tests/test_mcp_server.py`（initialize / tools/list / 各工具只读 / grant 拒绝 / LIMIT 上限 / SQL 白名单拦截）；`scripts/mcp/smoke_test.py` 管道冒烟（镜像 mule README）。
- **文档**（遵循 `AGENTS.md` 强约束：新增对外集成必补文档）：
  - 新增 `docs/modules/mcp.md`（已实现功能表 + 工具清单 + 配置项 + grant 模型）。
  - `docs/changelog.md` 顶部追版本条目。
  - `README.md` / `README_EN.md` 加「MCP 只读服务」小节 + 各客户端一键配置。
  - `mcp/README.md` 客户端接入细节。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 误开写连接破坏业务库 | 双保险只读（mode=ro + SQL 白名单）+ 审计 |
| 暴露隐私（health/私聊/chat） | deny-list 硬拒 + 默认关 + 显式 grant 才开 |
| `chat_messages` 3.6M 行拖垮 | 工具层强制 `LIMIT`（默认 50，上限 500） |
| 外接盘 IO 缓存假象导致读旧数据 | 只读连接每次 `PRAGMA integrity_check` 轻量校验（可选）；判定以 python 直读为准 |
| 引入 `mcp` SDK 破坏零依赖 | Phase 1 严格 stdlib；sqlite-vec 仅在 Phase 2 经 `load_extension` 按需加载 |
| 与现有孤儿进程抢主库锁 | MCP 只读连接走 `mode=ro`，不持写锁，不与 API 进程冲突 |

---

## 10. 给你的决策点（确认后开干）

1. **Phase 0 先跑通** 还是直接冲 Phase 1 全量？
2. **Phase 0 示范工具**用哪三个？（我默认：oss_projects / resume_find / interview_questions）
3. `chat_analysis` / `events` / `health` 这几个含私聊或 PII 的库，**是否默认关**（我建议关，按需开）？
4. 是否要**顺手注册成 WorkBuddy 连接器**（manifest 已兼容，几乎零成本）？
