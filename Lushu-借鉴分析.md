# Lushu（路书）借鉴分析

> 仓库：`github.com/yangyang5214/lushu`（AGENTS.md 在仓内，工程很规范）
> 本地克隆：`/Volumes/固态硬盘1T/002-探索项目/_ref_lushu`
> 一句话定位：**基于 Cloudflare Pages + D1 的「路书」规划/分享应用**（搜地点、规划驾车路线、把多天行程整理成可分享路书），**前端 React+Vite + 后端 Pages Functions**，并且**自带一个 Chrome 扩展**（MV3）用来从小红书 问点点 抓攻略、用 DeepSeek 抽地点、回写路书。

---

## 0. 为什么它对你价值最高

| 仓库 | 与你的关系 |
|---|---|
| TraeWorkAssistant | 桌面应用，形态不符，仅工程手法 |
| WikiTok | 纯前端 TikTok 卡片流，feed UI 可抄 |
| **Lushu** | **自己就是个 Chrome 扩展 + 多平台自动化 + LLM 抽取**，与 OpenBiliClaw 扩展架构同构 |

OpenBiliClaw 扩展也是 `content/`（各平台注入）+ `background/service-worker.ts`（跨站调度）+ `main/*`（MAIN world 抓 token/状态）。Lushu 的扩展是这套模式的**完整可运行样例**，尤其小红书那块。

---

## 1. 整体结构

```
extension/         ← 重点：Chrome 扩展（MV3）
  manifest.json       content_scripts: panel.js+panel.css（注入 lushu 页面）
                      background: service_worker（module）
  content/panel.js    悬浮面板 UI + 同源写回（登录态/PUT 路书）
  background.js       消息路由：只做两件事——抓小红书、调 DeepSeek
  lib/xhs.js          MAIN world 驱动小红书 问点点（核心精华）
  lib/deepseek.js     LLM 客户端（timeout + 错误分类 + json 模式）
  lib/extract.js      LLM 抽取 prompt 构建 + 健壮解析
  lib/storage.js / options / popup
src/                ← Web 应用（地图/路线/多天拆分）
functions/          ← Cloudflare Pages Functions（D1 读写、鉴权）
shared/             ← 客户端与边缘函数共用的纯逻辑（geo.ts 等）
schema.sql / wrangler.toml.example
```

---

## 2. 扩展架构：content 与 background 的分工（对照你）

`extension/manifest.json` + `background.js:1-12` 把职责切得很干净，且理由写在注释里：

- **content script（panel.js）只做「同源」事**：查登录态（`/api/auth/me`）、定位 POI（`/api/places`）、**PUT 路书**。因为写路书要带登录 cookie，放 content script 同源发最稳。
- **background 只做「跨站」事**：抓小红书（`xhs.ask`）、调 DeepSeek（`ai.extract`）。注释明确说「避免跨站 cookie 问题」。
- 通信：`panel.js` 用 `chrome.runtime.sendMessage` 把跨站活交给 background；background 用 `chrome.tabs.sendMessage(tabId,{type:'lushu.progress'})` 把进度推回来源标签页。

**直接对标你的扩展**：`content/zhihu.ts` 等做同源采集、`service-worker.ts`（+ `*-task-dispatcher.ts`）做跨站调度、`main/xhs-token-sniffer.js` 在 MAIN world 抓凭证——分工思路一致。Lushu 的价值是给了**更完整的「content↔background 消息协议 + 进度回推」样板**（`background.js:19-62` 的 `switch(msg.type)` 很清晰）。

---

## 3. 最值钱：MAIN world 驱动小红书 问点点（`lib/xhs.js`）

这是与你 `main/xhs-*` 直接对标的部分，6 个手法都带行号：

1. **React 受控输入必须走 descriptor setter**（`:199-209` `setReactValue`）：
   ```js
   const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
   setter.call(el, value)
   el._valueTracker?.setValue(last)            // 否则 React 认为「值没变」不重渲
   el.dispatchEvent(new Event('input', {bubbles:true, composed:true}))
   ```
   isolated world 直接赋值会被清掉，注释（`:10`）明确要 `world:'MAIN'`。

2. **真点击要用 Pointer+Mouse 事件分派**（`:161-197` `realClick`）：不是 `.click()`，而是派发 `pointerdown/mousedown/pointerup/mouseup/click` 带 clientX/Y——绕过框架的点击守卫。

3. **导航会销毁注入上下文 → 必须分两阶段重注入**（`:4-6`、`:316-397` 阶段A 首页、`412-565` 阶段B 会话页）：点「问点点」会让页面跳转，注入的函数上下文就没了，所以 phaseA 注入→点完→phaseB 在新页面再注入。

4. **流式输出稳定性检测**（`:507-517`）：每 1.2s 轮询，文本连续 4 次相同且长度≥阈值才停——因为 点点 是流式，短的「AI 总结 xx 篇笔记」会先稳定，必须等正文够长。

5. **注入函数必须自包含**（`:8-9`、`:601-640`）：用 `chrome.scripting.executeScript({world:'MAIN', func, args})` 注入，`func` 不能引用外部变量，只能靠 `args`；进度通过共享的 `window.__LUSHU_XHS__` 状态对象回读（`runInjected` 轮询这个对象）。

