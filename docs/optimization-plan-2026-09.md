# OpenBiliClaw 优化计划

> 基于 Agent-SaveMark、Trove AI、Cruxwire 三个同类项目的深度研究
> 制定日期：2026-09-06

## 研究摘要

### 三个项目核心亮点

| 项目 | 定位 | 最值得借鉴 |
|------|------|-----------|
| **Agent-SaveMark** | 自托管AI个人知识库（7.2MB） | 实体规范化、实体综合(Karpathy wiki)、分阶段enrichment pipeline、MCP Server、混合搜索 |
| **Trove AI（拾遗）** | 中文互联网AI稍后读+知识库（2.5MB） | 学习路径生成、微信/飞书Bot入口、概念页面、Obsidian同步 |
| **Cruxwire** | 本地AI新闻阅读器（1.2MB，纯stdlib） | LLM内容评分+自定义兴趣描述、跨来源聚类、个性化品味学习、粘性保留、按需TL;DR |

---

## 第一阶段：知识图谱质量提升（高价值，中复杂度）

### 1.1 实体规范化（Entity Canonicalization）
**来源：Agent-SaveMark canonicalizer.py**

**问题：** 当前知识图谱648个实体中存在大量重复（如"JS"/"JavaScript"、"AI"/"人工智能"、"大模型"/"LLM"）。

**方案：**
- 添加 `entity_aliases` 表（entity_id, alias, source）
- 三层匹配逻辑：
  - Tier 1: 精确别名匹配（不区分大小写）
  - Tier 2: 规范化名称匹配（去标点、折叠空白、小写）
  - Tier 3: 创建新实体
- 合并描述：新描述是已有描述的子串则跳过，否则追加
- 在知识图谱构建时应用规范化

**预期效果：** 实体数量减少30-40%，图谱质量大幅提升。

### 1.2 实体综合（Entity Synthesis - Karpathy Wiki Pattern）
**来源：Agent-SaveMark synthesizer.py**

**问题：** 当前实体只有名称和提及次数，没有深度描述。

**方案：**
- 对热门实体（mention_count >= 3）生成 LLM 综合页面
- 输入：实体 + 最显著的文章提及（按salience排序）+ 最相关的实体
- 输出结构化 JSON：
  ```json
  {
    "summary": "2-4句基于证据的描述",
    "themes": [" recurring-context phrases"],
    "key_contexts": [{"context": "1-2句摘录", "source_article_id": "..."}],
    "relationships": [{"entity_name": "相关实体", "nature": "关系描述"}],
    "confidence": "low|medium|high"
  }
  ```
- 自动再生规则：3+次新提及后触发，24小时节流
- LLM 缓存：基于内容哈希（evidence_hash）
- 存储在实体的 `synthesis` 字段

**预期效果：** 点击实体可以看到类似维基百科的综合页面，而不只是标签云。

### 1.3 实体提取增强
**来源：Agent-SaveMark extractor.py**

**方案：**
- 添加 gleaning 二次检查（第一轮提取后，LLM再检查是否遗漏重要实体）
- 扩展实体类型：person, org, concept, tool, product, event, location, other（当前只有 concept/tech 等）
- 添加 salience 评分（实体在文章中的重要性，用于综合时排序）
- Few-shot 示例提升提取质量

---

## 第二阶段：推荐与学习路径（高价值，低复杂度）

### 2.1 学习路径生成（Learning Paths）
**来源：Trove AI learning.py**

**问题：** 用户准备百度广告面试，但内容库中相关内容分散，没有循序渐进的学习路径。

**方案：**
- API：`POST /api/self-evolution/learning-paths/generate`
- 输入：`{topic: "程序化广告面试", description: "准备百度深圳岗位"}`
- 流程：
  1. 从内容库搜索匹配文章（标题/正文/tags包含topic关键词）
  2. 取 top 50 篇，传给 LLM
  3. LLM 排序文章，生成循序渐进的课程（入门→进阶→实战）
  4. 输出：title, description, ordered_articles（文章ID列表 + 每篇的学习要点）
- 存储：`learning_paths` 表，含 progress 0-100% 跟踪
- 前端：自进化页面新增「学习路径」卡片

**预期效果：** 一句话生成面试备战课程，直接从8万条内容中挑出最相关的并排序。

### 2.2 LLM 内容评分（Content Scoring）
**来源：Cruxwire _score()**

**问题：** 当前推荐主要基于协同过滤和标签，没有内容层面的质量/相关度评分。

**方案：**
- 对新入库内容生成 0-10 相关度评分
- 基于用户自定义的「兴趣描述」（每个分类一段描述，写进prompt）
- 同时生成 1-2 句摘要和分类
- 评分用于推荐排序权重
- 配置：用户可以在设置中编辑兴趣描述

**示例兴趣描述：**
```
广告算法: "程序化广告、DSP、RTB、出价策略、归因模型、CTR预估"
推荐系统: "召回、粗排、精排、重排、多目标优化、探索利用"
大模型: "LLM、RAG、Agent、微调、推理优化、MCP"
```

### 2.3 跨平台内容聚类（Cross-Platform Clustering）
**来源：Cruxwire cluster()**

**问题：** 同一主题在B站/知乎/小红书/V2EX都有内容，推荐时可能重复推送。

**方案：**
- 基于 embedding 余弦相似度聚类
- 锚定在最高分文章上（避免传递性链式合并 A~B, B~C → A~C 的问题）
- 阈值：0.82 余弦相似度
- 多来源报道有 boost：`boost = min(1.5, 0.6 * log2(cluster_size))`
- 推荐时同一聚类只推代表文章，其他折叠在「相关报道」下

