# 豆瓣 feed 文章阅读源接入阅读库

## Context（背景）

豆瓣书影音模块已上线（清单 + 画像分析）。用户希望进一步**把豆瓣的定期内容（评论/小组讨论/日记）作为阅读库来源**，在"我的阅读库"里持续读到豆瓣相关内容。

用户已确认：
- **feed 类型**：关注作者最新评论 + 小组讨论 feed + 日记/专栏
- **接入方式**：新建 feed 源接入现有阅读库
- **cookie**：使用已有的豆瓣 cookie

关键事实：**豆瓣官方提供标准 RSS feed**（早已确认存在）：
- 个人新评论：`https://douban.com/feed/people/{uid}/`
- 全站最新评论：`https://douban.com/feed/review/latest`
- 小组话题：`https://www.douban.com/feed/group/{group_id}/discussion`
- 日记/专栏官方 RSS 覆盖有限 → 需 RSSHub：`https://rsshub.app/douban/user/{uid}/status`

项目已有**完整的 RSS feed → 阅读库链路**可复用，这是最稳的落地基础。

## 目标

新增"豆瓣 feed 阅读源"：从豆瓣官方 RSS（评论/小组）抓取内容，写入现有阅读库 `articles` 表（按 url 去重），并在推荐池注入；支持带 cookie；用户可在阅读库页面读到，可手动触发或定时轮询。

## 已核实的关键事实

| 事实 | 说明 |
|---|---|
| 阅读库写入 | `upsert_article(source_type, source_name, title, url, author, summary, content_text, published_at)`，按 url 去重（`storage/_article_mixin.py`） |
| 现有 RSS 链路 | `RssAdapter`（feedparser）→ `rss_tasks.run_rss_polling()` → `_persist_items()` 写 `articles` + `inject_article_to_pool` |
| RssAdapter 现状 | **不带 cookie**（只 `feedparser.parse(feed_url)`）——需给豆瓣带 cookie |
| RSS 订阅配置 | `config.scheduler.rss_subscriptions: list[dict[str,str]]`（L241） |
| 阅读库展示 | 经 `reading_routes` → `get_recent_articles`，来源筛选用 `READING_SOURCE_SYNONYMS`（`api/utils.py`、`api/app.py`） |
| `articles.source_type` | 无约束字符串列，需保持一致命名（`douban_feed`） |
| `_persist_items` | 硬编码 `source_type="rss"`，需参数化或新增 |

## 实施步骤

### 1. 抓取 adapter（带 cookie）
- **新增** `src/openbiliclaw/sources/douban_feed_adapter.py`：`DoubanFeedAdapter`（薄，仿 RssAdapter）：
  - `__init__(self, cookie: str = "")` 接收豆瓣 cookie。
  - `fetch(recipe, profile, limit)`：`requests.get(feed_url, cookies=self._cookie_or_none, timeout=20, headers=UA)` 取原始 XML（同步，走现有线程池），再 `feedparser.parse(xml)`；字段归一化与 RssAdapter 相同（title/link/author/summary/published/content_text），返回 `DiscoveredContent(source_platform="douban_feed")`。
  - 内置 feed URL 模板，按 `feed_kind` 拼：
    - `comment` → `https://douban.com/feed/people/{uid}/`
    - `review` → `https://douban.com/feed/review/latest`（无需 uid）
    - `group` → `https://www.douban.com/feed/group/{group_id}/discussion`
    - `diary` → `https://rsshub.app/douban/user/{uid}/status`（官方 RSS 有限，条目可能仅摘要无正文）

### 2. 订阅源配置
- **修改** `src/openbiliclaw/config.py`：`SchedulerConfig` 新增 `douban_feed_subscriptions: list[dict[str, str]] = field(default_factory=list)`（仿 L244 xiaoyuzhou，每条 `{name, feed_kind, uid}` 或 `{name, feed_kind, group_id}`）。
- **修改** `config.py` TOML 输出区（L2539 附近）：加 `douban_feed_subscriptions` 序列化。
- **修改** `config.example.toml`：`[scheduler]` 下加注释样例。

