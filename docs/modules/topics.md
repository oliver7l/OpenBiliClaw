# 话题导出（topics）

> 把阅读库中的专题内容导出为结构化文件目录，附带融合草稿、关键词、去重与全局索引。
> 代码位于 `src/openbiliclaw/topics/`，入口脚本 `scripts/export_topics.py`。

## 概述

`topics` 是一个**内容组织/导出工具模块**。它从阅读库（`articles`）读某专题下的条目，
去重、抽关键词、做跨平台内容融合，输出一层可读、机器可查、可供外部 AI 读取的目录结构
（每专题一个 `TOPIC.md` + 原始存档 `sources/` + 全局 `INDEX.md` + `AGENTS.md` 指南）。

## 已实现功能

| 功能 | 说明 | 入口 |
|------|------|------|
| 标签抽取 | TF-IDF 风格关键词抽取 | `extract_keywords(text, top_k)` |
| 去重 | 基于内容哈希校验库内重复 | `is_duplicate / compute_content_hash` |
| schema 迁移 | 表缺 `content_hash` 列时自动补 | `ensure_content_hash_column` |
| 跨平台融合 | 条目标题 / 正文 / 标签融合为草稿 | `fuse_topic_content / _build_fusion_draft` |
| 结构化导出 | 生成 `TOPIC.md` + `metadata.json` + 原始存档 | `export_topic_to_files` |
| 全局索引 | 更新 `INDEX.md`（所有专题主目录） | `_update_global_index` |
| Agent 指南 | 生成 `AGENTS.md` 供外部 AI 读取 | `_update_agents_guide` |

## 公开 API

```python
from openbiliclaw.topics.exporter import (
    TopicItem,
    extract_keywords,
    fuse_topic_content,
    export_topic_to_files,
    is_duplicate,
)

export_topic_to_files(topic, items, output_root)   # 导出单专题
```

关键对象 / 函数：

- `TopicItem`：单条专题内容（标题 / 正文 / 源平台 / key 等）。
- `FusionResult`：跨平台融合草稿对象。
- `extract_keywords(text, top_k=20)`：返回 `[(关键词, 权重), ...]`。
- `export_topic_to_files(topic, items, output_root, *, include_content=True, run_fusion=True)`：
  导出目录（`TOPIC.md` / `metadata.json` / `fusion_draft.md` / `sources/<platform>/<key>.md`），返回统计。
- `_update_global_index(output_root)` / `_update_agents_guide(output_root)`：维护根级索引与 AI 指南。

## 配置项

`topics` 无专有配置段。输出根目录、源库路径由 `scripts/export_topics.py` 传入
（本质上是**脚本驱动的收尾导出**，不常驻运行）。

## 设计决策

- **脚本驱动**：无后台服务，作为一次性 / 定时导出脚本运行，落盘即交付，不占常驻进程。
- **目录即接口**：产物是一层可读目录，`INDEX.md` 汇总、`AGENTS.md` 指引外部 AI，人机都能消费。
- **平台分目录存档**：`sources/<platform>/` 保留原始内容对账，融合草稿另存，兼顾原始与整合。
- **去重防膨胀**：借 `content_hash` 列查重，避免同一内容反复入专题。

## 关联

- 数据源：阅读库 `articles`（由 `sources` / `refill` 维护）。
- 入口：`scripts/export_topics.py`。
- 文档：`docs/modules/reading-library.md`。