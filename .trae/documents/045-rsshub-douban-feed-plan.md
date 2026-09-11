# 豆瓣 feed 改接自部署 RSSHub

## Context（背景）

上一轮已实现 `DoubanFeedAdapter`，但**豆瓣官方 RSS 基本不维护**（实测 `/feed/review/latest` 返回空）。经讨论，用户确认改用 **RSSHub**（社区维护、持续更新豆瓣路由），覆盖 **榜单/新书、关注用户广播、小组讨论、豆列更新** 四类内容。

关键现实：RSSHub 官方公共实例 `rsshub.app` 已限制访问（403，注明仅供测试、建议自部署），其他公共实例也不稳定数十秒超时。因此**必须自部署**。用户确认：
- **Docker 自部署 RSSHub**（推荐，项目已有 Colima/Docker）
- **项目启动时自动拉起**（仿 autostart/Ollama）
- 用户有 **127.0.0.1:7890 代理**（Clash），RSSHub 拉豆瓣如被墙可走代理

## RSSHub 豆瓣路由（已确认）

| 内容 | 路由 |
|---|---|
| 电影榜单 | `/douban/movie/weekly`、`/douban/movie/playing`、`/douban/movie/ustop` |
| 小组 | `/douban/group/:groupid`（官方注明"反爬严格"，需 cookie 时可能不稳） |
| 用户广播 | `/douban/people/:userid/status` |
| 用户想看 | `/douban/people/:userid/wish` |
| 豆列 | `/douban/doulist/:id` |
| 新书速递 | `/douban/book/latest`（或有） |
| 通用参数 | `?mode=fulltext` 全文输出、`?limit=N`、`?filter=` |

## 已核实的项目复用点

| 能力 | 可复用 | 文件 |
|---|---|---|
| Docker 服务启动 | `mule-cli/install.sh` 的 docker run + restart=unless-stopped + 端口映射 | `mule-cli/install.sh` L53-75 |
| 本机代理检测 + 注入 | `resolve_optional_proxy_env()`（探测 127.0.0.1:7890 连通 → 返回 HTTP/HTTPS 代理 env） | `docker_runtime.py` L135-161 |
| HTTP 代理统一 | `network.httpx_kwargs_for()` / `can_connect()` | `network.py` L148-180 |
| autostart 配置 | `AutostartConfig(enabled, manage_ollama)` | `config.py` L407-412 |
| 启动自动拉起 | cli `start` 检查 Ollama 未跑则拉起 | `cli/__init__.py` L315-334 |

## 实施步骤

### 1. RSSHub 自部署（Docker）
- **新增** 服务启动逻辑（放 `src/openbiliclaw/runtime/` 或复用 docker 封装）：`ensure_rsshub()` ——
  - 检测 `127.0.0.1:1200`（RSSHub 默认端口）是否存活（`can_connect`）。
  - 未存活则 `docker run -d --name rsshub --restart unless-stopped -p 127.0.0.1:1200:1200 diygod/rsshub`（或用 docker-compose）。
  - 等待端口就绪。
  - **代理注入**：若需访问墙外/受限内容，用 `resolve_optional_proxy_env()` 探测 7890 代理并作为容器 `-e HTTP_PROXY/HTTPS_PROXY` 传入（RSSHub 拉豆瓣/依赖的网络走代理）。
- RSSHub 默认端口 1200，`config` 提供 `rsshub_url`（默认 `http://127.0.0.1:1200`）。

### 2. 配置
- **修改** `config.py` `AutostartConfig` 加 `manage_rsshub: bool = False`（默认关，显式开启才自部署拉起）。
- **修改** `config.py` `SchedulerConfig`：现有 `douban_feed_subscriptions` 每条加 `kind` 枚举扩展（`movie_weekly`/`group`/`user_status`/`user_wish`/`doulist`），或复用现有 `feed_kind` 加新值。
- **修改** `config.py` TOML 序列化 + `config-show` 输出 + `config.example.toml` 样例。

