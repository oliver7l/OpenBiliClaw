# 数据库地图（DB_MAP）

> 整理日期：2026-09-10 ｜ 整理人：WorkBuddy
> 范围：面试相关数据库（求职知识库离线体系 + OpenBiliClaw 主程序面试/知识库体系）
>
> 🆕 **2026-09-17 归档更新**：本表「一、求职知识库体系」中的 **18 个重型库已移出**到项目根
> `_archive/求职知识库引擎-20260917/`（772M），另删除 876M 冗余 `.bak` 中间备份，本目录 **1.6G → 8.2M**。
> **仍在原地且活跃的只有**：`数据/01~07*.csv`（7 个）+ `数据/knowledge.db`(+shm/wal) + `规范/`。
> 判据：`src/openbiliclaw/interview/job/engine.py` 在线依赖仅此三项；其余库仅被已自归档的
> `scripts/_archive_oneoffs/` 一次性脚本引用。下表各库的**大小/表结构仍然有效**（对归档副本而言），
> 但**路径前缀已变为 `_archive/求职知识库引擎-20260917/`**。恢复方式见 README 顶部说明块。
> 备注：`面试弹药库.db` / `幻灯片笔记.db` 的内容此前已收口到主程序 `data/interview.db`，
> 归档不影响在线功能；`file_index.db` 已被 `数据/knowledge.db` 取代。

> 本次整理动作：① 删除根目录 4 个 0B 死壳；② `knowledge.db`(索引) 改名 `file_index.db`；③ `面试处理库.chunk` 路径归一化（孤儿 1310→106）；④ 18GB 历史备份归档到 `data/_archive/`；⑤ 删除 106 个真丢失孤儿 chunk 行（637 行，chunk 13630→12993），FTS 重建；⑥ **结构化数据统一收口到主程序 `data/interview.db`**：把离线侧 `面试弹药库.db` + `幻灯片笔记.db` 全部表合并进 `interview.db`（仅新增表，应用表原样保留），原始库 `面试资料总库.db` 不动；⑦ **书籍单独分库（已从总库移除）**：`面试资料总库.db` 中 `category ∈ ('书籍','技术书籍')` 的 87 篇文档 + 其 `doc_chunk`(7878) / `doc_content`(80) / `doc_vector`(7878) / `doc_fts` 完整复制进新建的 `书籍库.db`（144.3MB，独立可用全文+语义检索），**并已从总库移除这 87 篇**（doc/doc_chunk/doc_content/doc_vector 删除 + `doc_fts` rebuild，外部内容 FTS 不可手动删索引行否则报 malformed，须用 rebuild）。总库由 459MB 瘦身至 289MB。备份：`data/_archive/面试资料总库_移除书籍前_20260910_091332.db`。
⑧ **方向知识库独立分库（已从总库移除）**：`面试资料总库.db` 中 `category='方向知识库'` 的 75 篇文档 + 其 `doc_chunk`(2287) / `doc_content`(73) / `doc_vector`(2287) / `doc_fts` 完整复制进新建 `方向知识库.db`（26.5MB，独立可用全文+语义检索），**并已从总库移除这 75 篇**（doc/doc_chunk/doc_content/doc_vector 删除 + `doc_fts` rebuild，同书籍流程）。总库由 289MB 瘦身至 247MB。备份：`data/_archive/面试资料总库_移除方向知识库前_20260910_102517.db`。
⑨ **清理"其他"类脏数据（2026-09-10）**：`面试资料总库.db` 中 `category='其他'` 的 1 篇「个人生活对话导出.json」(id=2426, 2455 chunk, 5.2MB，隐私+无关) 已移出，其余 6 篇 README/索引/体检报告归位到 `工作资料`，「其他」类清零。备份：`data/_archive/面试资料总库_清理其他前_20260910_100840.db`。总库现 doc 2118 / chunk 23138 / vec 23134。
⑩ **幻灯片笔记独立分库（已从总库移除）**：`面试资料总库.db` 中 `category='幻灯片笔记'` 的 410 篇文档级原始抽取 + 其 `doc_chunk`(1148) / `doc_content`(234) / `doc_vector`(1144) / `doc_fts` 完整复制进新建 `幻灯片原始库.db`（27.9MB，独立可用全文+语义检索），**并已从总库移除这 410 篇**（doc/doc_chunk/doc_content/doc_vector 删除 + `doc_fts` rebuild，同书籍流程）。总库由 247MB 瘦身至 225MB。备份：`data/_archive/面试资料总库_移除幻灯片笔记前_20260910_103911.db`。注：幻灯片级 OCR 仍在 `幻灯片笔记.db`（离线活源）与 `data/interview.db.slide`（主程序收口，二者逐行一致 435 条），三层架构为 **源(幻灯片原始库.db) → 加工(幻灯片笔记.db) → 收口(interview.db.slide)**。
⑪ **工作资料细粒度分库（已从总库移除，2026-09-10）**：`面试资料总库.db` 中 `category='工作资料'` 的 1336 篇（腾讯 2019–2020 工作档案）按源目录主题**细粒度拆成 10 个独立库**——微视推荐库(21)/算法面试库(355)/内部资料库(255)/技术分享库(192)/看点库(137)/十级答辩库(144)/图神经网络库(9)/UGC推荐库(14)/无量ModelZoo库(13)/腾讯综合工作库(146)，合计 1286 篇复制进新库（各含 doc/chunk/content/vector/fts，schema 同总库含 FTS 触发器，可直接全文+语义检索）；另 50 篇垃圾/错分（`~$`临时锁文件、`.ipynb_checkpoints`、误入的 `.workbuddy/memory`、错分的索引/体检报告/README、`06项目代码`）从总库删除（不入库）。**全部 1336 篇工作资料已从总库移除**（doc/doc_chunk/doc_content/doc_vector 删除，FTS 由触发器自动维护）。总库由 225MB 瘦身至 119.4MB（doc 1633→297）。备份：`data/_archive/面试资料总库.db.bak-20260910-140210`。脚本：`kb_split_work_themes.py`（内置 `--selftest` 副本验证）。

