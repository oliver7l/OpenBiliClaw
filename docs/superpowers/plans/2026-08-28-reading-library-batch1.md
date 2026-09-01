# 阅读库第一批次 · RSS/Atom 订阅源 + 前端调整

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 RSS/Atom 订阅源，作为第一个新内容源验证数据流，并调整前端推荐页面展示为阅读风格。

**Architecture:** 沿用现有 `sources/` 目录的 `SourceAdapter` 协议模式。新增 `rss_adapter.py` 实现 RSS 抓取，`rss_tasks.py` 实现定时调度。新增 `articles` 表存储抓取的文章。前端调整 tab 样式和卡片布局，增加"阅读库"标签页入口。

**Tech Stack:** `feedparser` (RSS 解析), `httpx` (异步 HTTP), Python 3.11+, SQLite, vanilla JS

---

### Task 1: 添加 RSS 依赖和配置项

**Files:**
- Modify: `src/openbiliclaw/config.py` — 添加 RSS 订阅列表配置项
- Modify: `pyproject.toml` 或 `requirements.txt` — 添加 `feedparser` 依赖

- [ ] **Step 1: 安装 feedparser**

```bash
pip install feedparser
```

- [ ] **Step 2: 在 config.py 添加 RSS 配置项**

在 `_DEFAULT_POOL_SOURCE_SHARES` 定义之后添加：

```python
# RSS 订阅源配置
_DEFAULT_RSS_SUBSCRIPTIONS: list[dict[str, str]] = [
    # {"name": "示例博客", "url": "https://example.com/feed.xml"},
]
```

在 `@dataclass` 配置类中找到 `pool_source_shares` 配置字段附近，添加：

```python
rss_subscriptions: list[dict[str, str]] = field(
    default_factory=lambda: list(_DEFAULT_RSS_SUBSCRIPTIONS)
)
```

- [ ] **Step 3: 更新 `_DEFAULT_POOL_SOURCE_SHARES` 添加 rss 源**

```python
_DEFAULT_POOL_SOURCE_SHARES = {
    "bilibili": 5,
    "xiaohongshu": 1,
    "douyin": 1,
    "youtube": 1,
    "twitter": 1,
    "zhihu": 1,
    "v2ex": 1,
    "reddit": 1,
    "rss": 2,  # 新增 RSS 源
}
```

- [ ] **Step 4: Commit**

```bash
git add src/openbiliclaw/config.py
git commit -m "chore: add RSS subscription config items"
```

---

### Task 2: 创建 RSS 表结构

**Files:**
- Modify: `src/openbiliclaw/storage/database.py` — 添加 articles 表

- [ ] **Step 1: 在 Database 类的 `_ensure_tables` 方法中添加 articles 表创建**

在 `_ensure_tables` 方法中找到 `CREATE TABLE IF NOT EXISTS pool` 附近的表创建语句，添加：

```python
self._execute(
    """CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL,
        source_name TEXT DEFAULT '',
        title TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        author TEXT DEFAULT '',
        summary TEXT DEFAULT '',
        content_text TEXT DEFAULT '',
        published_at TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
)
```

- [ ] **Step 2: 添加 articles 的增删查方法**

在 Database 类中添加：

```python
def upsert_article(self, source_type: str, source_name: str, title: str,
                    url: str, author: str, summary: str,
                    content_text: str, published_at: str) -> int | None:
    """Insert or update an article. Returns row id or None."""
    try:
        cursor = self._execute(
            """INSERT INTO articles (source_type, source_name, title, url,
                author, summary, content_text, published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                title=excluded.title, summary=excluded.summary,
                updated_at=CURRENT_TIMESTAMP""",
            (source_type, source_name, title, url, author, summary,
             content_text, published_at),
        )
        return cursor.lastrowid if cursor else None
    except Exception:
        logger.exception("Failed to upsert article: %s", title)
        return None

def get_recent_articles(self, limit: int = 50, offset: int = 0,
                         source_type: str | None = None) -> list[dict]:
    """Get recent articles, optionally filtered by source_type."""
    where = "WHERE source_type = ?" if source_type else ""
    params = (source_type,) if source_type else ()
    try:
        rows = self._query(
            f"""SELECT id, source_type, source_name, title, url, author,
                       summary, published_at, created_at
                FROM articles {where}
                ORDER BY published_at DESC, created_at DESC
                LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        )
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("Failed to query articles")
        return []
```

- [ ] **Step 3: Commit**

```bash
git add src/openbiliclaw/storage/database.py
git commit -m "feat: add articles table and CRUD methods"
```

---

### Task 3: 创建 RSS 源适配器

**Files:**
- Create: `src/openbiliclaw/sources/rss_adapter.py`
- Create: `src/openbiliclaw/sources/rss_tasks.py`

- [ ] **Step 1: 创建 rss_adapter.py**

