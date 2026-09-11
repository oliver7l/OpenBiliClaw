# 豆瓣书影音模块

> 展示与复用用户从豆瓣抓取的书影音清单（影视/书/音乐 × 看过/想看/在看），
> 支持搜索/筛选、跳转豆瓣原文，并可选项作为内容源进入发现链路。

## 概述

`douban/` 包把用户通过登录态抓取的豆瓣书影音数据沉淀为独立结构化数据库
（`data/douban.db`），通过桌面「📚 豆瓣」tab 与 `/api/douban*` 接口展示清单，
并提供了一个默认关闭（`[sources.douban].enabled=false`）的内容源 adapter。

数据来源：`data/douban/*.json`（用户手动抓取的全量清单），经
`python -m openbiliclaw.douban.import_data` 导入 `douban_items` 表。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 存储层 | 独立 `douban.db` 单表持久化，按 url 去重 | `store.py` |
| 业务层 | 清单检索 / 统计封装 | `service.py` |
| API 层 | `/api/douban/items|stats` | `routes.py` |
| 导入脚本 | JSON → douban.db | `import_data.py` |
| 内容源 | 可选 source adapter（默认 off） | `sources/douban_adapter.py` |
| 前端页面 | 桌面「📚 豆瓣」tab | `web/desktop/assets/js/douban-app.js` |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 独立数据库 | ✅ | `data/douban.db`，与主库锁域隔离（同 media/health 模式） |
| 清单展示 | ✅ | 影视/书/音乐 × 看过/想看/在看，卡片网格 |
| 搜索筛选 | ✅ | 关键词搜索 + 分类 + 状态子 tab |
| 阅读入口 | ✅ | 每条含「去豆瓣看原文 ↗」外链，新标签打开 |
| 统计条 | ✅ | `/api/douban/stats` 展示总数与各分类计数 |
| 统计画像 | ✅ | `/api/douban/analytics` 按年份/分类/分布聚合（纯数据，不调 LLM） |
| 深度画像报告 | ✅ | `POST /api/douban/insight` 让 LLM 生成"我的观影/读书画像"报告，可缓存 |
| 内容源 | 🔶 | `DoubanAdapter` 已实现，默认关闭不注入推荐流 |
| 文章 feed 阅读源 | 🔶 | `DoubanFeedAdapter` 拉豆瓣 RSS（评论/小组/日记）写入阅读库；需配置订阅；豆瓣官方 RSS 覆盖有限，`diary` 走本地自部署 RSSHub（`[sources.douban].rsshub_url`） |

## 公开 API

### Python API

```python
from openbiliclaw.douban import DoubanStore, DoubanService
from openbiliclaw.douban.analytics import DoubanAnalytics
from openbiliclaw.douban.import_data import import_from_json

store = DoubanStore("data/douban.db")          # 初始化/连接独立库
r = store.import_items([...])                  # 批量导入（按 url 去重）
count = store.count()
items = store.list_items(category="movie", status="collect", search="出走的决心")
stats = store.stats()

svc = DoubanService("data/douban.db")
svc.items(category="book", status="wish", search="")

# 统计画像（纯聚合，不调 LLM）
report = DoubanAnalytics(store).full_report()

# 导入已抓取的 JSON
res = import_from_json("data/douban/douban_all.json", "data/douban.db")
```

### REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/douban/items?category=&status=&search=` | 清单（参数均可选） |
| GET | `/api/douban/stats` | 分类×状态计数概览 |
| GET | `/api/douban/analytics` | 统计画像（总量/分布/年度趋势/品味跨度） |
| GET | `/api/douban/insight` | 读缓存深度画像报告 |
| POST | `/api/douban/insight` | 生成深度画像报告（`{force: bool}` 可强制重生成） |

### 画像分析

画像视图位于豆瓣 tab 内「画像分析」子视图，包含：

- **统计画像**（不调 LLM，秒回）：`DoubanAnalytics` 聚合——总量与实际消费占比、分类×状态分布、按年份的消费趋势条、最早/最近年份的品味跨度。
- **深度报告**（可选、需 LLM）：点击「生成画像报告」→ `POST /api/douban/insight`，让模型读用户全部书影音清单生成"我的观影/读书画像"报告；结果缓存到 `data/douban/profile_report.json`，非强制时直接读缓存。LLM 未配置时优雅返回 `{ok: false}`，统计画像不受影响。

### 内容源 adapter

