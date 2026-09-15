# 稍后读/收藏的原生同步（saved_sync）

> 「本地先记住，再帮你同步到平台」这条链路的实现层：本地成员表 → 同步任务 →
> 平台适配器 → 状态机。**2026-09-15 之前它是一条不可达路径**（见下文「历史」）。

## 概述

本地保存（`saved_memberships`，正本见 `docs/modules/storage.md`）是**先行的**、永不失
败的一步；原生同步是**可选的第二步**：把条目推送到 B 站「稍后再看」/ 收藏夹等平台自
带的位置。两者的状态分开记：成员表记「已保存」，`native_save_states` 记「同步到平台
的进展」。

| 组件 | 文件 | 职责 |
|---|---|---|
| 身份 | `identity.py` | `item_key` 生成（`platform:content_id`，无 id 时用 URL 的 sha256 前 24 位）；平台别名归一（`bili`→`bilibili`…） |
| 模型 | `models.py` | `SavedItemInput` / `NativeSaveCapability` / `NativeSaveRoute` / `NativeSaveResult` |
| 路由 | `router.py` | 按平台能力解析「请求动作 → 落定动作 + 目标文案」；能力不足抛 `UnsupportedNativeSaveError` |
| 适配器 | `adapters.py` | **真实现平台动作**（B 站服务端 cookie 重放；需扩展的平台如实回报） |
| 服务 | `service.py` | 任务落库、申领/心跳/超时/分离看门狗、结果归一化 |
| 路由层 | `api/saved_sync_routes.py` | `/api/saved/*`、`/api/reading/*`、`/api/read-archive/*` |
| 接线 | `api/runtime_context.py` 第 12 步 | 用 `build_native_save_router(...)` 构造（**不许空构造**） |

## HTTP 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/saved/{list_kind}` | 列出本地成员（`list_kind` ∈ `favorite` \| `watch_later`，单数！） |
| POST | `/api/saved/{list_kind}` | 保存一条（本地先行；`auto_sync` 开关见「已知缺口」） |
| POST | `/api/saved/{list_kind}/remove` | 移除本地成员 |
| GET | `/api/saved/{list_kind}/status` | 单条状态（含原生同步状态） |
| **POST** | **`/api/saved/{list_kind}/sync`** | **创建同步任务**，body `{"item_keys": ["bilibili:BV1xx", ...]}` |
| **GET** | **`/api/saved-sync/tasks/{task_id}`** | 查任务进展/结果 |
| GET | `/api/reading/{items,count,sources,search}` | 阅读库条目（与 `reading_routes.py` 平分同一前缀） |
| PATCH | `/api/reading/items/{id}/{tags,status}` | 改标签/状态 |
| GET | `/api/read-archive/...` | 已读库 |

## 状态机（词表唯一事实在 `storage/_saved_sync_vocab.py`）

```
pending → syncing → ┬─ synced          成功写入平台
                    ├─ already_synced  平台上已有（本次不再写）
                    ├─ login_required  Cookie 缺失/失效 → 用户去登录
                    ├─ extension_required  该平台需要浏览器扩展
                    ├─ rate_limited    被风控/限速 → 稍后重试
                    ├─ unsupported     平台没有原生保存（或该动作不支持）
                    └─ failed          其余失败（error_code/message 带真实原因）
```

七种终态**语义不同**，这正是设计意图：用户看到 `extension_required` 会去开扩展，看到
`login_required` 会去重新登录，看到 `unsupported` 才知道这个平台根本不行。

## 平台能力与实现方式（2026-09-15 落地）

| 平台 | 稍后再看 | 收藏 | 实现方式 | `requires_extension` |
|---|---|---|---|---|
| `bilibili` | ✅ | ✅ 默认收藏夹 | **服务端 cookie 重放**（`BilibiliAPIClient`） | `False` |
| `youtube` | ✅ | ✅ 播放列表 | 需浏览器扩展 → `extension_required` | `True` |
| `xiaohongshu` | ❌ | ✅ | 需浏览器扩展 → `extension_required` | `True` |
| `douyin` | ❌ | ✅ | 需浏览器扩展 → `extension_required` | `True` |
| 其它（`weibo`/`reddit`/`v2ex`…） | ❌ | ❌ | 无适配器 → `unsupported`（`weibo` 是设计上的 local-only） | — |

清单的**唯一事实**是 `adapters.native_save_platforms()` / `EXTENSION_SAVE_TARGETS`，
测试会核对它和实际注册的适配器一致（防止「文档说支持、代码没注册」）。

B 站写接口（`bilibili/api.py`）：

