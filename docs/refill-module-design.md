# OpenBiliClaw 阅读库统一回补模块设计（refill-module）

> 状态：M1 ✦ M2 ✦ M3 ✦ M4 通道层已全部按本设计落地（Scheduler + direct/search_click/ytdlp/getnote/bili_cli/zhihu_api）；
> 旧脚本停用/归档为 M4 收尾运维步骤，M5 文档同步随各里程碑进行。
> 关联：`docs/body-refill-2026-09-11.md`（此前补抓方案）、`docs/modules/storage.md`、`docs/modules/reading-library.md`

## 1. 背景与问题

阅读库 `data/content.db` 的 `articles` 表存在大量「有标题无正文」条目。用于给这些条目补 `content_text` 的**回补能力**目前已碎片化散落 5 处，各自维护通道、状态、调度与日志，导致：

- **重复与遗漏并存**：多个脚本可同步选到同一条缺正文文章重复抓取；又可能因调度失效（进程崩溃无人知晓）整体漏补。
- **状态不共享**：`body_fetch_attempts` 只被部分通道写入，其余通道不感知，重试/淘汰边界互不一致。
- **无法统一观测**：各脚本日志落在不同目录、不同格式，没有一处能回答「各平台还剩多少缺正文、进度如何、最近是否在跑」。
- **维护面大**：每加一个平台就要新增一个进程/脚本/调度，极易漂移。

### 1.1 待补缺口（2026-09-19 实测 content.db）

| 来源 | 缺正文 | 现有回补通道 | 说明 |
|---|---:|---|---|
| xiaohongshu | 38,573 | direct + search_click | 现两个 PM2 进程仅覆盖此源 |
| youtube | 19,081 | yt-dlp 字幕/简介 | 曾试 1057 次仍缺，PO token 限制命中率 |
| bilibili | 1,559 | bili CLI | 可用约 7s/条 |
| douyin | 718 | 无 | 需得到大脑兜底 |
| zhihu | 116 | 直连 api.zhihu.com | 可用约 1.6s/条，尝试已满需换通道 |
| wechat / xiaoyuzhou | 9 | autocli / 无 | 少量 |
| **合计** | **≈60,056** | — | — |

### 1.2 现有资产清单（迁移源）

| 位置 | 脚本 | 通道/职责 |
|---|---|---|
| `scripts/` | `refill_article_bodies.py`、`refill_youtube_subtitles.py` | bili/yt 旧直连补抓 |
| `scripts/content_library/` | `refill_library_bodies_v2.py`、`zhihu_api_body.py` | 聚合补抓、zhihu 直连 |
| `06_正文补抓/02_执行脚本/` | `refill_via_getnote.py` | 得到大脑服务端兜底（配额 1000/天） |
| `16_浏览器自动化/` | `web_capture.py`、`backfill.py`、`linuxdo_capture.py`、`xhs_backfill.sh` | 登录态 Chrome 直接访问 + 防假命中（首选） |
| `二创/xhs_refill/` | `agentlimb_xhs_batch.py`、`xhs_cli_batch.py` 等 | 小红书搜索点击通道 |

## 2. 目标与设计原则

- **单一事实来源**：一张中央队列表记录所有「待补/进行中/已满/淘汰」条目及重试、归属通道、最后错误，所有通道只认这张表 → 天然去重、不重不漏、跨进程可见。
- **通道可插拔**：现有各通道收敛为统一 `Channel` 接口，按「平台 + URL 特征」自动路由；新平台只需注册一个 channel。
- **统一调度**：单一 PM2 进程按平台配额轮转补抓，一次 cron，消除「增进程改脚本导致漂移」。
- **可观测**：CLI 一处查看各平台待补/已满/失败、进度、最近补抓结果。
- **渐进迁移、可回退**：先接管小红书 + YouTube 两条线，旧脚本保留为回退通道，验证稳定后逐步停用。

## 3. 架构

