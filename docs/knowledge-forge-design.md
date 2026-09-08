# Knowledge Forge（知识锻造炉）开发设计文档

> 编写日期：2026-09-08
> 最后更新：2026-09-08（v1.11：实体网络规模化回填——生产库 774 实体 / 4,712 共现关系）
> 目标：将 87,000+ 篇文章的阅读库升级为自进化的知识网络，实现正文清理、分层摘要、实体/概念聚合、知识 Wiki 构建、缺口分析和质量审计
> 状态：设计定稿；数据库迁移已完成（2026-09-08，生产库 87,269 篇，迁移前已备份）；阶段一完成（六条管线 + 入库接入 + CLI），阶段二完成（矛盾检测/死链/低质量/自动修复/API 路由），阶段三完成（批量处理/定时任务/知识图谱可视化/自动补充闭环/实体页增强/实体间关联持久化），§5 前端页面全部落地（含时间线全量/处理建议展示/矛盾标记处理），阶段四补全（待人工类型建议生成 + 定时任务完整闭环）；生产库实体网络 774 实体 / 4,712 共现关系；`tests/test_knowledge_forge.py` 28 用例 + `tests/test_api_knowledge_forge.py` 20 用例 + `tests/test_knowledge_forge_pipeline.py` 24 用例通过，ruff/mypy 对新增模块零错误
>
> **注意**：本文档中的文章数量为编写时的统计值，执行前请重新统计。实际 articles 表结构以 `PRAGMA table_info(articles)` 为准。

---

## 1. 背景与目标

### 1.1 现状

OpenBiliClaw 已积累 **87,000+ 篇文章**（截至 2026-09-08 为 87,269 篇），覆盖知乎、小红书、B站、YouTube、V2EX、抖音等多个平台，涵盖推荐系统、广告算法、AI Agent、量化投资、旅游、短剧等多个主题。

**实际 articles 表结构**（以数据库为准；v1.2 起已含 §3.0.5 正文清理 5 字段 + §3.1.3 摘要 6 字段，共 30 列）：
```
id, source_type, source_name, title, url, author, summary, content_text,
published_at, created_at, updated_at, tags, status, body_fetch_attempts,
reading_percent, reading_progress, favorited, ai_summary, content_hash,
-- v1.2 迁移新增：
content_cleaned, content_clean_score, content_clean_log, content_verified, content_verify_result,
summary_detailed, summary_compact, summary_ultra_compact, summary_quality, summary_version, summary_generated_at
```

> **注意**：表中使用 `source_type` + `source_name` 标识来源，没有单独的 `platform` 字段。`source_type` 如 `zhihu`/`xiaohongshu`/`bilibili`/`youtube`/`v2ex`，`source_name` 如 `zhihu_article`/`zhihu_answer`/`xhs_note`。

当前存在的问题：

| 问题 | 现状 | 影响 |
|---|---|---|
| **正文污染** | 抓取的正文经常混入评论、推荐内容、广告等无关内容（已在实际入库中发现） | 数据质量差，影响摘要生成和实体提取 |
| 摘要单一 | 只有一层 `ai_summary`，且仅约 13% 的文章有摘要 | 不同场景无法灵活适配，大部分文章无摘要 |
| 无实体聚合 | 作者、主题、概念分散在文章中，没有聚合页 | 无法按作者/主题浏览，知识分散 |
| 无知识关联 | 文章之间只有标签关联，无语义关联 | 无法发现相似文章、观点矛盾、知识延伸 |
| 无缺口分析 | 不知道哪些主题覆盖不足 | 内容采集无方向，盲目抓取 |
| 无质量审计 | 不知道有多少重复、死链、低质量内容 | 知识库质量不可控，垃圾内容累积 |

### 1.2 目标

构建 **Knowledge Forge（知识锻造炉）** 模块，实现：

0. **正文清理器**（Content Cleaner）：清理抓取正文中的 HTML 标签、评论、推荐内容、广告等无关内容，验证正文质量
1. **分层摘要引擎**：每篇文章生成 detailed/compact/ultra_compact 三层摘要，适配不同场景
2. **实体/概念聚合页**：为高频作者、主题、概念自动构建聚合页
3. **知识 Wiki 构建**：自动建立文章间语义关联，检测观点矛盾，构建知识图谱
4. **知识缺口分析**：基于已有标签和专题，识别覆盖不足的主题，建议补充内容
5. **文章质量审计**：扫描重复、死链、内容污染、缺失摘要、内容过短等问题，支持自动修复

### 1.3 设计原则

- **渐进式**：不改变现有数据结构，通过新增字段和表实现，向后兼容
- **低频异步**：所有重计算（全量审计、缺口分析、知识图谱）异步执行，不影响主流程
- **可配置**：检测阈值、修复策略、批处理大小均可配置
- **可追溯**：所有自动操作记录日志，支持回滚
- **参考开源项目**：借鉴 LLM Wiki Agent、Agent Knowledge Forge、Knowledge Agent 等项目的设计

---

## 2. 整体架构

### 2.1 模块架构

```
Knowledge Forge（知识锻造炉）
│
├── 🧹 0. 正文清理器（Content Cleaner）
│   ├── HTML 标签清理
│   ├── 评论/推荐内容检测与截断
│   ├── 广告/无关内容过滤
│   ├── 正文质量验证（长度、相关性、实质内容）
│   └── 输出：清理后的正文 + 质量评分
│
├── 📝 1. 分层摘要引擎（Summary Engine）
│   ├── 输入：清理后的文章正文
│   ├── 处理：detailed → compact → ultra_compact 逐级压缩
│   └── 输出：三层摘要 + 质量评分
│
├── 🏷️ 2. 实体/概念提取器（Entity & Concept Extractor）
│   ├── 作者提取 → 作者页
│   ├── 主题提取 → 主题/专题页
│   ├── 概念提取（LLM）→ 概念页
│   └── 机构/人物提取 → 实体页
│
├── 🔗 3. 知识 Wiki 构建器（Wiki Builder）
│   ├── 文章间语义关联（相似/引用/延伸）
│   ├── 观点矛盾检测
│   ├── 交叉引用自动建立
│   └── 知识图谱构建
│
├── 📊 4. 知识缺口分析器（Gap Analyst）
│   ├── 主题覆盖度分析
│   ├── 概念覆盖度分析
│   ├── 时间衰减分析
│   ├── 跨平台覆盖差异分析
│   ├── 关联密度/孤岛检测
│   └── 补充建议生成
│
└── 🔍 5. 文章质量审计器（Quality Auditor）
    ├── 重复内容检测
    ├── 死链检测
    ├── 内容污染检测（评论/推荐/广告混入）
    ├── 缺失摘要/标签/作者检测
    ├── 内容过短/低质量检测
    ├── 抓取质量评估（正文完整性、图片下载、OCR完成度）
    ├── 格式错误检测
    ├── 审计报告生成
    └── 自动修复（可选）
```

### 2.2 数据流

```
新文章抓取
  │
  └─→ 正文清理器 → 清理 HTML/评论/推荐/广告 → 验证正文质量
      │
      └─→ 新文章入库（清理后的正文）
          │
          ├─→ 分层摘要引擎 → 写入 articles 表的 3 个摘要字段
          │
          ├─→ 实体/概念提取器 → 写入 entities/concepts/article_entities/article_concepts 表
          │
          ├─→ 知识 Wiki 构建器 → 写入 article_relations 表
          │
          └─→ 质量审计器（增量）→ 写入 audit_issues 表

定时任务（每天/每周）
  │
  ├─→ 知识缺口分析器 → 生成缺口报告
  ├─→ 质量审计器（全量）→ 生成审计报告
  └─→ 知识图谱更新 → 更新实体-概念-文章关系网络
```

### 2.3 目录结构

```
src/openbiliclaw/
└── knowledge_forge/           ← 新增模块
    ├── __init__.py
    ├── config.py               # 模块配置（阈值、批大小等）
    ├── summary_engine.py       # 分层摘要引擎
    ├── entity_extractor.py     # 实体/概念提取器
    ├── wiki_builder.py         # 知识 Wiki 构建器
    ├── gap_analyst.py          # 知识缺口分析器
    ├── quality_auditor.py      # 文章质量审计器
    ├── models.py               # 数据模型（dataclass）
    ├── prompts.py              # LLM 提示词模板
    ├── cli.py                  # CLI 命令入口
    └── utils.py                # 工具函数（simhash、文本清理等）

api/
└── routes/
    └── knowledge_forge.py      # 新增 API 路由

web/
└── src/pages/
    ├── authors/                # 作者页
    ├── topics/                 # 主题页
    ├── concepts/               # 概念页
    ├── knowledge-graph/        # 知识图谱页
    ├── audit/                  # 质量审计页
    └── gap-analysis/           # 缺口分析页
```

---

## 3. 模块详细设计

### 3.0 正文清理器（Content Cleaner）

#### 3.0.1 设计思路

**问题背景**：实际入库过程中发现，从知乎等平台抓取的正文经常混入评论、推荐内容、广告等无关内容。

**实际案例**（2026-09-08 入库的 M3 市场微观结构文章）：
- 正文结束后混入了：
  - "一看就是t0的老哥"（用户评论）
  - "论文关键是能中刊，能不能用就是另一说了"（用户评论）
  - "一、价值链分析..."（完全无关的推荐文章内容）
  - "原以为只有MBB有传说中的case interview..."（推荐文章内容）
- 清理前：1,909 字（含约 250 字污染内容）
- 清理后：1,653 字（纯正文）

**设计原则**：
- **规则优先，LLM 兜底**：先用确定性规则清理 80% 的常见污染，剩余疑难案例用 LLM 抽样检测
- **保守截断**：宁可少清理，也不误删正文内容；不确定时标记为"待人工审核"
- **可追溯**：记录清理前后的差异，支持回滚
- **平台适配**：不同平台（知乎/小红书/B站/YouTube）有不同的污染特征，需要分别适配

#### 3.0.2 清理规则

