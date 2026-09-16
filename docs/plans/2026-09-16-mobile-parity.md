# 移动端功能补全计划（mobile parity）

日期：2026-09-16 ｜ 状态：实施中 ｜ 负责批次：B1→B2→B3→B4

## 1. 背景与目标

用户已通过本地构建的预配置 APK（`mobile/` 源码，基于上游 OpenBiliClaw-mobile
v0.3.158）在安卓手机上使用后端（公网入口 `http://114.66.28.184:25573`，frp 隧道）。
对照 mobile `lib/api/*.dart` 逐条核对后，后端还缺 **11 个端点**，导致 App 的
设置页登录、历史标签、评论楼中楼、平台徽标、对话流式等功能降级或不可用。

本计划分四批补齐，全部遵循既定工程纪律：**零真实请求测试**（MockTransport/假客户端）、
契约以 mobile dart 源码为准、每批真链路冒烟、`pm2 restart 84` 快照纪律。

## 2. 契约对照总表（mobile 请求 → 后端现状）

| mobile 调用 | 方法 | 现状 | 批次 |
|---|---|---|---|
| `/bilibili/auth/status` | GET | ✗ 缺 | B1 |
| `/bilibili/auth/qrcode` | POST | ✗ 缺 | B1 |
| `/bilibili/auth/qrcode/poll?key=` | GET | ✗ 缺 | B1 |
| `/bilibili/auth/import`（cookies+ua+buvid） | POST | ✗ 缺（可复用已有 cookie 同步逻辑） | B1 |
| `/bilibili/auth/session` | DELETE | ✗ 缺 | B1 |
| `/bilibili/video/relation?bvid=` | GET | ✗ 缺 | B1 |
| `/bilibili/video/comment-replies` | GET | ✗ 缺 | B1 |
| `/recommendations/platform-availability` | GET | ✗ 缺 | B1 |
| `/content-history?category=&limit=&cursor=` | GET | ✗ 缺 | B3 |
| `/chat/stream`（SSE） | POST | ✗ 缺 | B4 |
| `runtime-status` / `activity-feed` | GET | ✓ 已有，缺缓存 | B2 |

上游参照物：`/tmp/up_app.py`（origin/main app.py 导出）、`git show origin/main:src/openbiliclaw/bilibili/api.py`。
mobile 契约参照：`mobile/lib/api/*.dart`、`mobile/lib/models/*.dart`。

## 3. B1：手机体验包（7 个端点 + 3 个客户端方法）

### 3.1 bilibili/api.py 新增客户端方法（全部走 `_get_json`，可测）

| 方法 | B 站接口 | 说明 |
|---|---|---|
| `get_video_relation_state(bvid)` | `/x/web-interface/archive/relation?bvid=` | like/coin/favorite/dislike 布尔与计数 |
| `get_video_comment_replies(bvid, root_rpid, page?)` | `/x/v2/reply/reply` | 楼中楼列表（分页字段对齐 mobile 模型） |
| `generate_qrcode()` | `passport.bilibili.com/x/passport-login/web/qrcode/generate` | 返回 qrcode_key + url |
| `poll_qrcode(key)` | `passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=` | 返回原始状态（pending/scanned/confirmed/expired） |

### 3.2 路由（app.py，移动端段，风格与 play-url 一致：现取 cookie、现建客户端、finally 关闭）

- `GET /api/bilibili/auth/status` → `{state: anonymous|logged_in, user:{mid,name,face,vip}, scopes:[], expires_at}`。
  实现：resolve cookie → nav 接口判登录态（`get_nav_info` 已有）。
- `POST /api/bilibili/auth/qrcode` → `{ok, qrcode_key, qrcode_url, expires_in:180, expires_at:""}`。
- `GET /api/bilibili/auth/qrcode/poll?qrcode_key=` → `{ok, status, user, message}`。
  confirmed 时从回调 url 解析 cookie（上游 `_qr_cookie_header` 同款解析），写入凭据走
  **我们已有的 cookie 同步路径**（AuthManager 持久化 + config 镜像 + 运行时重建——比上游
  `_write_source_credential` 更完整），成功后 status=logged_in。
- `POST /api/bilibili/auth/import` → body `{cookies: dict, user_agent, buvid, source}`；
  拼成 cookie 头走同一写入路径，返回与 auth/status 同形的 BilibiliAuthInfo。
- `DELETE /api/bilibili/auth/session` → 清除持久化 cookie（AuthManager 删除 + config 镜像清空 +
  运行时重建），返回 `{ok: true}`。
- `GET /api/bilibili/video/relation?bvid=` → `{ok: true, **relation_state}`。
- `GET /api/bilibili/video/comment-replies?bvid=&root_rpid=&page=` → 上游同形透传。

