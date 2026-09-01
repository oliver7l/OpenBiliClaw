# 阅读库功能设计 · 基于大模型的聚合信息流阅读

## 需求背景

当前项目 `OpenBiliClaw` 已经实现了多平台（B站、知乎、小红书）内容聚合和基于兴趣的推荐系统。用户希望将项目演进为**阅读库**，核心目标是：

- 拓展更多阅读类内容源（公众号、RSS、写作平台）
- 依托大模型提供更精准的智能推荐
- 提供标签分类和阅读状态管理，便于组织和追踪阅读计划
- 卡片展示摘要，点击跳转原文阅读

## 设计概要

采用**渐进式增强**方案：在现有架构基础上，分批次增量添加功能，不重构核心推荐系统，风险可控。

**核心模块**：

1. 新内容源接入（微信公众号、RSS/Atom、少数派/虎嗅）
2. 阅读管理功能（标签系统、阅读状态）
3. LLM 推荐增强（阅读画像深化、内容质量评分、推荐理由生成）

## 模块一：新内容源接入

### 架构设计

沿用项目现有的 `sources/` 目录数据源接入模式，每个新源作为独立 adapter。

### 内容源列表

| 内容源 | 接入方式 | 数据存储 | 说明 |
|--------|---------|----------|------|
| **微信公众号** | 通过工具抓取文章链接和正文 | `articles` 表 | 需要用户配置订阅列表 |
| **RSS/Atom 订阅** | `feedparser` 解析标准 Feed | `articles` 表 | 用户自添加订阅链接 |
| **少数派** | 抓取首页/热门榜单 | `articles` 表 | 提取标题摘要 |
| **虎嗅** | 抓取首页/精选文章 | `articles` 表 | 提取标题摘要 |

### 数据流

1. 每个源独立**定时抓取**（可配置抓取间隔）
2. 抓取后统一格式化为 `RecommendationItem` 结构
3. 存入推荐数据库，进入推荐池参与排序

### 数据结构

```sql
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type TEXT NOT NULL,         -- 'wechat' | 'rss' | 'sspai' | 'huxiu'
  source_name TEXT,                 -- RSS 源名称/公众号名称
  title TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  author TEXT,
  summary TEXT,                    -- LLM 生成的摘要
  published_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 配置管理

- 在 `config.py` 增加配置项：`wechat_subscriptions`, `rss_subscriptions`
- 格式：列表形式，用户可手动编辑添加
- 示例：
```python
# 微信公众号订阅列表（通过 openid 或昵称）
WECHAT_SUBSCRIPTIONS = []
# RSS 订阅链接列表
RSS_SUBSCRIPTIONS = [
    "https://example.com/feed.xml",
]
```

## 模块二：阅读管理功能

### 现有基础

项目已经实现了 `saved_sync` 模块（收藏/稍后再看同步），在此基础上增加：

1. 标签系统
2. 阅读状态追踪

### 数据结构

```sql
CREATE TABLE IF NOT EXISTS reading_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_id TEXT NOT NULL,         -- 对应 recommendation id 或 article id
  content_url TEXT NOT NULL,
  content_title TEXT NOT NULL,
  source_platform TEXT NOT NULL,
  tags TEXT,                        -- JSON 数组 ["AI", "投资"]
  status TEXT DEFAULT 'unread',     -- 'unread' | 'reading' | 'finished'
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(content_id)
);
```

### 功能设计

**标签系统**：
- 在卡片右下角（操作区旁）显示标签
- 用户可点击标签，筛选所有该标签的文章
- LLM 建议标签：根据标题+摘要自动生成候选标签，用户可确认/修改

**阅读状态**：
- 卡片右上角显示状态标识（颜色区分）
  - 未读：浅灰色圆点
  - 正在读：蓝色圆点
  - 已读完：绿色勾选
- 点击状态可切换
- 可按状态筛选（只看未读/正在读）

**前端入口**：
- 在 tab 栏增加"阅读库"标签页
- 阅读库页展示：
  - 筛选区：标签筛选 + 状态筛选
  - 列表区：卡片网格（和推荐页一致样式）
  - 支持组合筛选

## 模块三：LLM 推荐增强

### 推荐增强点

| 增强点 | 说明 |
|--------|------|
| **阅读画像深化** | 在现有兴趣画像基础上，增加阅读维度：阅读频次、主题分布、阅读完成率 |
| **内容质量评分** | LLM 对每篇新内容打分（1-5）：兴趣匹配度、信息密度、内容质量 |
| **推荐理由生成** | 给每张推荐卡片生成一句话推荐理由，解释为什么推荐这篇 |

### 质量评分 prompt 示例

```
你是阅读推荐助手，请根据用户的兴趣画像和文章内容，给这篇文章打分并生成推荐理由。

用户兴趣画像：
{profile_text}

文章信息：
标题：{title}
作者：{author}
摘要：{summary}

请输出：
1. 评分：1-5，5最好
2. 一句话推荐理由（10-30字）
```

### 排序加权

```
最终分数 = 0.6 * MAB匹配分数 + 0.4 * LLM质量分数
```

### 推荐理由展示

- 在卡片下方显示推荐理由，字体略小，灰色
- 让用户知道为什么这篇会被推荐，也能更快判断要不要读

## 实现顺序

按这个顺序分批实现，每批完成后可测试验证：

1. **第一批次**：接入 RSS/Atom 订阅源 + 前端展示调整
2. **第二批次**：接入微信公众号 + 少数派
3. **第三批次**：阅读管理功能（标签 + 阅读状态）
4. **第四批次**：LLM 推荐增强（评分 + 理由生成）

## 风险与控制

- **抓取频率控制**：RSS/公众号抓取间隔至少 1 小时，不频繁请求避免被封
- **存储增量**：新增表不会影响现有数据，回滚方便
- **性能影响**：LLM 评分在抓取时离线完成，展示不阻塞推荐请求

## 验收标准

1. 新内容源可正常抓取和进入推荐池
2. 阅读库标签页可正常筛选和展示
3. LLM 推荐理由正常生成显示
4. 原有推荐功能不受影响