6. **选择器可配置 + 打分容错**（`:141-159` `pickSearchInput`）：候选按 `placeholder 含「搜索」/位置/可见性/禁用` 打分选最优，注释说「改设置里的选择器即可适配 DOM 变化」——比你写死选择器稳。

> 这套就是你 `content/xhs/task-executor.ts` + `main/xhs-state-bridge.js` 想要的「在小红书真实页面里可靠驱动 UI」的完整答案。Lushu 还多处理了一个你没提的难点：**问点点会跳转新页面**（上下文丢失），以及**回答是流式、要等稳定**。

---

## 4. 健壮的 LLM 抽取（直接能抄的 robustness 模板）

`lib/extract.js` 把「模型不听话」的情况都兜住了，对你抽结构化内容很有用：

- **严格 JSON 协议**：system prompt 规定字段 + `response_format:{type:'json_object'}`（`deepseek.js:37`）。
- **解析兜底**（`parseExtract :148-218`）：
  - 先剥 ```` ```json ```` 围栏（`stripFence`）；
  - `JSON.parse` 失败 → 取首个 `{` 到最后一个 `}` 的子串再 parse；
  - 模型多包一层？处理 `{places}` / `{data:{places}}` / `{routes:[...]}` 三种形状，挑**地点最多**的那条（`candidates.reduce`）；
  - 去重（`city|name` 小写 key）、补 `query`、强制 day/night 不变量（`markNightsFromDays` / `evenNightIndexes`）。
- **干净 LLM 客户端**（`deepseek.js`）：`AbortController` 120s 超时、401/402/429 分门别类报错、key 只存 `chrome.storage.local` 不上传。

对标你 `openbiliclaw` 的「理解/抽取」链路——这套「多形状解析 + 不变量修复」正是把 LLM 脏输出变成可信结构数据的标准做法。

---

## 5. 客户端/边缘函数共享纯逻辑 + 写回乐观并发

- **`shared/geo.ts` 单一顺序真相源**：前端 `src/lib/geo.ts` 与 Pages Function `functions/api` 共用一份 `orderRoute`/`haversineKm`/2-opt+or-opt 路线优化。注释（`:1-6`、`:188-191`）点出关键设计：**存下来的 `orderedIds` 只是缓存，读时按起点/终点用同一算法重推**——导入或 AI 生成的地点天然无顺序也无害。这避免了「信任客户端存的顺序」导致的数据腐化，是可复用的数据完整性原则。
- **写回站点的乐观并发**（`panel.js:382-442` `putBook`）：`x-edit-token` + `x-owner-key` + `baseUpdatedAt` 做冲突检测；`409 duplicate_title` 自动重试 5 次换名。任何「扩展回写第三方站点」的写流程都该借鉴。

---

## 6. 对你的两个落点

### ① OpenBiliClaw 扩展（强相关）
- 直接补强 `content/xhs/` 与 `main/xhs-*`：Lushu 的「MAIN world 驱动 问点点 + 流式稳定检测 + 跳转重注入」是现成参考答案（`extension/lib/xhs.js` 全文件）。
- 补一套 content↔background 的「任务协议 + 进度回推」样板（`background.js:19-62` + `panel.js:67-77,512-518`）。
- 抽结构化内容时抄 `extract.js` 的「多形状解析 + 不变量修复」。

### ② 你的新疆 8 天自驾路书（顺带可用）
Lushu 本质就是**自驾路书规划器**（多天行程、过夜拆分、地图路线、可分享）。你记忆里有详细的「新疆8天」自驾计划（白哈巴/喀纳斯/魔鬼城/赛湖/独山子大峡谷）。它是 Cloudflare Pages+D1 **免费档可自托管**，理论上能把你的行程落成一本带地图的路书。
- ⚠️ 但注意：它默认用 **高德 POI / OSRM 驾车路线**，国内坐标需 WGS84↔GCJ-02 偏移（代码里 `/api/places` 已统一成 WGS84）；你新疆行程若想用，要确认有高德 key 或走 Nominatim 兜底（见 `panel.js:100-128`）。

---

## 7. 不建议照搬 / 注意

- **整站接管 vs 注入**：Lushu 扩展是注入自身站点（`lushu.fittools.cc/d/*`）的辅助面板，不是改版第三方首页——和你的「知乎首页卡片化」目标不同，卡片化那条还是看 WikiTok。
- **小红书驱动依赖 DOM 选择器**：xhs 的 `inputSelectors`/`dianEntrySelectors` 等必须用户在 options 里配，`README` 也承认小红书 DOM 会变，得常维护。
- **LLM 是 DeepSeek 直连**：你的扩展若接自有 LLM provider，可复用 `deepseek.js` 的 client 骨架但换 endpoint。

---

## 8. 精读文件清单

- `extension/lib/xhs.js` — **最该精读**：MAIN world 驱动小红书 问点点全流程
- `extension/background.js` — content/background 消息协议 + 进度回推
- `extension/lib/extract.js` — 健壮 LLM JSON 抽取
- `extension/lib/deepseek.js` — 干净 LLM 客户端
- `extension/content/panel.js` — 注入面板 UI + 同源写回 + 乐观并发
- `shared/geo.ts` — 客户端/边缘共享纯逻辑 + 单一顺序真相源