### 3.3 platform-availability（**不抄上游实现**，只对齐响应契约）

- mobile 模型只吃 `{by_platform: {name: count}, version}` 形状（tabs 徽标 + 自动加载判定）。
- 上游绑定其分叉的 `load_pool_platform_availability_async`；我们在 `pool.db`
  `recommendations` 表上自写 loader（`SELECT platform, COUNT(*) GROUP BY platform`，
  只计 servable 状态），包装成同形异步方法挂 `ctx.database`，缺失时 503（与上游一致）。
- 失败语义照抄上游注释原则：**绝不以全零应答失败**（会让 App 判定「全线无货」）。

## 4. B2：runtime-status / activity-feed 短 TTL 缓存

- 手机每 8s 轮询这两个 GET；上游 419963ed 给了 in-process 短缓存。
- 实现：模块级 TTL 缓存（~2s）挂在现有 handler 前，命中直接返回缓存副本；
  带 `?refresh=1` 绕过。零新依赖，测试断言两次调用只触发一次底层构造。

## 5. B3：content-history 兼容垫层（拍板：垫层方案）

- 契约（mobile `content_history.dart`）：`{category, items[], total, retention_days:30,
  next_cursor, has_more}`；item 字段 `item_key, source_platform, content_id, content_url,
  content_type, title, author_name, cover_url, body_text, recommendation_id, occurred_at,
  context, restored, contexts[]`；category ∈ clicked/shown/removed。
- 我们的存储现状：`events.db.view_history`（bvid/title/source_platform/content_url/up_name/
  viewed_at，当前 2 行）记录了「看过」；「shown」可从 `pool.db.recommendations` 的
  serve/shown 状态取；「removed」对应反馈删除（`/api/feedback` 侧数据）。
- **垫层设计**：`storage` 层新增 `list_content_history_page(category, limit, cursor)`——
  三类各自映射到现有表查询，keyset 游标（`<occurred_at>|<id>` base64，与上游同思路），
  统一投影成上游 item 形状（缺的字段给空串，`contexts` 给空数组）。**不新建表、不迁移数据**。
- `GET /api/content-history` 路由 + `POST /api/recommendation-click` 已存在（mobile
  reportClick 直用），只需核对字段名兼容。
- 已知取舍：垫层的历史深度受限于各表现有数据保留策略，`retention_days` 如实报 30。

## 6. B4：chat-stream SSE（打字机分两步走）

- **发现**：我们 `POST /api/chat/turns` **已经是 durable 设计**（建 pending turn →
  `asyncio.create_task(_complete_durable_chat_turn)` → 立即返回）——与上游架构同款，
  mobile 的 startTurn 直接可用。缺的只是 SSE 推送端。
- `POST /api/chat/stream`（SSE，body 与 startTurn 相同）：
  1. 读 turn 行；pending 则轮询（200ms 间隔）直到 done/failed；
  2. 回复有增长就发 `event: content\ndata: {"delta": "<新增后缀>"}\n\n`
     （当前补完是一次性写入 → 实际表现为一次整段 delta；将来对话层支持真流式时
     **协议不变**，只是 delta 变碎）；
  3. done → `event: done\ndata: {"reply": "<全文>"}\n\n`；failed → `done` 带
     错误前缀文本（mobile 的 catch 分支也会正确处理断连）；
  4. 每 15s 发 SSE 注释行 `: ping`（mobile 解析器忽略，保活公网代理链路）；
  5. 超时上限与补完任务一致（120s+缓冲）。
- 真·打字机（token 级流式）需要 obc-soul/obc_llm 暴露 stream 接口，**本轮不做**，
  协议已为其预留。

## 7. 测试与验收

- 每个端点：路由层测试（假客户端/假 loader）+ 客户端方法 MockTransport 测试；
  SSE 用 httpx ASGI 流式断言事件序列。
- 突变校验：至少覆盖 relation 字段映射、availability 失败不全零、SSE done 序列。
- 门禁：ruff / mypy src / 相关测试面全绿。
- 真链路：`pm2 restart 84`（快照纪律）后逐端点公网冒烟（未登录 401、登录后 200）。
- 交付：重打包预配置 APK（`mobile/` 源码无需改动——纯后端补齐），dist/android/ 更新。

## 8. 风险与边界

- passport 扫码接口无登录态即可调用，但风控敏感——poll 间隔由 mobile 控制（2s），
  后端不加额外节流。
- content-history 垫层的数据深度受现有表保留策略限制；历史标签可能比上游「浅」。
- SSE 经 frp/PassNAT 公网链路，靠 15s 注释行保活；若中间层有读超时，需调大或降频。
- 不动 `openbiliclaw-api` 之外的任何 pm2 进程；所有改动可独立回滚（每批一个 commit）。
