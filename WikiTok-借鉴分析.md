# WikiTok 借鉴分析（指向知乎首页卡片化扩展）

> 仓库：`github.com/IsaacGemal/wikitok`（MIT，2025-02）
> 本地克隆：`/Volumes/固态硬盘1T/002-探索项目/_ref_wikitok`
> 一句话定位：**TikTok 式全屏竖向卡片流，刷随机 Wikipedia 文章**。纯前端（React18 + TS + Tailwind + Vite），无后端，浏览器直接 fetch Wikipedia API。

---

## 0. 为什么它比 TraeWorkAssistant 更值得参考

| | TraeWorkAssistant | **WikiTok** |
|---|---|---|
| 形态 | Tauri 桌面应用（独立窗口） | **网页/卡片流（与扩展注入形态同构）** |
| 核心交互 | 多账号管理、签到 | **全屏竖向 snap 滚动 + 无限加载** |
| 对你扩展的可用度 | 仅工程手法可搬 | **feed 视觉与滚动机制可直接抄** |

你的目标 = 把知乎首页改成「卡片式竖向流」，WikiTok 就是这个 pattern 的最小可运行实现。

---

## 1. 核心可迁移的 5 招（带代码位置）

### ① 全屏竖向 snap 流（TikTok 感的核心）
`frontend/src/App.tsx:72` 容器 + `frontend/src/components/WikiCard.tsx:52` 卡片：

```tsx
// App.tsx — 滚动容器
<div className="h-screen w-full bg-black text-white overflow-y-scroll snap-y snap-mandatory hide-scroll">
// WikiCard.tsx — 每张卡占满一屏并吸附
<div className="h-screen w-full flex items-center justify-center snap-start relative" ...>
```

要点：**`snap-y snap-mandatory` + 每张卡 `h-screen snap-start`** 就是原生 CSS 实现的整屏吸附，无需 JS 计算滚动位置。

### ② IntersectionObserver 无限滚动（提前预取）
`frontend/src/App.tsx:17-38, 247`：

```tsx
const observer = new IntersectionObserver(handleObserver, {
  threshold: 0.1,
  rootMargin: "100px",   // 距底部还有 100px 就开始加载，无缝衔接
});
if (observerTarget.current) observer.observe(observerTarget.current);
// 末尾哨兵
<div ref={observerTarget} className="h-10 -mt-1" />
```

`handleObserver` 命中且非 loading 时调 `fetchArticles()`。这是无依赖无限流的标准写法，content script 里同样可用。

### ③ 图片预加载缓冲（滚动不卡顿的关键）
`frontend/src/hooks/useWikiArticles.ts:5-12, 65-76`：

```ts
const preloadImage = (src) => new Promise((res, rej) => {
  const img = new Image(); img.src = src; img.onload = res; img.onerror = rej;
});
// 渲染前先把本批缩略图全 preload 完
await Promise.allSettled(newArticles.filter(a=>a.thumbnail).map(a=>preloadImage(a.thumbnail.source)));
// 双缓冲：主列表用完→从 buffer 补→后台再填 buffer
if (forBuffer) setBuffer(newArticles);
else { setArticles(prev => [...prev, ...newArticles]); fetchArticles(true); }
```

**双缓冲（buffer + 主列表）** 是它流畅的秘诀：用户滚的是已预载好的主列表，下一屏在后台悄悄填 buffer。

### ④ 图片淡入 + 骨架 + 失败兜底
`frontend/src/components/WikiCard.tsx:23, 60-70`：

```tsx
const [imageLoaded, setImageLoaded] = useState(false);
<img loading="lazy" onLoad={()=>setImageLoaded(true)}
     onError={(e)=>{ console.error(...); setImageLoaded(true); /* 图挂了也显示文字 */ }}
     className={`... transition-opacity duration-300 ${imageLoaded?'opacity-100':'opacity-0'}`} />
{!imageLoaded && <div className="absolute inset-0 bg-gray-900 animate-pulse" />}
```

图未载完先显示 `animate-pulse` 骨架，载完淡入；`onError` 仍放行内容——避免单张坏图卡住整卡。

### ⑤ 全幅图 + 渐变蒙版 + 底部内容条
`frontend/src/components/WikiCard.tsx:54-118`：

```tsx
<img className="w-full h-full object-cover ..." />              // 满屏封面
<div className="absolute inset-0 bg-gradient-to-b from-black/20 to-black/60" />  // 渐变压暗
<div className="absolute ... bottom-[10vh] ... backdrop-blur-xs bg-black/30 ...">  // 底部内容：标题+摘要+Read more
```

这是「沉浸式卡片」的排版范式：图做底、渐变保证文字可读、内容贴底。

---

## 2. 映射到你的知乎扩展（4 个适配点）

| WikiTok 做法 | 你的扩展要注意 |
|---|---|
| 整个页面就是 feed（拥有 DOM） | 扩展是**注入知乎现有 DOM** → 把 feed 包在一个 `position:fixed` 覆盖层，或用 scoped class 前缀（如 `.obc-card`）隔离，避免和知乎样式互踩 |
| `fetch` Wikipedia 公共 API | 你读的是**知乎已渲染的 DOM**（或知乎内部 API）→ 「article 对象」来自 DOM 抽取，不是 fetch；抽取逻辑放 `content/zhihu.ts`，复用 §1 的 feed 渲染 |
| `h-screen` 整屏 | 知乎可能在容器里 → 用 `100dvh` 而非 `100vh`（移动端地址栏 bug），并 `overflow-y-scroll` 限定在你的覆盖层内 |
| 无后端、纯前端 | MV3 content script 受 CSP 限制：不能内联 `<script>`，但 `fetch` 知乎 API 需 `host_permissions` 里已有 `*://*.zhihu.com/*`（manifest 已含）→ 可直接用 |

**直接能复用的三块**：① snap 滚动容器结构 ② IntersectionObserver 哨兵 + rootMargin 预取 ③ 双缓冲预加载。这三块与数据源无关，抄过去就能让知乎卡片流同样丝滑。

---

## 3. 不建议照搬的

- **整页接管**：WikiTok 是独立站点，可 `h-screen` 占满；扩展应保留知乎原有导航/不破坏登录态，做成「首页 feed 替换」而非整页覆盖。
- **`console.log` 调试代码**：`WikiCard.tsx:27` 留了 `console.log('Article data'...)`，落地时删掉。
- **点赞/分享/导出侧栏**：那是它的产品功能，与卡片化无关，除非你想给知乎卡加「收藏/稍后读」。

---

## 4. 关键文件清单（想深挖直接看）

- `frontend/src/App.tsx` — 滚动容器 + 无限滚动 orchestration
- `frontend/src/components/WikiCard.tsx` — **卡片组件（最该精读）**：全幅图、渐变、淡入、点赞/分享
- `frontend/src/hooks/useWikiArticles.ts` — **数据获取 + 双缓冲预加载**
- `frontend/src/components/ArticleList.tsx` — 无障碍列表（role/aria/tabIndex，可选参考）
- `frontend/src/languages.ts` — 多语言配置（对应你可能的多平台适配思路）
