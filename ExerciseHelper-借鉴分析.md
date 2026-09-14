# ExerciseHelper（闻鼓而动）借鉴分析

> 仓库：`touchren/exercise-helper` · AGPL-3.0 · 克隆：`_ref_exercise-helper`（109 文件）
> 研究日期：2026-09-14 · 背景：为 OpenBiliClaw Web UI / 扩展寻找可复用工程手法

---

## 1. 一句话定位

**纯前端 PWA（Vanilla HTML/CSS/JS，无后端、无构建、无账号），给久坐办公族的晨练/晚练工具：隔天抗阻 + 每日拉伸，全程预生成语音引导，「打开 → 按开始」闭眼跟练。** 名字取自「闻鼓而进」——语音就是鼓声。

项目自述很清醒：**它不教动作**（零基础先去 B 站跟视频），它解决的是「学会之后如何坚持」——把坚持的反人性设计（看屏幕对节奏、自己数次数、记顺序）全部卸给语音和状态机。

## 2. 技术形态

| 维度 | 选择 |
|---|---|
| 形态 | PWA（`morning/`、`evening/` 两套独立子应用 + 根级选择页 + 多个 SEO 静态页） |
| 技术栈 | 纯 Vanilla HTML/CSS/JS，**零依赖、零构建**，打开即用 |
| 数据 | 全 localStorage（`morning:`/`evening:` 前缀隔离），无服务器、无追踪 |
| 语音 | **构建期预生成 mp3**（`tts/xiaoxiao/*.mp3`，Azure 晓晓音色），运行时按文本精确匹配播放，不跑 TTS 服务 |
| 离线 | Service Worker，分层缓存策略（见 §3.4） |
| 协议 | AGPL-3.0（闭源商用需授权；微信小程序版因隐私设计不开源） |

## 3. 四个核心子系统（全部读完源码）

### 3.1 训练状态机 `engine.js`（392 行，最值钱）
单条 1 秒 `setInterval` tick 驱动全部定时步骤，但**节奏敏感的播报不走 tick**：

- **事件表模式**：每步构建 `events = [{at, run, fired}]`，tick 时统一触发，跳步/恢复时按 `phaseElapsed` 补记 `fired`，不重播。
- **预排程双轨**：倒计时 token 与结束前蜂鸣序列各自用**独立 setTimeout 预排程**（`_scheduleCountdown`/`_scheduleBeeps`），注释明说原因——主线程繁忙时 `setInterval` 的 catch-up 会把「3-2-1」压缩成一坨，破坏听感节奏。恢复暂停时按 `fromElapsedSec` 跳过已播 token。
- **语音驱动步骤**（announce/transition/complete）不用 tick：`onEnd` 回调推进 + `_estimateDurationMs` 超时兜底，**语音失败绝不卡流程**。
- 暂停/恢复/跳过全路径都正确处理 `totalPausedMs` 累计与抗阻秒数统计（`countsTowardResistance` 精确到部分完成）。
- 所有 Web API 调用 try-catch 包装。

### 3.2 音频管理器 `audio.js`（633 行，健壮性范本）
**三通道降级 + token 防串音**：

1. 主路径：所有环境统一播**预生成 mp3**（音质稳定、微信/原生体验一致）。文本→文件映射两道解析：精确匹配 → 稳定前缀兜底（用户改了训练参数导致播报文本变体时仍能出声）。
2. `<audio>` 元素播放；`play()` 被自动播放策略拒绝 → 回退 **AudioContext 解码播放**（开始手势内已解锁，不受非手势限制）。
3. 未命中 mp3 且非微信 → 原生 SpeechSynthesis 兜底；**微信 WebView 不支持 Web Speech API → 静默推进**（定时器兜底，不挂起）。
- **token 防串音**：`lastSpeechToken` 单调递增，每次播报携带 token，settle 回调只认当前 token——快速连续计数（speakCount）时旧播报的 onEnd 不会误触发推进。
- **硬编码 TTS_MAP** 而非 fetch manifest：注释记录根因——真机微信内 fetch manifest 失败导致映射为空，「v44-v46 无声」。
- 环境音（雨声/心跳/和弦）全部 **Web Audio API 程序合成，零文件体积**；语音播报时 `duckDown`（音量压到 0.2x），播完 `duckUp`。

