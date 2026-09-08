# 笔记模块（Notes Module）建设与参考项目集成研究

> 状态：研究/提案阶段，未实施。
> 参考仓库：`references/bili-video2book`、`references/wandao`（2026-09-07 克隆）。
> 结论先看 §1。

---

## 1. 结论摘要（TL;DR）

| 参考项目 | 是什么 | 许可证 | 集成方式 |
|---|---|---|---|
| [bili-video2book](https://github.com/LINJIANG12/bili-video2book) | B 站视频 → 精读长文 + 模块化复习笔记的转写管线 | **MIT** | **可直接搬代码**：音频获取/切片/转录/清洗/知识元提取 + 全套 prompt 模板 |
| [wandao](https://github.com/tllovesxs/wandao) | 多平台知识库 Markdown 导入导出工具（Tauri2 桌面端） | **AGPL-3.0** | **只借鉴设计思想，严禁复制代码**：checkpoint 任务模型、metadata 扩展、声明式能力、结构化日志 |

**核心判断**：

1. **两个项目解决的是笔记系统的两半**：video2book 补齐"内容怎么变成笔记"（视频→转录→结构化笔记的生成管线）；wandao 启发"笔记怎么被可靠地管理"（任务断点续跑、增量同步、导入导出、资源本地化）。
2. **主项目已有大量可复用的地基**：`articles` / `read_archive` / `article_notes` / `article_snapshots` 四张表（含 FTS5 全文索引）、`diary/` 的 RAG 与知识图谱、`embedding_cache.db`、`bilibili/api.py`（WBI 签名、视频详情、收藏夹、限速与风控退避均已就绪）。笔记模块**不需要从零建存储**，缺的是统一服务层 + 视频转写链路。
3. **建议新建 `src/openbiliclaw/notes/` 模块**，定位为"内容消费 → 知识沉淀"的统一入口，见 §5。
4. **许可证红线**：OpenBiliClaw 是 MIT。wandao 是 AGPL-3.0，复制其任何代码文件会污染主项目许可证（AGPL 的网络服务条款也与"本地私有部署"的定位冲突）。wandao 的价值在于架构模式，全部自己重写。

---

## 2. bili-video2book 研究报告

### 2.1 定位

将 B 站长视频/连载网课一键转化为"可替代原视频"的深度精读长文 + 模块化复习速查笔记。双层架构：

```
第一阶段 工具层（纯本地 CLI，零第三方 Python 依赖）
  B站链接解析 → 人声提取(64kbps AAC) → 10分钟均衡切片 → faster-whisper 转录
  → 非破坏性文本清洗 → 知识元原子抽取 → 全局 Topic 规划 → 导出 Agent 任务书

第二阶段 合成层（LLM 执行）
  ASR 语义校对纠偏 → 单集精读长文 → 跨集知识块融合 → 模块复习速查笔记
```

### 2.2 核心模块清单（`references/bili-video2book/src/`）

| 文件 | 职责 | 对主项目的价值 |
|---|---|---|
| `core/parser.py` | BV 号提取、短链解析、分集索引、多P/合集分类 | **低**：主项目 `bilibili/api.py` 已有 `get_video_info` |
| `core/wbi.py` | WBI 签名器（密钥缓存 1h 内存 + 24h 文件） | **低**：主项目已实现 `_sign_wbi_params` 等 |
| `core/fetcher.py` | playurl 音频流获取、按码率择优、防盗链下载、限速熔断 | **高**：主项目**缺** playurl 接口与音频下载 |
| `core/audio_chunker.py` | ffmpeg stream copy 无损 10 分钟均衡切片 | **高**：主项目无任何音频处理 |
| `core/local_media.py` | 本地视频/音频识别、自然排序、音频提取 | 中：导入本地视频做笔记时用 |
| `core/transcriber.py` | faster-whisper 本地离线转录（CPU int8/GPU fp16） | **高**：主项目无转录能力 |
| `core/workspace.py` | 任务工作区、manifest 断点续传、原子写入 | 中：可简化后用于笔记生成任务 |
| `generator/cleaner.py` | 非破坏性清洗（保留成语/叠词，只折叠标点空白） | **高**：可直接复用 |
| `generator/classifier.py` | study/news/general 内容自适应分类 | 中：笔记路由到不同模板 |
| `generator/kernel_extractor.py` | 知识元原子抽取、头尾噪声剥离 | **高**：结构化笔记的核心步骤 |
| `generator/topic_planner.py` | 多集知识块规划（语义规划 + 启发式 fallback） | **高**：系列视频聚合笔记 |
| `generator/block_synthesizer.py` | 跨集知识块聚合渲染 | **高** |
| `generator/prompt_templates.py` | 6 个 prompt 模板（转录/校对/学习/新闻/通用/精读） | **极高**：直接对接主项目 `llm_service` |

### 2.3 值得整体吸收的工程设计

- **风控三件套**（与主项目"优先规避风控"的取向完全一致）：元数据接口最小间隔限速、指数退避重试（2¹/2²/2³s）、连续失败 5 次熔断停手、412 风控状态定性并落盘 `.cli_status.json`。
- **断点续传**：`manifest.json` 记录每集状态（done/needs-whisper/pending），下一轮只补未完成集；`parts.json` 缓存分集列表；产物存在且大小达标即跳过。
- **三级转录降级**：Agent 原生听译 → whisper 兜底 → 人工外挂文本。
- **Strict Grounding prompt 纪律**（详见 2.4）。
- **纯标准库工具层**：requirements.txt 为空，系统依赖仅 ffmpeg/ffprobe——搬进主项目不引入新的 Python 依赖（faster-whisper 做可选依赖）。

### 2.4 Prompt 工程亮点（`prompt_templates.py`）

1. **ASR 校对 "只改字，不改话"**：仅修复同音错别字、统一专有名词，严禁删减/压缩/改写口吻；同音字多候选且无法确定时**保留原文**；注入视频标题作领域线索。
2. **精读长文事实边界**：严禁虚构转录中未出现的事实/案例/数据，未说明处必须写"转录文本未说明"；保留讲述者的比喻与类比（不许用外部术语替换）；附"完成前检查"自检清单。
3. **知识块聚合的本体融合（Ontology Merge）**：同一概念跨集分散讲解的必须合并为一条目（本质→机理→变体→边界），条目末尾标 `> 来源: P01, P03`；正文严禁出现"在上一讲中"等分集口吻；对比表格只在天然可比实体存在时才画。
4. **按需生长**：自测题只在教程类且有必要时附 2-3 道，非教学类坚决不加。

---

## 3. wandao 研究报告

### 3.1 定位（重要澄清）

wandao **不是笔记应用，也不是 AI 笔记**。它是"万能导"——把飞书/语雀/印象/有道/为知/OneNote/知识星球/ima/钉钉/WPS/知乎/公众号等 20 个平台的知识库**导出为本地 Markdown**（或反向导入），尽量保留目录树、正文、图片附件。形态是 Tauri 2 桌面端 + Python Provider 脚本插件。

它的 AI 能力只是"把导出的文档+源码交给外部 AI 学习"的提示词模板（`prompts/项目学习导师提示词.md`），**没有 RAG、没有 embedding、没有笔记对话**。

### 3.2 架构

```
Tauri 2 桌面端（Rust 主进程 + HTML/JS renderer）
  └─ Plugin v1（plugins/<平台>/plugin.json，可在线安装/回滚）
       └─ Provider v1（providers/<能力>/provider.json，声明式 fields/actions）
            └─ Python backend 脚本（进程级隔离，结构化日志 @@WANDAO_LOG@@）
核心库 wandao_core/：checkpoint(SQLite) / report / errors / logging / credentials / source_paths
```

### 3.3 值得借鉴的设计（只学思想，不抄代码）

| 设计 | wandao 中的实现 | 对笔记模块的启发 |
|---|---|---|
| **任务 checkpoint 模型** | SQLite 五表：`tasks`（含 resume_key、lease 运行时锁）/ `items`（文档级状态+attempts）/ `resources`（图片附件状态）/ `cursors`（细粒度游标）/ `events`（结构化事件流） | 视频转笔记是长耗时批量任务，**必须**有断点续跑与失败重试的表结构。笔记生成任务可简化为 `note_tasks` + `note_task_items` |
| **resume_key** | `provider+action+source+target+outputDir` 派生，同 key 任务可恢复而非重跑 | 笔记任务以 `source_url + pipeline_version` 为恢复键，pipeline 升级后自动重跑 |
| **metadata_json 灵活扩展** | 显式字段 + JSON 扩展字段并存 | `notes.metadata` 存平台差异字段（BV号/UP主/集数/时长），避免频繁改表 |
| **声明式能力（capabilities）** | provider.json 声明 export/import/images/attachments/retryFailures 等 | 笔记源适配器声明能力：`transcribe/summarize/snapshot/export_md` |
| **fields/actions 自动表单** | 前端按 manifest 自动渲染表单与按钮 | 插件端"存笔记"动作可声明式配置 |
| **统一报告 TaskResult v1** | totalDocs/successCount/failures/resourceFailures + JSON Schema | 笔记批处理结束后输出统一报告，UI 可渲染 |
| **错误脱敏协议** | 结构化错误自动脱敏 Cookie/Token/Signature | 笔记任务日志同样不得落敏感信息 |
| **source_paths 安全边界** | 导入时路径解析限制在 source_root 内，防 `../../` 越界 | 导入本地 Markdown/附件到笔记库时的安全约束 |
| **目录快照增量检测** | 文件路径+大小+mtime+哈希的差异对比 | 已读库/本地 Vault 增量同步 |

### 3.4 不需要搬的部分

Tauri 桌面端、Plugin 在线分发/签名/回滚、Rust 进程管理器、20 个平台的自动化脚本（主项目的 sources/ 体系已覆盖自己的平台矩阵，且实现取向不同：主项目走 extension 真实浏览器登录态，风控更稳）。

---

## 4. 主项目现状盘点（笔记相关的已有资产）

| 资产 | 位置 | 状态 |
|---|---|---|
| `articles` 表 | `storage/database.py` | 已有：ai_summary、reading_progress、favorited、status、tags + FTS5 |
| `read_archive` 表 | 同上 | 已有：离线阅读存档（url 唯一、全文、FTS5 trigram） |
| `article_notes` 表 | 同上 | 已有：文章内摘录/高亮/批注（quote/note/color，绑定 article_id） |
| `article_snapshots` 表 | 同上 | 已有：原文 HTML 快照防失效 |
| diary 模块 | `src/openbiliclaw/diary/` | 已有：RAG、知识图谱、时间线、情绪、反思——**笔记模块最直接的结构参照** |
| embedding 缓存 | `data/embedding_cache.db` | 已有，`[llm.embedding]` 已配置本地 provider |
| bilibili 客户端 | `src/openbiliclaw/bilibili/api.py` | 已有：WBI 签名、视频详情、搜索、收藏夹、历史、相关推荐、评论、限速与搜索退避；`subtitle.py` 已有字幕获取。**缺**：playurl 音频流、音频下载 |
| `notes/` 目录（仓库根） | 用户手工归档 | `已读库/` 每篇四件套（meta.json/raw.html/content.md/reading.html）+ 根目录 22 篇 `YYYY-MM-DD-标题.md`。**是数据不是代码**，笔记模块应提供导入而非直接读写 |
| 插件端 | `extension/` | 已有推荐/反馈/稍后再看 UI，笔记模块需新增"存笔记"入口 |

**缺口**：没有统一的笔记服务层（notes 领域逻辑分散在 articles 相关 API 里）；没有"视频→笔记"的内容生成链路（B 站作为核心源，视频内容目前只能进推荐池，无法沉淀为知识）；没有笔记与画像/推荐回流的通道。

---

## 5. 笔记模块设计提案

### 5.1 定位

> **笔记模块 = 内容消费的沉淀层**：把"看过的好内容"（B 站视频、专栏、知乎、小红书……）转化为结构化笔记入库，成为可检索、可回顾、可影响画像的私有知识库。

三层职责划分：

```
内容层（已有）        理解层（本提案新建）                    沉淀层（已有部分，补齐）
─────────────       ──────────────────────                ─────────────────────
sources/ 多源发现  →  notes/ 生成管线（转写/清洗/抽取/合成） →  notes 存储 + FTS + embedding
bilibili/api.py   →  video2book 管线（MIT 搬运适配）        →  diary RAG / 知识图谱 复用
extension 采集    →  wandao 思想（checkpoint/报告/导入导出） →  memory 事件回流画像
```

### 5.2 模块结构

```
src/openbiliclaw/notes/
├── __init__.py
├── models.py        # Note/NoteTask Pydantic 模型
├── service.py       # 笔记 CRUD、检索、与 articles/read_archive 联动、事件回流
├── store.py         # notes 表 + note_tasks 表（含 FTS5 触发器，风格对齐 storage/database.py）
├── pipeline.py      # 视频转笔记编排：字幕优先 → 音频兜底 → 转录 → 清洗 → LLM 合成
├── transcribe/      # 从 video2book 搬运适配
│   ├── fetcher.py       # playurl 音频流 + 防盗链下载（复用主项目 WBI 与限速）
│   ├── chunker.py       # ffmpeg 无损切片
│   ├── whisper.py       # faster-whisper 本地转录（可选依赖）
│   └── cleaner.py       # 非破坏性文本清洗
├── synthesis/       # 从 video2book 搬运适配
│   ├── prompts.py       # ASR 校对/精读长文/知识块聚合模板（改走主项目 llm_service）
│   ├── kernel.py        # 知识元原子抽取
│   └── planner.py       # 多集知识块规划
├── importer.py      # 导入：已读库四件套、本地 Markdown、wandao 导出的 Vault 目录
└── exporter.py      # 导出：Markdown 单篇/整库（保留双链与来源 URL）
```

API 路由：`src/openbiliclaw/api/notes_routes.py`；CLI：`openbiliclaw note ...`（list/get/search/create/video2note/import/export）。

### 5.3 数据模型

主库新增两张表（不进 pool.db，不碰用户 `notes/` 目录）：

```sql
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content_md TEXT NOT NULL DEFAULT '',      -- 结构化笔记正文
    note_type TEXT NOT NULL DEFAULT 'video',  -- video/article/essay/import
    source_platform TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    source_ref TEXT DEFAULT '',               -- BV号/专栏ID/知乎ID
    author TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',               -- wandao 思想：JSON 扩展字段（时长/集数/UP主/转录方式…）
    raw_ref TEXT DEFAULT '',                  -- 关联 article_snapshots / 转录稿 / 原文
    task_id TEXT DEFAULT '',                  -- 关联生成任务
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_platform, source_ref)
);
-- + notes_fts（title/content_md/tags/author，trigram，触发器风格对齐 read_archive_fts）

CREATE TABLE IF NOT EXISTS note_tasks (
    task_id TEXT PRIMARY KEY,                 -- uuid
    source_platform TEXT NOT NULL,
    source_ref TEXT NOT NULL,                 -- 多P视频整包任务用合集ID
    resume_key TEXT NOT NULL UNIQUE,          -- source_ref + pipeline_version
    status TEXT NOT NULL DEFAULT 'pending',   -- pending/running/done/failed/partial
    current_stage TEXT DEFAULT '',
    items_json TEXT DEFAULT '[]',             -- 分集状态：[{page, status, attempts, last_error}]（wandao items 简化版）
    error_summary TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**与现有表的关系**：

- `article_notes`（文内高亮/摘录）保持原样，是"原文内批注"；`notes` 是"一篇内容一篇结构化笔记"，两者通过 `source_url` 关联。
- `read_archive`/`article_snapshots` 继续存原文快照；`notes.raw_ref` 指过去，不重复存。
- 语义检索复用 `embedding_cache.db`，不新建向量库。
- 原始音频、切片、转录临时文件均写入系统临时目录，管线完成后清理。清洗后的转录文本（clean text）作为笔记的 `raw_ref` 持久化，不进 SQLite 避免大字段，改为单独文件存储。

### 5.4 视频转笔记管线（video2book 集成核心）

> **临时文件策略**：音频下载、切片、转录中间产物均写入系统临时目录（`tempfile.gettempdir()`），管线完成后立即清理。不保留任何原始音频/视频文件。只有最终结构化笔记正文和清洗后的转录文本（作为 `raw_ref` 备份）会持久化入库。

```
输入：BV 号 / 收藏夹 / 推荐流条目"存笔记"
  │
  ├─ ① 字幕优先：bilibili/subtitle.py 取 CC 字幕（有则跳过 ②③）
  │
  ├─ ② 音频兜底：fetcher(playurl 择优选流 → 防盗链下载)
  │     └─ 复用主项目 WBI 签名 + min_request_interval 限速 + video2book 熔断/退避策略
  │
  ├─ ③ 切片+转录：chunker(10min stream copy) → whisper(可选依赖，缺失时提示安装)
  │
  ├─ ④ 清洗：cleaner 非破坏性清洗
  │     └─ 清理临时音频文件（切片、原始音频）
  │
  ├─ ⑤ LLM 合成（走主项目 llm_service，不是外部 Agent）：
  │     a. ASR 校对（"只改字不改话"）
  │     b. 按内容类型路由模板（study/news/general）
  │     c. 结构化笔记正文 —— 默认遵循用户偏好格式：
  │        一句话核心 → 论点脉络 → 金句摘录 →（教程类附加）自测题
  │     d. 多P/系列：planner 知识块规划 → 聚合为模块复习笔记（本体融合 + 来源标注）
  │
  └─ ⑥ 入库：notes 表 + FTS + embedding + events(note_create) → 画像回流
        + TaskResult 风格统一报告（成功/失败/跳过/耗时/token 消耗）
```

**风控纪律**（video2book 已实现，全盘保留）：元数据接口 1.5s 间隔、音频下载退避、连续 5 次失败熔断、412 定性落盘。主项目现有 0.2s 限速保持不动，笔记管线用自己的更保守间隔。

### 5.5 与画像/推荐的回流

笔记是**最强的兴趣信号**（用户愿意花时间沉淀的内容）：

1. `note_create`/`note_update`/`note_review` 写入 events 表 → 偏好层权重高于普通 like。
2. 笔记 tags 与 kernel 主题进入 insight 候选（"正在研究 RAG/推荐系统"）。
3. 推荐侧：已生成笔记的内容/作者加入负采样（已消化，别再推），同主题深挖加权——与现有"最大化候选池"的取向不冲突，只是调整排序信号。

### 5.6 用户侧入口

- **插件端**：推荐卡片/视频页加"存笔记"按钮 → `POST /api/notes/from-content`（带 source_url/platform）→ 后台任务异步生成。
- **Web 端**：笔记页（卡片式、三卡一行、tab 与按钮栏左对齐、选中态用文字色变化——对齐既有 UI 偏好），列表/详情/搜索/标签筛选。
- **CLI**：`openbiliclaw note video BV1xxx`、`note list`、`note search <query>`、`note import --dir notes/已读库`、`note export --format md`。

---

## 6. 实施路线（建议 4 个阶段）

| 阶段 | 内容 | 交付物 |
|---|---|---|
| **P1 存储与服务层** | notes/note_tasks 建表 + FTS；service CRUD + 检索；API 路由；CLI 基础命令；手工写笔记/导入已读库四件套 | `docs/modules/notes.md` + 可用的笔记库 |
| **P2 视频转笔记** | fetcher/chunker/whisper/cleaner 搬运适配；prompts 对接 llm_service；note_tasks 断点续跑；单P→笔记全链路 | `note video` 命令 + 插件"存笔记"按钮 |
| **P3 系列聚合与知识层** | kernel/planner/block 聚合；多P与收藏夹批量；embedding 入库；diary 知识图谱打通 | 模块复习笔记 + 语义检索 |
| **P4 导入导出与回流** | wandao 式 Markdown 导入导出（路径安全边界、增量检测）；画像/推荐回流信号 | 导入导出 + 推荐质量提升 |

P1/P2 是 MVP（约等于把"已读库手工流程"升级为"产品化管线"）；P3/P4 可按需推进。

## 7. 风险与注意事项

1. **许可证**：wandao（AGPL-3.0）代码一行都不能进主仓库；video2book（MIT）代码搬入时在其文件头保留原版权声明。建议在 `docs/changelog` 与模块文档中注明来源。
2. **风控**：音频流接口（playurl）比元数据接口更敏感，务必沿用保守限速 + 熔断，且优先走已登录 cookie；字幕优先策略本身就是最大的风控规避（多数请求根本不碰音频流）。
3. **体量控制**：whisper 为可选依赖（`pip install -e ".[transcribe]"` extra），无 ffmpeg/模型时给出明确降级路径，不破坏 `openbiliclaw init` 的开箱体验。
4. **数据边界**：不读写用户 `notes/` 目录下的手工归档（导入是单向复制）；笔记表进主库不进 pool.db；raw 转录稿走文件系统。
5. **文档同步**：按 AGENTS.md 强制规则，实施时同步 `docs/modules/notes.md`、`cli.md`、`config.md`、`architecture.md` 架构图、`changelog.md`。

---

## 附：参考项目关键文件索引

```
references/bili-video2book/
├── SKILL.md                          # 双层架构与 Agent 工作流协议
├── src/core/{parser,wbi,fetcher,audio_chunker,local_media,transcriber,workspace}.py
├── src/generator/{cleaner,classifier,kernel_extractor,topic_planner,block_synthesizer,doc_builder,prompt_templates}.py
├── src/cli.py                        # pipeline 全流程命令
├── scripts/batch_processor.py        # 并发分集批处理
└── tests/                            # 11 个测试文件（parser/清洗/规划/聚合/412 重试等）

references/wandao/
├── wandao_core/{checkpoint,report,errors,logging,credentials,source_paths}.py
├── schemas/{task-result,task-event,error}.schema.json
├── plugins/plugin.schema.json        # Plugin v1 声明式能力模型
├── providers/provider.schema.json    # fields/actions 表单模型
├── docs/本地数据存储策略.md            # 按数据性质选存储的设计说明
└── prompts/项目学习导师提示词.md
```