```python
"""RSS/Atom feed source adapter.

Fetches and parses RSS/Atom feeds, normalises items into DiscoveredContent
for the recommendation pipeline.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import feedparser

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)


class RssAdapter:
    """Adapter for RSS/Atom feed sources."""

    def __init__(self, *, http_client: object | None = None) -> None:
        self._http_client = http_client

    @property
    def source_type(self) -> str:
        return "rss"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: SoulProfile,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch and parse RSS feed defined by recipe config."""
        feed_url = (recipe.config or {}).get("url", "")
        feed_name = recipe.name or recipe.config.get("name", feed_url)
        if not feed_url:
            logger.warning("RssAdapter: no URL in recipe %s", recipe.id)
            return []

        # feedparser is synchronous, run in thread
        import asyncio
        feed = await asyncio.to_thread(feedparser.parse, feed_url)

        if feed.bozo and not feed.entries:
            logger.warning("RssAdapter: failed to parse %s: %s", feed_url, feed.bozo_exception)
            return []

        from openbiliclaw.discovery.engine import DiscoveredContent

        items: list[DiscoveredContent] = []
        for entry in feed.entries[:limit]:
            title = (getattr(entry, "title", "") or "").strip()
            link = (getattr(entry, "link", "") or "").strip()
            if not title or not link:
                continue

            author = ""
            if hasattr(entry, "author") and entry.author:
                author = entry.author.strip()
            elif hasattr(entry, "authors") and entry.authors:
                author = entry.authors[0].get("name", "")

            summary = ""
            if hasattr(entry, "summary") and entry.summary:
                summary = entry.summary.strip()
            elif hasattr(entry, "description") and entry.description:
                summary = entry.description.strip()
            # Strip HTML tags from summary
            if summary:
                import re
                summary = re.sub(r"<[^>]+>", "", summary)[:500]

            published = ""
            if hasattr(entry, "published") and entry.published:
                published = entry.published
            elif hasattr(entry, "updated") and entry.updated:
                published = entry.updated

            content_id = f"rss-{hash(link) & 0xFFFFFFFF:08x}"

            items.append(DiscoveredContent(
                content_id=content_id,
                content_url=link,
                source_platform="rss",
                title=title,
                description=summary[:300],
                author_name=author,
                published_at=published,
                up_name=feed_name,
                extra={"source_name": feed_name},
            ))

        logger.info("RssAdapter: fetched %d items from %s", len(items), feed_url)
        return items
```

- [ ] **Step 2: 创建 rss_tasks.py**

```python
"""RSS feed scheduled fetching tasks.

Periodically fetches configured RSS feeds and stores articles into
the database, then injects them into the recommendation pool.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveryEngine
    from openbiliclaw.sources.registry import AdapterRegistry
    from openbiliclaw.sources.protocol import SourceRecipe
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

_RSS_FETCH_INTERVAL_SECONDS = 3600


async def run_rss_polling(
    adapter_registry: AdapterRegistry,
    db: Database,
    subscriptions: list[dict[str, str]],
) -> int:
    """Fetch all configured RSS feeds using the registered adapter.

    Resolves the "rss" adapter from the registry, creates a SourceRecipe
    for each subscription, and stores fetched articles.

    Returns:
        Total number of articles fetched.
    """
    from openbiliclaw.sources.protocol import SourceRecipe

    import uuid

    total = 0
    for sub in subscriptions:
        feed_url = sub.get("url", "")
        feed_name = sub.get("name", feed_url)
        if not feed_url:
            continue

        recipe = SourceRecipe(
            id=str(uuid.uuid4()),
            source_type="rss",
            name=feed_name,
            strategy="feed",
            config={"url": feed_url, "name": feed_name},
        )
        adapter = adapter_registry.resolve(recipe)
        if adapter is None:
            logger.warning("RSS adapter not registered, skipping %s", feed_url)
            continue

        try:
            items = await adapter.fetch(recipe, profile=None, limit=30)  # type: ignore[arg-type]
        except Exception:
            logger.exception("RSS fetch failed for %s", feed_url)
            continue

        for item in items:
            db.upsert_article(
                source_type="rss",
                source_name=feed_name,
                title=item.title,
                url=item.content_url,
                author=item.author_name or "",
                summary=item.description or "",
                content_text="",
                published_at=item.published_at or "",
            )
            total += 1

    logger.info("RSS polling complete: %d articles from %d feeds", total, len(subscriptions))
    return total
```

- [ ] **Step 4: 在 runtime_context.py 中注册 RSS 适配器**

在 `runtime_context.py` 中 Twitter 适配器注册之后（约 612 行），添加：

```python
# Register RSS adapter — standalone feed fetcher, no LLM required
from openbiliclaw.sources.rss_adapter import RssAdapter

rss_adapter = RssAdapter()
new_discovery_engine.register_adapter(rss_adapter)
```

- [ ] **Step 5: Commit**

