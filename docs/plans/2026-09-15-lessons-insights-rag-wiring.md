# 经验记忆接入 RAG：让 lessons / 洞察 / 内容库可被自动检索 · 方案

> 状态：**待确认**（2026-09-15 按用户指示出方案，未动代码）
> 日期：2026-09-15
> 动机：2026-09-14 收藏的 #133（Hermes 四层记忆架构）对照本项目时发现——`diary/insight_engine.py`、`diary/advanced_memory.py` 在持续产出经验数据（lessons 448 / 洞察模式 24 / 高级记忆 134），但**全项目无任何消费方**（grep 零 import）；内容库 137 条也只存在 `conversation_archive` 里没有检索入口。而检索基建（RAG 通道）已经建成并在服务 chat 接地——**只差把数据源接上**。

---

## 1. 现状盘点（2026-09-15 实测）

### 1.1 已有且可复用的检索通道（一条已通的链路）

```
scripts/build_article_rag.py   →  data/article_rag.db   →  src/openbiliclaw/rag/retriever.py  →  chat 接地
（离线建索引：分块+本地 bge-m3 嵌入）  （独立 SQLite，float32 BLOB 向量）  （内存矩阵余弦扫描，~15ms/32k chunks）  （chat_probe_routes 注入 prompt）
```

- 建索引脚本**已支持多源**：`SOURCE_TABLES = (("articles","阅读库"), ("read_archive","已读库"))`，每 chunk 带 `source_table` 列区分来源、按 `(source_table, article_id)` 幂等去重、可断点续跑。
- retriever 端 `ArticleRagRetriever` **零改动即可消费新源**：读列时已容忍任意 `source_table` 值；`format_context` 按 title/author/source_name 渲染引用。
- 嵌入配置走 `config.toml [llm.embedding]`（⚠️ 已知硬契约：provider/model/api_key/base_url 必须同源，切 provider 必须同时清 api_key+base_url，否则恒 404）。
- 测试已有 `tests/rag/test_rag_retriever.py`（离线 stub 嵌入）。

### 1.2 待接入的数据源（四个，均已核实 schema 与行数）

| 源 | 库 | 行数 | 可用文本列 | 元数据映射（→chunks 列） |
|---|---|---|---|---|
| `diary_lessons` | diary.db | 448 | `content` | title=`[经验·{category}]`前缀，source_name=`经验教训`，url=''，author=''；带 `confidence`(0.45~0.9) 与 `status`(validated/disputed) |
| `diary_insight_patterns` | diary.db | 24 | `title` + `description` | text=`{title}。{description}`，title=`[洞察·{pattern_type}]`，source_name=`洞察模式`，confidence/severity 保留 |
| `diary_advanced_memories` | diary.db | 134 | `content` | title=`[画像·{layer}]`，source_name=`用户画像`，importance→可作过滤权重 |
| `conversation_archive` | openbiliclaw.db | 137 | `extracted_original_md`（原文）/ `my_analysis_md`（我的解读） | title=`question_title`，url=`source_url`，source_name=`source_type`，author=`author`；原文与解读**分两个 source_table**，引用时可区分 |

注意：lessons 里存在 `status='disputed'`（有争议）条目（如 id=1 confidence 0.45）——**这正是 #137 李沐说的「敢认错」该有的样子**，检索引用时应原样带上 disputed 标记而不是悄悄过滤。

### 1.3 三个约束

1. **跨库**：diary.db 与 openbiliclaw.db 是两个文件，build 脚本目前只连 openbiliclaw.db → 需 `ATTACH DATABASE`。
2. **schema 不齐**：现有 build 主流程假设每源都有 `(id,title,url,author,source_name,content_text)` 统一列 → 四个新源都没有这组列，需要**每源 SELECT 适配器**（SQL 里 AS 出统一形状，主流程不用改）。
3. **服务在线**：build 只**读** diary.db / openbiliclaw.db、**写**独立的 article_rag.db，不碰服务的写锁路径；但连接需 `timeout=15` + 只读意图，避免与 pm2 服务偶发竞争。

---

## 2. 目标 / 非目标