| 规则类型 | 检测方法 | 处理方式 | 适用平台 |
|---|---|---|---|
| **HTML 标签** | 正则匹配 `<[^>]+>` | 移除标签，保留文本 | 所有平台 |
| **HTML 实体** | 正则匹配 `&[a-z]+;` `&#\d+;` | 解码为对应字符 | 所有平台 |
| **多余空白** | 正则匹配 `\s{3,}` | 压缩为单个换行 | 所有平台 |
| **知乎评论特征** | 行首匹配口语化短语："一看就是"、"原以为"、"我觉得"、"楼上"、"谢邀"等 | 截断该行及之后内容 | 知乎 |
| **知乎推荐内容** | 正文后出现新的文章标题格式（如"一、XXX"、"# XXX#"），且与当前文章主题无关 | 截断该段及之后内容 | 知乎 |
| **小红书广告** | 匹配"点击链接"、"购买"、"优惠券"、"私信"等营销关键词 + 链接 | 移除该段 | 小红书 |
| **B站弹幕/评论** | 匹配时间戳格式（如"[00:01:23]"）+ 短文本 | 移除 | B站 |
| **YouTube 描述区链接** | 匹配视频描述区的社交链接、赞助链接 | 移除 | YouTube |
| **通用广告** | 匹配"扫码关注"、"公众号"、"微信号"、"加微信"等 | 标记为疑似广告，人工审核 | 所有平台 |

#### 3.0.3 验证规则

清理完成后，对正文进行质量验证：

| 验证项 | 规则 | 不通过时处理 |
|---|---|---|
| **最低字数** | 正文 < 200 字 | 标记为 `content_too_short`，进入质量审计 |
| **标题相关性** | 正文与标题的语义相似度 < 0.3（embedding 计算） | 标记为 `title_mismatch`，人工审核 |
| **实质内容检查** | 正文有效句子数 < 3 句 | 标记为 `low_quality`，进入质量审计 |
| **残留污染检查** | 清理后仍包含评论特征词 | 标记为 `residual_contamination`，重新清理或人工审核 |
| **编码检查** | 正文包含乱码字符（如 `�`） | 标记为 `encoding_error`，重新抓取 |

#### 3.0.4 处理流程

```
抓取原始正文（HTML/Markdown/纯文本）
  │
  ├─ 1. HTML 解析与标签清理
  │   ├─ 移除 <script>、<style>、<nav>、<footer> 等非正文标签
  │   ├─ 移除 <a> 标签但保留链接文本（或保留链接，取决于配置）
  │   ├─ 解码 HTML 实体
  │   └─ 输出：纯文本正文
  │
  ├─ 2. 平台特定清理
  │   ├─ 知乎：检测评论特征 → 截断；检测推荐内容 → 截断
  │   ├─ 小红书：检测广告 → 移除；检测话题标签 → 保留或移除（配置）
  │   ├─ B站：检测弹幕/评论 → 移除；检测视频描述区链接 → 移除
  │   └─ YouTube：检测描述区链接 → 移除；检测时间戳 → 保留或移除（配置）
  │
  ├─ 3. 通用清理
  │   ├─ 压缩多余空白
  │   ├─ 移除空行（保留段落分隔）
  │   └─ 统一换行符（\n）
  │
  ├─ 4. 质量验证
  │   ├─ 最低字数检查
  │   ├─ 标题相关性检查（embedding）
  │   ├─ 实质内容检查
  │   ├─ 残留污染检查
  │   └─ 编码检查
  │
  ├─ 5. 结果输出
  │   ├─ 清理后的正文
  │   ├─ 清理质量评分（0-100）
  │   ├─ 清理日志（移除了什么、截断位置）
  │   └─ 验证结果（通过/不通过 + 原因）
  │
  └─ 6. 入库
      ├─ 验证通过 → 直接入库
      ├─ 验证不通过但可自动修复 → 修复后入库 + 标记
      └─ 验证不通过且无法自动修复 → 入库 + 标记为待人工审核
```

#### 3.0.5 数据库变更

在 `articles` 表新增以下字段：

```sql
ALTER TABLE articles ADD COLUMN content_cleaned TEXT;          -- 清理后的正文
ALTER TABLE articles ADD COLUMN content_clean_score REAL;       -- 清理质量评分 0-100
ALTER TABLE articles ADD COLUMN content_clean_log TEXT;         -- 清理日志（JSON）
ALTER TABLE articles ADD COLUMN content_verified INTEGER DEFAULT 0;  -- 是否通过验证 0/1
ALTER TABLE articles ADD COLUMN content_verify_result TEXT;     -- 验证结果（JSON）
```

> **注意**：`content_text` 保留原始抓取内容，`content_cleaned` 存储清理后的正文。后续摘要生成、实体提取等都使用 `content_cleaned`。

#### 3.0.6 配置项

```toml
[knowledge_forge.content_cleaner]
# 启用正文清理
enabled = true

# HTML 清理
remove_script_tags = true
remove_style_tags = true
remove_nav_footer = true
remove_links = false              # false=保留链接文本，true=完全移除链接
decode_html_entities = true

# 平台特定清理
zhihu_remove_comments = true      # 移除知乎评论
zhihu_remove_recommendations = true  # 移除知乎推荐内容
xhs_remove_ads = true             # 移除小红书广告
xhs_remove_hashtags = false       # 保留话题标签
bilibili_remove_danmaku = true    # 移除B站弹幕
youtube_remove_description_links = true  # 移除YouTube描述区链接

# 通用清理
compress_whitespace = true
remove_empty_lines = true

# 质量验证
min_content_length = 200          # 最低字数
min_title_similarity = 0.3        # 标题相关性阈值（embedding）
min_effective_sentences = 3       # 最低有效句子数

# LLM 抽样检测（可选，用于疑难案例）
llm_detection_enabled = false      # 是否启用 LLM 抽样检测
llm_detection_sample_rate = 0.05   # 抽样比例 5%
llm_detection_provider = "zhipu"   # LLM provider
```

#### 3.0.7 与其他模块的关系

- **分层摘要引擎**：输入从 `content_text` 改为 `content_cleaned`，确保摘要基于干净的正文
- **实体/概念提取器**：同样使用 `content_cleaned`，避免从评论/推荐中提取错误实体
- **质量审计器**：增加 `content_contamination` 检测维度，扫描历史文章中的污染内容
- **知识 Wiki 构建器**：文章关联基于 `content_cleaned` 的 embedding，避免污染内容影响相似度计算

---

### 3.1 分层摘要引擎（Summary Engine）

#### 3.1.1 设计思路

借鉴 Agent Knowledge Forge 的多层记忆包设计，每篇文章生成三层摘要：

| 层级 | 字数范围 | 用途 | 内容 |
|---|---|---|---|
| **detailed**（详细版） | 2000-5000字 | 文章详情页、专题生成、深度阅读 | 完整要点、核心观点、关键数据、结构梳理、引用原文 |
| **compact**（精简版） | 500-1000字 | 推荐列表、搜索结果、专题文章列表 | 核心观点 + 关键数据 + 3-5个要点 |
| **ultra_compact**（超精简版） | 50-200字 | 知识图谱节点、快速浏览、相关文章推荐 | 一句话摘要 + 核心标签 |

#### 3.1.2 生成流程

```
清理后的正文（content_cleaned）
  │
  ├─ 1. 生成 detailed 摘要
  │   └─ LLM 输入：全文 + 提示词"生成详细摘要，包含核心观点、关键数据、结构梳理"
  │
  ├─ 2. 从 detailed 压缩生成 compact 摘要
  │   └─ LLM 输入：detailed摘要 + 提示词"压缩为500-1000字，保留核心观点和关键数据"
  │
  ├─ 3. 从 compact 压缩生成 ultra_compact 摘要
  │   └─ LLM 输入：compact摘要 + 提示词"压缩为一句话摘要+核心标签"
  │
  └─ 4. 质量自检（可选）
      └─ LLM 输入：三层摘要 + 提示词"评估摘要是否准确覆盖原文核心，评分0-1"
```

> **注意**：摘要生成使用 `content_cleaned`（清理后的正文），而非 `content_text`（原始抓取内容），避免评论/推荐等污染内容影响摘要质量。

**为什么从 detailed 逐级压缩，而不是分别生成？**
- 保证三层摘要的一致性
- 节省 token（detailed 只需要读一次全文）
- 质量可控（detailed 是基础，基础好了后面都好）

#### 3.1.3 数据库变更

在 `articles` 表新增字段：

```sql
ALTER TABLE articles ADD COLUMN summary_detailed TEXT;      -- 详细版摘要
ALTER TABLE articles ADD COLUMN summary_compact TEXT;       -- 精简版摘要
ALTER TABLE articles ADD COLUMN summary_ultra_compact TEXT; -- 超精简版摘要
ALTER TABLE articles ADD COLUMN summary_quality REAL;       -- 摘要质量评分（0-1）
ALTER TABLE articles ADD COLUMN summary_version INTEGER;    -- 摘要版本号
ALTER TABLE articles ADD COLUMN summary_generated_at TEXT;  -- 摘要生成时间
```

**兼容策略**：现有 `ai_summary` 字段保留，作为 compact 层的兼容字段。新代码优先读取 `summary_compact`，为空时回退到 `ai_summary`。

#### 3.1.4 各场景使用层级

| 场景 | 使用层级 | 原因 |
|---|---|---|
| 文章详情页 | detailed | 用户要深度阅读 |
| 推荐流/首页卡片 | ultra_compact 或 compact | 快速浏览 |
| 搜索结果 | compact | 需要足够信息判断是否点击 |
| 专题文章列表 | compact | 专题内多篇文章并列 |
| 专题总览生成 | detailed | 需要完整信息做综合分析 |
| 知识图谱节点 | ultra_compact | 节点上只能显示简短文字 |
| 相关文章推荐 | ultra_compact | 侧边栏/底部推荐 |
| AI对话/问答 | 根据上下文窗口动态选择 | 上下文够就用 detailed |
| 每日摘要推送 | compact | 邮件/推送不宜太长 |