```
                          ┌─────────────────────────────────┐
   调度(单 PM2 常驻/cron) │       Refill Scheduler          │
                          │  pick by quota → route → mark    │
                          └──────────────┬──────────────────┘
                                         │ reads/writes  refill.db
                          ┌──────────────▼──────────────────┐
                          │       refill_queue (中央状态)     │
                          │  id/source/url/title/state/       │
                          │  attempts/channel/last_error/... │
                          └──────┬──────────┬──────────┬─────┘
                                 │          │          │
        ┌────────────────────────▼──┐   ┌───▼──────┐   └───▼────────────┐
        │ Channel: direct          │   │ search   │       │ getnote     │
        │ web_capture 登录态 Chrome │   │ agenlimb │       │ 得到大脑     │
        │ 可读抽取 + 防假命中        │   │ 搜索点击  │       │ 服务端兜底    │
        └────────────┬─────────────┘   └──┬───────┘       └───┬──────────┘
                     └──────────────┬───────┘                 │
                                    ▼                        ▼
                          content.db.articles.content_text  (唯一写入目标)
```

关键点：

- **refill_queue 独立放 `refill.db`**（不并入 content.db）：回补是高频写（attempts/状态高频更新），独立库避免与阅读库读写锁竞争；借鉴 `pool.db` 独立子库既有决策。
- **content_text 是唯一去重锚点**：任何通道抓成功即写 `articles.content_text`，队列项随即标 `done`；其他通道下一轮 query 自然不会再选到它。即使队列被并发写，靠 content_text 断言也在 DB 层兜底。
- **通道只做「抓」不决定「选谁」**：选谁由 Scheduler 从队列按配额决定，杜绝多个通道各选各的造成重复。

### 3.1 模块位置

`src/openbiliclaw/refill/`（对齐项目其他业务模块）：

```
src/openbiliclaw/refill/
  __init__.py
  queue.py        # refill_queue 读写、schema、迁移(DDL)
  scheduler.py    # 单进程调度：按平台配额 pick → 分派 → 收口
  channels/
    __init__.py
    base.py       # Channel 协议
    direct.py     # web_capture 登录态 Chrome 直接访问(防假命中)
    search_click.py  # 小红书 标题搜索+CDP点击(复用 agentlimb_xhs_batch 核心)
    getnote.py    # 得到大脑服务端兜底(复用 refill_via_getnote)
    ytdlp.py      # youtube 字幕/简介补抓(复用 refill_youtube_subtitles)
    zhihu_api.py  # zhihu 直连(复用 zhihu_api_body)
    bili_cli.py   # bilibili CLI(复用 refill_article_bodies)
  cli.py          # openbiliclaw refill 子命令
  config.py
```

## 4. 数据模型（refill.db）

```sql
CREATE TABLE IF NOT EXISTS refill_queue (
  id          INTEGER PRIMARY KEY,
  source_type TEXT NOT NULL,          -- 平台标识, 对齐 articles.source_type
  url         TEXT NOT NULL UNIQUE,   -- 唯一 → 天然去重(跨进程也幂等)
  title       TEXT DEFAULT '',
  state       TEXT NOT NULL DEFAULT 'pending', -- pending/done/skipped/dropped
  channel     TEXT,                   -- 命中的通道名
  attempts    INTEGER DEFAULT 0,      -- 当前通道尝试次数
  max_attempts INTEGER DEFAULT 3,     -- 达上限→dropped(可换通道重置)
  last_error  TEXT,
  fetched_len INTEGER,                -- 最近成功补到的正文字数
  created_at  TEXT,
  updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_refill_pick
  ON refill_queue(state, source_type, updated_at);
```

- 回填来源：首次调度扫描 `content.db.articles` 中 `content_text` 为空的条目一次性灌入队列（增量），此后仅跟踪队列。
- 通道换路：某平台本机通道全部 `dropped` 时，Scheduler 可把仍 `pending` 项改挂更长尾通道（如小红书 search_click → direct → getnote），`attempts` 按通道维度累计或重置需在设计中明确。

**补铺策略（首个版本）**：为控制风险，队列灌入时按 `(source_type, 每类上限)` 分批铺，每类平台维护一个「活跃窗口」，只有窗口内的条目参与 pick，避免一次性面临 6 万条全量调度。

## 5. Channel 协议

```python
class Channel(Protocol):
    name: str
    # 该通道能处理哪些来源
    def supports(self, source_type: str, url: str) -> bool: ...
    # 抓正文: 返回 (ok: bool, body: str, detail: str)
    #   ok=True 且 body 达到阈值 → 写 content_text; 否则按 throws 语义回写 attempts
    def fetch(self, item: RefillItem) -> tuple[bool, str, str]: ...
```

平台 → 通道路由优先级（首个版本）：

