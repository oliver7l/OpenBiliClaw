# 本地媒体浏览模块（media）

> 浏览用户在 `[media] roots` 配置的本地媒体目录（视频 + 图片），提供
> `/media` 独立页面（3 列卡片 + 图片灯箱 + 视频播放）与后端 `/api/media*` 接口。

## 概述

`media/` 包实现本地媒体浏览系统，作为一个**独立查看器**：读取用户本机媒体根目录，
分页列出视频/图片、逐层进入子目录、图片灯箱轮播、HTML5 视频播放（支持 Range 拖动）。
模块不注入推荐流、不新增数据源，仅做本地文件浏览/播放。

页面形态：以**桌面 SPA 内嵌视图**为主（`mediaPage`，与专题/健康/旅行等页一致的
`card-grid.is-minimal` 3 列小白卡 + 左对齐 subtab），桌面顶栏「🎬 媒体」tab 直达
`/web/media`；同时保留独立 `/media` 页（可书签直达的兜底实现）。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 服务 | 目录扫描、安全路径解析、分页列表 | `service.py` `MediaService` |
| 状态存储 | 收藏/评级持久化（`media_state.db`） | `store.py` `MediaStateStore` |
| API | `/api/media/*` REST 接口 | `routes.py` `build_media_router` |
| 前端(SPA) | 桌面内嵌页 `mediaPage` + 卡片/灯箱/视频 | `desktop/assets/js/media-app.js` |
| 前端(独立) | `/media` 独立页（兜底） | `src/openbiliclaw/web/media/index.html` |
| 入口 | 桌面顶栏「🎬 媒体」tab → `/web/media` | `desktop/index.html` + `app.js` |
| 测试 | `tests/api/test_api_media.py`（15 例） | 路由级 TestClient 用例 |

## 模块结构

```
src/openbiliclaw/media/
├── __init__.py           # 导出 MediaService
├── service.py            # MediaService：roots_meta / list_items / resolve_file / resolve_root
└── routes.py             # build_media_router → /api/media/*
src/openbiliclaw/web/
└── desktop/assets/js/media-app.js    # 桌面 SPA 内嵌页 mediaPage（主入口，/web/media）
```

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 根目录管理 | ✅ | `[media] roots` 多根；页面内「+ 添加目录」一键追加并持久化 |
| 根元信息 | ✅ | 每个根的存在性 + 顶层视频/图片数量（`os.scandir` 非递归计数） |
| 媒体列表 | ✅ | 分页（offset/limit）、类型过滤（all/dir/video/image）、文件名搜索（忽略大小写） |
| 目录筛选 | ✅ | 「目录」分类单列子目录；切到「视频/图片」时不混入目录，目录与文件分开浏览 |
| 子目录导航 | ✅ | 逐层进入子目录 + 面包屑返回；目录始终展示便于下钻 |
| 图片浏览 | ✅ | 卡片 `<img loading="lazy">` → 点击灯箱轮播（上一张/下一张） |
| 视频播放 | ✅ | 灯箱 `<video controls autoplay>`，后端 Range 流式传输，可拖动进度条 |
| 视频封面 | ✅ | 卡片显示首页帧缩略图（ffmpeg 抽帧 + `data/media_thumbs` 缓存）；失败回退播放图标 |
| 格式提示 | ✅ | 浏览器不支持的容器（mkv/avi/flv/wmv/ts）在播放器内给出转码提示 |
| 安全校验 | ✅ | 文件访问仅限已配置根目录内，`../` 穿越与未配置根一律 404 |
| 收藏 | ✅ | 卡片左上角 ☆/★ 一键收藏/取消，持久化在 `media_state.db`；「只看收藏」视图汇总全根目录收藏 |
| 评级 | ✅ | 卡片 1-5 ★ 点击评级/清空（点同值清除），列表富化实时回显 |
| 随机播放 | ✅ | 「🎲 随机播放」从当前列表随机起播，播完自动连播下一个随机；关闭弹窗停止 |
| 删除 | ✅ | 卡片右上角 🗑 删除，经确认后**移入回收站**（`data/media_trash/`）可找回，并清收藏/评级与封面缓存；穿防止 `../` |
| 桌面入口 | ✅ | 桌面顶栏「🎬 媒体」tab → `/web/media` 内嵌视图（与专题/健康/旅行同款布局） |

## 公开 API