#### 3.1.5 提示词模板

```python
# detailed 摘要提示词
DETAILED_SUMMARY_PROMPT = """
请为以下文章生成详细摘要，要求：
1. 包含核心观点、关键数据、结构梳理
2. 按文章逻辑组织，分点列出
3. 保留重要的原文引用（用引号标注）
4. 字数控制在 2000-5000 字
5. 不要添加文章中没有的信息

文章标题：{title}
文章作者：{author}
文章正文：
{content}
"""

# compact 摘要提示词
COMPACT_SUMMARY_PROMPT = """
请将以下详细摘要压缩为精简版，要求：
1. 保留核心观点和关键数据
2. 列出 3-5 个要点
3. 字数控制在 500-1000 字
4. 不要添加新信息

详细摘要：
{detailed_summary}
"""

# ultra_compact 摘要提示词
ULTRA_COMPACT_SUMMARY_PROMPT = """
请将以下摘要压缩为超精简版，要求：
1. 一句话概括文章核心内容
2. 列出 3-5 个核心标签
3. 总字数控制在 50-200 字

摘要：
{compact_summary}
"""
```

---

### 3.2 实体/概念提取器（Entity & Concept Extractor）

#### 3.2.1 实体类型

| 类型 | 说明 | 示例 |
|---|---|---|
| **author** | 作者 | 张佳玮、QuantML、Keep Learning |
| **topic** | 主题/专题 | AI Agent 自进化、推荐系统、广告算法 |
| **concept** | 技术概念/方法/框架 | TokenFormer、MoS、Point-in-Time、FactorMoE |
| **organization** | 机构/公司/团队 | WorldQuant、AQR、Two Sigma、普林斯顿 |
| **person** | 人物（非作者） | Emanuel Derman、Angana Jacob |
| **platform** | 平台 | 知乎、小红书、B站、YouTube |

#### 3.2.2 数据库设计

```sql
-- 实体表
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,              -- 实体名称
    type TEXT,                     -- author/topic/concept/organization/person/platform
    description TEXT,              -- 实体简介（自动生成+持续更新）
    article_count INTEGER DEFAULT 0, -- 引用该实体的文章数
    first_seen_at TEXT,
    last_updated_at TEXT,
    metadata TEXT                  -- JSON: 额外元数据（如作者的平台、关注者数等）
);

-- 文章-实体关联
CREATE TABLE article_entities (
    article_id INTEGER,
    entity_id INTEGER,
    relevance REAL,                -- 相关度 0-1
    context TEXT,                  -- 文章中提及该实体的上下文片段
    PRIMARY KEY (article_id, entity_id),
    FOREIGN KEY (article_id) REFERENCES articles(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

-- 实体间关联
CREATE TABLE entity_relations (
    entity_id_a INTEGER,
    entity_id_b INTEGER,
    relation_type TEXT,            -- related/similar/opposite/part_of
    confidence REAL,
    description TEXT,
    PRIMARY KEY (entity_id_a, entity_id_b, relation_type)
);

-- 索引
CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_entities_name ON entities(name);
CREATE INDEX idx_article_entities_article ON article_entities(article_id);
CREATE INDEX idx_article_entities_entity ON article_entities(entity_id);
```

#### 3.2.3 提取流程

```
新文章入库
  │
  ├─ 1. 作者提取（确定性）
  │   ├─ 从 articles.author 字段提取
  │   ├─ 查找/创建 entities 记录（type=author）
  │   └─ 建立 article_entities 关联
  │
  ├─ 2. 主题提取（确定性+LLM）
  │   ├─ 从 articles.tags 字段提取标签
  │   ├─ LLM 辅助：从正文提取核心主题（2-5个）
  │   ├─ 主题标准化（同义词合并，如"粗排"="粗排序"）
  │   ├─ 查找/创建 entities 记录（type=topic）
  │   └─ 建立 article_entities 关联
  │
  ├─ 3. 概念提取（LLM）
  │   ├─ LLM 从正文提取技术概念/方法/框架（3-8个）
  │   ├─ 概念标准化（去重、同义词合并）
  │   ├─ 查找/创建 entities 记录（type=concept）
  │   └─ 建立 article_entities 关联（含上下文片段）
  │
  ├─ 4. 机构/人物提取（LLM，可选）
  │   ├─ LLM 从正文提取重要机构和人物
  │   └─ 查找/创建 entities 记录
  │
  └─ 5. 实体页更新
      ├─ 更新 article_count
      ├─ 更新 last_updated_at
      └─ 定期重新生成 description（综合所有引用文章的摘要）
```

#### 3.2.4 实体页内容结构

以作者页为例：

```
作者：张佳玮
├── 基本信息
│   ├── 简介（自动生成）
│   ├── 平台：知乎
│   └── 关注者数（如有）
├── 文章统计
│   ├── 总文章数：N 篇
│   ├── 总字数：X 字
│   ├── 最早文章：YYYY-MM-DD
│   └── 最新文章：YYYY-MM-DD
├── 主题分布
│   ├── 历史：40%
│   ├── 篮球：30%
│   └── 影视：20%
├── 文章列表（按时间/热度/相关度排序）
│   ├── 关羽水淹七军...（2026-09）
│   ├── 阿森纳2-1切尔西...（2026-09）
│   └── ...
└── 相关作者
    ├── 苏苏（同主题：影视评论）
    └── ...
```

以概念页为例：

```
概念：Point-in-Time（时间点语义）
├── 概念定义（自动生成，持续优化）
├── 分类：数据处理/回测/量化
├── 核心要点
│   ├── 财报重述会导致未来信息泄露
│   ├── 指数成分变化会产生幸存者偏差
│   └── 每次发布修订都要留下切片
├── 引用文章
│   ├── 数据：量化真正的竞争壁垒（QuantML）
│   └── ...
├── 相关概念
│   ├── 幸存者偏差
│   ├── 回测过拟合
│   └── 数据泄漏
└── 演进时间线
    ├── 2026-06：首次出现在库中
    ├── 2026-08：被 3 篇文章引用
    └── 2026-09：被 5 篇文章引用，概念定义更新
```

---

### 3.3 知识 Wiki 构建器（Wiki Builder）

#### 3.3.1 文章间语义关联

| 关联类型 | 说明 | 检测方法 |
|---|---|---|
| **similar** | 内容相似 | embedding 相似度 > 0.85 |
| **references** | 引用/提及 | 文章A中提到文章B的标题/作者/概念 |
| **extends** | 延伸/深入 | 文章B在文章A的基础上展开（LLM判断） |
| **contradiction** | 观点矛盾 | LLM 检测观点冲突 |
| **same_topic** | 同主题 | 共享核心主题标签 |

#### 3.3.2 数据库设计

```sql
-- 文章间关联
CREATE TABLE article_relations (
    article_id_a INTEGER,
    article_id_b INTEGER,
    relation_type TEXT,            -- similar/references/extends/contradiction/same_topic
    confidence REAL,               -- 置信度 0-1
    description TEXT,              -- 关联描述（如矛盾点说明）
    created_at TEXT,
    PRIMARY KEY (article_id_a, article_id_b, relation_type),
    FOREIGN KEY (article_id_a) REFERENCES articles(id),
    FOREIGN KEY (article_id_b) REFERENCES articles(id)
);

-- 索引
CREATE INDEX idx_article_relations_a ON article_relations(article_id_a);
CREATE INDEX idx_article_relations_b ON article_relations(article_id_b);
CREATE INDEX idx_article_relations_type ON article_relations(relation_type);
```

#### 3.3.3 矛盾检测流程

```
新文章入库
  │
  ├─ 1. 查找同主题的已有文章（共享 2+ 核心标签）
  │
  ├─ 2. 对每对候选文章，LLM 检测观点矛盾
  │   ├─ 输入：文章A摘要 + 文章B摘要 + 主题
  │   ├─ 输出：是否矛盾 + 矛盾点描述 + 置信度
  │   └─ 阈值：confidence > 0.7 标记为矛盾
  │
  ├─ 3. 写入 article_relations 表（relation_type=contradiction）
  │
  └─ 4. 生成矛盾报告（定期汇总）
```

#### 3.3.4 知识图谱

知识图谱由三类节点和三类边组成：

**节点**：
- 文章节点（articles）
- 实体节点（entities）
- 概念节点（entities，type=concept）

**边**：
- 文章-实体关联（article_entities）
- 文章-文章关联（article_relations）
- 实体-实体关联（entity_relations）

**可视化方案**：
- 前端使用 ECharts 关系图或 D3.js force-directed graph
- 支持按实体类型筛选、按时间筛选
- 点击节点显示详情，点击边显示关联说明
- 支持导出为图片/JSON

---

### 3.4 知识缺口分析器（Gap Analyst）

#### 3.4.1 分析维度

| 维度 | 分析内容 | 输出 |
|---|---|---|
| **主题覆盖度** | 每个标签/专题的文章数量、时间分布、平台分布 | 覆盖不足的主题 TOP 20 |
| **概念覆盖度** | 每个技术概念的引用文章数、相关概念数 | 只有1-2篇引用的"冷门概念" |
| **时间衰减** | 哪些主题最近3个月/6个月没有新内容 | "过时"主题列表 |
| **跨平台差异** | 某个主题在知乎很多但小红书/B站很少 | 平台覆盖缺口 |
| **关联密度** | 哪些文章/实体/概念是"孤岛"（没有关联） | 孤立节点列表 |
| **深度评估** | 某些主题只有新闻/快讯，没有深度分析 | "浅层覆盖"主题 |

#### 3.4.2 分析流程

