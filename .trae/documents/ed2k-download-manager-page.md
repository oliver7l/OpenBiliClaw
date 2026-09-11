# 新增「ed2k/下载管理」标签页

## Context
项目已在本机搭建好 ed2k/Kad 下载系统：`mule` CLI（`/Users/imac/bin/mule`）驱动 Colima + Docker 里的 MLDonkey 经典核心，搜索/下载/进度/取消/commit 均由 `mule` 命令完成，下载落地目录为 `/Volumes/固态硬盘1T/009-暂存内容/e2dk下载`。

现要在项目前端顶部导航栏新增一个「ed2k/下载管理」标签页，让用户能在 Web 界面里：搜索 ed2k 文件 → 启动下载 → 查看进行中下载进度 → 取消 → commit 到本地目录。

用户已确认：**独立 web 页面，类似 `/web/interview` 的接入方式**（SPA 内新 section + 独立后端 API，顶部 tab 用 `navigateTo('/web/ed2k')`）。

## 后端

### 新模块 `src/openbiliclaw/ed2k/`
两文件：`service.py` + `routes.py`。

**service.py — `MuleService`**
不依赖项目外部的 `mule-cli/mcp/server.py`（路径在项目外、属独立仓库），自写约 30 行薄封装，镜像其 subprocess 调用方式：
- 定位 mule：`shutil.which("mule")`，或 `config.ed2k.mule_path` 覆盖
- 统一执行：`subprocess.run([...], capture_output=True, text=True, timeout=n)` → `(ok, text)`
- 方法：
  - `search(q, wait=35)` → `["mule","search",q,"--wait",str(wait),"--limit","25","--json"]`，timeout=wait+60
  - `download(ids)` → `["mule","download",*ids,"--json"]`，timeout=60
  - `downloads()` → `["mule","downloads","--json"]`
  - `cancel(ids)` → `["mule","cancel",*ids]`
  - `commit()` → `["mule","commit"]`
  - `net()` → `["mule","net","--json"]`
  - `path()` → 优先 `docker inspect mule-mldonkey --format '{{range .Mounts}}{{.Source}}{{end}}/incoming/files/'`，失败回退 `config.ed2k.download_dir`
- 对命令输出做 `json.loads` 容错（非 JSON 时返回 `{"raw": text, "error": ...}`）

**routes.py — `build_ed2k_router(config)`**
参考 `src/openbiliclaw/media/routes.py` 的分层写法，返回 `APIRouter(prefix="/api/ed2k")`：
- `GET /search?q=&wait=35` — 搜索（路由标记 `async def`，内部 `await asyncio.to_thread(...)` 跑慢搜索，不阻塞事件循环）
- `GET /downloads` — 进行中列表
- `GET /net` — 网络状态（Kad/ed2k 连接数）
- `GET /path` — 下载落地目录
- `POST /download`（body `{ids:[int]}`）
- `POST /cancel`（body `{ids:[int]}`）
- `POST /commit`

所有端点捕获 `subprocess.TimeoutExpired / FileNotFoundError`，超时/缺 mule 时返回友好 4xx/5xx JSON，不抛裸异常。

### 路由注册
- `src/openbiliclaw/api/_route_registry.py` 的 `register_all_routes()` 末尾仿 media 加：
  ```python
  from openbiliclaw.ed2k.routes import build_ed2k_router
  app.include_router(build_ed2k_router(config=config))
  ```
- `src/openbiliclaw/api/_web_ui_routes.py` 的 `_desktop_page_names` 集合加 `"ed2k"`（使 `/web/ed2k` 返回 SPA shell）。

### 配置
`config.example.toml` 增加 `[ed2k]` 段：`mule_path = ""`、`download_dir = ""`（均可为空，空时用 `which`/内置默认，不求强制配置）。`config.py` 加 `Ed2kConfig` dataclass 并在 `Config` 暴露 `self.ed2k`。

## 前端（SPA 内嵌，interview 同构）

### 1. `src/openbiliclaw/web/desktop/index.html`
- 顶部 tabBar（约 73-123 行）新增一个按钮：
  ```html
  <button class="tab-btn" id="ed2kBtn" type="button">⬇ ed2k 下载</button>
  ```
- 新增 `<section class="main-col" id="ed2kPage" ... hidden>` 页面容器（仿 interviewPage）：搜索框、结果列表、进行中下载列表、取消/commit 按钮、落地路径展示。

### 2. `src/openbiliclaw/web/desktop/assets/js/app.js`
- `MAIN_PAGE_IDS` 数组加 `"ed2kPage"`（约 1228 行）。
- `DESKTOP_PAGE_ROUTES` 加：`ed2k: () => openEd2kPage()`（约 1374 行）。
- 新增 `openEd2kPage()`：`closeMobileMenu()` → 关面板 → `showMainPage("ed2kPage")` → 调用渲染函数 → `scrollTo`（仿 `openInterviewPage`，1464 行）。
- tab 绑定（约 3823 行 mediaBtn 附近）加：`safeBind("#ed2kBtn","click",()=>{ window.navigateTo("/web/ed2k"); })`。

### 3. 页面逻辑（内联在 app.js 或独立 `web/desktop/assets/js/ed2k-app.js`）
独立 js 更符合本项目惯例（media-app.js、health-app.js 皆如此），故新建 **`assets/js/ed2k-app.js`**：
- `window.openEd2kPage()` / `window.reloadEd2kPage()` 渲染函数，由 `openEd2kPage` 调用，并在 `refreshMap`（app.js 1341 行附近）加 `ed2kPage: () => {...}`。
- 交互：
  - 搜索 → `fetch('/api/ed2k/search?q=...')` → 渲染结果列表（文件名 + 来源数 + 下载按钮）
  - 下载 → `POST /api/ed2k/download` `{ids}`
  - 进行中 → `GET /api/ed2k/downloads` 每 3s 轮询显示 percent/state，每行取消按钮
  - cancel → `POST /api/ed2k/cancel`
  -「Commit」→ `POST /api/ed2k/commit`
  - 顶部展示落地路径（`GET /api/ed2k/path`）
- 需把 `ed2k-app.js` 加进 `_web_ui_routes.py` 的 script 元组（约 77-86 行），以启用版本号缓存控制。

## 验证
1. `pip install -e ".[dev]"`（如已装则跳过）
2. 启动服务：`openbiliclaw start`（或项目既有启动方式，桌面应用端口 8420）
3. 终端连通性：`mule net --json`、`mule search "test" --wait 8 --json`
4. API：`curl` 验证 `/api/ed2k/net`、`/api/ed2k/search?q=...`
5. 浏览器打开 `/web/ed2k`，走通「搜索 → 下载 → downloads 查进度 → cancel/commit」闭环
6. 顶部 tab「⬇ ed2k 下载」跳转正常
7. 跑变更相关的单元测试即可（按用户偏好，不跑全量 lint）

## 文档（按 AGENTS.md 强制要求）
- `docs/changelog.md` 顶部追加版本块 + 描述
- `docs/modules/` 新增 `ed2k.md`（概述→已实现功能表格→公开 API→配置项）
- `docs/modules/cli.md` 无需改（未变 CLI）；若改了 config 字段，同步 `docs/modules/config.md`