```python
from openbiliclaw.sources.douban_adapter import DoubanAdapter

adapter = DoubanAdapter(db_path="data/douban.db")
adapter.source_type  # "douban"
content = await adapter.fetch(recipe=None, profile=None, limit=20)
```

启用后从 douban.db 回放最近书影音条目为 `DiscoveredContent`，供发现/阅读链路。

## 配置项

### `[storage] douban_db_path`

独立库路径，默认 `data/douban.db`。

```toml
[storage]
douban_db_path = "data/douban.db"
```

### `[sources.douban]`

内容源开关。默认 `enabled=false`，不进入推荐流。

```toml
[sources.douban]
enabled = false          # true 时注册 douban source adapter 与 feed adapter
cookie_env = "OPENBILICLAW_DOUBAN_COOKIE"  # 仅重新抓取清单时需要
rsshub_url = "http://127.0.0.1:1200"       # 本地自部署 RSSHub，diary 等聚合 feed 用
```

`douban_feed_subscriptions` 订阅条目（`[scheduler]`）里 `feed_kind` 为
`comment` / `review` / `group` / `diary`；其中 `diary`（个人动态）走上方
`rsshub_url` 指定的 RSSHub，其余走豆瓣官方 RSS。RSSHub 由本地 Docker 自部署，
`[autostart].manage_rsshub=true` 时 `openbiliclaw start` 会自动拉起（默认关）。

## 数据导入

```bash
# 把手动抓取的 data/douban/douban_all.json 灌入 douban.db
cd 040-OpenBiliClaw
python -m openbiliclaw.douban.import_data
# 或指定路径
python -m openbiliclaw.douban.import_data --json <path> --db <path>
```

导入是幂等的：按 `url` 去重，重复导入只更新不新增。

## 豆瓣文章 feed 阅读源

新增 `DoubanFeedAdapter` 拉取豆瓣官方 RSS feed 并写入**阅读库**（`articles` 表，
`source_type="douban_feed"`），用户可在「阅读库」页面读到豆瓣评论 / 小组讨论内容。

### Feed 类型与 URL 模板

| `feed_kind` | 说明 | 对应 URL |
|---|---|---|
| `comment` | 关注作者最新评论 | `https://douban.com/feed/people/{uid}/` |
| `review` | 全站最新评论 | `https://douban.com/feed/review/latest` |
| `group` | 小组讨论 | `https://www.douban.com/feed/group/{group_id}/discussion` |
| `diary` | 日记 / 动态 | `https://rsshub.app/douban/user/{uid}/status` |

### 订阅配置（`[scheduler] douban_feed_subscriptions`）

```toml
[scheduler]
douban_feed_subscriptions = [
  { name = "关注作者A评论", feed_kind = "comment", uid = "某uid", group_id = "" },
  { name = "某小组讨论",    feed_kind = "group",   uid = "",       group_id = "小组ID" },
]
```

### Cookie

个人评论 / 部分小组 feed 需要登录 cookie。cookie 从
`[sources.douban].cookie_env` 指定的环境变量读取（默认 `OPENBILICLAW_DOUBAN_COOKIE`），
**不写进 config.toml / 不进 git**。未配置 cookie 时仍可拉公开 feed（全站评论等）。

### 手动抓取

```bash
OPENBILICLAW_DOUBAN_COOKIE='...' python scripts/fetch_douban_feed.py
```

### 已知限制

豆瓣官方 RSS 维护较少，部分 feed（如 `/feed/review/latest`）可能为空或无条目；
日记类 feed 官方覆盖有限、需借助 RSSHub。接入后建议先手动跑一次确认目标 feed 有内容。

## 设计决策

### 1. 独立数据库

仿 `health.db` / `media_state.db`，书影音数据与主库锁域隔离。`data/` 已在
`.gitignore`，数据库不进 git，也不含任何真实 cookie。

### 2. 默认不注入推荐流

豆瓣是用户**主动阅读/回顾**场景，非主动发现源。因此 adapter 默认
`enabled=false`，与"新增 source 需用户显式启用"的项目保守取向一致
（仿小红书 stub 的注册方式）。

### 3. JSON 作为源，DB 作为结构化层

`data/douban/*.json` 保留为用户抓取到的原始数据，`douban.db` 是其结构化
镜像。两者并行，JSON 重抓后重新导入即可更新 DB。

## 后续规划

- [ ] 观影/读书画像分析（年份、评分分布、类别偏好）
- [ ] 与 diary / 推荐兴趣画像打通
- [ ] 支持从 tab 内直接抓取更新（需 cookie，谨慎）
- [ ] 豆瓣热评/榜单阅读源（真正在线拉取）