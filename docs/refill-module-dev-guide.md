# refill 模块 —— 开发 & 交接文档（给接手实现的开发者）

> 生成时间：2026-09-19 14:45 CST
> 说明：**为「阅读库正文统一回补模块」的实现交接给下一个开发者**
> 前置设计稿（必读）：[docs/refill-module-design.md](refill-module-design.md)
> 语言：中文。本文档全部路径均为绝对路径或相对仓库根。

---

## 1. 交接背景与当前进度

### 为什么做这个模块
阅读库 `data/content.db` 的 `articles` 表有约 **6 万条「有标题无正文」** 的文章（横跨 7 来源）。回补它们的代码散在 5 个目录、各自维护通道/状态/调度，造成重复抓、遗漏、观测不到。本模块把回补统一成 **单模块 + 中央队列 + 可插拔通道 + 单调度 + CLI 观测**。

### 当前进度快照（重要，承接点）
- ✅ 设计稿完成：`docs/refill-module-design.md`（5 个里程碑 M1–M5）
- ✅ **M1 已交付（2026-09-19）**：`RefillQueue`（refill_queue schema / 幂等灌入 / 实时口径 / 状态聚合）+ CLI `refill status` + `[refill]` 配置 + 测试。
- ✅ **M2 已交付（2026-09-19）**：`RefillScheduler`（配额 pick → route → 抓 → 写正文 → 收口，桥接不可用不计数）+ `channels/{base,bridge,direct,search_click}.py`（镜像 web_capture / agentlimb_xhs_batch 内核）+ `writer.py`（写 content_text）+ CLI `refill run/schedule/channel/reset` + `[refill.quota]`。测试 `tests/refill/test_refill_scheduler.py`。
- ✅ **M3 已交付（2026-09-19）**：`channels/ytdlp.py`（YouTube 字幕/简介，复用 `scripts/refill_youtube_subtitles.py`）+ `channels/getnote.py`（得到大脑兜底同步 save，复用 `refill_via_getnote.py`）。调度多基础设施化：AgentLimb 关断只跳过 `direct`/`search_click`、不阻塞 ytdlp/getnote；永久不可抓标 `skipped`。测试 `tests/refill/test_refill_channels.py`。refill 全量 21 passed。
- ✅ **M4 通道已交付（2026-09-19）**：`channels/bili_cli.py`（B站字幕/AI，复用 `scripts/refill_article_bodies.py`）+ `channels/zhihu_api.py`（知乎直连，zhihu-toolkit venv 子进程 + `scripts/content_library/zhihu_api_body.py`）。路由接入 bilibili/zhihu。测试 `tests/refill/test_refill_channels.py` / `test_refill_scheduler.py`（bili 永久→skipped）。refill 全量 31 passed。
- ✅ **M4 收尾已执行（2026-09-19）**：旧脚本 `git mv` 归档到 `06_正文补抓/archive`（5 个，不删除）；`ZhihuApiChannel.script` 默认路径已同步到归档位；PM2 收口为单进程 **`openbiliclaw-refill`**（`start-refill.sh`，cron `5 */2 * * *`，`--no-autorestart`），`xhs-backfill` / `xhs-refill-hourly` 已删除。

### 红线（本任务研发过程中的硬教训）
1. **禁用 `echo "$VAR" | crontab -` 式覆盖**——本项目曾因此**误清空用户整个 crontab**（已恢复：weather/每日提醒/目标规划/time_capsule/eq_tips/ai-builders-sync 等）。改 crontab 前先 `crontab -l > /tmp/bak`。
2. **PYTHONHOME 空串坑**：PM2 ecosystem 里 `env: {PYTHONHOME:""}` 会让 Python 启动崩 `No module named 'encodings'`。正确做法见 §6.1。
3. **不提交敏感数据**：真实 `config.toml`、Cookie、API Key 一律 gitignore / 不入库。

---

## 2. 服务状态（接手时先核对）

```
pm2 list   # 关注：
#  openbiliclaw-api           应用服务
#  openbiliclaw-*-feed/*-favorites   新内容采集 producer（与回补无关，勿改）
#  xhs-backfill        online   cron 5 */2 * * *   ↑本模块 M2 将替换它
#  xhs-refill-hourly   online   cron 0  * * * *    ↑本模块 M2 将替换它
crontab -l # 已恢复的用户定时任务（见 §1 红线1，勿再动）
```

缺口基线（content.db，运行同款 SQL 复核）：
```
SELECT source_type, COUNT(*) FROM articles
WHERE (content_text IS NULL OR length(content_text)=0)
GROUP BY source_type ORDER BY 2 DESC;
-- xiaohongshu 38573 / youtube 19081 / bilibili 1559 / douyin 718 / zhihu 116 / wechat 6 / xiaoyuzhou 3
```

