# 数据库地图（DB_MAP）

> 整理日期：2026-09-10 ｜ 整理人：WorkBuddy
> 范围：面试相关数据库（求职知识库离线体系 + OpenBiliClaw 主程序面试/知识库体系）
> 本次整理动作：① 删除根目录 4 个 0B 死壳；② `knowledge.db`(索引) 改名 `file_index.db`；③ `面试处理库.chunk` 路径归一化（孤儿 1310→106）；④ 18GB 历史备份归档到 `data/_archive/`；⑤ 删除 106 个真丢失孤儿 chunk 行（637 行，chunk 13630→12993），FTS 重建；⑥ **结构化数据统一收口到主程序 `data/interview.db`**：把离线侧 `面试弹药库.db` + `幻灯片笔记.db` 全部表（ammo_doc/concept/project/question/number/job/log/slide/source/processed_notes/run_log/unprocessed + 各自 FTS）合并进 `interview.db`（仅新增表，应用表 interview_questions/kb_documents/interview_reviews 原样保留），原始库 `面试资料总库.db` 不动。

---

## 一、求职知识库体系（离线档案 + 加工流水线）

路径前缀：`求职知识库/_系统_知识库引擎/数据/`

| 库 | 大小 | 角色 | 关键表 | 维护脚本 |
|---|---|---|---|---|
| 面试资料总库.db | 458.8MB | L0 原始库（全量文档抽取原文 + 整篇 FTS） | doc(2206) / doc_chunk(33471) / doc_content(1893) / doc_fts / doc_vector⚠️ | kb_ingest.py |
| 面试处理库.db | 174.0MB | L1 分块索引（搜索底层） | chunk(12993) / chunk_fts（外部内容 FTS5） / meta | build_index.py |
| 面试弹药库.db | 9.4MB | L2 面试弹药（结构化备考） | ammo_doc(105) / concept(33) / project(24) / question(25) / job(6) / log(2) / number(60) | kb*.py |
| 幻灯片笔记.db | 1.7MB | 幻灯片 OCR 笔记 | slide(435) / source(17) / run_log(52) / processed_notes(7) / unprocessed(5) | slides_*.py |
| file_index.db | 2.5MB | 全库文件索引 | file_index(6870) / layer_stats(view) | build_index.py |

> ⚠️ `doc_vector` 依赖 sqlite-vec 的 vec0 模块，本机 **未安装** → 语义/embedding 检索失效，目前仅 FTS 关键词可用。修复需 `pip install sqlite-vec` + 用 bge-m3 重新向量化（CPU 慢，暂未做）。
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