**目标**
1. 四个新数据源全量进入 `article_rag.db`，retriever 与 chat 接地**自动**覆盖（无需改 retriever 主逻辑）。
2. 提供只读查询 CLI：任意一句话 → 返回带来源标注的相关经验/洞察/内容库条目（供 Agent 会话开工前调用、供人排障用）。
3. 增量可续跑：新 lessons / 新内容库条目落库后重跑脚本只补增量（复用现有 `_indexed_ids` 机制）。
4. 引用可溯源：每条 hit 标明来源层（经验/洞察/画像/内容库原文/内容库解读）与 disputed 标记。

**非目标**
- 不动 `insight_engine.py` / `advanced_memory.py` 的生产逻辑（它们产出正常，缺的只是消费）。
- 不接 `diary_beliefs` / `diary_belief_conflicts` / `diary_drift_events` 三张空表（另案处理：接线或删除）。
- 不做重排序/混合检索（BMRF+向量融合等），先验证纯向量召回质量够不够。
- 不把 diary 类内容暴露到任何对外 API（仅本地 chat 接地 + 本地 CLI，见 §5 隐私）。

---

## 3. 设计

### 3.1 build 脚本多源适配器（P0 核心改动）

`SOURCE_TABLES` 从「表名二元组」升级为「源适配器」结构，每源声明：

```python
@dataclass(frozen=True)
class RagSource:
    key: str            # source_table 值，如 "diary_lessons"
    label: str          # 日志显示名
    db: str             # "main" | "diary"
    select_sql: str     # AS 出统一形状: id,title,url,source_name,author,content_text
    min_confidence: float = 0.0
```

五个新源的关键 SQL（在源库侧 AS 成统一形状，主流程零改动）：

- `diary_lessons`：
  ```sql
  SELECT id,
         '[经验·' || category || CASE WHEN status='disputed' THEN '·有争议' ELSE '' END || ']' AS title,
         '' AS url, '经验教训' AS source_name, '' AS author,
         content AS content_text
  FROM diary_lessons WHERE length(content) >= :min_len AND confidence >= :min_conf
  ```
- `diary_insight_patterns`：`title || '。' || description AS content_text`，title 前缀 `[洞察·pattern_type]`。
- `diary_advanced_memories`：title 前缀 `[画像·layer]`。
- `conversation_archive`（原文）：`extracted_original_md AS content_text`，title=`question_title`，url=`source_url`，source_name=`source_type`，author=`author`。
- `library_analysis`（解读，同库同 id 空间但独立 source_table）：`my_analysis_md AS content_text`，title=`'解读：' || question_title`。

实现要点：
- `main()` 里按 `db` 字段各开一个连接（openbiliclaw.db 直连；diary.db 直接 `sqlite3.connect(..., timeout=15)` 打开，**不用 ATTACH**——两库分属不同生命周期，独立连接更符合现状且避免写锁语义混淆）。
- `--source` 参数的 choices 扩到全部 7 个 key；`--min-confidence` 新增（默认 0，即全量）。
- `_chunk_text` 复用（600 字符/块、句子重叠）；lessons/洞察多为单段短文，天然 ≤1 块。
- 增量语义沿用 `_indexed_ids`：按 `(source_table, article_id)` 去重。⚠️ 一个已知边界：源内容**被更新**时（如 lesson 的 status 变化）增量不会感知——P0 接受此限制（lessons 更新频率极低），在 meta 表记录 `built_at` 供人工判断是否 `--force` 全量重建；`--force` 作为新参数提供（先 DELETE 该源全部 chunk 再重建）。

### 3.2 查询 CLI（P1）

新增 `scripts/rag_query.py`（只读，不写任何库）：

```
.venv/bin/python scripts/rag_query.py "怎么处理 xhs 抓取被风控" [--top-k 5] [--json] [--source diary_lessons]
```

- 直接复用 `ArticleRagRetriever`（单例 + mtime 热重载已内置），输出 `format_context` 文本或 JSON。
- 用途：①Agent（我）在开工前调用，把相关历史经验拉进上下文——这就是「开工前自动检索」的落点；②人排障时快速查「这事以前有没有踩过坑」。
- 预计规模：现有 ~3.2 万 chunks + 新增约 **700~900 chunks**（448 lessons + 24 洞察 + 134 画像 + 137 条内容库×平均 3~5 块），矩阵扫描耗时可忽略。

### 3.3 自动化（P1 可选）