| 方法 | 端点 | 备注 |
|---|---|---|
| `is_in_watch_later` / `add_to_watch_later` | `GET /x/v2/history/toview` / `POST /x/v2/history/toview/add` | 先查重再写 |
| `resolve_default_favorite_folder_id` | `GET /x/v3/fav/folder/created/list-all` | 按标题找「默认收藏夹」 |
| `is_favorited` / `add_to_favorites` | `GET /x/v3/fav/resource/list` / `POST /x/v3/fav/resource/deal` | 需要数字 **aid**（先 `get_video_aid`） |

写入失败码映射（**不猜码**）：`-101` → `login_required`；`-412`/`-799`/`-509` →
`rate_limited`；其余 → `failed` 并把真实 code/message 原样上报。cookie 里必须有
`bili_jct`（csrftoken），缺失直接判 `login_required` 而不是发一个注定失败的请求。

## 安全阀（三条都是「宁可不做，也不做错」）

1. **绝不静默挑收藏夹**：解析不到「默认收藏夹」就 `failed` +
   `favorite_folder_unresolved`，不写进第一个收藏夹。写错夹子是「没报错但数据到了错
   地方」的典型缺陷。
2. **写前先查重**：命中已有条目直接 `already_synced`，**不发写请求**（测试里有明确
   断言：`add_to_watch_later` 不出现在调用序列里）。
3. **Cookie 每次现取**：`build_native_save_router` 注入
   `resolve_runtime_cookie`（配置优先，回落 `data/bilibili_cookie.json`），所以浏览器里
   重新登录后**不需要重启服务**。

## 历史与接线（为什么会「活着的死代码」）

- 2026-09-15 及以前：`runtime_context` 里 `NativeSaveRouter()` **空构造**，全仓没有一处
  `register(...)` ⇒ `route()` 对所有平台抛 `UnsupportedNativeSaveError` ⇒ 857 行
  `service.py` 的申领/心跳/超时机制**一次都没跑到过 adapter**；佐证是
  `native_save_states` 恒 0 行。
- 2026-09-15 晚：补 `adapters.py` 并把构造收进 `build_native_save_router()`（测试可直
  接断言「每个平台都能解析出 route」），同时加了一条 **AST 静态防回归**：只要
  `runtime_context.py` 再出现空构造的 `NativeSaveRouter()` 就红灯。

## 测试

`tests/saved_sync/test_native_save_adapters.py`（54 例）——**全部用假客户端 /
`httpx.MockTransport`，一个真实请求都不发**：这条路径会写用户账号，测试里绝不允许真
实副作用。覆盖：平台清单与注册一致、写前查重（不发写）、失败分类（登录/限流/未知码/
网络/缺 bvid）、客户端必定关闭、`extension_required` 语义、结果终态契约、
`runtime_context` 不许空构造、Cookie 每次现取（改盘后第二次保存能读到新值）。

鉴别力校验：8 组故意改错各自让对应用例变红（去掉查重 / 收藏夹退化成取第一个 / 扩展
平台回 `unsupported` / 不关客户端 / 缺 csrf 不报错 / 恢复空构造 / 收藏夹按 title 匹配
删掉 / 结果非终态）。

## 已知缺口

- **`auto_sync_enabled` 永远不会为真**：路由里读的是 `ctx.config.saved_sync`，但
  `config.py` **没有 `SavedSyncConfig`**（`config.toml` 也没有 `[saved_sync]` 段），
  `getattr(..., None)` 恒为 `None` ⇒ 自动同步从不触发，目前只有
  `POST /api/saved/{kind}/sync` 这条手动路径可用。要开自动同步需要新建配置段（未做）。
- **`favorite` 的目标固定为「默认收藏夹」**：没有「选哪个夹子」的配置项（`supports_named_collection`
  已声明为 True，但界面/配置还没接）。
- **需扩展的平台只能回报 `extension_required`**：扩展侧尚未实现「代理原生保存」，
  所以那三种平台目前只是**如实告知**，不是能用。
- **前端**：桌面端有「稍后再看/收藏」入口；移动端只有 `watchLater/favorites/conversation`
  三块，同步状态展示未核对。
- **孤儿方法**：`SavedSyncService.validate_native_save_selection`（解析单条路由、不落任务）
  全仓无调用方——原本大概是给「保存前预览目标」用的，界面没接。
- **存量状态**：`native_save_states` 现有 2 行（`bilibili:mHgX-YXy2P0` 的 favorite /
  watch_later，均为 `pending`），是 2026-09-15 的稍后读迁移时按 `ensure_native_save_state`
  补出来的，从未真正跑过同步。
- 与 `reading`/`notes`/`conversation_archive` 的边界（「阅读库三套并存」）见
  `docs/module-review-2026-09-15.md` §4 R1，收敛方向已于 2026-09-15 拍板为「统一到
  `content.db.articles`」。
