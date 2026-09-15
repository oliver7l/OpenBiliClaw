# sources — 内容采集线

> 推荐内容的**采集核心**：把各平台内容抓进来、写进库、喂给推荐池。
> 11,263 行 / 56 文件；**零 TODO/FIXME**；最大文件 `douyin_direct.py` 646 行。
> 摸底日期 2026-09-15。排错前先读 `architecture-map.md` §1 的「三条线」。

## 1. 两套抽象（新人最容易混的地方）

| 抽象 | 定义 | 服务谁 | 覆盖平台 |
|---|---|---|---|
| **SourceAdapter** | `protocol.py:51`（`fetch(recipe, profile, limit) → list[DiscoveredContent]`）；配方 `SourceRecipe` 在 `protocol.py:15` | **推荐池线**：discovery_engine 按 SourceRecipe 抓取 → 评估入池 | bilibili / xhs（stub）/ rss / youtube / xiaoyuzhou / douban / twitter |
| **url_processors** | `url_processors/base.py:22` + `registry.py:24`（装饰器注册 URL pattern → processor） | **阅读库线**：library_routes / content_filler / gap_filler 抓**单篇文章**入 `articles` 表 | weibo / wechat / juejin / sspai 等只在这一套，**不进推荐池** |

注册点集中在 **`api/runtime_context.py:614-668`**：Bilibili:622、Xiaohongshu:631（stub，内容实际走浏览器扩展上报）、Rss:636、YtDlp:642、Xiaoyuzhou:648、Douban:657/667、Twitter:676+，全部受 `[sources.*].enabled` 配置门控。注册表本体 `sources/registry.py:14`。

## 2. 平台 → 文件 → 产出

| 平台 | 采集文件 | 入口 / 产出 |
|---|---|---|
| bilibili | `bili_tasks.py`、`bilibili_adapter.py` | `BiliTaskQueue`(bili_tasks.py:77) 管 `bili_tasks` 表；producer 写 `content_cache` |
| douyin | `dy_tasks.py`、`douyin_direct.py`、`douyin_plugin_search.py`、`douyin_signature.py`（XBogusSigner:15）、`douyin_auth.py` | `DyTaskQueue`；plugin_search 供 runtime/douyin_producer.py |
| xhs | `xhs_tasks.py`（`XhsTaskQueue`:188、`XhsCreatorStore`:461）、`xhs_keyword_gen.py:56`（LLM 生成关键词） | 扩展上报 observed-urls 入池 |
| zhihu | `zhihu_tasks.py`（`ZhihuTaskQueue`） | zhihu 发现项 → DiscoveredContent |
| youtube | `yt_tasks.py`（`YtTaskQueue`）、`youtube_adapter.py`（yt-dlp + cookie） | content_cache |
| x/twitter | `x_tasks.py`（`XCreatorStore`）、`x_client.py`、`x_auth.py`（`resolve_x_cookie`:54） | `twitter_adapter.py` |
| hupu / v2ex / toutiao / weibo | `url_processors/*_processor.py` + `runtime/*_feed_producer.py` | 如 hupu_feed_producer 写 `content_cache`(:33) |
| douban / rss / 小宇宙 / 微信 | `douban_feed_adapter.py`、`rss_adapter.py`、`xiaoyuzhou_adapter.py`、`wechat_tasks.py` | `run_*_polling` 持久化（rss_tasks.py:30 `_persist_items`） |

## 3. 数据流（两条线）

```
① Producer 线（pm2 常驻进程）
   runtime/*_producer.py → 写 data/inbox/<platform>.db 的 content_cache
   （runtime/_db.py:26-33）→ inbox_merger 定期合并进 pool.db

② Adapter 线（服务进程内）
   discovery_engine 按 SourceRecipe fetch → DiscoveredContent → 评估入推荐池

③ 浏览器扩展配合（xhs/bili/dy/zhihu/yt）
   GET  /api/sources/{platform}/next-task   领任务（source_routes.py:1155/1254/2347/2507/2614）
   POST /api/sources/{platform}/task-result 回传（:1173/1278/2365/2525/2632）
   → 推荐引擎 batch_buffer_refill 消费（recommendation/engine.py:1937）
```

调度：`runtime/refresh.py:46` `ContinuousRefreshController`（7 个 mixin 组合），平台轮询在 `_refresh_platform_loops_mixin.py:104-165`；**独立进程**调度器 `runtime/producer_scheduler.py:26-43`（PRODUCERS 列表顺序跑 bili/douyin/xhs/zhihu/youtube/xiaoyuzhou/v2ex/hupu/toutiao feed + favorites 簇——分开进程是为了避免 SQLite 写锁竞争）。

## 4. 配置与凭据

- 配置：`config.py:606` `SourcesConfig`，各平台段 douyin:461 / youtube:479 / twitter:496 / zhihu:518 / bilibili:586 / douban:593
- Cookie：环境变量 `OPENBILICLAW_{DOUYIN,X,DOUBAN}_COOKIE`（config.py:471/509/602）；B 站 cookie 直接存 config `[bilibili].cookie`（:249）
- 凭据视图：`/api/sources/credentials`（source_routes.py:2278）

## 5. 已知技术债（2026-09-15 摸底）

- **疑似死文件**：`sources/browser.py`、`sources/opml_import.py` —— src 与 tests 内 grep 不到任何 import，删前需再确认一次
- **双抽象并存**：SourceAdapter 与 url_processors 互不复用；`_persist_items` 在 `rss_tasks.py:30` 与 `xiaoyuzhou_tasks.py:29` 近似重复
- `producer_base.py:9-10` 自述仍有 run_forever/_main 重复待收敛
- ⚠️ **与日志 404 的关系**：日志里 `/api/sources/{x,weibo,v2ex,reddit}/next-task` 404 的调用方是仓库外客户端（旧版扩展），本模块只实现了 bili/xhs/dy/zhihu/yt 五个 next-task

## 6. 测试

`tests/source/`（adapter/recipe/task-queue 事务/douban_feed/policy）、`tests/xhs/`（7 个含 e2e）、`tests/zhihu/`、`tests/youtube/`、`tests/bilibili/`（含扩展 e2e×2）、`tests/douyin/`（6 个）、`tests/x/`（6 个）、`tests/feed_producers/`（hupu/toutiao）。合计约 40 个测试文件提及本包。