```
知识缺口分析（定时执行，每周一次）
  │
  ├─ 1. 主题覆盖分析
  │   ├─ 统计每个标签的文章数
  │   ├─ 按数量排序，找出底部 20% 的主题
  │   ├─ 分析时间分布（是否集中在某个时期）
  │   └─ 输出：覆盖不足主题列表 + 建议补充方向
  │
  ├─ 2. 概念覆盖分析
  │   ├─ 统计每个概念的引用文章数
  │   ├─ 找出只有 1-2 篇引用的概念
  │   └─ 输出：冷门概念列表 + 相关主题建议
  │
  ├─ 3. 时间衰减分析
  │   ├─ 对每个主题，计算最新文章的时间
  │   ├─ 找出超过 3/6/12 个月没有新内容的主题
  │   ├─ 区分"过时主题"和"被遗忘主题"
  │   └─ 输出：需要更新的主题列表
  │
  ├─ 4. 跨平台覆盖分析
  │   ├─ 对每个主题，统计各平台的文章数
  │   ├─ 找出平台分布严重不均的主题
  │   └─ 输出：平台覆盖缺口 + 建议在哪个平台补充
  │
  ├─ 5. 关联密度分析
  │   ├─ 构建文章-实体-概念关系图
  │   ├─ 计算每个节点的度（关联数）
  │   ├─ 找出度为 0 或 1 的"孤岛"节点
  │   └─ 输出：孤立节点列表 + 建议建立的关联
  │
  └─ 6. 综合建议生成
      ├─ 汇总以上所有分析结果
      ├─ 按优先级排序（高价值缺口 > 一般缺口）
      ├─ 生成具体的补充建议
      └─ 输出：知识缺口报告 + 补充建议清单
```

#### 3.4.3 报告输出示例

```markdown
# 📊 知识缺口分析报告（2026-09-08）

## 🔴 高优先级缺口（建议尽快补充）

### 1. 生成式推荐（Generative Recommendation）
- 当前文章数：3 篇
- 平台分布：知乎 3，小红书 0，B站 0
- 最新文章：2026-08-15（已24天无新内容）
- 相关概念：LLM推荐、对话式推荐、个性化生成
- 建议：补充 10-15 篇，重点在小红书和B站

### 2. 多模态推荐（Multimodal Recommendation）
- 当前文章数：2 篇
- 建议：补充 8-10 篇

## 🟡 中优先级缺口
- 短剧商业化（当前 8 篇，建议补充产业链分析）
- 积分运营（当前 3 篇，建议补充案例分析）

## ⚠️ 被遗忘的主题
- 异动归因：最后更新 2026-07-20（已50天）
- 广告算法面试：最后更新 2026-07-15（已55天）

## 🌐 跨平台覆盖缺口
- 推荐系统：知乎 156 篇，小红书 23 篇，B站 12 篇 → 建议补充小红书/B站
- 量化投资：知乎 45 篇，小红书 2 篇 → 建议补充小红书
```

#### 3.4.4 数据库设计

```sql
-- 缺口分析任务表
CREATE TABLE gap_analysis_tasks (
    id INTEGER PRIMARY KEY,
    status TEXT,                   -- pending/running/completed/failed
    started_at TEXT,
    completed_at TEXT,
    report_path TEXT,              -- 报告文件路径
    total_topics INTEGER,
    gaps_found INTEGER,
    created_at TEXT
);

-- 缺口记录表
CREATE TABLE gap_records (
    id INTEGER PRIMARY KEY,
    task_id INTEGER,               -- 关联分析任务
    gap_type TEXT,                 -- topic_coverage/concept_coverage/time_decay/platform_gap/island_node
    entity_id INTEGER,             -- 关联实体（主题/概念）
    severity TEXT,                 -- high/medium/low
    description TEXT,              -- 缺口描述
    current_count INTEGER,         -- 当前文章数
    suggested_count INTEGER,       -- 建议补充数量
    suggestion TEXT,               -- 具体建议
    status TEXT,                   -- open/in_progress/resolved/ignored
    created_at TEXT,
    FOREIGN KEY (task_id) REFERENCES gap_analysis_tasks(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
```

---

### 3.5 文章质量审计器（Quality Auditor）

#### 3.5.1 审计维度

| 维度 | 检测内容 | 严重程度 | 自动修复 |
|---|---|---|---|
| **重复内容** | 相同URL重复入库、内容高度相似（simhash>0.9） | 🔴 高 | ✅ 合并/标记重复 |
| **死链检测** | 文章URL无法访问（404/410/超时） | 🔴 高 | ⚠️ 标记+建议更新 |
| **内容污染** | 正文混入评论、推荐内容、广告等无关内容 | 🔴 高 | ✅ 自动重新清理（调用正文清理器） |
| **缺失摘要** | ai_summary 为空或过短（<100字） | 🟡 中 | ✅ 自动重新生成 |
| **内容过短** | 正文 <200字（可能是抓取失败） | 🟡 中 | ⚠️ 标记+建议重新抓取 |
| **缺失标签** | tags 为空或只有1个标签 | 🟢 低 | ✅ 自动补充标签 |
| **低质量内容** | 广告/垃圾/无意义文本、正文与标题不符 | 🔴 高 | ⚠️ 标记+建议删除 |
| **格式错误** | 正文包含大量HTML标签、乱码、特殊字符 | 🟡 中 | ✅ 自动清理格式 |
| **抓取质量** | 正文不完整、图片未下载、OCR未完成、视频无字幕 | 🟡 中 | ⚠️ 标记+建议重新抓取 |
| **缺失作者** | author 字段为空 | 🟢 低 | ⚠️ 标记+建议补充 |
| **时间异常** | created_at 早于2020年或晚于当前时间 | 🟡 中 | ⚠️ 标记+建议修正 |
| **content_hash 缺失** | 内容哈希为空，无法做去重 | 🟢 低 | ✅ 自动计算补充 |
| **状态异常** | status 不是 active/archived/deleted | 🟡 中 | ⚠️ 标记+建议修正 |

#### 3.5.2 审计流程

```
文章质量审计
  │
  ├─ 阶段一：全量扫描（只读，不修改数据）
  │   ├─ 1. 基础统计扫描
  │   │   ├─ 总文章数、各平台分布、各状态分布
  │   │   ├─ 有摘要/无摘要数量、有标签/无标签数量
  │   │   ├─ 字数分布（<200, 200-500, 500-1000, 1000-5000, >5000）
  │   │   └─ 时间分布（按年/月统计）
  │   │
  │   ├─ 2. 重复内容检测
  │   │   ├─ 按 URL 去重（完全相同URL）
  │   │   ├─ 按 content_hash 去重（内容完全相同）
  │   │   └─ 按 simhash 相似度检测（内容高度相似，>0.9）
  │   │
  │   ├─ 3. 死链检测（分批异步，避免风控）
  │   │   ├─ 对每篇文章的 URL 发 HEAD 请求
  │   │   ├─ 记录状态码（200/404/410/超时/重定向）
  │   │   └─ 对404/410标记为死链，对超时标记为待确认
  │   │
  │   ├─ 4. 内容质量检测
  │   │   ├─ 缺失摘要：ai_summary IS NULL OR length(ai_summary) < 100
  │   │   ├─ 内容过短：length(content_text) < 200
  │   │   ├─ 缺失标签：tags IS NULL OR tags = ''
  │   │   ├─ 缺失作者：author IS NULL OR author = ''
  │   │   ├─ 格式错误：content_text 包含大量HTML标签
  │   │   ├─ 乱码检测：包含大量替换字符（�）或异常Unicode
  │   │   ├─ content_hash 缺失：content_hash IS NULL
  │   │   └─ 内容污染检测：
  │   │       ├─ 评论特征检测：正文包含"一看就是"、"原以为"、"楼上"、"谢邀"等评论特征词
  │   │       ├─ 推荐内容检测：正文后出现新的文章标题格式且与当前主题无关
  │   │       ├─ 广告特征检测：包含"扫码关注"、"公众号"、"加微信"等营销关键词
  │   │       ├─ content_cleaned 缺失：清理后的正文为空（未经过正文清理器处理）
  │   │       └─ LLM 抽样检测：对疑似污染的文章用 LLM 确认（抽样比例 5%）
  │   │
  │   ├─ 5. 抓取质量检测
  │   │   ├─ 正文完整性：正文长度与预期不符（如视频文章只有标题没有字幕）
  │   │   ├─ 图片下载状态：图文文章的图片是否已下载（如有图片字段）
  │   │   ├─ OCR 完成状态：图片类文章是否已完成 OCR 文字识别
  │   │   ├─ 视频字幕状态：视频类文章是否有字幕/文字版
  │   │   └─ body_fetch_attempts 异常：抓取尝试次数过多（>3次仍失败）
  │   │
  │   └─ 6. 低质量内容检测（LLM辅助，抽样）
  │       ├─ 广告/垃圾内容识别
  │       ├─ 正文与标题不符检测
  │       └─ 无意义文本检测
  │
  ├─ 阶段二：问题分类与优先级排序
  │   ├─ 按严重程度排序（🔴高 > 🟡中 > 🟢低）
  │   ├─ 按影响范围排序（影响文章数多的优先）
  │   └─ 生成问题清单（每类问题的文章ID列表）
  │
  ├─ 阶段三：修复建议生成
  │   ├─ 对每类问题给出修复建议
  │   ├─ 对可自动修复的问题，生成修复脚本
  │   └─ 对需人工确认的问题，标记待审核
  │
  ├─ 阶段四：自动修复（可选，需用户确认）
  │   ├─ 重复内容：保留最新/最完整的一篇，标记其他为 duplicate
  │   ├─ 内容污染：调用正文清理器重新清理正文，更新 content_cleaned 字段
  │   ├─ 缺失摘要：调用LLM重新生成三层摘要
  │   ├─ 缺失标签：调用LLM补充标签
  │   ├─ 格式错误：自动清理HTML标签和乱码
  │   ├─ content_hash 缺失：自动计算补充
  │   └─ 死链/低质量/内容过短/抓取质量：标记待人工处理
  │
  └─ 阶段五：审计报告输出
      ├─ 总体质量评分（0-100）
      ├─ 各维度问题统计
      ├─ 问题文章列表（按类别）
      ├─ 修复建议清单
      ├─ 自动修复结果统计
      └─ 历史趋势对比
```

#### 3.5.3 数据库设计

