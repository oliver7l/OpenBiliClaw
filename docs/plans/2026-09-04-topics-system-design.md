# 专题（Topics）系统设计

> 2026-09-04 · OpenBiliClaw 内容专题：持续搜集用户感兴趣方向的内容，多专题并存，前端可查看。

## 1. 背景与目标

用户对「广告」方向产生持续兴趣（与求职广告算法/推荐/数据科学方向一致），并要求：
1. 持续搜集广告相关内容（多网站）；
2. 建立**专题**（topic）机制，后续可建多个专题（如《去有风的地方》）；
3. **前端可查看**专题与条目。

约束（用户明确）：**不要占用浏览器** —— 曾经尝试 autocli 的 douban/xiaohongshu/weibo 浏览器自动化通道，用户反馈会抢占正在使用的 Chrome，已全部移除。

## 2. 数据模型

```sql
topics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,      -- 专题名（广告 / 去有风的地方）
    slug        TEXT NOT NULL UNIQUE,      -- URL 标识（advertising / youfeng）
    description TEXT DEFAULT '',
    keywords    TEXT DEFAULT '[]',         -- JSON 关键词列表（搜集+匹配用）
    platforms   TEXT DEFAULT '["bilibili"]', -- JSON 来源通道（bilibili / rss）
    status      TEXT NOT NULL DEFAULT 'active',
    item_count  INTEGER NOT NULL DEFAULT 0,
    created_at / updated_at / last_collected_at TIMESTAMP
);
topic_items (
    id, topic_id REFERENCES topics ON DELETE CASCADE,
    content_key TEXT NOT NULL,             -- 平台:ID 归一化（幂等去重键）
    title / url / source_platform / source_name / cover_url / summary / topic_label,
    collected_at TIMESTAMP,
    UNIQUE(topic_id, content_key)
);
```

## 3. 搜集通道（纯 CLI，零浏览器）

| 通道 | 实现 | 覆盖来源 | 说明 |
|---|---|---|---|
| `bilibili` | `BilibiliAPIClient.search()`（项目自带 B 站官方 WBI 搜索 API） | B站视频 | 带用户 cookie + v_voucher 反风控退避 + 限速；每关键词最多 `limit` 条 |
| `rss` | `urllib` + `feedparser` 抓取公开 RSS | 少数派 / InfoQ / 极客公园 / IT之家 | 按专题关键词匹配标题与摘要，每源最多 `limit` 条 |
| `pool` | 直接匹配**项目内容池已有多来源内容** | content_cache 11 来源（zhihu/youtube/bilibili/xiaohongshu/v2ex/douyin/xiaoyuzhou/wechat/reddit…）+ articles 阅读库（8 万+ 条，youtube/zhihu/v2ex/xhs/虎嗅/小宇宙…） | **零网络请求、零浏览器**，一次跑完立即覆盖全平台；按关键词匹配 title/description/topic_group（content_cache）与 title/summary/tags（articles） |
| `csdn` | `so.csdn.net/api/v3/search` 公开 JSON API | CSDN 博客 + 资源 | 纯 HTTP 无签名；过滤 blog/article/download 类型 |
| `hot` | 60s.viki.moe 公开 API（用户本机 `008-zhihu` 同源） | 知乎实时热榜 + 微博热搜 | 按关键词匹配标题，给专题提供「正在发生」的新鲜内容；命中稀疏属预期 |
| `zhihu-cli` | 用户 pipx 安装的 `zhihu` CLI（知乎官方逆向 API） | 知乎搜索（文章/回答/问题） | 登录态由 CLI 管理，不碰浏览器窗口 |
| `xhs-cli` | 用户 pipx 安装的 `xiaohongshu-cli`（0.6.4） | 小红书笔记搜索 | cookie 由 CLI 从 Chrome 提取（已认证），触发验证码时失败跳过 |
| `bili-cli` | 用户 pipx 安装的 `bilibili-cli`（0.6.2） | B站视频搜索 | 与项目自带 WBI API 通道互补（搜索词一致） |
| `rdt-cli` | 用户 pipx 安装的 `rdt-cli`（0.4.1） | Reddit 帖子搜索 | 已认证，补充英文广告/推荐内容 |

> 登录态：xhs（Chrome cookie 已提取，authenticated）、rdt（已认证）、zhihu（已认证）均由 CLI 自行管理并落盘到各自配置目录，脚本不接触、不输出 cookie。twitter-cli 搜索返回 404（X API 侧问题，待其登录态恢复）；boss-cli（2018 年老包，cryptography 2.7 无法在现代 Python 构建）安装失败，均未接入。

### 风控与频率（用户要求）

- 4 个用户 CLI 通道（zhihu-cli / xhs-cli / bili-cli / rdt-cli）按**自然日冷却**：当天已跑过则该通道跳过（状态存 `data/.topic_cli_cooldown.json`），一天最多一次。
- **小红书额外风控**：每次搜索（每个关键词）之间**至少间隔 12 小时**——一次收集实际只搜 1 个关键词，搜完即停；触发验证码（captcha/frequent）时立即终止当日剩余并冷却到次日。
- 定时任务每天 22:15 一次；若当天已手动收集过，CLI 通道自动跳过，其余通道照常。

