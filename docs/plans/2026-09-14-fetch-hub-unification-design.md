# Fetch Hub：统一数据获取通道（AgentLimb 为兜底核心）· 方案

> 状态：**已确认并实施（2026-09-14，P0-P2 全量交付）**——`scripts/content_library/fetch_hub.py` + `fetchhub/` 包，14 离线单测全过；真实重放 4 个 v2ex URL（含降级路径）+ 知乎/小红书/B站各 1 条均成功。
> 日期：2026-09-14
> 动机：内容库归档实践中，同一平台（v2ex）出现了 3 条抓取通道、各自随机失效（CF 间歇、接口解析失败、缓存 miss）。每次都要现场降级试错。用户提出：把 AgentLimb（真 Chrome 桥）统一成通用获取方式 + 兜底。

---

## 1. 现状盘点（2026-09-14 实测）

| 平台 | 主通道 | 备用通道 | 已知失效模式 |
|---|---|---|---|
| 知乎 | `zhihu` CLI（已登录） | API 直连（api.zhihu.com 不风控） | web 端 403 `zh-zse-ck`；CLI 需 `env -u PYTHONHOME -u PYTHONPATH` |
| 小红书 | `xhs` CLI + pc_feed token | **xhslink 短链 `curl -L` 解析出新路径**（自带 token） | user_posts token ≠ explore token；批量触发风控 |
| B站 | `bili` CLI | PGC 接口（番剧） | ep 链接不被 `bili video` 识别 |
| v2ex | MindBack 13001 内部路由 | ① 代理 7890 直连官方 API ② **AgentLimb** | MindBack 间歇 `Failed to parse`；代理 API 间歇 403（CF 对出口 IP 间歇挑战）；AgentLimb 首次挑战需人工点一次 |
| 通用页面 | WebFetch / curl | — | CF / 登录墙 / 动态渲染全军覆没 |

**核心痛点**：通道知识散落在会话记忆里，每个平台、每次失效都要现场重新降级试错；且 AgentLimb 这个"最通用"的通道反而是最后才被想到的。

## 2. 目标 / 非目标

**目标**
1. 一个统一入口：喂任意 URL → 返回**规格化文档**（标题/作者/时间/正文/回复/图片/来源通道）。
2. 每个平台一条**声明式降级链**，链内自动按序尝试，失败自动落到下一条；AgentLimb 是所有平台的**最终兜底**。
3. 通道健康度留痕：每次抓取记录（url, platform, channel, ok, latency, error），后续可据此自动调序。
4. 与内容库归档流程打通：fetch 结果可直接进「待归档草稿」，减少人工搬运。

**非目标**
- 不做反爬对抗研究（遵守各站 ToS 边界：只读公开内容，不绕付费/登录墙，限速礼貌抓取）。
- 不替换现有 CLI（zhihu/xhs/bili 仍是各平台最优主通道，Hub 只做编排）。
- 不在本期接 MCP（与 `mcp-readonly-server.md` 方案解耦，后续可挂载）。

## 3. 核心抽象

### 3.1 UnifiedDoc（规格化输出）

```python
@dataclass
class UnifiedDoc:
    url: str                  # 规范化后的原始 URL
    platform: str             # zhihu|xhs|bilibili|v2ex|generic
    title: str
    author: str | None
    published_at: str | None  # ISO；拿不到就 None（如 AgentLimb DOM 抓取）
    content_md: str           # 正文 Markdown
    replies: list[Reply]      # 作者/时间/内容
    images: list[str]         # 本地化后的路径（CDN 签名图必须落盘）
    fetched_via: str          # "agentlimb" | "mindback" | "zhihu-cli" | ...
    fetched_at: str
    confidence: str           # full|partial（如缺元数据时标 partial）
```

### 3.2 通道（Channel）与降级链

```python
CHANNELS = {
  "v2ex":    ["mindback", "v2ex-api-proxy", "agentlimb"],
  "zhihu":   ["zhihu-cli", "zhihu-api-direct", "agentlimb"],
  "xhs":     ["xhs-cli", "xhs-shortlink-resolve+xhs-cli", "agentlimb"],
  "bilibili":["bili-cli", "agentlimb"],
  "generic": ["webfetch", "agentlimb"],
}
```

每个通道实现同一接口：`fetch(url) -> UnifiedDoc`，失败抛 `ChannelError`（带原因分类：`challenge`/`parse`/`not_found`/`auth`）。Hub 按链序执行，全部失败则汇总各通道错误返回。