---

## 3. 设计稿锚点（引用，不复制）

| 设计稿章节 | 内容 | 本开发文档对应 |
|---|---|---|
| §3 架构 | Scheduler/queue/Channel 分层 | §4 |
| §4 数据模型 | `refill_queue` DDL、独立 refill.db | §4.2 |
| §5 Channel 协议 | 路由优先级 | §4.4 |
| §7 配置 | `config.toml [refill]` | §4.6 |
| §8 CLI | 子命令签名 | §7 |
| §11 风险 | 复用内核 / 独立库 / 渐进迁移 | §9 |

---

## 4. 实现规格（do-this）

### 4.1 模块骨架与命名
新建 `src/openbiliclaw/refill/`，对齐项目业务模块风格（4 空格缩进、类型注解、简洁 docstring）。文件清单：

```
src/openbiliclaw/refill/
  __init__.py
  queue.py        # refill_queue 建表/迁移 + 读写 + 灌入扫描 + 活跃窗口
  scheduler.py    # 单进程调度: 按平台配额 pick → route → 派发 → 收口
  config.py       # 读 config.toml [refill], 缺省兜底
  channels/
    __init__.py   # 注册表 CHANNELS: list[Channel]
    base.py       # Channel Protocol + 常量(最小正文阈值等)
    direct.py     # 复用 web_capture 内核(登录态 Chrome 直接访问)
    search_click.py  # 复用 agentlimb_xhs_batch 内核(小红书搜索点击)
    getnote.py    # (M3) 复用 refill_via_getnote
    ytdlp.py      # (M3) 复用 refill_youtube_subtitles
    zhihu_api.py  # (M4) 复用 zhihu_api_body
    bili_cli.py   # (M4) 复用 refill_article_bodies
  cli.py          # openbiliclaw refill 子命令实现(收口到 cli/_cmd_refill.py 亦可)
```

### 4.2 refill_queue（独立 refill.db）
```sql
CREATE TABLE IF NOT EXISTS refill_queue (
  id INTEGER PRIMARY KEY,
  source_type TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,            -- 唯一 → 跨进程天然去重
  title TEXT DEFAULT '',
  state TEXT NOT NULL DEFAULT 'pending', -- pending/done/skipped/dropped
  channel TEXT,
  attempts INTEGER DEFAULT 0,
  max_attempts INTEGER DEFAULT 3,
  last_error TEXT,
  fetched_len INTEGER,
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_refill_pick ON refill_queue(state, source_type, updated_at);
```
- **灌入扫描**：首跑与增量跑扫 `content.db.articles` 中 `content_text` 为空者 `INSERT OR IGNORE` 入队（`url` 主键幂等）。密度大，**按平台分批灌、维护活跃窗口**，避免一次面对 6 万条全量调度（见设计稿 §4 补铺策略）。
- **去重锚点**：`content_text` 是唯一成功判据。任一通道成功写 `articles.content_text` → 队项标 `done`；DB 层用 `content_text` 断言兜底，防止并发重复写。

### 4.3 Scheduler 要点
- 单 PM2 进程 `openbiliclaw-refill`，`cron_restart "5 */2 * * *"`（或常驻）。
- `pick()` 按 `config.quota` 轮询多平台，每平台每轮补 `per_cycle` 条后退出，等下次 cron。
- **排水口正确卸载**：启动先检测 AgentLimb bridge/登录态是否可用，不可用则整轮跳过并记日志，不消耗 attempts（对齐既有"基础设施故障不计数"经验）。
- **进程异常退出不丢队**：状态存 refill.db，重启按队列续跑。

### 4.4 Channel 复用映射（关键代码位置）
| Channel | 复用文件（绝对路径） | 复用什么 | 注意 |
|---|---|---|---|
| `direct` | `16_浏览器自动化/web_capture.py` | `_grab(b,url)`、`_sniff(url)`、`_ingest(db,url,a,st,sn)`、`DEFAULT_DB`、`FAKE_SIGNATURES` | 防假命中守卫必须保留；`AgentLimbBrowser` 见 `16_浏览器自动化/agentlimb_eval.py` |
| `search_click` | `二创/xhs_refill/agentlimb_xhs_batch.py` | `fetch_one()` 搜索→CDP 点击→读 `noteDetailMap` 内核、`body_fetch_attempts` 语义 | 仅小红书「无 xsec_token + 有标题」；代理需 `ProxyHandler({})` |
| `getnote` (M3) | `06_正文补抓/02_执行脚本/refill_via_getnote.py` | 得到大脑 save/task/note 轮询链路、`getnote_body_task` 幂等 | 配额 1000/天，注意 00:00 重置 |
| `ytdlp` (M3) | `scripts/refill_youtube_subtitles.py` | `--write-subs --write-auto-subs --write-description` 话、`data/youtube_cookies.txt` 自动探测 | 本机无 PO token，简介兜底；命中率有限 |
| `zhihu_api` (M4) | `scripts/content_library/zhihu_api_body.py` | api.zhihu.com 直连取 content | zhihu-toolkit venv 下运行 |
| `bili_cli` (M4) | `scripts/refill_article_bodies.py` | bili CLI 补抓 | 约 7s/条 |