```sql
-- 审计任务表
CREATE TABLE audit_tasks (
    id INTEGER PRIMARY KEY,
    task_type TEXT,               -- full_audit/duplicate_check/dead_link_check/summary_check
    status TEXT,                  -- pending/running/completed/failed
    started_at TEXT,
    completed_at TEXT,
    total_articles INTEGER,
    issues_found INTEGER,
    issues_fixed INTEGER,
    report_path TEXT,
    created_at TEXT
);

-- 审计问题表
CREATE TABLE audit_issues (
    id INTEGER PRIMARY KEY,
    article_id INTEGER,
    issue_type TEXT,              -- duplicate/dead_link/missing_summary/too_short/missing_tags/format_error/low_quality/time_anomaly
    severity TEXT,                -- high/medium/low
    description TEXT,
    details TEXT,                 -- JSON: 详细信息（重复文章ID列表、状态码等）
    status TEXT,                  -- open/confirmed/fixed/ignored
    fix_suggestion TEXT,
    fixed_at TEXT,
    created_at TEXT,
    FOREIGN KEY (article_id) REFERENCES articles(id)
);

-- 文章质量分表
CREATE TABLE article_quality_scores (
    article_id INTEGER PRIMARY KEY,
    overall_score REAL,           -- 总体质量分 0-100
    completeness_score REAL,      -- 完整度（摘要/标签/作者是否齐全）
    content_score REAL,           -- 内容质量（字数/格式/相关性）
    link_score REAL,              -- 链接质量（是否死链）
    uniqueness_score REAL,        -- 唯一性（是否重复）
    last_audited_at TEXT,
    FOREIGN KEY (article_id) REFERENCES articles(id)
);

-- 审计配置表
CREATE TABLE audit_config (
    id INTEGER PRIMARY KEY,
    config_key TEXT UNIQUE,
    config_value TEXT,
    description TEXT
);

-- 初始配置
INSERT INTO audit_config (config_key, config_value, description) VALUES
('min_content_length', '200', '最小内容长度（低于此值标记为过短）'),
('min_summary_length', '100', '最小摘要长度（低于此值标记为缺失）'),
('simhash_threshold', '0.9', 'simhash相似度阈值（高于此值标记为重复）'),
('dead_link_timeout', '10', '死链检测超时时间（秒）'),
('batch_size', '500', '批处理大小'),
('auto_fix_enabled', 'false', '是否启用自动修复'),
('dead_link_concurrency', '5', '死链检测并发数');
```

#### 3.5.4 simhash 实现

```python
import hashlib
import re

def simhash(text: str, hash_bits: int = 64) -> int:
    """
    计算文本的 simhash 值，用于重复内容检测。
    算法：
    1. 分词（简单按空格和标点）
    2. 每个词计算 hash
    3. 按位加权求和
    4. 大于0的位设为1
    """
    # 简单分词
    words = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
    if not words:
        return 0

    # 初始化位向量
    v = [0] * hash_bits

    for word in words:
        # 计算词的 hash
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        # 按位加权
        for i in range(hash_bits):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1

    # 生成最终 hash
    result = 0
    for i in range(hash_bits):
        if v[i] > 0:
            result |= (1 << i)

    return result


def hamming_distance(hash1: int, hash2: int) -> int:
    """计算两个 simhash 的汉明距离"""
    x = hash1 ^ hash2
    count = 0
    while x:
        count += 1
        x &= x - 1
    return count


def similarity(hash1: int, hash2: int, hash_bits: int = 64) -> float:
    """计算两个 simhash 的相似度（0-1）"""
    distance = hamming_distance(hash1, hash2)
    return 1.0 - (distance / hash_bits)
```

---

## 4. API 设计

### 4.1 分层摘要 API

```
GET  /api/articles/{id}/summary?level=detailed|compact|ultra_compact
POST /api/articles/{id}/summary/generate          # 重新生成摘要
POST /api/articles/batch-summary/generate          # 批量生成摘要（异步任务）
GET  /api/summary-tasks/{task_id}                  # 查询批量生成任务状态
```

### 4.2 实体/概念 API

```
GET  /api/entities?type=author|topic|concept&page=1&size=20
GET  /api/entities/{id}                             # 实体详情
GET  /api/entities/{id}/articles                    # 引用该实体的文章列表
GET  /api/entities/{id}/related                     # 相关实体
GET  /api/authors                                    # 作者列表（实体type=author）
GET  /api/topics                                     # 主题列表（实体type=topic）
GET  /api/concepts                                   # 概念列表（实体type=concept）
POST /api/entities/extract                           # 手动触发实体提取
```

### 4.3 知识 Wiki API

```
GET  /api/articles/{id}/related                      # 相关文章
GET  /api/articles/{id}/contradictions              # 观点矛盾的文章
GET  /api/knowledge-graph?entity_type=...&limit=100 # 知识图谱数据
POST /api/relations/detect                           # 手动触发关联检测
```

### 4.4 知识缺口分析 API

```
POST /api/gap-analysis/run                            # 触发缺口分析（异步）
GET  /api/gap-analysis/tasks                          # 分析任务列表
GET  /api/gap-analysis/tasks/{id}                     # 分析任务详情
GET  /api/gap-analysis/tasks/{id}/report              # 分析报告
GET  /api/gap-records?severity=high&status=open       # 缺口记录列表
POST /api/gap-records/{id}/resolve                    # 标记缺口已解决
```

### 4.5 质量审计 API

```
POST /api/audit/run                                    # 触发审计（异步）
GET  /api/audit/tasks                                  # 审计任务列表
GET  /api/audit/tasks/{id}                             # 审计任务详情
GET  /api/audit/tasks/{id}/report                      # 审计报告
GET  /api/audit/issues?type=...&severity=high&status=open # 问题列表
POST /api/audit/issues/{id}/fix                        # 修复单个问题
POST /api/audit/issues/batch-fix                       # 批量修复
GET  /api/articles/{id}/quality-score                  # 单篇文章质量分
```

---

## 5. 前端设计

### 5.1 页面列表

| 页面 | 路由 | 说明 | 优先级 |
|---|---|---|---|
| 作者列表 | `/authors` | 所有作者，按文章数排序 | P0 ✅ |
| 作者详情 | `/authors?id=N` | 作者信息+文章列表+主题分布 | P0 ✅ |
| 主题列表 | `/topics` | 所有主题，按文章数排序 | P0 ✅ |
| 主题详情 | `/topics?id=N` | 主题概述+文章列表+子主题+跨平台分布 | P0 ✅ |
| 概念列表 | `/concepts` | 所有概念，按引用数排序 | P1 ✅ |
| 概念详情 | `/concepts?id=N` | 概念定义+引用文章+相关概念 | P1 ✅ |
| 知识图谱 | `/knowledge-graph` | 实体-概念-文章关系网络可视化 | P2 ✅ |
| 质量审计 | `/audit` | 审计报告+问题列表+修复操作 | P1 ✅ |
| 缺口分析 | `/gap-analysis` | 缺口报告+补充建议 | P1 ✅ |
| 矛盾报告 | `/contradictions` | 观点矛盾的文章对 | P2 ✅ |

### 5.2 作者详情页设计

```
┌─────────────────────────────────────────────────┐
│  张佳玮                                          │
│  知乎作者 | 25 篇文章 | 最早 2026-06 | 最新 2026-09 │
├─────────────────────────────────────────────────┤
│  简介（自动生成）                                  │
│  张佳玮，知名作家、篮球评论员，擅长历史、篮球、   │
│  影视评论，文笔生动，善于用比喻和故事讲清复杂概念。 │
├─────────────────────────────────────────────────┤
│  主题分布                    │  时间分布            │
│  ┌─────────────────────┐    │  ┌──────────────┐  │
│  │ 历史 40% ████████   │    │  │ 2026-09 ████ │  │
│  │ 篮球 30% ██████     │    │  │ 2026-08 ███  │  │
│  │ 影视 20% ████       │    │  │ 2026-07 ██   │  │
│  │ 其他 10% ██         │    │  │ 2026-06 █    │  │
│  └─────────────────────┘    │  └──────────────┘  │
├─────────────────────────────────────────────────┤
│  文章列表 [按时间] [按热度] [按相关度]            │
│  ┌─────────────────────────────────────────────┐ │
│  │ 关羽水淹七军...  2026-09-08  3041字  历史  │ │
│  │ 阿森纳2-1切尔西... 2026-09-07  2393字  篮球 │ │
│  │ ...                                          │ │
│  └─────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────┤
│  相关作者                                          │
│  苏苏（影视评论）  舒心酱（剧评）  ...            │
└─────────────────────────────────────────────────┘
```

### 5.3 主题详情页设计

```
┌─────────────────────────────────────────────────┐
│  AI Agent 自进化                                  │
│  12 篇文章 | 5 个相关概念 | 跨 3 个平台          │
├─────────────────────────────────────────────────┤
│  主题概述（自动生成，持续更新）                     │
│  AI Agent 自进化是指 Agent 系统能够从经验中自动   │
│  学习和改进，无需人工干预。主要包括因子挖掘、环境  │
│  扩展、知识积累等方向...                           │
├─────────────────────────────────────────────────┤
│  核心概念                     │  关键人物/机构      │
│  • AgentLoop                  │  • WorldQuant      │
│  • 环境扩展                   │  • 阿里云          │
│  • 因子挖掘SKILL              │  • 普林斯顿        │
│  • 自进化                     │  • 蚂蚁集团        │
├─────────────────────────────────────────────────┤
│  跨平台分布                                       │
│  知乎 8 篇 | 小红书 3 篇 | B站 1 篇              │
├─────────────────────────────────────────────────┤
│  文章列表 [按相关度] [按时间] [按平台]            │
│  ┌─────────────────────────────────────────────┐ │
│  │ WorldQuant因子挖掘SKILL...  知乎  2026-09  │ │
│  │ 从手工到自进化：Agent环境...  知乎  2026-09 │ │
│  │ ...                                          │ │
│  └─────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────┤
│  知识缺口 ⚠️                                      │
│  • 小红书覆盖不足（仅3篇），建议补充              │
│  • 最近15天无新内容，建议关注最新进展             │
└─────────────────────────────────────────────────┘
```

