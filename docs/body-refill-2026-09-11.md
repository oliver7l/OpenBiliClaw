# 阅读库正文补抓：现状与方案（2026-09-11）

## 1. 缺口（实测 data/content.db，articles 表）

| 来源 | 总条数 | 缺正文 | 已记满重试 | 本机通道 | 结论 |
|---|---:|---:|---:|---|---|
| xiaohongshu | 40904 | 40062 | 16 | xhs CLI | **不通**（风控 + 39546 条无 token） |
| youtube | 21107 | 20750 | 0（已重置 404） | yt-dlp | **本机被 bot 检测**，需重新导出 Cookie |
| bilibili | 4141 | 2942 | 1251 | bili CLI | 可用，约 7s/条 |
| douyin | 738 | 724 | 0 | 无 CLI | 走得到大脑兜底 |
| zhihu | 10173 | 696 | 87 | 直连接口 | **可用**，约 1.6s/条 |
| v2ex | 5741 | 72 | 49 | autocli | 可用 |
| reddit / wechat / xiaoyuzhou | 1313 | 29 | 3 | autocli | 可用 |

合计约 6.5 万条缺正文。

## 2. 根因：补抓链路曾整体停摆

- `refill_article_bodies.py` / `refill_library_bodies_v2.py` 在 v0.4.0 拆库（articles
  迁到 `data/content.db`）时补的 ATTACH 代码块**缩进顶格**，导致 `db` 未定义，
  两个脚本启动即崩。拆库后正文补抓实际一直在空转。
- zhihu CLI 新版移除 `answer` / `article` 子命令，旧调用 100% 失败。
- YouTube：`_yt_permanent()` 里的 "Sign in" 命中
  "Sign in to confirm you're not a bot" → 判成"该视频永久无字幕"，
  一天把 196 条队列记满作废（累计 404 条，已重置为 0）。

## 3. 修复

1. 两个脚本的 ATTACH 缩进 + `from pathlib import Path`（CLI 路径改 `shutil.which` 兜底）。
2. 新增 `scripts/content_library/zhihu_api_body.py`：复用 zhihu CLI 登录态直连 `api.zhihu.com`，
   拿 `content` 转 Markdown（需在 zhihu-toolkit 虚拟环境 python 下运行）。
3. 小红书语义：风控/报错 → 保留重试；裸链无 `xsec_token` → 跳过且**不计重试次数**。
4. YouTube：新增 `_yt_bot_blocked()`，命中即熔断停止、不消耗重试次数；
   `_yt_permanent()` 去掉宽泛的 "Sign in"。

## 4. 得到大脑兜底通道（`scripts/refill_via_getnote.py`）

本机被风控 / 没有 CLI 的源（小红书、抖音、YouTube）交给**得到大脑服务端**抓取：

```
getnote save <url>            # write_note 1000/天
getnote task <task_id>        # read 20000/天，轮询拿 note_id
getnote note <id> --field content
→ 写回 articles.content_text
```

- 任务状态落 `getnote_body_task` 表（url 主键，幂等，不覆盖已有正文）。
- 配额耗尽自动停止（今天 write_note 与 read 均已用尽，00:00 重置）。
- 理论吞吐 1000 条/天 → 6.5 万条约 65 天，实际会过滤掉服务端也抓不到的条目。

## 5. 调度

每日自动化「阅读库正文补抓（本机通道 + 得到大脑）」02:00：

```
refill_library_bodies_v2.py 400 --source=zhihu
refill_library_bodies_v2.py 300 --source=bilibili
refill_library_bodies_v2.py  80 --source=v2ex
refill_library_bodies_v2.py  30 --source=reddit
refill_via_getnote.py --limit 900
```

## 6. 后续：YouTube Cookie 已接入（2026-09-11 晚）

用户提供 `.youtube.com` 的 Cookie JSON，转成 Netscape 格式固定给 yt-dlp 用：

- 落盘 `data/youtube_cookies.txt`（`.gitignore` 内，权限 600）。
- `refill_youtube_subtitles.py` / `refill_library_bodies_v2.py` 检测到该文件即自动带
  `--cookies`；没有才回退 `--cookies-from-browser chrome`。可用 `YT_COOKIE_FILE` 覆盖路径。
- 同时补上 `--write-subs`（原先只有 `--write-auto-subs`，会漏掉纯人工字幕的视频）。
- 验证：此前必报 "Sign in to confirm you're not a bot" 的视频，现已抓到 31905 字字幕；
  被判 SKIP 的样本实测是 "Video unavailable"（视频已删），判定正确。
- 复用到期后重导：`python3 scripts/cookies_json_to_netscape.py <cookies.json>`。
- **字幕命中率有限**：YouTube 自动字幕现在要 PO token
  （`There are missing subtitles languages because a PO token was not provided`），
  切换 player_client（web_safari/tv/mweb）均无效，本机无 docker 装不了
  bgutil provider（npm 无此包）。因此加了**简介兜底**：一次调用同时
  `--write-description`，拿不到字幕就写 `【视频简介】…`。命中率从 2/14 提到 12/15。
- 说明：这样补进的正文有一部分只是视频简介（几百字），不是完整口播稿；
  后续若装上 PO token provider，可对已有正文的条目再做一次"字幕优先"覆盖补抓。

## 7. 待用户决策

- **小红书 4 万条**：39546 条无 `xsec_token`，现有 token 回填任务 1 条/2 小时，
  杯水车薪。建议：① 主要指望得到大脑服务端；② 或接受它们只有标题+封面。
- **YouTube 老自动化**：04:30 那份 `220 --desc --sleep 300` 参数过于保守
  （已实测 sleep 3 即可），建议停掉，改由每日 02:00 的统一任务跑 400 条。