### 4.5 写入目标（storage 复用）
- `src/openbiliclaw/storage/_article_mixin.py` → `Database.upsert_article(source_type, source_name, title, url, author="", summary="", content_text="", published_at="", tags=None)`：命中已有行时**不更新 author、tags 仅空时覆盖**。
- 回补是"补正文"，核心就一行：`content_text` 有值就 UPDATE articles（也可统一走 `upsert_article` 传 content_text）。

### 4.6 配置（config.toml）
```toml
[refill]
enabled = true
db = "data/refill.db"
[refill.quota]
xiaohongshu = { per_cycle = 1, interval_min = 120 }
youtube     = { per_cycle = 8, interval_min = 60 }
bilibili    = { per_cycle = 6, interval_min = 60 }
zhihu       = { per_cycle = 3, interval_min = 30 }
douyin      = { per_cycle = 2, interval_min = 120 }
[refill.jitter]
max_min = 30
```
- 明确：`xiaohongshu` 配额 `per_cycle=1 / 120min` 是为**防风控**，别按缺口数调大。

---

## 5. CLI 接入指引
- 新增 `src/openbiliclaw/cli/_cmd_refill.py`，命名/结构对齐现有 `_cmd_*.py`（参考 `_cmd_fetch.py` 的 command 注册方式）。
- 命令（与 `openbiliclaw` 主命令风格一致）：
  ```
  openbiliclaw refill status                 # 各平台 待补/进行中/已满/已完成 + 进度
  openbiliclaw refill run --source xiaohongshu --n 1   # 手动补一轮(验证/临时加仓)
  openbiliclaw refill reset --source youtube           # 重置已满 / 重置 attempts
  openbiliclaw refill channel --list                   # 已注册通道及各平台路由
  ```
- 加完跑 `openbiliclaw refill status` 自验（改 CLI 需在改后看终端输出）。

---

## 6. 已知坑 / 经验（实现必读，均在本仓库实测）

### 6.1 PYTHONHOME 空串会崩（本项目已踩）
- PM2 ecosystem `env: {PYTHONHOME:""}` → Python `Fatal Python error: No module named 'encodings'`。
- 根因：空串 ≠ unset。
- 正确做法：用 shell 包装 `exec env -u PYTHONHOME -u PYTHONPATH <venv python> <script> ...`，示例见 `16_浏览器自动化/xhs_backfill.sh`。手动命令行同样加 `env -u PYTHONHOME -u PYTHONPATH`。

### 6.2 macOS TCC：守护进程写不了外接盘中文路径
- cron / launchd 上下文的子进程写 `/Volumes/固态硬盘1T/...`（中文路径）会 `Operation not permitted`；LaunchAgent 甚至 `exit 78 EX_CONFIG`。
- **PM2 常驻进程由用户会话拉起、继承用户权限 → 可写**。所以本项目回补调度一律走 **PM2**，不要走 cron 直接写外接盘。
- 定期盘活（排障）：单测/手动运行用普通 shell 能跑通，不代表 cron 能跑，validate 时注意上下文归属。

### 6.3 防假命中守卫（保留，勿删）
`web_capture.py` 的 `FAKE_SIGNATURES`：token 失效被重定向回站点首页时，标题命中站点签名 → 判定失败、**不写库、不消耗成功数**。小红书签名如「小红书 - 你的生活兴趣社区」。多平台都要维护各自签名。

### 6.4 待补挑选用 RANDOM
`ORDER BY RANDOM()` 随机挑待补条目，避免卡单条过期 token 死循环（`backfill.py::_pending_urls`）。

### 6.5 代理坑
- urllib 调 AgentLimb bridge 需 `urllib.request.install_opener(ProxyHandler({}))`（本机代理 127.0.0.1:58753 会劫持 localhost）。
- 探测 bridge 用 curl 需 `--noproxy '*'`。