- 每日凌晨增量跑 `build_article_rag.py`（脚本本身幂等可续跑），跑完在 meta 记录本次增量数；连续失败告警。
- 自动化 prompt 里写明：`--source` 缺省全源、失败时输出 stderr 摘要、不重试超过 2 次。

### 3.4 chat 接地（P0 自动受益，零改动）

`chat_probe_routes` 已在注入 `get_retriever().retrieve(...)`——索引扩展后 chat 自动能引用 lessons/洞察/内容库。仅一处小改（P0 顺手）：`format_context` 的标题渲染无需变，因为来源区分已编码在 title 前缀（`[经验·…]`/`[洞察·…]`/`[画像·…]`）里；若要更醒目可在 §3.1 的 title 前缀上迭代，不动 retriever。

---

## 4. 分期

| 期 | 内容 | 验收 |
|---|---|---|
| **P0** | build 脚本多源适配器 + 5 个新源接入 + `--force`/`--min-confidence` 参数；`tests/rag/` 新增适配器离线单测（stub EmbedClient，复用现有测试基建） | ① 全源 dry-run 行数正确（448/24/134/137）；② 真跑后 `article_rag.db` chunks 总数=旧+新，按 source 分组计数正确；③ 现有 retriever 测试不回归；④ 三个已知 sanity query 各能召回预期条目（见 §6） |
| **P1** | `scripts/rag_query.py` CLI + 每日增量自动化 | CLI 两种输出模式可用；自动化连续 3 天成功 |
| **P2** | 质量评估：构造 10 个「应召回历史经验」的问题集人工评分；`--force` 重建流程演练；beliefs/conflicts 表去留另案 | P@5 ≥ 0.6 或给出调优结论 |

预计成本：P0 真跑约 700~900 chunks × ~3s（bge-m3 CPU、3 并发）≈ **15~20 分钟**后台任务；P0 编码 + 测试约一个工作段。

---

## 5. 风险与边界

1. **隐私**：diary 三源含个人日记提炼内容，进入 `article_rag.db` 后可被 chat 检索引用。`data/` 整体不入 git（既有政策），风险限于本机 chat 上下文——**可接受**；但 chat 的对外分享/导出功能若存在，需确认不会携带接地块（实现时核查 chat 响应链路）。
2. **嵌入同源契约**：全部新源沿用 `[llm.embedding]` 同一配置，无新增 provider 面；若未来切 provider，article_rag.db 需全量重建（meta 里记 provider/model 便于检测失配——P0 顺手在 meta 写入 `embed_model`，retriever 加载时可 warn 失配）。
3. **外接盘 IO**：build 写 article_rag.db 沿用现有 MEMORY journal 模式（已绕开沙箱日志文件拦截），无新风险。
4. **disputed 数据混入**：lessons 中 `status='disputed'` 条目照常索引但 title 带标记——宁可带争议标注可检索，不可静默丢失（与 #137「更新观点的速度比观点本身值钱」一致）。
5. **测试与服务互锁**：rag 测试全部离线 stub，不触发 SQLite 写锁问题；真跑 build 放后台、不与全量 api 测试并发。

---

## 6. 验收用 sanity query（P0 真跑后逐条验证）

| Query | 预期召回 |
|---|---|
| 「xhs 抓取被风控 / empty noteDetailMap」 | 内容库相关条目（#128 短链解析等）+ 相关 lessons |
| 「测试和服务写锁冲突怎么办」 | MEMORY/经验中「pytest 持锁→服务 500」相关条目（若在 lessons 中有提炼） |
| 「最常提及的人 / 家庭话题」 | `diary_insight_patterns` 对应洞察 |
| 「乐乐 带娃 看花园」 | `diary_advanced_memories` relationships 层对应画像 |
| 「Hermes 记忆架构」 | #133 内容库条目（原文+解读双命中） |

---

## 7. 明确不做（本轮）

- `diary_beliefs` / `diary_belief_conflicts` / `diary_drift_events` 三表：接线或删除，另出决策（涉及 insight_engine 逻辑改动，与本方案解耦）。
- 向量库升级（sqlite-vec / FAISS）：3.3 万级 chunks 内存矩阵扫描 15ms，无必要。
- 内容库 md 的 FTS5 全文索引（Hermes 式关键词检索）：向量检索已覆盖；若 P2 评估召回不佳再议混合方案。