### 5.4 质量审计页设计

```
┌─────────────────────────────────────────────────┐
│  文章质量审计                                      │
│  总体质量评分：78/100  ↑ +6（较上月）             │
├─────────────────────────────────────────────────┤
│  [运行审计] [查看报告] [自动修复]                  │
├─────────────────────────────────────────────────┤
│  问题概览（按严重程度）                             │
│  🔴 高优先级    🟡 中优先级    🟢 低优先级        │
│  重复 2,341      缺失摘要 76,010  缺失标签 8,788 │
│  死链 1,567      内容过短 3,456   缺失作者 5,678 │
│  低质量 892       格式错误 1,234   hash缺失 3,456│
├─────────────────────────────────────────────────┤
│  问题列表 [全部] [重复] [死链] [缺失摘要] ...     │
│  ┌─────────────────────────────────────────────┐ │
│  │ 🔴 重复  文章A 与 文章B 内容相似度 95%       │ │
│  │    [保留A] [保留B] [合并] [忽略]             │ │
│  │ 🔴 死链  文章C URL返回404                     │ │
│  │    [重新抓取] [标记] [删除] [忽略]            │ │
│  │ 🟡 缺失摘要  文章D 无AI摘要                   │ │
│  │    [生成摘要] [批量生成] [忽略]               │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## 6. CLI 命令设计

```bash
# 分层摘要
openbiliclaw summary generate --article-id 123 --level all
openbiliclaw summary batch-generate --limit 100 --dry-run
openbiliclaw summary status

# 实体/概念
openbiliclaw entities list --type author --page 1 --size 20
openbiliclaw entities show --id 123
openbiliclaw entities extract --article-id 123
openbiliclaw entities batch-extract --limit 100

# 知识 Wiki
openbiliclaw wiki relations detect --article-id 123
openbiliclaw wiki contradictions detect --limit 50
openbiliclaw wiki graph export --output graph.json

# 知识缺口分析
openbiliclaw gap-analysis run --output reports/gap-2026-09-08.md
openbiliclaw gap-analysis list
openbiliclaw gap-analysis show --task-id 1

# 质量审计
openbiliclaw audit full --output reports/audit-2026-09-08.md
openbiliclaw audit duplicates
openbiliclaw audit dead-links --concurrency 5
openbiliclaw audit missing-summaries
openbiliclaw audit short-content
openbiliclaw audit format-errors
openbiliclaw audit fix --type missing-summaries --batch-size 100 --dry-run
openbiliclaw audit fix --type format-errors --apply
openbiliclaw audit report --task-id 1
openbiliclaw audit history --limit 10
```

---

## 7. 配置项设计

在 `config.toml` 中新增 `[knowledge_forge]` 配置段：

```toml
[knowledge_forge]
# ============================================
# 正文清理器（Content Cleaner）
# ============================================
[knowledge_forge.content_cleaner]
enabled = true
# HTML 清理
remove_script_tags = true
remove_style_tags = true
remove_nav_footer = true
remove_links = false              # false=保留链接文本，true=完全移除链接
decode_html_entities = true
# 平台特定清理
zhihu_remove_comments = true      # 移除知乎评论
zhihu_remove_recommendations = true  # 移除知乎推荐内容
xhs_remove_ads = true             # 移除小红书广告
xhs_remove_hashtags = false       # 保留话题标签
bilibili_remove_danmaku = true    # 移除B站弹幕
youtube_remove_description_links = true  # 移除YouTube描述区链接
# 通用清理
compress_whitespace = true
remove_empty_lines = true
# 质量验证
min_content_length = 200          # 最低字数
min_title_similarity = 0.3        # 标题相关性阈值（embedding）
min_effective_sentences = 3       # 最低有效句子数
# LLM 抽样检测（可选，用于疑难案例）
llm_detection_enabled = false      # 是否启用 LLM 抽样检测
llm_detection_sample_rate = 0.05   # 抽样比例 5%

# ============================================
# 分层摘要
# ============================================
[knowledge_forge.summary]
detailed_max_length = 5000
compact_max_length = 1000
ultra_compact_max_length = 200
quality_check = true
batch_size = 50
# 使用清理后的正文生成摘要
use_cleaned_content = true

# ============================================
# 实体/概念提取
# ============================================
[knowledge_forge.entity]
extraction_enabled = true
concept_extraction_enabled = true
concept_max_per_article = 8
synonym_merge = true  # 同义词合并

# ============================================
# 知识 Wiki
# ============================================
[knowledge_forge.wiki]
relation_detection_enabled = true
similarity_threshold = 0.85
contradiction_detection_enabled = true
contradiction_confidence_threshold = 0.7

# ============================================
# 知识缺口分析
# ============================================
[knowledge_forge.gap_analysis]
schedule = "weekly"  # daily/weekly/monthly
high_priority_threshold = 5    # 文章数低于此值标记为高优先级缺口
time_decay_days = 90           # 超过此天数无新内容标记为时间衰减

# ============================================
# 质量审计
# ============================================
[knowledge_forge.audit]
schedule = "weekly"
min_content_length = 200
min_summary_length = 100
simhash_threshold = 0.9
dead_link_timeout = 10
dead_link_concurrency = 5
auto_fix_enabled = false
batch_size = 500
# 内容污染检测
contamination_detection_enabled = true
contamination_llm_sample_rate = 0.05  # LLM 抽样检测比例
# 抓取质量检测
fetch_quality_detection_enabled = true

# ============================================
# LLM 多 Provider 配置（使用现有的多 provider 系统）
# ============================================
# 项目已配置 4 个 LLM provider：
#   - openai (商汤日日新 SenseNova)：默认 provider，sensenova-6.8-flash-lite
#   - zhipu (智谱 GLM)：glm-4-flash（永久免费）
#   - modelscope (阿里魔搭)：Qwen/Qwen3.5-35B-A3B
#   - siliconflow (硅基流动)：Qwen/Qwen2.5-7B-Instruct（仅用9B以下免费模型）
#
# 每个模块可配置主 provider + fallback provider，主 provider 失败时自动降级
#
# 重要约束：
#   - 硅基流动只用 9B 以下免费模型，不用付费模型
#   - 优先使用日日新（商汤 SenseNova）作为默认 provider
#   - 智谱 glm-4-flash 永久免费，适合作为兜底

[knowledge_forge.llm]
# 摘要生成：优先日日新（质量好），降级智谱（免费稳定）
summary_provider = "openai"
summary_model = "sensenova-6.8-flash-lite"
summary_fallback_provider = "zhipu"
summary_fallback_model = "glm-4-flash"

# 正文清理 LLM 检测：用智谱（免费），抽样检测
content_clean_provider = "zhipu"
content_clean_model = "glm-4-flash"

# 实体/概念提取：用智谱（免费稳定），降级硅基流动
entity_provider = "zhipu"
entity_model = "glm-4-flash"
entity_fallback_provider = "siliconflow"
entity_fallback_model = "Qwen/Qwen2.5-7B-Instruct"

concept_provider = "zhipu"
concept_model = "glm-4-flash"
concept_fallback_provider = "siliconflow"
concept_fallback_model = "Qwen/Qwen2.5-7B-Instruct"

# 观点矛盾检测：用 ModelScope（35B大模型，推理能力强），降级日日新
contradiction_provider = "modelscope"
contradiction_model = "Qwen/Qwen3.5-35B-A3B"
contradiction_fallback_provider = "openai"
contradiction_fallback_model = "sensenova-6.8-flash-lite"

# 质量审计 LLM 检测：用智谱（免费），抽样检测
audit_provider = "zhipu"
audit_model = "glm-4-flash"

# 缺口分析建议生成：用日日新（质量好）
gap_analysis_provider = "openai"
gap_analysis_model = "sensenova-6.8-flash-lite"

# ============================================
# Embedding 服务配置
# ============================================
# 项目已配置 embedding 服务：
#   - 主：硅基流动 BAAI/bge-m3（云端，不占本地资源）
#   - 降级：本地 Ollama bge-m3（1.2G，作为 fallback）
#
# 用于：文章间语义关联、标题相关性检查、实体相似度计算

