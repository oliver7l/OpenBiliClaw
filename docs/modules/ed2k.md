# ed2k / Kad 下载管理模块（ed2k）

> 经本机 `mule` CLI 驱动 MLDonkey（Colima + Docker 容器），提供 ed2k/Kad
> 文件的搜索、下载、进度、取消、commit 管理，带一个桌面 SPA 内嵌页面。

## 概述

`ed2k/` 包实现 ed2k / Kad 下载管理。后端封装 `mule` CLI（一个薄 subprocess 前端，
驱动 MLDonkey 经典核心），把搜索/下载/进度/取消/commit 暴露为 `/api/ed2k/*` REST 接口；
前端在桌面顶栏新增「⬇ ed2k 下载」tab → `/web/ed2k`，打开 SPA 内嵌页完成交互。

下载落地目录默认为容器 `incoming/files` 挂载的宿主路径（本机通常为
`009-暂存内容/e2dk下载`），也可用 `[ed2k] download_dir` 覆盖。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 服务 | 封装 mule CLI，统一 subprocess 调用 | `service.py` `MuleService` |
| API | `/api/ed2k/*` REST 接口（慢搜索走线程池） | `routes.py` `build_ed2k_router` |
| 前端(SPA) | 桌面内嵌页 `ed2kPage`：搜索/下载/进度/取消/commit | `desktop/assets/js/ed2k-app.js` |
| 入口 | 桌面顶栏「⬇ ed2k 下载」tab → `/web/ed2k` | `desktop/index.html` + `app.js` |

## 模块结构

```
src/openbiliclaw/ed2k/
├── __init__.py           # 导出 MuleService
├── service.py            # MuleService：net / search / download / downloads / cancel / commit / path
└── routes.py             # build_ed2k_router(prefix="/api/ed2k")
src/openbiliclaw/web/desktop/assets/js/ed2k-app.js   # 桌面 SPA 内嵌页（主子页）
```

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 搜索 | ✅ | `GET /api/ed2k/search?q=&wait=&limit=`；Kad 慢搜索走 `asyncio.to_thread` 不阻塞事件循环 |
| 下载 | ✅ | `POST /api/ed2k/download {ids}`，按最近一次搜索的 id 加入下载 |
| 进度 | ✅ | `GET /api/ed2k/downloads`；前端 3 秒轮询显示 state / 百分比进度条 |
| 取消 | ✅ | `POST /api/ed2k/cancel {ids}`（处理 MLDonkey 确认交互） |
| Commit | ✅ | `POST /api/ed2k/commit`，把完成文件移入落地目录 |
| 网络状态 | ✅ | `GET /api/ed2k/net`：Kad 连接与否 + ed2k 服务器数 |
| 落地路径 | ✅ | `GET /api/ed2k/path`：优先容器 incoming/files 挂载源，可被 `[ed2k] download_dir` 覆盖 |
| 桌面入口 | ✅ | 桌面顶栏「⬇ ed2k 下载」tab → `/web/ed2k` 内嵌页 |

## 公开 API

`build_ed2k_router(*, config)` → `APIRouter(prefix="/api/ed2k")`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ed2k/net` | 网络状态 `{kad_connected, servers, ...}` |
| GET | `/api/ed2k/search` | `?q=&wait=35&limit=25` → `{query, count, results:[{id,name,size,sources,ed2k}]}` |
| POST | `/api/ed2k/download` | `{ids:[int]}` → 按最近搜索 id 发起下载 |
| GET | `/api/ed2k/downloads` | 进行中/已完成下载 `{count, rate, downloads:[{id,state,name,percent,size}]}` |
| POST | `/api/ed2k/cancel` | `{ids:[int]}` → 取消下载 |
| POST | `/api/ed2k/commit` | 把已完成文件移入落地目录 |
| GET | `/api/ed2k/path` | 完成文件落地目录（宿主路径） |

### Python

```python
from openbiliclaw.ed2k import MuleService

s = MuleService()
s.net()                       # 网络状态
s.search("ubuntu", wait=35)   # 搜索结果 dict
s.download([3, 5])            # 按 id 下载
s.downloads()                 # 进行中列表
s.cancel([3])                 # 取消
s.commit()                    # 移入落地目录
s.path()                      # 落地目录
```

## 配置项

`config.toml` 的 `[ed2k]` 段：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `mule_path` | string | `""` | mule CLI 路径；留空时从 PATH 查找（`shutil.which`） |
| `download_dir` | string | `""` | 落地目录；留空时从容器 incoming/files 挂载推断 |

```toml
[ed2k]
mule_path = ""
download_dir = ""
```

## 设计决策

1. **薄 subprocess 封装**：不依赖项目外的 `mule-cli/` 仓库代码（独立仓库），自写
   `MuleService` 镜像其调用方式，保证模块独立可部署；`mule` 通过 `which` 或配置项
   定位。
2. **慢搜索不阻塞事件循环**：Kad 搜索 30-60s，路由声明 `async def` + `asyncio.to_thread`
   跑在线程池，避免占住整个 uvicorn 事件循环。
3. **超时与容错**：每个命令统一捕获 `subprocess.TimeoutExpired / FileNotFoundError`，
   返回友好 error JSON；非 JSON 输出包一层 `{raw, error}` 保证前端可解析。
4. **落地目录推断**：容器可有多个挂载，`path()` 精确匹配 destination 为
   `/var/lib/mldonkey/incoming/files` 的挂载源（`{{println $m.Source}}` 避免换行转义坑），
   `download_dir` 配置可覆盖。
5. **独立模块定位**：ed2k 为纯下载工具，不注入推荐流、不新增 source、不改数据流，
   因此不改变系统架构图；仅新增前端「下载管理」查看页。