---

## 一、求职知识库体系（离线档案 + 加工流水线）

路径前缀：`求职知识库/_系统_知识库引擎/数据/`（⚠️ **2026-09-17 起，下表 18 个重型库实际路径已改为
`_archive/求职知识库引擎-20260917/`；仅 7 个 CSV 与 `knowledge.db` 仍在原前缀**）

| 库 | 大小 | 角色 | 关键表 | 维护脚本 |
|---|---|---|---|---|
| 面试资料总库.db | 119.5MB | L0 原始库（全量**非书籍/非方向知识库/非幻灯片笔记/非工作资料**文档抽取原文 + 整篇 FTS，**书籍→书籍库.db、方向知识库→方向知识库.db、幻灯片笔记→幻灯片原始库.db、工作资料→下面 10 个主题库均已迁出**；2026-09-10 新增字节面试弹药：PDF(童力-百度-数据分析)+飞书链接MD 入 岗位弹药） | doc(299) / doc_chunk(4577) / doc_fts / doc_vector(4577，已修复) | kb_ingest.py |
| 书籍库.db | 186.8MB | **书籍专库**（category∈书籍/技术书籍，已从总库拆分独立；2026-09-10 另入库 2 本新书：黄佳《大模型应用开发 动手做AI Agent》PDF·449页27万字符、阿耐《大江大河四部曲》EPUB·199万字符） | doc(89) / doc_chunk(9761) / doc_content(82) / doc_fts / doc_vector(9736) | kb_split_books.py / kb_ingest_books_two.py |
| 方向知识库.db | 26.5MB | **方向知识库专库**（category=方向知识库，已从总库拆分独立） | doc(75) / doc_chunk(2287) / doc_content(73) / doc_fts / doc_vector(2287) | kb_split_direction.py |
| 幻灯片原始库.db | 27.9MB | **幻灯片笔记源库**（category=幻灯片笔记·文档级原始，已从总库拆分独立） | doc(410) / doc_chunk(1148) / doc_content(234) / doc_fts / doc_vector(1144) | kb_split_slides_original.py |
| 微视推荐库.db | 5.7MB | **工作资料主题库①**：微视(Weishi)推荐项目（图嵌入召回+TS 等简历核心亮点，21 篇） | doc(21) / doc_chunk(209) / doc_fts / doc_vector(209) | kb_split_work_themes.py |
| 算法面试库.db | 25.3MB | **工作资料主题库②**：候选人面试刷题/算法书（leetcode-obsidian 280 + fucking-algorithm 70 + 简历/PDF，355 篇） | doc(355) / doc_chunk(2556) / doc_fts / doc_vector(2556) | kb_split_work_themes.py |
| 内部资料库.db | 75.2MB | **工作资料主题库③**：2020年09月内部资料（会议/项目文档，255 篇，chunk 占比最大） | doc(255) / doc_chunk(8494) / doc_fts / doc_vector(8494) | kb_split_work_themes.py |
| 技术分享库.db | 17.0MB | **工作资料主题库④**：2020年08月技术分享（192 篇） | doc(192) / doc_chunk(997) / doc_fts / doc_vector(997) | kb_split_work_themes.py |
| 看点库.db | 13.5MB | **工作资料主题库⑤**：腾讯看点（图集/搜索 + 小说深度调研，138 篇） | doc(138) / doc_chunk(857) / doc_fts / doc_vector(857) | kb_split_work_themes.py |
| 十级答辩库.db | 11.8MB | **工作资料主题库⑥**：10 级晋升答辩材料（144 篇） | doc(144) / doc_chunk(657) / doc_fts / doc_vector(657) | kb_split_work_themes.py |
| 图神经网络库.db | 5.2MB | **工作资料主题库⑦**：图神经网络专题（9 篇） | doc(9) / doc_chunk(140) / doc_fts / doc_vector(140) | kb_split_work_themes.py |
| UGC推荐库.db | 5.3MB | **工作资料主题库⑧**：UGC 推荐专题（14 篇） | doc(14) / doc_chunk(131) / doc_fts / doc_vector(131) | kb_split_work_themes.py |
| 无量ModelZoo库.db | 4.7MB | **工作资料主题库⑨**：无量 ModelZoo（13 篇） | doc(13) / doc_chunk(98) / doc_fts / doc_vector(98) | kb_split_work_themes.py |
| 腾讯综合工作库.db | 14.6MB | **工作资料主题库⑩**：腾讯工作档案兜底（其余未归主题的 146 篇） | doc(146) / doc_chunk(856) / doc_fts / doc_vector(856) | kb_split_work_themes.py |
| 面试处理库.db | 174.0MB | L1 分块索引（搜索底层） | chunk(12993) / chunk_fts（外部内容 FTS5） / meta | build_index.py |
| 面试弹药库.db | 9.4MB | L2 面试弹药（结构化备考） | ammo_doc(105) / concept(33) / project(24) / question(25) / job(6) / log(2) / number(60) | kb*.py |
| 幻灯片笔记.db | 1.7MB | 幻灯片 OCR 笔记 | slide(435) / source(17) / run_log(52) / processed_notes(7) / unprocessed(5) | slides_*.py |
| file_index.db | 2.5MB | 全库文件索引 | file_index(6870) / layer_stats(view) | build_index.py |

