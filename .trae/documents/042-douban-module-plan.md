# 豆瓣模块：内容源 + tab + 独立数据库

## Context（背景）

用户已用登录态抓取了豆瓣全部书影音数据（1410 条，存于 `data/douban/*.json`，`.gitignore` 已忽略）。现在要将其沉淀为项目内正式功能，包含三部分：

1. **完整 source adapter**：新增豆瓣内容源，仿抖音/小红书，可被 discovery 发现链路识别（`source_type="douban"`），但**默认 enabled=false** 不主动抓取；`fetch()` 从豆瓣库返回内容供阅读。
2. **豆瓣 tab**（桌面前端）：展示书影音清单（看过/想看/在看），可筛选搜索，提供跳转豆瓣原文的阅读入口。仿现有 ed2k/travel tab 接线。
3. **独立 douban.db**：仿 health.db 建独立数据库 + 独立表，把已抓的 1410 条 JSON 导入成结构化表。

用户已确认：完整 source adapter + 展示+阅读入口 + 独立 douban.db。

## 已核实的项目模式（复用依据）

| 关注点 | 既有模式 | 位置 |
|---|---|---|
| 独立库 store | health 模块 `open_db_conn` + 独立表 | `src/openbiliclaw/health/store.py`（`_SCHEMA_SQL` + `health_*` 表） |
| 轻模块路由 | `build_*_router(config)` → `APIRouter(prefix=...)` | `src/openbiliclaw/travel/routes.py`、`ed2k/routes.py` |
| 路由注册 | try/except include_router | `src/openbiliclaw/api/_route_registry.py` L233-281 |
| source adapter 注册 | `register_adapter()` 在 runtime_context | `src/openbiliclaw/api/runtime_context.py` L598-624（xiaohongshu/youtube 等） |
| enabled 门控 | 仿 twitter `[sources.twitter].enabled` | `runtime_context.py` L631-632 |
| 前端 tab 接线 | `MAIN_PAGE_IDS` + tabSync + safeBind + `open*Page` | `web/desktop/assets/js/app.js` L1228/L1283/L1342/L3838 |
| 前端页面 JS | IIFE 暴露 `window.reloadXxxPage` | `web/desktop/assets/js/ed2k-app.js`（模板） |
| 网页白名单 | assets 版本 + `_desktop_page_names` | `api/_web_ui_routes.py` L77-87 / L109-140 |
| 顶栏 tab 按钮 | `<button class="tab-btn" id="xxxBtn">` | `web/desktop/index.html` L121-123 |

## 实施步骤

### 1. 独立数据库 douban.db（store）
- **新增** `src/openbiliclaw/douban/store.py`：仿 `health/store.py`，用 `db_path`（`data/douban.db`）建独立 SQLite + 单表：
  ```sql
  CREATE TABLE IF NOT EXISTS douban_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      category TEXT NOT NULL,   -- movie / book / music
      status TEXT NOT NULL,     -- collect / wish / do
      name TEXT NOT NULL,
      url TEXT DEFAULT '',
      date TEXT DEFAULT '',
      comment TEXT DEFAULT '',
      rating TEXT DEFAULT '',   -- movie/book
      pub TEXT DEFAULT '',      -- book 出版社
      intro TEXT DEFAULT '',    -- music 简介
      imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(url)
  );
  ```
  方法：`count()`、`list(category, status, search)`、`import_items(list[dict])`（按 url 去重，upsert）。
- **新增** `src/openbiliclaw/douban/import_data.py`：读 `data/douban/*.json` → 映射 `影视→movie/书→book/音乐→music`、`collect|wish|do→status` → 写 `douban_items`。字段兜底：movie 用 date/rating/comment，book 用 pub/comment/date，music 用 intro/date。
- **新增** `src/openbiliclaw/scripts/import_douban_db.py`（或独立可运行入口）调用 import_data。

### 2. 后端 API（routes + service）
- **新增** `src/openbiliclaw/douban/service.py`：`DoubanService(db_path)`，封装 `list_items(category,status,search)`、`stats()`。
- **新增** `src/openbiliclaw/douban/routes.py`：`build_douban_router(config)` → `APIRouter(prefix="/api/douban")`，端点：
  - `GET /api/douban/items?category=&status=&search=` → 分页清单
  - `GET /api/douban/stats` → 各类/状态计数
- **新增** `src/openbiliclaw/douban/__init__.py`：导出 `DoubanStore/DoubanService/DoubanItem`。
- **修改** `src/openbiliclaw/api/_route_registry.py`：在 `# 本地媒体浏览 API` 附近（L279 区域）加 try/except include `travel` 同款注册 `build_douban_router(config=config)`。

### 3. 内容源 adapter
- **新增** `src/openbiliclaw/sources/douban_adapter.py`：`class DoubanAdapter`，`source_type="douban"`、`source_name="豆瓣"`；`async fetch(recipe, profile, limit)` 从 douban.db 读最近条目返回 `DiscoveredContent`（不主动网络抓取）。
- **修改** `src/openbiliclaw/api/runtime_context.py`：在 L624（xiaoyuzhou 注册后）仿 twitter 加 enabled 门控注册：
  ```python
  douban_cfg = getattr(getattr(config, "sources", None), "douban", None)
  if douban_cfg is not None and bool(getattr(douban_cfg, "enabled", False)):
      from openbiliclaw.sources.douban_adapter import DoubanAdapter
      new_discovery_engine.register_adapter(DoubanAdapter())
  ```