| 来源 | 通道顺序 | 备注 |
|---|---|---|
| xiaohongshu | search_click → direct → getnote | 无 token+有标题走搜索点击；带 token 或标题空走 direct |
| youtube | ytdlp（字幕→简介兜底）→ getnote | PO token 限制字幕命中率，简介兜底 |
| bilibili | bili_cli → getnote | |
| zhihu | zhihu_api → getnote | |
| douyin | getnote | 本机无通道 |
| wechat/xiaoyuzhou | direct → getnote | |

**风控守恒**：`scheduler` 对每个平台维护独立速率（如小红书每 2h 1 条、direct 通道额外 jitter），全部沿用现有「低密度、随机化、防假命中」经验（见 `16_浏览器自动化/README.md`）。

## 6. 调度

- 单 PM2 进程 `openbiliclaw-refill`，`cron_restart "5 */2 * * *"`（或常驻 + 平台定时器）。
- `scheduler.pick()` 按配额轮询多平台，一次唤醒每个平台补配置数量的条目后退出，等下次 cron。
- 启动、退出全部写 `refill.db` + 统一日志 `~/Library/Logs/refill.{out,err}.log`，`pm2 logs openbiliclaw-refill` 可查。

## 7. 配置项（config.toml / refill.config）

```toml
[refill]
enabled = true
db = "data/refill.db"
# 每平台每轮补抓配额与时隙(分钟)，--sources 可覆盖
[refill.quota]
xiaohongshu = { per_cycle = 1, interval_min = 120 }
youtube     = { per_cycle = 8, interval_min = 60 }
bilibili    = { per_cycle = 6, interval_min = 60 }
zhihu       = { per_cycle = 3, interval_min = 30 }
douyin      = { per_cycle = 2, interval_min = 120 }
[refill.jitter]
max_min = 30        # 随机化首段憩志防风控
```

## 8. CLI

延续 `openbiliclaw` 主命令（见 `docs/modules/cli.md`），新增子命令：

```
openbiliclaw refill status          # 各平台: 待补/进行中/已满/已完成 + 进度
openbiliclaw refill run --source xiaohongshu --n 1   # 手动补一轮(验证/临时加仓)
openbiliclaw refill reset --source youtube           # 重置已满/重置 attempts
openbiliclaw refill channel --list                  # 列出已注册通道及各平台路由
```

## 9. 进度指标与验收

- **统一观测**：`refill status` 一处反映全部平台。
- **不重复**：对任意 URL 并发跑两轮，`content_text` 不会同时被两通道写、队列 attempts 正确累计。
- **不漏**：扫描缺口全量入队，进程重启后按队列续跑（异常退出不丢队）。
- **先验证**：先接 小红书 + YouTube 跑 3 天，观察命中率/风控，再扩散其余平台。

## 10. 里程碑

| M | 内容 | 产出 | 状态 |
|---|---|---|---|
| M1 | `refill_queue` schema + 灌入扫描 + CLI `status` | 能统一看到 6 万缺口 | ✅ 2026-09-19 |
| M2 | Scheduler 单进程 + `direct`/`search_click` 通道（小红书双路） | 替换现有两个 PM2 进程（编排待确认落地） | ✅ 2026-09-19 |
| M3 | `ytdlp` 通道接入 YouTube + `getnote` 备用 | 覆盖缺口第二大平台与无本机通道源 | ✅ 2026-09-19 |
| M4 | 迁移 zhihu/bili，**停用旧脚本**，归档到 `06_正文补抓/archive` | 全平台单模块 | ✅ 2026-09-19（含 PM2 收口为单进程 openbiliclaw-refill） |
| M5 | 更新 `docs/modules/{refill,reading-library,cli,config}.md` + architecture + changelog | 文档同步 | ✅ 2026-09-19 |

## 11. 风险与决策记录

| 风险/决策 | 处置 |
|---|---|
| 复用旧脚本内核 vs 重写 | 复用可证明的抓取内核（web_capture 防假命中、agentlimb 搜索点击），只重写「调度/队列/观测」层，降低回归风险 |
| refill_queue 独立库 | 独立 `refill.db`，避免与 content.db 锁竞争（同 pool.db 决策） |
| 是否继续保留得到大脑 | 作为无本机通道来源(抖音)及主通道失败后的兜底保留 |
| YouTube 字幕命中率 | 维持「字幕→简介兜底」；后续 PO token provider 就绪后再做字幕优先覆盖补抓 |
| 旧脚本处理 | 新模块稳定运行一段时间后归档，**不删除**，保留可回退 |