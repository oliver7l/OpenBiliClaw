# 16_浏览器自动化 — 浏览器操作脚本（AgentLimb 桥）

通过 **AgentLimb** 扩展桥接用户真实 Chrome（带登录态）执行自动化。不是无头浏览器，Cookie/会话全自动。

## 前置条件

1. Chrome 已启动
2. **AgentLimb 侧边栏面板开着**（面板关了扩展 SW 就停 → `extensionOffline`）
3. Bridge 常驻 `127.0.0.1:7791`（LaunchAgent 自动拉起）
4. `extensionOffline` 修复：`chrome://extensions` 里刷新 AgentLimb ⟳ → 重开侧边栏

## 文件

| 文件 | 用途 |
|---|---|
| `agentlimb_eval.py` | 通用封装类 `AgentLimbBrowser`：start / tabs / navigate / eval_js（显式 tabId、双 JSON 解析） |
| `weread_collect.py` | 实例：微信读书拉任意公众号全部文章列表（改 `BOOK_ID`/`TAB_ID` 可复用） |
| `linuxdo_capture.py` | 实例：Linux.do 帖子全文入阅读库（Discourse JSON，作者/标签最干净） |
| `web_capture.py` | **通用入口**：任意 URL → 登录态 Chrome 抓标题+可读正文 → 按域名自动判源入阅读库 |
| `backfill.py` | 低密度批量回填：从阅读库取缺正文条目批量补（默认小红书，--n 节流防风控） |

## 快速用法

```python
from agentlimb_eval import AgentLimbBrowser
b = AgentLimbBrowser()
b.start()
print(b.tabs())          # 拿 tabId
b.navigate("https://weread.qq.com/")
print(b.eval_js("1+1", tab_id=1972143383))
```

## 关键坑（2026-09-18 实测）

1. **`javascript_eval` 默认打在 active tab** —— 用户会切 tab！先 `tabs_context` 拿 tabId，每次 eval 显式传 `tabId`。
2. **复杂 JS 写单行 then 链**（`fetch(...).then(r=>r.json()).then(o=>...)`），多行 / async 箭头函数容易 `SyntaxError`；长参数用 python 写 JSON 文件再 `"$(cat p.json)"` 传给 CLI。
3. 探测 Bridge 用 curl 必须 `--noproxy '*'`（本机代理会劫持）。
4. 工具 schema：`GET http://127.0.0.1:7791/api/mvp/docs/tools/<name>`；任务收尾调 `task_complete`（否则 5 分钟超时报失败）。
5. CLI 位置：`/Volumes/固态硬盘1T/002-探索项目/1172-github项目/AgentLimb/agentlimb-extension/agentlimb-chrome-v0.1.4/kernel/bridge/mvp/terminal-client.mjs`（16 个工具：navigate / javascript_eval / tabs_context / page_snapshot / form_input / muscle_* / task_* 等）。

## 微信读书公众号通道（weread_collect.py 的原理）

- 公众号在微信读书里是一本书：`bookId = "MP_WXS_" + atob(biz)`（biz 即文章 URL 里的 `__biz`，base64 解出来是数字）
- 登录态下首页可调：`/web/shelf/sync`（书架，deepLink 里 `v=` 拼阅读器 URL）、`/mp/shelf/addToShelf`（订阅）
- **核心接口 `/web/mp/articles?bookId=<id>&offset=N` 只能在 `/web/mp/reader/<hash>` 页面上下文调**（首页调返回 -2041）
- 翻页：`offset += 本页 reviews 数`，`reviews==0` 停；每条群发含 `subReviews[]`（多图文），文章直链 = `mp.weixin.qq.com/s/` + originalId（`~` 还原成 `_`）
- 直链是公开的，正文直接 curl 即可抓（无需任何密钥）

## Linux.do 帖子全文入口（linuxdo_capture.py）

linux.do 全程在 Cloudflare 人机验证后，后端直连 Discourse JSON 返回 403；浏览器扩展通道又依赖"扩展在线 + 已构建"。**首选走这条 AgentLimb 桥**：在登录态真实 Chrome 里 `fetch('/t/{id}.json')` 取首帖 `cooked` 全文，`upsert_article` 写进阅读库（`source_type=linuxdo`）。

```bash
cd /Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw
.venv/bin/python 16_浏览器自动化/linuxdo_capture.py <url|id> [<url|id> ...]
# 例：.venv/bin/python 16_浏览器自动化/linuxdo_capture.py https://linux.do/t/topic/188854/11 2920473
```

- 给 URL → 保留原始 URL（含 `/188854/11` 楼层）命中同一行；给裸 id → 拼 canonical `/t/{id}`。
- 写入后补一次 `author` 与话题 tags（`upsert_article` 命中已存在行时不更新这俩）；`body_text`/正文来自首帖 `cooked` 去 HTML 后文本。
- 前置同顶部"前置条件"：Chrome 已启动 + AgentLimb 面板开着 + Bridge 7791 在线。

## 通用任意 URL 抓正文入阅读库（web_capture.py，首选入口）

给任意 URL，登录态真实 Chrome 里抓标题 + 可读正文（优先 `article`/`main` 容器，兜底 `innerText`），按域名自动判 `source_type`/`source_name` 写入阅读库 `articles`。覆盖阅读库里的主要来源（linux.do / zhihu / xiaohongshu / douyin / youtube / bilibili / v2ex / douban / wechat 等），未匹配的域名落为 `web/<host>`。

```bash
cd /Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw
.venv/bin/python 16_浏览器自动化/web_capture.py "https://www.xiaohongshu.com/explore/<note>?xsec_token=..." [更多 URL]
```

- URL 已带 `xsec_token` 等鉴权参数时务必整体加引号、原样传入（如小红书链接）。
- 抓正文用通用可读抽取，作者/标签尽量读 OG/meta（`og:article:author`/`keywords`）；若平台正文走 JSON 端点（如 Linux.do），用专门的 `linuxdo_capture.py` 拿作者/标签更干净。
- `upsert_article` 命中已存在行时不更新 author、tags 仅在空时覆盖 → 脚本会补写一次。
- 2026-09-19 实测：小红书笔记从 0 补到 1895 字。

**低密度定时回填（PM2 cron_restart，不占 Trae Work 会话）**
- 用 **PM2**（`ecosystem.xhs-backfill.config.js`，`cron_restart "5 */2 * * *"`）每 2 小时 :05 触发 + 脚本内随机憩志 0-30 分钟打散触发点，全天约 12 条、节奏极慢防风控。日志 `data/backfill_xiaohongshu.log` + `~/Library/Logs/xhs-backfill.{out,err}.log`。
- `backfill.py` 用 `ORDER BY RANDOM()` 随机挑待补条目，避免卡在单条过期 token 上；防假命中守卫会拒收失效页。
- 常用命令：`pm2 logs xhs-backfill` / `pm2 restart xhs-backfill` / `pm2 save`（进程列表已存 `dump.pm2`）。
- **坑**：1) 脚本用 `__file__` 定位仓库根，不依赖 cwd。2) 曾试 LaunchAgent 与 crontab 均失败——守护进程上下文（cron/launchd）在 TCC 下**无权限写外接盘 `固态硬盘1T` 中文路径**，`Operation not permitted` / `exit 78 EX_CONFIG`。PM2 常驻进程由用户会话拉起、继承用户权限，实测可正常写 `data/` 并入阅读库。3) 开机自启需 `sudo pm2 startup`（见下），未执行该步则重启后需手动 `pm2 resurrect`。