---

## 第三阶段：自进化增强（中价值，中复杂度）

### 3.1 知识缺口分析（Knowledge Gap Analysis）
**来源：Agent-SaveMark**

**方案：**
- AI识别"收集了但缺乏深度"的主题
- 判断标准：某主题文章数量多但平均阅读完成率低、收藏少、笔记少
- 输出：缺口主题列表 + 建议深读的文章
- 和现有的「深读候选」功能结合

### 3.2 跨平台洞察（Cross-Platform Insights）
**来源：Agent-SaveMark**

**方案：**
- 发现不同平台内容之间的隐藏联系
- 例如：知乎上的「广告归因」文章和B站上的「归因模型实战」视频互补
- 生成专题洞察报告
- 在专题页面展示「跨平台视角」

### 3.3 按需 TL;DR（On-Demand TL;DR）
**来源：Cruxwire generate_tldr()**

**方案：**
- 收藏文章后后台生成全文要点
- 输出：3-5个要点 + 一句话结论 + 阅读时间估算
- 阅读时间基于 wordCount，200 wpm
- 和现有的知识卡片生成互补（TL;DR是快速浏览，知识卡片是深度学习）

---

## 第四阶段：生态与集成（中价值，高复杂度）

### 4.1 MCP Server
**来源：Agent-SaveMark mcp/tools.py**

**方案：**
- 让 Claude Desktop/Cursor/Codex 等 AI 工具使用你的知识库作为持久记忆
- 10个工具：
  - `save_knowledge`: 保存URL或内容
  - `search_knowledge`: 混合搜索（关键词+语义）
  - `get_knowledge`: 获取单篇详情
  - `update_knowledge`: 更新标题/内容/标签
  - `refresh_knowledge`: 重新处理
  - `delete_knowledge`: 删除
  - `list_collections`: 列出专题
  - `add_to_collection`: 添加到专题
  - `get_entity`: 获取实体综合页面
  - `get_related_entities`: 获取相关实体
- 基于 Personal Access Token 的权限控制

### 4.2 分阶段 Enrichment Pipeline
**来源：Agent-SaveMark enrichment_pipeline.py**

**方案：**
- 6个阶段：chunked → embedded → tagged → summarized → entities_extracted → synthesized
- 依赖关系管理：
  - embedded 依赖 chunked
  - entities_extracted 依赖 chunked
  - synthesized 依赖 entities_extracted
  - tagged/summarized 独立，可并行
- 每个阶段有状态跟踪（pending/running/done/failed/skipped）
- 重试支持（attempts计数）
- 失败不阻塞其他阶段

### 4.3 过期内容检测（Stale Content Detection）
**来源：Agent-SaveMark**

**方案：**
- 标记可能需要更新的内容
- 技术类内容尤其需要（API版本、框架特性可能过时）
- 判断标准：内容年龄 > 1年 + 包含版本号/API/框架名
- 在阅读时提示「这篇内容可能已过时，是否查找更新版本」

---

## 实施优先级矩阵

| 阶段 | 功能 | 价值 | 复杂度 | 预计工时 | 依赖 |
|------|------|------|--------|---------|------|
| 1.1 | 实体规范化 | ⭐⭐⭐ | 中 | 4h | 无 |
| 1.2 | 实体综合 | ⭐⭐⭐ | 中 | 6h | 1.1 |
| 2.1 | 学习路径 | ⭐⭐⭐ | 低 | 3h | 无 |
| 2.2 | LLM内容评分 | ⭐⭐ | 低 | 3h | 无 |
| 2.3 | 跨平台聚类 | ⭐⭐ | 中 | 5h | 2.2 |
| 1.3 | 实体提取增强 | ⭐⭐ | 中 | 4h | 无 |
| 3.1 | 知识缺口分析 | ⭐⭐ | 中 | 4h | 无 |
| 3.2 | 跨平台洞察 | ⭐⭐ | 中 | 5h | 无 |
| 3.3 | 按需TL;DR | ⭐ | 低 | 2h | 无 |
| 4.1 | MCP Server | ⭐⭐ | 高 | 8h | 无 |
| 4.2 | Enrichment Pipeline | ⭐ | 高 | 8h | 无 |
| 4.3 | 过期内容检测 | ⭐ | 低 | 2h | 无 |

**总计：约 54 工时**

---

## 建议实施顺序

**第一批（今天可做）：**
1. 实体规范化（1.1）— 直接提升现有知识图谱质量
2. 学习路径生成（2.1）— 对你的面试备战最有价值

**第二批（本周）：**
3. 实体综合（1.2）— 知识图谱从标签云升级为维基百科
4. LLM内容评分（2.2）— 提升推荐质量

**第三批（下周）：**
5. 跨平台聚类（2.3）
6. 知识缺口分析（3.1）
7. 跨平台洞察（3.2）

**第四批（远期）：**
8. MCP Server（4.1）
9. Enrichment Pipeline（4.2）
10. 其他增强

---

## 与现有系统的衔接

- **知识图谱**：现有 `knowledge_graph` 表存 `graph_json`，规范化在构建时应用，综合存在实体的 `synthesis` 字段
- **推荐系统**：LLM评分作为新特征加入现有推荐权重
- **自进化页面**：新增「学习路径」「知识缺口」卡片
- **对话式推荐**：学习路径可以通过对话触发（"帮我准备广告面试"）
- **专题系统**：跨平台洞察集成到专题页面