- **URL 处理器**：现有 `src/openbiliclaw/sources/url_processors/douban_processor.py` 已注册 `douban` source_type，已足够；无需新写，但确认 `url_processors/__init__.py` 已 import（已在）。

### 4. 配置（config.py + config.example.toml）
- **修改** `src/openbiliclaw/config.py`：
  - 新增 `@dataclass DoubanSourceConfig`（`enabled: bool=False`、`cookie_env: str=""`），在 `SourcesConfig`（L565 附近）加 `douban: DoubanSourceConfig = field(default_factory=DoubanSourceConfig)`。
  - 在 `from_dict` 加 `douban=DoubanSourceConfig(enabled=bool(raw.get("sources",{}).get("douban",{}).get("enabled",False)))`。
  - `StorageConfig` 加 `douban_db_path: str = "data/douban.db"`。
- **修改** `config.example.toml`：加 `[sources.douban]`（`enabled = false`）+ `[storage]` 下 `douban_db_path = "data/douban.db"`。

### 5. 前端（tab + 页面 JS）
- **修改** `web/desktop/index.html`：
  - L123 附近加顶栏按钮 `<button class="tab-btn" id="doubanBtn">📚 豆瓣</button>`。
  - 新增 `<section class="main-col content-page" id="doubanPage" hidden>`（仿 travelPage）：含分类子 tab（影视/书/音乐）+ 状态 sub-tab（看过/想看/在看）+ 搜索框 + 条目网格卡片（每条含名、日期、短评、跳原文 `<a href>`）。
  - L1939 后加 `<script src="/web/assets/js/douban-app.js" defer></script>`。
- **新增** `web/desktop/assets/js/douban-app.js`：仿 `ed2k-app.js` IIFE，暴露 `window.initDoubanPage`/`window.reloadDoubanPage`，`fetch("/api/douban/items?...")` 渲染网格，子 tab 切换筛选，点击跳原文。
- **修改** `web/desktop/assets/js/app.js`：
  - `MAIN_PAGE_IDS`（L1228）加 `"doubanPage"`；
  - `tabSync`（L1283区）加 `doubanPage:"doubanBtn"`；
  - `refreshMap`（L1342区）加 `doubanPage:()=>{if(window.reloadDoubanPage)window.reloadDoubanPage();}`；
  - `DESKTOP_PAGE_ROUTES` 加 `douban:()=>openDoubanPage()`；
  - 仿 `openEd2kPage` 加 `openDoubanPage()`；
  - L3838 后 `safeBind("#doubanBtn","click",()=>window.navigateTo("/web/douban"))`。

### 6. 网页侧白名单
- **修改** `src/openbiliclaw/api/_web_ui_routes.py`：
  - assets 版本白名单（L77-87）加 `"douban-app.js"`；
  - `_desktop_page_names`（L109-140）加 `"douban"`。

### 7. 数据导入
- 先运行 `import_douban_db.py` 把 `data/douban/*.json` 1410 条灌入 `data/douban.db`。JSON 目录 `data/douban` 与库并行（JSON 作为源，库作为结构化层）。

### 8. 测试
- **新增** `tests/test_douban.py`（或拆分）：store 用 tmp db 测 import/count/list 去重；routes 用 TestClient mock；adapter 用 mock douban.db。仿 `tests/api/test_api_media.py` 或 `test_api_ed2k.py`。

### 9. 文档（遵循项目强制规则）
- **新增** `docs/modules/douban.md`（概述/已实现功能/API/配置/设计决策）。
- **更新** `docs/changelog.md` 顶部版本块加条目。
- **更新** `docs/modules/config.md`（`[sources.douban]` + `douban_db_path`）、`docs/modules/sources.md`（new source douban）、`docs/architecture.md`（若改跨模块数据流则更新源图）。

## 验证
1. `python -m openbiliclaw.douban.import_data`（或 scripts/import_douban_db.py）导入成功，`douban.db` 有 1410 条、url 去重。
2. TestClient `GET /api/douban/items?category=movie&status=collect` 返回真实清单；`/api/douban/stats` 返回 三类×三状态计数。
3. 服务启动后 `create_app` 成功，`/api/douban` 路由已注册（可 `openbiliclaw config-show` 或前端验证）。
4. 前端桌面顶栏出现「📚 豆瓣」tab，点击进入 doubanPage，加载书影音网格，筛选/搜索正常，点击条目可跳豆瓣原文链接。
5. `pytest tests/test_douban*` 通过；若改 config/接口跑 `mypy src/` + `ruff check src/ tests/`。
6. douban source 因 enabled=false，默认不主动进入发现链路，不影响现有推荐（与小红书 stub 同构）。

## 约束
- douban source **默认 off**，与项目"新增 source 需用户显式启用"的保守取向一致。
- 数据存入 `data/douban.db`（`.gitignore` 已忽略 `data/`），不提交真实 cookie。
- 复用既有模式（health/travel/ed2k），不另造新架构。