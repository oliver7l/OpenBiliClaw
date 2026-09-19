/* ==========================================================================
   宝贝成长册 · Service Worker
   --------------------------------------------------------------------------
   设计原则：白名单式拦截。
   - 只接管「静态外壳 + CDN 脚本」，云 API / 云存储 / 带鉴权头的请求一律放行，
     绝不介入数据链路（避免缓存到别人的数据或把写操作缓存住）。
   - 页面外壳：网络优先，离线回退缓存（保留「离线也能打开」的能力）。
   - 静态资源：先给缓存、后台更新（stale-while-revalidate），保证打开快。
   改版本号 = 全量换新缓存，旧缓存自动清理。
   ========================================================================== */

const VERSION = 'v2-20260918';
const SHELL_CACHE = 'bgz-shell-' + VERSION;   // 页面外壳
const ASSET_CACHE = 'bgz-asset-' + VERSION;   // 同源静态资源 / 云图片
const CDN_CACHE   = 'bgz-cdn-' + VERSION;     // 跨域脚本（云 SDK）

// 首次安装就预取，保证「装到桌面后第一次离线打开」也是完整的
const SHELL = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon.png',
  '/icons/favicon-32.png'
];

// 唯一的跨域依赖：云端数据 SDK。离线要能打开，它必须也在缓存里。
const CDN = [
  'https://cdn.jsdelivr.net/npm/@tencent-ai/workbuddy-cloud-sdk@dev/lib/index.global.js'
];

// 缓存条数上限：云图片是签名 URL，每次可能不同，加个闸门防止无限膨胀
const MAX_ASSET = 240;

// 这些 destination 才算「静态资源」，其余（= 空中字符串的 fetch/XHR）一律放行
const STATIC_DEST = ['script', 'style', 'font', 'manifest'];

/* ------------------------------ 安装 / 激活 ------------------------------ */

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const shell = await caches.open(SHELL_CACHE);
    await shell.addAll(SHELL.map(u => new Request(u, { cache: 'reload' })));

    // 跨域脚本拿到的是 opaque 响应（status=0），cache.add() 会因状态校验直接抛错，
    // 必须用 fetch + cache.put()。踩过：用 add() 时预缓存永远静默失败。
    const cdn = await caches.open(CDN_CACHE);
    await Promise.all(CDN.map(async u => {
      try {
        const res = await fetch(new Request(u, { mode: 'no-cors' }));
        if (res) await cdn.put(u, res);
      } catch (e) { /* CDN 不通不阻塞安装；之后首次真实请求会补缓存 */ }
    }));

    await self.skipWaiting();   // 新版本立即待命
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const alive = [SHELL_CACHE, ASSET_CACHE, CDN_CACHE];
    const keys = await caches.keys();
    await Promise.all(
      keys.filter(k => k.startsWith('bgz-') && !alive.includes(k))
          .map(k => caches.delete(k))
    );
    await self.clients.claim();  // 立刻接管已开的页面
  })());
});

/* -------------------------------- 请求分流 -------------------------------- */

self.addEventListener('fetch', event => {
  const req = event.request;

  // 只处理读请求；写操作（上传照片、发动态）绝不介入
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // ① 跨域：只认那张「CDN 白名单」，其余跨域（云存储、第三方）直接放行
  if (url.origin !== self.location.origin) {
    if (CDN.indexOf(url.href) !== -1) {
      event.respondWith(cacheFirst(req, CDN_CACHE));
    }
    return;
  }

  // ② 带鉴权头的请求：放行。缓存 key 只认 URL，带鉴权的内容若被缓存会串号
  if (req.headers.has('authorization')) return;

  // ③ 页面导航：网络优先，断网回退外壳
  if (req.mode === 'navigate') {
    event.respondWith(networkFirstShell(req));
    return;
  }

  // ④ 同源图片：只缓存「带查询串」的（= 签名 URL，天然按 token 隔离）。
  //    不带查询串的同源图片可能靠 Cookie 鉴权，缓存了会让不同账号互相看到，放行。
  if (req.destination === 'image') {
    if (url.search) event.respondWith(staleWhileRevalidate(req, ASSET_CACHE));
    return;
  }

  // ⑤ 其余同源静态资源：先给缓存、后台更新
  if (STATIC_DEST.indexOf(req.destination) !== -1) {
    event.respondWith(staleWhileRevalidate(req, ASSET_CACHE));
    return;
  }

  // ⑥ 兜底：一律放行（云 API 的 fetch/XHR 走的都是这里）
});

/* -------------------------------- 策略实现 -------------------------------- */

// 网络优先：拿到新页面就顺手更新缓存；断网时给回缓存的外壳
async function networkFirstShell(req) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const res = await fetch(req);
    if (res && res.ok) {
      cache.put('/', res.clone()).catch(() => {});
    }
    return res;
  } catch (err) {
    const hit = (await cache.match('/')) || (await cache.match('/index.html'));
    if (hit) return hit;
    return new Response('离线中，且本机还没有缓存到这个页面。', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' }
    });
  }
}

// 缓存优先：命中就直接给（跨域脚本用，避免每次开页面都去 CDN 拉）
async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res) cache.put(req, res.clone()).catch(() => {});
    return res;
  } catch (err) {
    return new Response('', { status: 504, statusText: 'offline' });
  }
}

// 先给缓存、后台更新：打开快，且下次进来就是最新
async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req);

  const network = fetch(req).then(res => {
    if (res && (res.status === 200 || res.type === 'opaque')) {
      cache.put(req, res.clone())
           .then(() => trimCache(cacheName, MAX_ASSET))
           .catch(() => {});
    }
    return res;
  }).catch(() => null);

  if (hit) return hit;

  const res = await network;
  if (res) return res;
  return new Response('', { status: 504, statusText: 'offline' });
}

// 缓存条数封顶，先删最旧的
async function trimCache(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= max) return;
  await Promise.all(keys.slice(0, keys.length - max).map(k => cache.delete(k)));
}

/* --------------------------- 与页面的消息通道 --------------------------- */

// 页面点到「立即更新」时，跳过等待并让新版本接管
self.addEventListener('message', event => {
  if (event.data === 'skip-waiting') self.skipWaiting();
});