### 3. 读库 + 调度
- **新增** `src/openbiliclaw/sources/douban_feed_tasks.py`：`run_douban_feed_polling(adapter, db, subscriptions, cookie)`：
  - 逐条构造 `SourceRecipe(source_type="douban_feed", name, config={feed_kind,uid/group_id})`。
  - 调 `adapter.fetch(recipe, ...)`。
  - 写库：复用/参数化 `rss_tasks._persist_items` 的写入逻辑（`db.upsert_article` + `inject_article_to_pool`），但 `source_type="douban_feed"`。
- **修改** `src/openbiliclaw/sources/rss_tasks.py`：把 `_persist_items` 的写库核心抽成一个可传 `source_type` 的辅助（`_persist_articles(db, items, feed_name, source_type)`），rss 与 douban 复用；或直接在新文件复制一份写库逻辑（更简单，避免改动 rss 行为）。**倾向：复制一份写库逻辑**，避免动现有 rss 链路。
- **修改** `runtime/_refresh_platform_loops_mixin.py`：加 `_loop_douban_feed_polling`（仿现有 RSS/小宇宙 loop），从 `config.scheduler.douban_feed_subscriptions` + `config.sources.douban.cookie_env` 环境变量取 cookie 轮询。
- **新增** `scripts/fetch_douban_feed.py`：手动触发一次（仿 `scripts/fetch_rss.py`）。

### 4. source_type 登记
- **修改** `api/utils.py` ~L270 `READING_SOURCE_SYNONYMS`：加 `"豆瓣feed": "douban_feed"`、`"豆瓣评论": "douban_feed"`。
- **修改** `api/app.py` ~L227 `_READING_SOURCE_SYNONYMS`（若有）：同步加。
- douban tab 也可在列表/画像里提示"豆瓣 feed 已进阅读库"（可选，不强制）。

### 5. 注册 adapter
- **修改** `api/runtime_context.py` L633 附近：注册 `DoubanFeedAdapter(cookie=resolve_douban_cookie())`（cookie 从 `[sources.douban].cookie_env` 环境变量读取；未配置则空，仍可拉公开 feed）。

### 6. 测试
- **新增** `tests/source/test_douban_feed_adapter.py`：mock `requests.get` 返回固定 XML，断言不触发网络、条目字段（title/url/author/content_text）正确、cookie 传递正确。
- **新增** `tests/source/test_douban_feed_tasks.py`：用假 adapter 返回固定 `DiscoveredContent`，断言写库时 `source_type="douban_feed"`、`upsert_article` 收到正确字段。
- **修改** config 测试：断言 `douban_feed_subscriptions` 反序列化。

### 7. 文档
- 更新 `docs/modules/douban.md`：加"豆瓣 feed 阅读源"（feed 类型、URL 模板、cookie、订阅配置、进阅读库）。
- 更新 `docs/changelog.md` 顶部加 v0.3.228 条目。
- 若新增配置字段，更新 `docs/modules/config.md` 的 `[scheduler]` 段。

## 验证
1. `GET https://douban.com/feed/people/{uid}/`（带 cookie）返回合法 XML（冒烟，curl 或脚本）。
2. `python scripts/fetch_douban_feed.py` 手动抓一次，`articles` 表出现 `source_type="douban_feed"` 记录，`get_recent_articles` 能读到，阅读库页可显示。
3. 重复运行不产生重复（url 去重）。
4. cookie 未配置时仍能拉公开 feed（全站评论/部分小组），不报错。
5. `pytest tests/source/test_douban_feed*` 全过；ruff + mypy 干净。

## 约束
- **限定真实性**：豆瓣官方 RSS 主要覆盖评论/收藏/小组；**日记/专栏长文 feed 官方覆盖有限**，或仅摘要无全文（`content_text` 可能为空）。计划会据此说明，不承诺"豆瓣全部文章全文"。
- 不重复造 wheel：复用 feedparser 解析、`upsert_article` 写库、`inject_article_to_pool` 注入、现有 RSS 轮询骨架。
- cookie 从 `cookie_env` 环境变量读取，不写进 config.toml / 不提交 git。
- 新增 feed 源默认可先手动触发，scheduler 轮询作为增强（避免上线即高频拉取触发豆瓣风控）。