> ✅ **`doc_vector` 已修复（2026-09-10）**：根因为本机缺 `sqlite-vec`（vec0 扩展加载不了），并非缺数据——装包后 `doc_vector` 实际已有 32843/33471 向量；再用 `kb_revectorize.py` 幂等补缺 574 块，覆盖率达 **99.99%**（仅 4 个 ≤20 字极短块跳过）。语义/embedding 检索现已可用（bge-m3 1024 维，与表维度一致）。重跑：`cd 求职知识库/_系统_知识库引擎/scripts && ../../.venv/bin/python kb_revectorize.py 执行`（须项目 .venv 的 python 3.11，且 Ollama 已拉 bge-m3）。
> `面试处理库.chunk.rel_path` 已归一化对齐磁盘（含 `03_工作资料/` 层），与 `面试资料总库.doc.rel_path` 一致；**原 106 个孤儿（源文件确不存在）已于 2026-09-10 清理**（删除 637 行），当前 chunk 表无孤儿。

---

## 二、OpenBiliClaw 主程序体系（在线服务）

路径前缀：`data/`

| 库 | 大小 | 角色 | 关键表 | 引用方 |
|---|---|---|---|---|
| interview.db | 20.8MB | 面试功能**生产库** + **结构化面试数据统一收口** | 应用表：interview_questions(199) / interview_reviews(2) / kb_documents(684)；结构化表：ammo_doc(105) / concept(33) / project(24) / question(25) / number(60) / job(6) / log(2) / slide(435) / source(17) / processed_notes(7) / run_log(52) / unprocessed(5)（各 FTS 一并并入） | `src/openbiliclaw/interview/*` + 离线合并脚本 |
| knowledge.db | 4.4MB | 阅读库知识图谱 | entities(774) / entity_relations(4712) / knowledge_cards(267) / topics(8) / topic_items(2153) | `src/openbiliclaw/knowledge*` |
| knowledge_audit.db | 242.9MB | 阅读库质量审计 | article_quality_scores(87k) / audit_issues(100万) / gap_records(397k) | `src/openbiliclaw/self_evolution/*` |

> `data/interview.db` 现在是**面试相关数据的统一收口**：① 主程序应用表（interview_questions / kb_documents / interview_reviews，由 `import_*.py` 单向导入）；② 离线侧结构化面试数据（`面试弹药库` + `幻灯片笔记` 全部表，2026-09-10 合并并入，**仅新增表、应用表原样保留**）。原始全文数据仍在 `面试资料总库.db`（L0 原始库），**不要**把原始库并入 interview.db（体量 459MB、含向量，会拖累主程序）。

