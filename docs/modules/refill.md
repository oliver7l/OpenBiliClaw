# refill —— 阅读库正文统一回补模块

> 状态：M1 ✦ M2 ✦ M3 ✦ M4 全部通道已交付（中央队列 + Scheduler + direct/search_click/ytdlp/getnote/bili_cli/zhihu_api）；旧脚本停用/归档为运维交接步骤。
> 设计稿：`docs/refill-module-design.md`；开发/交接：`docs/refill-module-dev-guide.md`。

## 概述

阅读库 `data/content.db` 的 `articles` 表有约 **6 万条「有标题无正文」** 的文章（横跨
小红书 / YouTube / B 站 / 抖音 / 知乎 / 微信 / 小宇宙 等来源）。此前补抓能力碎片化散落
5 处脚本、各自维护通道 / 状态 / 调度 / 日志，造成重复抓、遗漏与无法统一观测。本模块把
回补收敛为 **单模块 + 中央队列 `refill_queue` + 可插拔 Channel + 单调度 + CLI 观测**。

## 已实现功能（M1 ✦ M2 ✦ M3 ✦ M4）

| 能力 | 说明 | 状态 |
|------|------|------|
| `refill_queue` 中央队列 | 独立子库 `data/refill.db`，`url` 唯一 → 跨进程天然去重 | ✅ |
| 缺口灌入扫描 | 扫 `content.db.articles` 中 `content_text` 为空者 `INSERT OR IGNORE` 入队，幂等、可按平台限制单轮灌入量 | ✅ |
| 实时缺口口径 | `count_missing()` 返回 articles 表「仍然缺正文」的真实欠账（与队列解耦） | ✅ |
| Scheduler 调度 | `RefillScheduler`：按配额 pick → route → 抓 → 写正文 → 收口；AgentLimb 关断只跳过依赖它的通道，不阻塞其它 | ✅ |
| `direct` 通道 | 登录态 Chrome 直接访问 + 防假命中守卫（镜像 web_capture） | ✅ |
| `search_click` 通道 | 小红书标题搜索 + CDP 点击读 `noteDetailMap`（镜像 agentlimb_xhs_batch） | ✅ |
| `ytdlp` 通道 | YouTube 字幕/简介（镜像 refill_youtube_subtitles，字幕→简介兜底） | ✅ M3 |
| `getnote` 通道 | 得到大脑服务端兜底（同步 save + 异步 task/note 回收；配额打满熔断） | ✅ M3 |
| `bili_cli` 通道 | B 站字幕口播稿 / AI 总结 + 简介 兜底（镜像 refill_article_bodies） | ✅ M4 |
| `zhihu_api` 通道 | 知乎 api.zhihu.com 直连（zhihu-toolkit venv 子进程，镜像 zhihu_api_body） | ✅ M4 |
| 永久不可抓标 `skipped` | ytdlp/bili 判定真无正文 → 标 `skipped`，不消耗重试 | ✅ |
| CLI | `refill status` / `run` / `schedule` / `channel` / `reset` | ✅ |
| 旧脚本停用 / 归档到 `06_正文补抓/archive` | 迁移运维步骤（待确认执行） | ⬜ M4 收尾 |

## 公开 API

```python
from openbiliclaw.refill import RefillQueue

# 建表（幂等）+ 懒连接
queue = RefillQueue(refill_db_path, content_db_path)
queue.ensure_schema()          # 或 with 块进入一次
queue.fill_gaps()              # 全量灌入，返回各平台新入队数
queue.fill_gaps(limit_per_source=1, sources=("youtube",))
queue.count_missing()          # {source_type: 仍缺正文字数}
queue.status()                 # List[StatusRow]，按入队总量降序
queue.close()
```

`StatusRow` 字段：`source_type` / `pending` / `done` / `skipped` / `dropped`，属性
`total`。队列状态取值 `pending` / `done` / `skipped` / `dropped`；`content_text` 为去重
锚点——任一通道抓成功写回正文后，队项失配下次取值窗口。

调度与通道（M2 ✦ M3 ✦ M4）：

```python
from openbiliclaw.refill import RefillScheduler
from openbiliclaw.refill.channels import build_channels, AgentLimbBridge
from openbiliclaw.refill.writer import build_content_writer

bridge = AgentLimbBridge()                      # AgentLimb HTTP 桥（127.0.0.1:7791）
writer = build_content_writer(content_db_path)  # 写 content_text（挂 cleaner）
sch = RefillScheduler(queue, min_body_len=30, quota=cfg.refill.quota,
                      write_content=writer, bridge=bridge)
summary = sch.run_cycle(sources=("bilibili",))      # {source: {picked/done/...}}
```

- `build_channels(bridge=None)` → `direct / search_click / ytdlp / getnote / bili_cli /
  zhihu_api`。`direct` / `search_click` 依赖 AgentLimb（`requires_bridge=True`）；其余用本机
  子进程、不依赖桥接——`bridge=None` 即可纯 ytdlp/getnote/bili_cli/zhihu_api 调度。
- 路由见 `channels.base.route_channels`：小红书 `search_click→direct→getnote`、YouTube
  `ytdlp→getnote→direct`、抖音 `getnote→direct`、B站 `bili_cli→getnote→direct`、知乎
  `zhihu_api→getnote→direct`、其余 `direct`。
- 语义：抓成功且正文 ≥ `min_body_len` → 写正文、标 `done`；detail 以 `PERMANENT` 开头
  （YT 无字幕 / B站无字幕AI简介等真不可抓）→ 标 `skipped`；否则 `attempts+1`，达
  `max_attempts` 置 `dropped`；基础设施故障抛 `BridgeUnavailableError` → 不计数、跳过该条继续。
- 外部依赖：`bili_cli` 需本机 `bili` CLI；`zhihu_api` 需 `zhihu-toolkit` venv python 与
  `scripts/content_library/zhihu_api_body.py`；`getnote` 需本机 getnote CLI 与配额。

## 配置项

见 `docs/modules/config.md` §`[refill]`：`enabled`（默认 `true`）、`db`（默认
`"refill.db"`，相对 `data` 目录），以及 M2 的 `min_body_len`、`[refill.quota]`、
`jitter_max_min`。

## 设计决策

- **独立子库 `refill.db`**：回补是高频状态写，独立库避免与阅读库 `content.db` 锁竞争
  （同 `pool.db` 既有决策）。
- **`content_text` 唯一去重锚点**：即使队列并发写，也靠 DB 层对 `content_text` 的断言兜底。
- **通道只「抓」不「选谁」**：选谁由 Scheduler 从队列按配额决定，杜绝多通道各自选各自导致的重复。

## 关联

- 设计稿：`docs/refill-module-design.md`
- 开发交接：`docs/refill-module-dev-guide.md`
- 历史补抓方案：`docs/body-refill-2026-09-11.md`
- 阅读库 / 存储：`docs/modules/reading-library.md`、`docs/modules/storage.md`