- 幂等：`add_topic_item` 按 `(topic_id, content_key)` 去重，重复运行不产生重复条目。
- 容错：单关键词/单源失败打印警告并继续，不影响整体。
- **关键词反哺项目调度（seed）**：每次收集后把专题关键词以 `pending` 状态写入 `discovery_keywords`（platform=bilibili），项目自己的 `discovery_cron`（每 8 小时）会用 B站官方 API 持续搜索这些词，新内容进 content_cache 后被 pool 通道自动匹配回专题——专题挂在项目既有爬取管线上，形成纯 CLI 的「发现→入库→匹配→回填」闭环。
- 已移除/未接入（会抢占浏览器或依赖浏览器扩展）：autocli 浏览器通道（douban/xiaohongshu/weibo）、本机 get-xiaohongshu-* 工具（agentlimb-bridge + Chrome 扩展，扩展当前离线）、fetch-xhs（需 daemon+扩展+登录）、discover-zhihu 类浏览器插件命令。
- 暂未接入：掘金（juejin）搜索 API 需要签名（请求返回空结果）；ima CLI 工具路径失效；data_warehouse 为用户私有分析库（如需收录专题可再接入）。

## 4. 入口

```bash
.venv/bin/python scripts/collect_topic.py             # 全部 active 专题
.venv/bin/python scripts/collect_topic.py --slug advertising --limit 6
.venv/bin/python scripts/collect_topic.py --no-rss    # 只跑 bilibili
```

定时：每日 22:15 cron 任务「OpenBiliClaw 专题每日收集」自动执行全部 active 专题。

## 5. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/topics` | 专题列表（含 item_count / last_collected_at） |
| POST | `/api/topics` | 创建专题（name/slug/description/keywords/platforms） |
| GET | `/api/topics/{slug}` | 专题详情 + 条目（200 条内，新→旧） |
| POST | `/api/topics/{slug}/collect` | 手动触发一次收集（同步返回统计） |

## 6. 前端

独立页面 `http://<host>:8420/topics`（`src/openbiliclaw/web/topics/index.html`）：
- 专题卡片列表（名称/描述/关键词 chips/收录数/最近收集时间）；
- 详情视图（条目列表：标题/来源/作者/摘要/链接 + 「立即收集一次」按钮）；
- 新建专题表单（名称/slug/关键词逗号分隔/多选平台）；
- 「全部收集一次」顺序触发所有专题。

## 7. 实施验证（2026-09-04）

**专题：广告**（keywords：计算广告/广告投放/信息流广告/广告算法/程序化广告/广告素材/出价策略/广告拍卖/投放策略/买量/oCPX/广告归因）
多轮收集后**共 451 条**，0 失败。四通道覆盖来源分布（前 200 条抽样）：小红书文章 77 / B站 37 / 抖音 29 / 知乎 21 / YouTube 11 / CSDN 96 / RSS 若干。
命中示例：广告拍卖（GSP）上科大公开课、移动广告归因原理（AppsFlyer）、OCPX 解读、互联网广告变现计费（CPC/CPM/oCPC/oCPM）、游戏买量、亚马逊广告 Agent 归因闭环、广告算法 vs 推荐算法异同（知乎）、CVR 延迟建模、搜推广秋招·广告归因机制（小红书）、广告归因架构实现（CSDN）。

**专题：去有风的地方**（keywords：去有风的地方/刘亦菲/大理旅居/云苗村/田园治愈/有风小院/大理）
**共 217 条**，0 失败。命中示例：大理深度游攻略、有风小院 vlog、苍山洱海风花雪月、剧集相关讨论。

- 数据库 CRUD 测试：`tests/test_topics.py`（7 个用例全过，含幂等/排序/计数刷新）。
- API/前端：`/api/topics`、`/api/topics/advertising`、`/topics/` 均返回 200 且数据正确。

## 8. 已知限制与后续

1. **关键词精度**：B 站/CSDN 按词匹配，「GSP」类缩略词会引入歧义噪音（如药品 GSP）——已通过换用「广告拍卖」等明确词缓解；历史噪音条目保留，可在前端人工识别。
2. **来源覆盖**：掘金搜索 API 需签名暂不可用；机器之心/36氪/虎嗅 RSS 有反爬已失效；如需要可后续换源、自建 RSSHub 或补掘金签名。
3. **条目去重粒度**：bilibili 按 BV 号、CSDN/RSS 按 URL、pool 按平台内容键；同一内容跨站不会合并（保持来源独立）。
4. **不接画像**：专题条目只入库展示，不进入推荐排序信号（避免专题兴趣污染主画像）；如后续希望「专题 = 显式兴趣」，可把专题关键词并入发现池。
5. **pool 通道匹配**：LIKE 全词 OR 匹配，宽泛关键词（如「大理」）会引入较多弱相关条目；如噪音明显可加「标题必须命中」或相关性阈值。