### 3.3 本地存储 `storage.js`（161 行，小而正确）
- key 前缀隔离（`morning:`），**读取一律返回 clone，绝不外泄内部引用**。
- `mergeSettings`：**结构以默认为准、数值以存储为准**——新增设置项自动获得默认值，旧数据无缝升级；exercises 按数组 id 对齐合并。
- `dayType` 归一化：显式值优先，旧记录按 `resistanceTimeSec` 推导——**数据迁移零成本**。
- `getRecommendedMode`：「隔天抗阻」判定（昨天完成 full → 今天 stretch），状态推导而非存储状态，避免脏状态。

### 3.4 Service Worker `sw.js`（99 行，每条策略带踩坑根因）
分层缓存，**每条注释都是真机教训**：

| 资源 | 策略 | 原因（注释原文） |
|---|---|---|
| `sw.js` 自身 | 网络优先 | 保证新版本能及时更新 |
| HTML 导航 + CSS | 网络优先 | 微信 WebView 懒检查 SW 更新，cache-first 把旧 HTML/样式锁死在旧版 |
| JS | 网络优先 | 真机微信 SW 缓存把 audio.js 卡在旧版，代码改动长期不生效 |
| 其余（图片/音频） | 缓存优先 | 静态资源，离线优先 |

版本号（`morning-v86`）变更 → activate 阶段清旧缓存 + `skipWaiting`/`clients.claim`。离线兜底统一回落 `index.html`。

## 4. 可迁移手法 × OpenBiliClaw 落点

| # | 手法 | 源码位置 | OpenBiliClaw 落点 |
|---|---|---|---|
| 1 | **事件表 + 预排程双轨定时**（tick 兜底 + 敏感 token 独立 setTimeout，恢复时按已过秒数跳过） | `morning/engine.js:181-237,287-329` | 任何按秒推进的 UI：阅读计划看板进度、定时抓取任务的进度条、番茄钟类功能。核心是「节奏敏感的用 setTimeout 预排，粗粒度的用 tick 兜底」 |
| 2 | **三通道语音降级 + token 防串音 + 超时兜底**（mp3→AudioContext 解码→原生 TTS→静默推进；onEnd 只认当前 token） | `morning/audio.js:120-354` | 若给 Web UI 做「朗读收藏库/晨间简报/训练语音」直接抄这套骨架；token 模式适用于一切「快速连续异步操作只认最新一次」场景 |
| 3 | **SW 分层缓存 + 注释带根因**（HTML/CSS/JS 网络优先、静态缓存优先） | `morning/sw.js:39-98` | Web UI 现在无离线能力；上 PWA 时照抄分层策略。**注意**：我们 UI 走 `/web/*` 动态路由，HTML 网络优先正合适 |
| 4 | **mergeSettings：结构以默认为准、数值以存储为准** + 读取即 clone | `morning/storage.js:42-57,31-34` | 前端设置/偏好持久化的标准姿势（对比我们扩展现有的直接 JSON 存取） |
| 5 | **数据归一化兼容旧记录**（dayType 显式优先、旧数据推导） | `morning/storage.js:37-40` | 任何 schema 演进场景（conversation_archive/oss_research 加新字段时） |
| 6 | **环境音程序合成 + ducking**（零音频文件，语音时自动压低） | `morning/audio.js:443-632` | 未来任何带播报的功能；也适合「专注模式」类小工具 |
| 7 | **llms.txt**（给 LLM 看的项目说明书） | `llms.txt` | `AGENTS.md` 思路的 Web 标准版；可在 README 旁补一份 |
| 8 | **隐私即特性的产品叙事**（无服务器无账号，README 开篇明说「不适合谁」） | `README.md:19-75` | 文档写作范式：先讲边界再讲价值 |

## 5. 不建议照搬

- **两套 morning/evening 目录各复制一份同构代码**（audio/engine/storage 各两份）——我们用模块复用即可，这种「每子应用自包含」是为它零构建的部署形态服务的。
- **硬编码 68 条 TTS_MAP**——它有 `tts/gen-*.js` 生成脚本保证一致，我们若做语音应该生成而非手写。
- **`_dbg` 全局调试日志**——作者自己注释「验证完删除」，属临时脚手架。

## 6. 精读文件清单

1. `morning/engine.js` — 事件表 + 预排程双轨定时状态机（最值钱）
2. `morning/audio.js` — 三通道语音降级 + token 防串音
3. `morning/sw.js` — 分层缓存策略（每条注释带真机根因）
4. `morning/storage.js` — 纯净持久化 + 数据迁移归一化
5. `README.md` — 产品叙事与隐私设计