### 3.3 AgentLimb 适配器（兜底核心）

已验证的调用模式（本机桥 `127.0.0.1:7791`）：

```
POST /api/mvp/browser/call  {"tool":"navigate","params":{"url":...,"waitForLoad":true}}
POST /api/mvp/browser/call  {"tool":"javascript_eval","params":{"expression":<抽取JS>}}
GET  /api/mvp/browser/calls/<id>   → .call.result.result.value
```

适配器要点（全部来自实战踩坑）：
- **挑战页判定**：不能看 `<h1>`/`title`（挑战页 h1 也是域名），必须探测**内容选择器存在性**（如 v2ex 的 `.topic_content`）；探测失败 → 等待重试 N 轮 → 仍失败则返回 `challenge` 错误并**提示用户手动过一次挑战**（cookie 种下后自动放行，可持续数天）。
- **平台选择器注册表**：每平台一段抽取 JS（v2ex: `.topic_content`/`#Main .cell[id^="r_"]`；后续按需补知乎/小红书/B站）。
- **能力边界**：DOM 抽取拿不到精确发帖时间/节点等元数据 → `published_at=None` + `confidence=partial`，不瞎编。
- **限速**：navigate 间隔 ≥ 数秒，避免对真浏览器会话造成风控压力。

### 3.4 通道健康度记录

SQLite 表 `fetch_log(url, platform, channel, ok, error_kind, latency_ms, ts)`，存 `data/content.db`。仅记录，不自动改链序（避免抖动）；查错时人看数据调 `CHANNELS` 顺序即可——KISS。

## 4. 接口形态

**CLI（第一期唯一形态）**：

```bash
.venv/bin/python scripts/fetch_hub.py <url> --json          # 抓取并输出 UnifiedDoc
.venv/bin/python scripts/fetch_hub.py <url> --archive-draft # 顺手生成归档草稿 md（半成品，解读段留空）
.venv/bin/python scripts/fetch_hub.py --health              # 查看近 N 天通道成功率
```

- 落位 `scripts/content_library/fetch_hub.py`（内容库体系内，README 登记）。
- 归档草稿 = 五段式骨架 + 元数据已填 + 正文已填，「我的解读/批注」留 TODO——**人写解读，机器搬内容**，符合内容库「解读必须人过脑」的约定。
- HTTP API / 自动化接入留到第二期（若 pm2 服务需要）。

## 5. 分期实施

| 期 | 内容 | 验收 |
|---|---|---|
| P0 | Hub 骨架 + v2ex 三通道（mindback/api-proxy/agentlimb）+ fetch_log + CLI | 用今天失效过的 1241165、1241536、1240524 三个 URL 重放，全部成功且 `fetched_via` 正确标注降级路径 |
| P1 | zhihu / xhs / bilibili 通道接入 + `--archive-draft` 草稿生成 | 用近 3 天各平台已归档 URL 重放，输出与人工归档字段一致 |
| P2 | generic 通道（WebFetch→AgentLimb）+ `--health` 报表 + tests | 单测覆盖 URL 路由与降级链逻辑（mock 各通道） |

## 6. 风险与边界

- **AgentLimb 许可**：BSL 1.1（非开源），本机个人使用无碍；若未来要把 Hub 对外分发，需检查其许可条款。**不把桥的调用细节做成对外文档**，仅内部使用。
- **人工挑战依赖**：AgentLimb 首遇 CF 挑战需人点一次。缓解：失效时 Hub 明确提示「请在 AgentLimb 浏览器里手动打开 <url> 过一次挑战」，并把 `challenge` 错误与健康度报表关联，让用户知道什么时候需要出手。
- **风控礼貌**：所有通道默认单次请求、无重试轰炸；xhs 保持「1 篇/批」既有约定。
- **不许抢跑**：本方案未确认前不写任何代码。

## 7. 与既有体系的关系

- **内容库三件套**（md→DB→前端）：Hub 只负责「取」，归档仍走既有 sync 流程，单向依赖，互不侵入。
- **`mcp-readonly-server.md`**（停在方案阶段）：方向互补——那是「对外暴露只读查询」，本方案是「对内统一采集」；若两者都落地，fetch_hub 可作为 MCP server 的一个 tool 挂载。
- **自动化任务**：每日「补正文/内容库同步」两个自动化未来可改调 Hub，避免再出现旧路径静默失效类问题。