---

## 三、已归档（data/_archive/，非删除）

| 目录 | 内容 |
|---|---|
| `_dead_shells_20260909/` | 根目录 4 个 0B 死壳（面试资料总库/knowledge/knowledge_audit/openbiliclaw） |
| `backups_20260910/` | `data/backups` 历史备份（17 个 openbiliclaw_*.db + WAL/SHM） |
| `_backup_p8_20260910/` | P8 阶段手工备份（knowledge_audit×2、openbiliclaw×2） |
| `面试处理库_归一化前_*.db` | T3 路径归一化前备份（2 份时间戳） |
| `面试处理库_清理孤儿前_20260910_000802.db` | 删除 106 孤儿 chunk 前备份（174MB） |

> 应用备份逻辑（`storage/maintenance.py` 的 `rotate_database_backups`）会在 `data/backups/` 缺失时自动重建空目录，归档不影响主程序。

---

## 四、关键约定（防再次变乱）

1. **路径约定**：`面试资料总库.doc.rel_path` 与磁盘一致（含 `03_工作资料/` 层）；`面试处理库.chunk.rel_path` 已对齐同一约定。新增入库脚本须沿用此约定。
2. **同名规避**：求职知识库的文件索引现名 `file_index.db`（原名 `knowledge.db`，与 `data/knowledge.db` 重名已改）。引用它的脚本：`build_index.py` / `kb.py` / `doctor.py`。
3. **派生关系**：`面试处理库` 是 `面试资料总库` 的分块索引；`data/interview.db` 由求职知识库导入；不要把它们当独立数据源重复维护。
4. **备份纪律**：日常备份走 `data/backups/`（自动轮转）；大体积/阶段性手动备份统一进 `data/_archive/`，不要在项目根目录或主 `data/` 散落裸库。
5. **书籍独立库（2026-09-10）**：书籍体量偏大（87 篇却占全库 23.5% 的 chunk），已拆出独立 `书籍库.db`（自包含 doc/chunk/content/vector/fts，可独立做全文+语义检索），并**已从总库移除**这 87 篇（`kb_remove_books_from_master.py`，先备份总库）。⚠️ **关键坑**：`doc_fts` 是外部内容 FTS5，手动 `DELETE FROM doc_fts` 后紧跟 `DELETE FROM doc` 会报 `database disk image is malformed`；正确做法是删完 doc 后对 `doc_fts` 执行 `INSERT INTO doc_fts(doc_fts) VALUES('rebuild')` 重建索引。移除后可 `VACUUM` 总库回收空闲页（459MB→289MB）。
6. **方向知识库独立库（2026-09-10）**：方向知识库体量适中（75 篇占全库 9.9% chunk），已拆出独立 `方向知识库.db`（自包含 doc/chunk/content/vector/fts，可独立做全文+语义检索），**并已从总库移除**这 75 篇（`kb_remove_direction_from_master.py`，先备份总库）。移除后总库 289MB→247MB。
7. **幻灯片三层架构（2026-09-10）**：幻灯片资料分三层——① `幻灯片原始库.db`（文档级原始抽取，从总库迁出，kb_split_slides_original.py）；② `幻灯片笔记.db`（幻灯片级 OCR 活源，4+ 脚本实时维护）；③ `data/interview.db.slide`（主程序收口，与 ② 逐行一致 435 条）。三层职责分离，勿把 ② 与 ③ 当冗余删除（③ 是既定的"结构化数据收口到 interview.db"策略）。总库已不含幻灯片笔记（`kb_remove_slides_from_master.py` 移除，先备份）。
8. **工作资料 10 主题库（2026-09-10）**：`工作资料` 本质是腾讯 2019–2020 工作档案，已按源目录主题细粒度拆成 **10 个独立库**（微视推荐/算法面试/内部资料/技术分享/看点/十级答辩/图神经网络/UGC推荐/无量ModelZoo/腾讯综合工作），schema 与总库一致（含 `doc_fts` 触发器，插入/删除 `doc` 自动维护 FTS，**改这些库时勿手动 DELETE doc_fts 行**）。总库已不含 `工作资料`。新增工作类文档若仍需入总库，归类时优先归入对应主题库；若主题不明则入 `腾讯综合工作库.db`。脚本 `kb_split_work_themes.py` 可重跑（幂等：已存在则 INSERT OR IGNORE），但真库执行前务必先看 `--selftest` 副本验证。