### 6.6 其它
- URL 带 `xsec_token` 等处整体加引号、原样传入。
- `16_浏览器自动化/backfill.py` 用 `__file__` 定位仓库根，不依赖 cwd（新 channel 沿用此原则）。

---

## 7. 测试与验收（M1–M2 门禁）

- 单元测试可 mock channel、用临时 refill.db / content.db（符合项目"优先 mock、真实调用留集成"）。
- 命令遵循 `16_` 等脚本不在主干测试范围，但 `src/openbiliclaw/refill/` 属主代码：
  ```
  pip install -e ".[dev]"
  ruff format src/openbiliclaw/refill/ tests/
  ruff check  src/openbiliclaw/refill/ tests/
  mypy src/openbiliclaw/refill/
  pytest tests/ -k refill
  ```
- **验收用例**：
  1. `refill status` 一处看到多平台缺口（M1）
  2. 并发跑两轮同一 URL，不重复写、attempts 正确累计（不重复）
  3. kill -9 调度进程，重启后按队列续跑、不丢队（不漏）
  4. M2 后 PM2 只保留 `openbiliclaw-refill`，`xhs-backfill`/`xhs-refill-hourly` 停用（旧脚本归档不删，可回退）

---

## 8. 里程碑与下一步

| M | 任务 | 完成判据 |
|---|---|---|
| M1 | `refill_queue` schema + 灌入 + CLI `status` | 一处看 6 万缺口 |
| M2 | Scheduler + `direct`/`search_click` 通道, 替换现有两 PM2 | 小红书双路收口, 单进程 |
| M3 | `ytdlp` + `getnote` 通道(YouTube、抖音) | 覆盖缺口前两大 | ✅ 2026-09-19 |
| M4 | zhihu/bili 等迁移, 旧脚本归档 | 全平台单模块 | ✅ 2026-09-19（含 PM2 收口） |
| M5 | 同步 docs: `docs/modules/refill.md` + `reading-library.md` + `cli.md` + `config.md` + architecture + changelog | 文档同步 | ✅ 2026-09-19（architecture/spec/README/reading-library 已同步） |

**下一步（接续我未做的）**：refill M1–M5 已全部落地。若要把 YouTube / B站 / 知乎纳入调度，在
`[refill.quota]` 加对应项即可（默认仅调度小红书）。

---

## 9. 相关文档索引
- 设计稿：`docs/refill-module-design.md`
- 既有补抓方案(历史)：`docs/body-refill-2026-09-11.md`
- CLI 参考：`docs/modules/cli.md`；配置参考：`docs/modules/config.md`；阅读库：`docs/modules/reading-library.md`；存储：`docs/modules/storage.md`
- 浏览器自动化经验：`16_浏览器自动化/README.md`（坑 6.1–6.6 多源于此）
- 架构图/规范：`docs/architecture.md`、`docs/spec.md`；变更日志：`docs/changelog.md`

---

## 10. M4 收尾 —— 停用旧脚本 / 归档（✅ 已于 2026-09-19 执行）

设计规约：旧脚本**归档不删除**（保留可回退）。已执行：

1. 旧脚本 `git mv` 到 `06_正文补抓/archive/`（5 个）：
   `refill_article_bodies.py` / `refill_youtube_subtitles.py` /
   `refill_library_bodies_v2.py` / `zhihu_api_body.py` / `refill_via_getnote.py`。
2. **`ZhihuApiChannel.script` 默认路径已同步**到 `06_正文补抓/archive/zhihu_api_body.py`
   （`channels/zhihu_api.py`）。仅此一处代码引用了被归档脚本路径，已核对。
3. **PM2 收口**：`pm2 start start-refill.sh --name openbiliclaw-refill --cron-restart
   "5 */2 * * *" --no-autorestart --time`；`xhs-backfill` / `xhs-refill-hourly` 已删除。
4. 前置核查：crontab 无 refill 引用（已备份 `/tmp/obc_crontab_backup_20260919.txt`），
   归档脚本不被任何 PM2 / cron 引用，迁移无中断风险。

> 待办：若要把 YouTube / B 站 / 知乎等平台纳入 `refill schedule`，在 `[refill.quota]`
> 加对应项（见 `docs/modules/config.md` §`[refill]`）——默认仅调度小红书。

---

## 建议下一会话使用的技能
| 任务类型 | 推荐技能 |
|---|---|
| 实现 M1 建表/CLI | `/tdd` — 测试驱动开发 |
| 实现 M2 channel 迁移 | `/plans` — 写实现计划 |
| 需求或边界澄清 | `/grill-me` — 深入讨论 |
| 提交/PR | `/git-commit` — Conventional Commits |