### 3. adapter 改指 RSSHub
- **修改** `src/openbiliclaw/sources/douban_feed_adapter.py`：
  - URL 构造改为拼 `rsshub_url + /douban/{route}`，按 feed_kind 映射（group→/douban/group/{id}、user_status→/douban/people/{uid}/status、movie_weekly→/douban/movie/weekly、doulist→/douban/doulist/{id} 等）。
  - RSSHub 返回的是标准 RSS/ATOM，`requests.get` + `feedparser.parse` 逻辑复用。
  - 保留 cookie 支持（小组反爬时可视化，经 `/douban/...` 多数不需要；RSSHub 可配 `DOUBAN_COOKIE` 环境变量，容器注入）。
  - 无需代理时也走 RSSHub（本地实例），把 `rsshub_url` 作为 adapter 构造参数。

### 4. 自动拉起接入
- **修改** `cli/__init__.py` `start` 启动流程：若 `config.autostart.manage_rsshub` 为 true，则启动前调用 `ensure_rsshub()`（仿现有 Ollama 检查）。
- **修改** `runtime/` 或 autostart primary 入口：同样在 daemon 启动时确认 RSSHub。

### 5. 轮询/写库
- 复用已实现的 `douban_feed_tasks.run_douban_feed_polling()` + `_persist_items()`（无需改，adapter 换数据源即可）。订阅配置已在 `[scheduler] douban_feed_subscriptions`。

### 6. README / 部署文档
- 更新 `docs/modules/douban.md`、`docs/agent-install.md`（RSSHub 自部署一节）、`config.md`（`[autostart].manage_rsshub` + `douban_feed_subscriptions` kind）。
- `docs/changelog.md` 加一条。

### 7. 测试
- **修改** `tests/source/test_douban_feed.py`：
  - adapter：mock `requests.get` 断言 URL 拼成 `http://127.0.0.1:1200/douban/movie/weekly` 等各类 route；解析仍正确。
  - 保留 cookie / feedparser 相关测试。
  - 新增 `ensure_rsshub` 单测：mock `can_connect` 控制存活/未存活分支，断言 docker run 调用（mock subprocess/docker）。

## 验证
1. `docker run -d --name rsshub ... diygod/rsshub` 后 `127.0.0.1:1200` 可访问。
2. 本地起 RSSHub，`GET http://127.0.0.1:1200/douban/movie/weekly` 返回含 item 的 RSS。
3. `python scripts/fetch_douban_feed.py`（配置 film/group/user_status/doulist 订阅）抓取成功，`articles` 表出现 `source_type="douban_feed"` 文章。
4. `start` 命令在 `manage_rsshub=true` 时自动拉起 RSSHub（未运行则 docker run）。
5. 7890 代理可用时 RSSHub 容器按需带代理（`docker inspect rsshub` 看 HTTP_PROXY 注入）；被墙路由可通。
6. `pytest tests/source/test_douban_feed*` 全过；ruff + mypy 干净。

## 约束
- **小组反爬**：RSSHub 豆瓣小组注明"反爬严格"，可能不稳定/需 cookie。若某小组拉不到，通过 `can_connect`/日志降级跳过，不阻塞其余源。
- RSSHub 是可选服务：`manage_rsshub` 默认关，不影响主流程；开后才自动拉起 Docker。
- 代理：复用 `resolve_optional_proxy_env()` 自动探测 7890，不硬编码；RSSHub 容器经代理访问受限内容。
- 部署方式：优先 `docker run` 单容器（最简单）；若项目已用 docker-compose，可改为 compose 服务（`depends_on` + `extra_hosts` 参考现有 compose）。

## 说明
这轮不需要动 `DoubanFeedAdapter` 的写库/轮询/测试核心，主要是「数据源从豆瓣官方改指 RSSHub」+「RSSHub 自部署与自动拉起」。已实现的下游（阅读库写入、订阅配置、手动脚本、阅读意图登记）全部保留复用。