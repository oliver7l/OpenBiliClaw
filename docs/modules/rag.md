# 阅读库检索增强（rag）

> 对已抓取的阅读库（`articles.content_text`）做 RAG 检索：查询 → 嵌入 → 相似度 → top-k 上下文。
> 代码位于 `src/openbiliclaw/rag/`。

## 概述

`rag` 提供对用户已采集的**阅读库全文**的检索能力。它把 `articles` 表里非空的 `content_text`
切块并嵌入（默认本地 ollama `bge-m3`，1024 维），查询时检索最相关的 chunk 并格式化成
上下文文本，供日记 RAG、chat probe 等下游模块在回答时引用真实已读内容。

## 已实现功能

| 功能 | 说明 | 入口 |
|------|------|------|
| 全文索引加载 | 懒加载已读文章并分块（带内存锁，线程安全） | `_ensure_loaded / _load_locked` |
| 嵌入服务 | 查询 / 文档分别嵌入（`is_query` 区分指令后缀） | `embed(text, is_query=...)` |
| 语义检索 | 返回 top-k chunk（含相似度分数） | `retrieve_chunks(query, top_k)` |
| 上下文格式化 | chunk 拼接为可直接馈给 LLM 的上下文 | `format_context(hits)` |
| 便捷检索 | 一步得上下文字符串 | `retrieve(query, top_k)` |
| 就绪探测 | 嵌入服务 / 数据是否就绪 | `ready()` / `count()` |

## 公开 API

```python
from openbiliclaw.rag import ArticleRagRetriever, get_retriever

r = get_retriever()                     # 单例
if r.ready():
    ctx = r.retrieve("那篇文章讲了什么？", top_k=4)   # 直接拿上下文文本
    hits = r.retrieve_chunks("...", top_k=4)          # 或拿结构化命中
```

### `retriever`（核心）

- `ArticleRagRetriever(config)`：构造时读 `[llm.embedding]` 与阅读库路径。
- `embed(text, *, is_query)`：对查询 / 文档嵌入，query 模式会附加查询指令。
- `retrieve_chunks(query, top_k=4)`：返回 `[{content, score, ...}]`。
- `format_context(hits)`：把 hits 拼成带分隔的上下文块。
- `retrieve(query, top_k=4)`：`retrieve_chunks` + `format_context` 的便捷封装。
- `get_retriever()`：进程级单例（含依赖注入，供 `diary` / `chat_probe` 复用）。

## 配置项

复用 `[llm.embedding]` 段（与 recommendation / soul / diary 同一套嵌入配置）：

| 键 | 用途 |
|----|------|
| `[llm.embedding].provider` | 为空=禁用嵌入/检索；`ollama`=本地 `bge-m3` |
| `[llm.embedding].model` | 嵌入模型（默认按 provider 推导，ollama→`bge-m3`） |
| `[llm.embedding].output_dimensionality` | 目标向量维度（默认 1024 匹配 bge-m3） |
| `[llm.embedding].base_url / api_key` | 远程嵌入端点凭据 |

阅读库路径与 `[general].data_dir` / `content.db` 关联。

## 设计决策

- **懒加载 + 单例**：首查才加载，`get_retriever()` 保证全局一份，避免多份索引内存膨胀。
- **线程安全**：`_ensure_loaded` 用锁保护加载与嵌入，`check_same_thread=False` 兼容异步路由。
- **查询 / 文档指令分离**：query 端嵌入拼指令，文档端不拼，提升检索对齐（标准 rag embedding 手法）。
- **复用既有嵌入链**：不另起一套，直接读 `[llm.embedding]`，与其余模块一致，避免双配置漂移。

## 关联

- 消费方：`api/chat_probe_routes.py`（聊天探针引用已读内容）、`diary/service.py`（日记回答检索）。
- 数据源：阅读库 `articles.content_text`（由 `refill` 等模块维护）。
- 文档：`docs/modules/diary.md`（RAG 章节）、`docs/modules/config.md`（`[llm.embedding]`）。