`build_media_router(*, config, config_save_lock=None, config_path=None)` → `APIRouter(prefix="/api/media")`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/media/roots` | 根目录元信息 `[{path, name, exists, is_dir, video_count, image_count}]` |
| POST | `/api/media/roots` | 添加根目录 `{path}`，追加到 `config.media.roots` 并持久化；返回 `{roots, added}` |
| GET | `/api/media/list` | `?root=&kind=&q=&sub=&offset=&limit=` → `{items, has_more, offset}` |
| GET | `/api/media/file` | `?root=&path=` → 单文件流式返回（支持 HTTP Range），未知格式回退 `application/octet-stream` |
| GET | `/api/media/poster` | `?root=&path=` → 视频首页帧缩略图（ffmpeg 抽帧 + 按 路径/mtime 缓存），无法生成 404 |
| GET | `/api/media/item` | `?root=&path=` → 单个文件收藏/评级状态 `{favorite, rating}` |
| POST | `/api/media/item` | `{root, path, favorite?, rating?}` → 写收藏/评级（至少一个），返回新状态 |
| GET | `/api/media/favorites` | `?root=&kind=&q=` → 收藏列表 `{items, total}`（跳过已不存在的文件） |
| DELETE | `/api/media/item` | `?root=&path=` → 删除文件：移入 `data/media_trash/` 并清状态/封面缓存 |

- 根目录参数必须是配置中已存在的**绝对路径**；`path` 为根目录内相对路径。
- 未命中根/文件/越界访问均返回 404（detail 说明原因）。

### Python

```python
from openbiliclaw.media import MediaService

svc = MediaService(["/Volumes/.../Telegram Desktop"])
svc.roots_meta()                     # 根元信息
svc.list_items(root, kind="video", q="", sub="", offset=0, limit=60)
svc.resolve_file(root, "photo.jpg")  # → 绝对 Path（越界抛 MediaNotFoundError）
```

## 配置项

`config.toml` 的 `[media]` 段：

| 键 | 类型 | 默认值 | 说明 |
|----|------|--------|------|
| `roots` | string[] | `[]` | 本地媒体根目录绝对路径列表；可在 `/media` 页面内「添加目录」追加 |

```toml
[media]
roots = [
  "/Volumes/固态硬盘1T/009-暂存内容/115网盘下载",
  "/Volumes/固态硬盘1T/009-暂存内容/Telegram Desktop",
]
```

## 设计决策

1. **非递归扫描**：媒体根目录可能极大（GB 级视频），递归遍历会阻塞首屏。
   仅 `os.scandir` 顶层；子目录由用户逐层进入，每层同样只扫描当前目录。
2. **安全路径解析**：`resolve_file` 把相对路径 `resolve()` 后与根目录做共同前缀校验，
   杜绝 `../` 目录穿越与越界读取；未配置根目录也一律拒绝。
3. **Range 流式播放**：`/api/media/file` 走 Starlette `FileResponse`，原生支持 HTTP
   Range，视频拖动进度条不变全量下载；超大文件按需切块。
4. **浏览器格式限制**：HTML5 `<video>` 仅能播 mp4/webm/mov/m4v 等常见编码；
   mkv/avi/flv/wmv/ts 仍列出，播放器内给出转码提示，不额外引入 ffmpeg 依赖。
5. **视频封面对按需抽帧 + 缓存**：`/api/media/poster` 用 ffmpeg 抽首页帧生成
   `640` 宽 JPEG，缓存到 `data/media_thumbs/<md5(路径|mtime|size)>.jpg`，随文件
   mtime/size 变化自动失效；无 ffmpeg 或解码失败返回 404，前端回退播放图标。
   ffmpeg 通过 `shutil.which` + 常见 homebrew 路径探测，无硬编码依赖。
6. **配置驱动 + 页面可写**：根目录以 `config.media.roots` 为权威；「添加目录」通过
   `save_config` 持久化（受 `config_save_lock` 保护），页面与配置保持"改完立即可用"。
7. **独立查看器定位**：媒体浏览为纯本地文件浏览/播放，不注入推荐流、不新增 source、
   不读取 B 站等站点数据，因此不改变现有数据流与架构图。
8. **收藏/评级独立子库**：`media_state.db` 独立存放收藏/评级（`MediaStateStore`），
   与主库/推荐池锁域隔离，避免低频繁的收藏写与推荐流写互相加锁。以文件绝对路径的
   md5 为主键，文件被移动则视为新条目；`/list` 批量富化状态，前端即时回显。
9. **删除可恢复**：删除不直接 `unlink`，而是把文件移到 `data/media_trash/<根目录名>/<相对路径>`
   保留结构（不污染媒体根目录列表），并同步清收藏/评级与封面缓存；误删可从回收站手动找回。