[knowledge_forge.embedding]
provider = "siliconflow"
model = "BAAI/bge-m3"
fallback_provider = "ollama"
fallback_model = "bge-m3"
# 批量处理
batch_size = 32
# 缓存
cache_enabled = true
cache_ttl_days = 30
```

### 7.1 Provider 选择策略说明

| 模块 | 主 Provider | 原因 | Fallback | 原因 |
|---|---|---|---|---|
| 摘要生成 | 日日新 | 质量好，作为默认 | 智谱 glm-4-flash | 永久免费，稳定 |
| 实体/概念提取 | 智谱 glm-4-flash | 免费稳定，适合结构化提取 | 硅基流动 Qwen2.5-7B | 免费，7B模型够用 |
| 观点矛盾检测 | ModelScope 35B | 大模型推理能力强，适合复杂判断 | 日日新 | 质量好 |
| 质量审计 LLM 检测 | 智谱 glm-4-flash | 免费，抽样检测成本低 | - | - |
| 缺口分析建议 | 日日新 | 质量好，生成建议需要创造力 | 智谱 | 免费兜底 |
| Embedding | 硅基流动 bge-m3 | 云端，不占本地资源 | 本地 Ollama bge-m3 | 离线可用 |

### 7.2 降级策略

1. **自动降级**：主 provider 调用失败（超时/限流/错误）时，自动切换到 fallback provider
2. **降级记录**：每次降级都记录日志，包括失败原因、切换时间、fallback 调用结果
3. **熔断机制**：主 provider 连续失败 N 次（默认 5 次）后，临时熔断 5 分钟，期间直接用 fallback
4. **恢复检测**：熔断期间定期探测主 provider，恢复后自动切回
5. **多级降级**：如果 fallback 也失败，记录错误并标记任务为失败，进入重试队列

### 7.3 成本控制

- **免费优先**：实体提取、质量审计等高频任务用免费 provider（智谱 glm-4-flash）
- **抽样检测**：LLM 辅助检测只抽样 5%，不全量检测
- **批量处理**：摘要生成等批量任务用日日新，利用其免费配额
- **缓存机制**：embedding 结果缓存 30 天，避免重复计算
- **硅基流动约束**：只用 9B 以下免费模型，不调用付费模型

---

## 8. 实现计划与优先级

### 8.1 阶段一：基础能力（P0）— 预计 3-4 天

> **注意**：正文清理器是其他所有模块的基础，必须最先实现。摘要生成、实体提取、语义关联等都依赖清理后的正文。

| # | 任务 | 内容 | 依赖 |
|---|---|---|---|
| 1.0 | **正文清理器** | 实现 content_cleaner.py：HTML标签清理、平台特定清理（知乎评论/推荐、小红书广告等）、通用清理、质量验证（字数/相关性/实质内容）；修改入库流程调用；articles 表新增 content_cleaned/content_clean_score/content_clean_log/content_verified/content_verify_result 字段 | 无 |
| 1.1 | 数据库迁移 | articles 表新增 6 个摘要字段 + 5 个正文清理字段；创建 entities/article_entities/entity_relations 表；创建 audit_tasks/audit_issues/article_quality_scores/audit_config 表；创建 gap_analysis_tasks/gap_records 表 | 无 |
| 1.2 | 分层摘要引擎 | 实现 summary_engine.py：detailed→compact→ultra_compact 逐级压缩；使用 content_cleaned 作为输入；修改入库流程调用；兼容旧 ai_summary 字段 | 1.0, 1.1 |
| 1.3 | 实体提取器（基础） | 实现 entity_extractor.py：作者提取（确定性）+ 主题提取（标签+LLM）；使用 content_cleaned 作为输入；作者页/主题页 API；前端作者列表/详情页、主题列表/详情页 | 1.0, 1.1 |
| 1.4 | 质量审计器（基础扫描） | 实现 quality_auditor.py：基础统计+重复检测+缺失检测+格式错误检测+**内容污染检测**+**抓取质量检测**；审计报告生成；CLI 命令；前端审计页 | 1.0, 1.1 |

### 8.2 阶段二：知识网络（P1）— 预计 3-5 天

| # | 任务 | 内容 | 依赖 |
|---|---|---|---|
| 2.1 | 概念提取器 | LLM 提取技术概念；概念标准化（同义词合并）；概念页 API+前端 | 1.3 |
| 2.2 | 文章间语义关联 | embedding 相似度计算（使用硅基流动 bge-m3，本地 fallback）；基于 content_cleaned 计算；article_relations 表；相关文章推荐 API+前端 | 1.0, 1.1 |
| 2.3 | 观点矛盾检测 | LLM 检测同主题文章观点冲突；矛盾报告 API+前端 | 2.2 |
| 2.4 | 知识缺口分析器 | 6 个维度分析；缺口报告生成；CLI 命令；前端缺口分析页 | 1.3, 2.1 |
| 2.5 | 质量审计器（进阶） | 死链检测（异步分批，考虑平台差异）；低质量内容检测（LLM抽样）；内容污染自动修复（调用正文清理器）；自动修复脚本 | 1.4 |

### 8.3 阶段三：自进化闭环（P2）— 预计 5-7 天

| # | 任务 | 内容 | 依赖 |
|---|---|---|---|
| 3.1 | 知识图谱可视化 | ECharts/D3 关系图；按类型/时间筛选；节点/边详情 | 2.2 |
| 3.2 | 实体页增强 | 实体间关联；实体时间线；实体描述自动更新 | 2.1 |
| 3.3 | 自动补充闭环 | 缺口分析→自动搜索→自动入库（含正文清理）→更新知识库 | 2.4 |
| 3.4 | 定时任务集成 | 每周自动运行缺口分析+质量审计；结果通知 | 2.4, 2.5 |
| 3.5 | 历史文章批量处理 | 分批补全正文清理/摘要/标签/实体提取/质量分；优先处理高价值文章 | 1.0, 1.2, 1.3, 1.4 |

### 8.4 阶段四：优化与完善（P3）— 持续迭代

| # | 任务 | 内容 |
|---|---|---|
| 4.1 | 摘要质量优化 | 不同类型文章使用不同提示词；摘要质量评分反馈优化 |
| 4.2 | 实体/概念标准化 | 更智能的同义词合并；实体消歧；人工审核界面 |
| 4.3 | 审计规则可配置 | 前端可配置检测阈值和规则 |
| 4.4 | 性能优化 | 增量更新；缓存机制；批量处理优化 |
| 4.5 | 导出功能 | 知识图谱导出为图片/JSON；审计报告导出为 PDF |
| 4.6 | 正文清理器优化 | 更多平台适配；更智能的评论/推荐识别；LLM 辅助清理疑难案例 |

---

## 9. 风险与注意事项

### 9.1 技术风险

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| LLM 调用成本高 | 87,000+ 篇文章全部重新生成摘要成本高 | 分批处理，优先处理高价值文章；使用免费/低价 model；缓存结果；智谱 glm-4-flash 永久免费作为兜底 |
| LLM 提取质量不稳定 | 实体/概念提取可能不准确 | 人工审核界面；置信度阈值；多次提取取交集 |
| simhash 对中文效果一般 | 重复内容检测可能漏检 | 结合 content_hash 精确匹配；使用 jieba 中文分词；调整分词策略；停用词过滤 |
| 死链检测触发风控 | 批量请求可能被目标网站封禁 | 低频异步（每天500篇）；随机延迟；使用代理；不同平台采用不同策略（知乎/小红书更谨慎） |
| 数据库性能 | 87,000+ 篇文章的关联查询可能慢 | 合理建索引；分页查询；缓存热点数据 |
| **正文清理误删** | 清理规则可能误删正文内容，导致数据丢失 | 保守截断策略；保留原始 content_text；清理日志记录；支持回滚；不确定时标记为待人工审核 |
| **正文清理漏检** | 评论/推荐内容可能未被识别，残留污染内容 | 规则+LLM 抽样双重检测；质量审计器定期扫描；持续优化清理规则 |
| **平台页面结构变化** | 知乎/小红书等平台改版后，清理规则可能失效 | 平台适配层抽象；定期检查清理效果；失败时标记并告警；LLM 辅助清理疑难案例 |
| **多 provider 降级混乱** | 主 provider 失败时降级逻辑可能出错 | 统一降级框架；熔断机制；降级日志记录；定期测试降级链路 |
| **embedding 服务不可用** | 硅基流动 embedding 服务故障时，语义关联功能失效 | 本地 Ollama bge-m3 作为 fallback；embedding 结果缓存；降级为标签匹配 |

### 9.2 数据风险

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| 自动修复误操作 | 可能误删/误改文章 | 所有自动操作记录日志；支持回滚；默认 dry-run 模式 |
| 实体合并错误 | 可能把不同实体合并 | 合并前人工确认；保留合并历史；支持撤销 |
| 摘要生成偏差 | LLM 可能添加原文没有的信息 | 提示词强调"不要添加原文没有的信息"；质量自检 |

### 9.3 注意事项

1. **向后兼容**：所有新增字段和表都不影响现有功能，旧代码继续正常运行
2. **渐进式上线**：先上线基础功能（分层摘要+作者/主题页），再逐步上线高级功能
3. **可关闭**：所有新功能都可以通过配置项关闭，不影响主流程
4. **监控**：记录 LLM 调用次数、成本、成功率；定期检查自动修复结果
5. **用户确认**：涉及删除/合并等破坏性操作时，必须用户确认

---

## 10. 参考项目

本设计参考了以下开源项目的思路：

| 项目 | GitHub | 参考点 |
|---|---|---|
| **LLM Wiki Agent** | `SamurAIGPT/llm-wiki-agent` | 实体/概念页自动构建、交叉引用、矛盾标记、知识图谱、Lint报告 |
| **Agent Knowledge Forge** | `redflyingfish/Agent-Knowledge-Forge` | 多层记忆包（detailed/compact/ultra_compact）、知识卡片、评估循环、新兴主题挖掘 |
| **Knowledge Agent** | `Calandrel/knowledge_agent` | Analyst（知识缺口分析）、Auditor（数据质量审计）、Fixer（自动修复）、多Agent架构 |
| **Living Wiki** | `bronsonelliott/living-wiki` | 自进化知识库、Obsidian集成 |
| **Karpathy LLM Wiki** | Gist | 让 LLM 像程序员写代码一样持续维护 Markdown 知识库 |

---

## 11. 验收标准

### 阶段一验收

- [x] articles 表新增 6 个摘要字段，迁移脚本可执行（v0.3.208 完成，`migrations/001_knowledge_forge.py`）
- [x] 新文章入库时自动生成三层摘要（v0.3.210 摘要引擎 + 入库清理钩子；摘要生成由 CLI/批处理触发）
- [x] 旧文章的 ai_summary 字段兼容读取（read 兼容保留，旧字段未删）
- [x] entities/article_entities 表创建完成（v0.3.208 迁移，实体提取已实现）
- [x] 作者页/主题页 API 可正常返回数据（v0.3.210，`/api/entities?type=author|topic|concept`）
- [ ] 前端作者列表/详情页、主题列表/详情页可正常访问（前端未实现，见 §5）
- [x] 质量审计基础扫描可执行，生成审计报告（v0.3.209，全量 87,281 篇 7.5s）
- [x] CLI 命令 `openbiliclaw audit full` 可执行（实现为 `knowledge-forge audit`）

### 阶段二验收

- [x] 概念提取器可从文章中提取技术概念（v0.3.209，EntityExtractor 概念 LLM 提取）
- [x] 文章间语义关联可计算，相关文章推荐正常（v0.3.209 WikiBuilder + v0.3.210 `/api/articles/{id}/related`）
- [x] 观点矛盾检测可识别同主题文章的观点冲突（v0.3.210，ContradictionDetector + CLI + 矛盾报告）
- [x] 知识缺口分析可生成完整报告（v0.3.209，GapAnalyst 四维度 + `/api/gap-records`）
- [x] 死链检测可异步执行，不阻塞主流程（v0.3.210，DeadLinkChecker 分批异步 + CLI）
- [x] 低质量内容检测（LLM 抽样）可识别广告/垃圾/标题不符内容（v0.3.211，LowQualityDetector + CLI，真实 LLM 验证通过）
- [x] 内容污染自动修复可调用正文清理器重新清理（v0.3.211，IssueFixer `content_contamination` → ContentCleaner 重清）
- [x] 自动修复脚本可处理缺失摘要/格式错误等问题（v0.3.211，IssueFixer 覆盖 污染/哈希/标签/摘要/重复，`auto_fix_enabled=false` 默认需显式开启）

### 阶段三验收

- [x] 知识图谱可视化：ECharts 关系图、按类型筛选、节点/边详情（v0.3.212，`/knowledge-graph` 独立页；类型筛选 + 节点详情 + 关联文章列表）
- [x] 实体页增强：实体间关联、实体时间线、实体描述自动更新（v0.3.213 时间线/描述 + v0.3.214 `entity_relation_builder` 共现持久化至 `entity_relations`，API 优先读持久化、实时共现回退；生产库写入 465 对）
- [x] 自动补充闭环：缺口分析→自动搜索→自动入库→更新知识库（v0.3.212，`GapFiller`：高危主题缺口 → B站搜索 → url_processors 提取 → upsert_article 入库 → 缺口 resolved；真实闭环验证通过，缺口「科学」已补齐）
- [x] 定时任务：每周自动运行缺口分析+质量审计，结果通知（v0.3.211，`Scheduler.run_scheduled` + `schedule-show` crontab 指南；通知为报告落库 + 日志）
- [x] 历史文章批量处理：分批补全正文清理/摘要/标签/实体提取/质量分，高价值优先（v0.3.211，`BatchProcessor` + CLI `backfill`，进度落 `audit_tasks` 支持断点续跑）

### 阶段三验收（简版）

- [x] 知识图谱可视化页面可正常展示实体-概念-文章关系（v0.3.212）
- [x] 自动补充闭环可运行（缺口分析→搜索→入库，v0.3.212 真实闭环验证）
- [x] 定时任务可每周自动运行分析和审计（v0.3.211）
- [x] 历史文章批量处理可分批执行，不影响系统性能（v0.3.211）

---

## 附录 A：数据库迁移脚本（已落地）

> **已实现**：迁移逻辑已落地为可执行脚本 `migrations/001_knowledge_forge.py`（2026-09-08 执行，生产库 87,269 篇迁移完成并验证）。
> 迁移实现与 `src/openbiliclaw/storage/database.py::Database._ensure_knowledge_forge_tables()` 为同一份逻辑（`Database.initialize()` 每次启动幂等补齐，独立脚本通过它触发），避免两份 SQL 漂移。
> 迁移前备份：`data/backups/openbiliclaw_pre_knowledge_forge_20260908_154717.db`。
> 执行命令：`.venv/bin/python migrations/001_knowledge_forge.py`

```python
# migrations/001_knowledge_forge.py
"""
Knowledge Forge 模块数据库迁移
执行：.venv/bin/python migrations/001_knowledge_forge.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/openbiliclaw.db")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. articles 表新增正文清理器字段（必须最先添加，其他模块依赖 content_cleaned）
    cursor.execute("ALTER TABLE articles ADD COLUMN content_cleaned TEXT")          -- 清理后的正文
    cursor.execute("ALTER TABLE articles ADD COLUMN content_clean_score REAL")       -- 清理质量评分 0-100
    cursor.execute("ALTER TABLE articles ADD COLUMN content_clean_log TEXT")         -- 清理日志（JSON）
    cursor.execute("ALTER TABLE articles ADD COLUMN content_verified INTEGER DEFAULT 0")  -- 是否通过验证 0/1
    cursor.execute("ALTER TABLE articles ADD COLUMN content_verify_result TEXT")     -- 验证结果（JSON）

    # 2. articles 表新增摘要字段
    cursor.execute("ALTER TABLE articles ADD COLUMN summary_detailed TEXT")
    cursor.execute("ALTER TABLE articles ADD COLUMN summary_compact TEXT")
    cursor.execute("ALTER TABLE articles ADD COLUMN summary_ultra_compact TEXT")
    cursor.execute("ALTER TABLE articles ADD COLUMN summary_quality REAL")
    cursor.execute("ALTER TABLE articles ADD COLUMN summary_version INTEGER DEFAULT 0")
    cursor.execute("ALTER TABLE articles ADD COLUMN summary_generated_at TEXT")

    # 3. 实体表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            type TEXT,
            description TEXT,
            article_count INTEGER DEFAULT 0,
            first_seen_at TEXT,
            last_updated_at TEXT,
            metadata TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)")

    # 3. 文章-实体关联
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS article_entities (
            article_id INTEGER,
            entity_id INTEGER,
            relevance REAL,
            context TEXT,
            PRIMARY KEY (article_id, entity_id),
            FOREIGN KEY (article_id) REFERENCES articles(id),
            FOREIGN KEY (entity_id) REFERENCES entities(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_article_entities_article ON article_entities(article_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_article_entities_entity ON article_entities(entity_id)")

    # 4. 实体间关联
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_relations (
            entity_id_a INTEGER,
            entity_id_b INTEGER,
            relation_type TEXT,
            confidence REAL,
            description TEXT,
            PRIMARY KEY (entity_id_a, entity_id_b, relation_type)
        )
    """)

    # 5. 文章间关联
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS article_relations (
            article_id_a INTEGER,
            article_id_b INTEGER,
            relation_type TEXT,
            confidence REAL,
            description TEXT,
            created_at TEXT,
            PRIMARY KEY (article_id_a, article_id_b, relation_type),
            FOREIGN KEY (article_id_a) REFERENCES articles(id),
            FOREIGN KEY (article_id_b) REFERENCES articles(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_article_relations_a ON article_relations(article_id_a)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_article_relations_b ON article_relations(article_id_b)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_article_relations_type ON article_relations(relation_type)")

    # 6. 审计任务表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_tasks (
            id INTEGER PRIMARY KEY,
            task_type TEXT,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            total_articles INTEGER,
            issues_found INTEGER,
            issues_fixed INTEGER,
            report_path TEXT,
            created_at TEXT
        )
    """)

    # 7. 审计问题表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_issues (
            id INTEGER PRIMARY KEY,
            article_id INTEGER,
            issue_type TEXT,
            severity TEXT,
            description TEXT,
            details TEXT,
            status TEXT DEFAULT 'open',
            fix_suggestion TEXT,
            fixed_at TEXT,
            created_at TEXT,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_issues_article ON audit_issues(article_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_issues_type ON audit_issues(issue_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_issues_status ON audit_issues(status)")

    # 8. 文章质量分表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS article_quality_scores (
            article_id INTEGER PRIMARY KEY,
            overall_score REAL,
            completeness_score REAL,
            content_score REAL,
            link_score REAL,
            uniqueness_score REAL,
            last_audited_at TEXT,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
    """)

    # 9. 审计配置表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_config (
            id INTEGER PRIMARY KEY,
            config_key TEXT UNIQUE,
            config_value TEXT,
            description TEXT
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO audit_config (config_key, config_value, description) VALUES
        ('min_content_length', '200', '最小内容长度'),
        ('min_summary_length', '100', '最小摘要长度'),
        ('simhash_threshold', '0.9', 'simhash相似度阈值'),
        ('dead_link_timeout', '10', '死链检测超时时间（秒）'),
        ('batch_size', '500', '批处理大小'),
        ('auto_fix_enabled', 'false', '是否启用自动修复'),
        ('dead_link_concurrency', '5', '死链检测并发数')
    """)

    # 10. 缺口分析任务表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gap_analysis_tasks (
            id INTEGER PRIMARY KEY,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            report_path TEXT,
            total_topics INTEGER,
            gaps_found INTEGER,
            created_at TEXT
        )
    """)

    # 11. 缺口记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gap_records (
            id INTEGER PRIMARY KEY,
            task_id INTEGER,
            gap_type TEXT,
            entity_id INTEGER,
            severity TEXT,
            description TEXT,
            current_count INTEGER,
            suggested_count INTEGER,
            suggestion TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            FOREIGN KEY (task_id) REFERENCES gap_analysis_tasks(id),
            FOREIGN KEY (entity_id) REFERENCES entities(id)
        )
    """)

    conn.commit()
    print("✅ Knowledge Forge 数据库迁移完成")
    conn.close()

if __name__ == "__main__":
    migrate()
```

---

## 附录 B：提示词模板汇总

```python
# 详见 src/openbiliclaw/knowledge_forge/prompts.py

# 1. 详细摘要提示词
DETAILED_SUMMARY_PROMPT = "..."

# 2. 精简摘要提示词
COMPACT_SUMMARY_PROMPT = "..."

# 3. 超精简摘要提示词
ULTRA_COMPACT_SUMMARY_PROMPT = "..."

# 4. 主题提取提示词
TOPIC_EXTRACTION_PROMPT = "..."

# 5. 概念提取提示词
CONCEPT_EXTRACTION_PROMPT = "..."

# 6. 实体描述生成提示词
ENTITY_DESCRIPTION_PROMPT = "..."

# 7. 矛盾检测提示词
CONTRADICTION_DETECTION_PROMPT = "..."

# 8. 低质量内容检测提示词
LOW_QUALITY_DETECTION_PROMPT = "..."

# 9. 标签补充提示词
TAG_SUPPLEMENT_PROMPT = "..."

# 10. 缺口建议生成提示词
GAP_SUGGESTION_PROMPT = "..."
```

---

**文档版本**：v1.0  
**编写日期**：2026-09-08  
**状态**：待评审，待执行  
**下一步**：评审通过后，按阶段一计划开始实现