```bash
git add src/openbiliclaw/sources/rss_adapter.py src/openbiliclaw/sources/rss_tasks.py src/openbiliclaw/sources/__init__.py
git commit -m "feat: add RSS/Atom feed source adapter"
```

---

### Task 4: 前端调整 — 统一 tab 栏样式 + 增加阅读库标签页入口

**Files:**
- Modify: `src/openbiliclaw/web/desktop/index.html` — 增加"阅读库" tab 按钮
- Modify: `src/openbiliclaw/web/desktop/assets/css/app.css` — tab 样式已统一，调整卡片推荐理由展示

- [ ] **Step 1: 在 index.html 的 tabbar 中添加"阅读库"按钮**

在 `#chatBtn` 之后添加：

```html
<button class="tab-btn" id="libraryBtn" type="button">阅读库</button>
```

- [ ] **Step 2: 在 app.css 中为阅读库 tab 增加高亮样式**

```css
/* 阅读库标签页 */
.tab-btn#libraryBtn.is-active { color: #1a1a1a; font-weight: 500; }
```

- [ ] **Step 3: 在 app.js 中绑定阅读库 tab 切换逻辑**

在 `renderAll()` 或 tab 切换逻辑处，阅读库暂时和推荐页共用推荐卡片展示，但筛选条件不同。找到 `#chatBtn` 附近的 tab 切换代码，添加：

```javascript
// 阅读库标签页
const libraryBtn = $("#libraryBtn");
if (libraryBtn) {
    libraryBtn.addEventListener("click", () => {
        setActiveTab("library");
        // 切换到阅读库视图
        showLibraryView();
    });
}
```

- [ ] **Step 4: 在 app.js 中添加 `showLibraryView` 函数**

```javascript
function showLibraryView() {
    // 设置 active tab
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("is-active"));
    $("#libraryBtn")?.classList.add("is-active");
    
    // 隐藏推荐相关区域，显示阅读库内容
    const grid = $("#recommendationGrid");
    if (!grid) return;
    
    // 通过 API 获取已收藏/已保存的文章列表
    fetch("/api/reading/items?limit=30")
        .then(r => r.json())
        .then(items => {
            if (!items || !items.length) {
                grid.innerHTML = '<div class="empty-state">阅读库为空，先去推荐页收藏感兴趣的内容吧</div>';
                return;
            }
            grid.classList.add("is-minimal");
            grid.replaceChildren(...items.map(item => {
                const card = document.createElement("article");
                card.className = "video-card is-minimal";
                card.innerHTML = `
                    <p class="video-card-title">${escapeHtml(item.title || item.content_title)}</p>
                    <div class="video-card-meta">
                        <span class="video-card-author">${escapeHtml(item.author || "")}</span>
                        <span class="video-card-tag">${escapeHtml(item.source_platform || "")}</span>
                    </div>
                    <div class="video-card-actions">
                        <button class="feedback-icon-btn" data-action="read" type="button" title="标记已读">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M5 12l5 5L20 6" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        </button>
                    </div>`;
                card.addEventListener("click", (e) => {
                    if (e.target.closest("[data-action]")) return;
                    if (item.content_url || item.url) window.open(item.content_url || item.url, "_blank");
                });
                return card;
            }));
        })
        .catch(() => {
            grid.innerHTML = '<div class="empty-state">加载阅读库失败</div>';
        });
}
```

- [ ] **Step 5: 在 API 路由中添加阅读库列表接口**

编辑 `src/openbiliclaw/api/saved_sync_routes.py` 或相关路由文件，添加：

```python
@router.get("/api/reading/items")
async def get_reading_items(request: Request, limit: int = 30, offset: int = 0):
    """Get reading library items (saved/favorited content)."""
    db = request.app.state.db
    items = db.get_recent_articles(limit=limit, offset=offset)
    return JSONResponse(items)
```

- [ ] **Step 6: Commit**

```bash
git add src/openbiliclaw/web/desktop/index.html src/openbiliclaw/web/desktop/assets/css/app.css src/openbiliclaw/web/desktop/assets/js/app.js
git commit -m "feat: add reading library tab and frontend view"
```

---

### Task 5: 集成测试

- [ ] **Step 1: 重启服务并验证 RSS 源正常工作**

```bash
pm2 restart openbiliclaw-api
```

- [ ] **Step 2: 验证 RSS 源能正常抓取**

```bash
curl -s http://localhost:8420/api/health | python3 -m json.tool
# 检查服务是否正常
```

- [ ] **Step 3: 验证阅读库 API 能正常返回**

```bash
curl -s http://localhost:8420/api/reading/items?limit=5
```

- [ ] **Step 4: 打开浏览器确认前端页面正常**

访问 http://localhost:8420 ，确认：
- tab 栏新增"阅读库"按钮
- 点击"阅读库"能正常显示
- 推荐页卡片正常展示

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: verify RSS integration and reading library UI"
```