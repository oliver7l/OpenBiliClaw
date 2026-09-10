(() => {
    const DEFAULT_API_BASE = "http://127.0.0.1:8420/api";
    const ENDPOINTS = {
      health: "/health",
      initStatus: "/init-status",
      startInit: "/init",
      recommendations: "/recommendations",
      refresh: "/recommendations/refresh",
      reshuffle: "/recommendations/reshuffle",
      append: "/recommendations/append",
      runtimeStatus: "/runtime-status",
      activityFeed: "/activity-feed",
      notificationPending: "/notifications/pending",
      notificationSent: "/notifications/sent",
      delightBatch: "/delight/pending-batch",
      delightRespond: "/delight/respond",
      profile: "/profile-summary",
      feedback: "/feedback",
      click: "/recommendation-click",
      chatTurns: "/chat/turns",
      chatRecommend: "/chat/recommend",
      interestProbeRespond: "/interest-probes/respond",
      avoidanceProbeRespond: "/avoidance-probes/respond",
      insightFeedback: "/insights/feedback",
      sourceShareSuggestion: "/config/source-share-suggestion",
      sourceCredentials: "/sources/credentials?reveal_keys=true",
      configProbe: "/config/probe-service",
      updateStatus: "/update-status",
      updateCheck: "/update/check",
      updateApply: "/update/apply",
      config: "/config?reveal_keys=true",
      watchLater: "/watch-later",
      favorites: "/favorites",
      profileEdit: "/profile/edit",
      profileEditState: "/profile/edit-state",
      subscriptions: "/subscriptions",
      subscriptionsStats: "/subscriptions/stats",
      poolAll: "/pool/all",
      poolFeed: "http://127.0.0.1:8421/api/pool/feed",
      savedStatus: "/saved-status",
    };
    // 注意：这些路径不带 /api 前缀——requestJson 会自动拼接 API base（默认 /api），
    // 若带前缀会拼成 /api/api/... 导致 404（与其余 ENDPOINTS 条目保持一致）。
    ENDPOINTS.userFeedback = "/user-feedback";
    ENDPOINTS.userFeedbackBatch = "/user-feedback/batch";

    ENDPOINTS.interestTags = "/interest-tags";
    ENDPOINTS.viewRecord = "/view-record";
    ENDPOINTS.viewDwell = "/view-dwell";
    ENDPOINTS.viewHistory = "/view-history";

    const state = {
      query: "",
      filter: "全部",
      activeFeedback: null,
      profile: null,
      editingProfile: false,
      profileEditState: null,
      initStatus: null,
      initReason: "",
      initBusy: false,
      initSelectedSources: ["bilibili"],
      activity: null,
      activityItems: [],
      activityCursor: "",
      activityHasMore: false,
      profileCognitionCursor: "",
      profileCognitionHasMore: false,
      delights: [],
      delightIndex: 0,
      delight: null,
      config: null,
      sourceStatus: null,
      sourceCredentials: null,
      runtimeStatus: null,
      runtimeSocket: null,
      customFilterSources: null,
      customContentTypes: null,
      customKeyword: "",
      customLimit: 50,
      poolAllItems: [],
      poolAllLoading: false,
      poolAllLoadCount: 50,
      poolFilterItems: [],
      poolFilterLoading: false,
      poolFilterPlatform: "全部",
      poolExploreFilters: {},
      videos: [],
      messages: [],
      messageListSnapshot: null,
      messageListDomLocked: false,
      resolvingMessageKeys: new Set(),
      resolvedMessageResults: new Map(),
      handledProbeKeys: new Set(),
      messageScrollTop: 0,
      messageChatDomain: "",
      messageChatPrompt: "",
      messageChatScope: "probe",
      messageChatSubjectTitle: "",
      chat: [
        { role: "agent", text: "你可以直接告诉我最近想多看什么、少看什么，或者评价一条推荐为什么准/不准。" }
      ]
    };

    const $ = (selector) => document.querySelector(selector);
    const grid = $("#videoGrid");
    const sourceFilterDefinitions = [
      { key: "bilibili", label: "B 站" },
      { key: "xiaohongshu", label: "小红书" },
      { key: "douyin", label: "抖音" },
      { key: "youtube", label: "YouTube" },
      { key: "twitter", label: "X (Twitter)" },
      { key: "zhihu", label: "知乎" },
      { key: "v2ex", label: "V2EX" },
      { key: "reddit", label: "Reddit" },
      { key: "wechat", label: "微信公众号" },
      { key: "xiaoyuzhou", label: "小宇宙" },
      { key: "rss", label: "RSS" },
      { key: "user_favorite", label: "用户收藏" },
    ];
    const sourceFilterOrder = sourceFilterDefinitions.map((source) => source.label);
    const contentTypeFilterDefinitions = [
      { key: "video", label: "视频" },
      { key: "article", label: "文章" },
      { key: "answer", label: "回答" },
      { key: "note", label: "笔记" },
      { key: "topic", label: "话题" },
      { key: "question", label: "问题" },
      { key: "post", label: "帖子" },
      { key: "tweet", label: "推文" },
      { key: "thread", label: "推文串" },
    ];
    const platformLabel = { bilibili: "B 站", youtube: "YouTube", douyin: "抖音", xiaohongshu: "小红书", xhs: "小红书", twitter: "X (Twitter)", x: "X (Twitter)", zhihu: "知乎", v2ex: "V2EX", reddit: "Reddit", wechat: "微信公众号", xiaoyuzhou: "小宇宙", rss: "RSS", user_favorite: "用户收藏" };
    const platformAliases = { bili: "bilibili", bilibili: "bilibili", xhs: "xiaohongshu", xiaohongshu: "xiaohongshu", rednote: "xiaohongshu", dy: "douyin", douyin: "douyin", tiktok: "douyin", yt: "youtube", youtube: "youtube", x: "twitter", twitter: "twitter", zh: "zhihu", zhihu: "zhihu" };
    const textCardContentTypes = new Set(["tweet", "thread", "answer", "article", "question"]);
    // v0.3.118+: bilibili is selectable like every other source — default
    // checked (recommended) but no longer forced. At least one source must
    // stay checked to start.
    const INIT_SOURCE_OPTIONS = [
      { key: "bilibili", label: "B 站", defaultChecked: true },
      { key: "xiaohongshu", label: "小红书", defaultChecked: true },
      { key: "douyin", label: "抖音", defaultChecked: true },
      { key: "youtube", label: "YouTube", defaultChecked: true },
      { key: "twitter", label: "X", defaultChecked: true },
      { key: "zhihu", label: "知乎", defaultChecked: true },
      { key: "v2ex", label: "V2EX", defaultChecked: true },
      { key: "reddit", label: "Reddit", defaultChecked: true },
      { key: "wechat", label: "微信公众号", defaultChecked: true },
      { key: "xiaoyuzhou", label: "小宇宙", defaultChecked: true },
      { key: "rss", label: "RSS", defaultChecked: true },
    ];
    const INIT_SOURCE_LOGIN_HINT = "勾选要纳入初始化的平台（至少一个）。使用某个平台前，请先在当前浏览器登录该平台账号；勾选会同时开启该来源。";
    const INIT_REASON_TEXT = {
      unsupported_runtime: "当前运行环境不支持图形化初始化，请改用 CLI 初始化入口。",
      already_running: "初始化正在进行中。",
      bilibili_not_logged_in: "还没检测到 B 站登录。",
      llm_not_ready: "AI 服务还没配好或当前不可用。",
      embedding_not_ready: "向量模型还没就绪，请等待 bge-m3 下载完成或修复 Ollama 后重试。",
      already_initialized: "已经初始化过了；如需重建，请到设置页。",
      local_only: "只能在本机发起初始化。",
      no_sources_selected: "至少勾选一个数据来源。",
      internal_error: "初始化过程中出错了，请稍后重试。",
      none: ""
    };
    const INIT_STATUS_POLL_MS = Number(window.__OBC_TEST_INIT_POLL_MS) || 3000;
    const INIT_STATUS_START_POLL_MS = Number(window.__OBC_TEST_INIT_START_POLL_MS) || 1200;
    const INIT_STATUS_WATCHDOG_MS = Number(window.__OBC_TEST_INIT_WATCHDOG_MS) || 15000;
    const INIT_FIRST_POOL_WAIT_TEXT = "画像已生成，正在整理首轮内容池；等第一批内容可刷后才算初始化完成。";
    const CHAT_PLACEHOLDERS = [
      "说说你最近怎么想——你是什么样的人、喜欢什么、讨厌什么，都可以直接说。",
      "比如：我喜欢慢慢讲清楚的长视频，讨厌标题党和故意搞悬念的。",
      "比如：最近老点开国际新闻和商业分析，想知道自己到底在找什么。",
      "比如：我经常刷到一半就退出，好像注意力很难集中。",
      "比如：我偏爱小众冷门内容，热门排行榜上的反而不太想看。",
      "比如：这阵子心情一般，老看一些治愈系的东西。",
      "比如：我在学一门新技能，想看看有没有靠谱教程。"
    ];
    let chatPlaceholderIndex = 0;
    let chatPlaceholderTimer = null;
    let activityRailHeightFrame = 0;
    let backendHydrationTimer = null;
    let backendHydrationInFlight = false;
    let backendHydrationPending = false;
    let initPollTimer = null;
    let initRefreshInFlight = false;
    let initRefreshPending = false;
    let activityPageRefreshTimer = null;
    let activityPageRefreshInFlight = false;
    let activityPageRefreshPending = false;

    function debounceAsync(fn, delayMs = 1000) {
      let timer = null;
      let inFlight = false;
      let pending = false;
      const run = async () => {
        if (inFlight) { pending = true; return; }
        inFlight = true;
        try { await fn(); } finally {
          inFlight = false;
          if (pending) { pending = false; timer = window.setTimeout(run, 0); }
        }
      };
      return () => {
        if (timer !== null) window.clearTimeout(timer);
        timer = window.setTimeout(() => { timer = null; run(); }, delayMs);
      };
    }

    const scheduleDelightQueueRefresh = debounceAsync(() => window.fetchDelightQueue(), 1000);

    async function runBackendHydration() {
      if (backendHydrationInFlight) {
        backendHydrationPending = true;
        return;
      }
      backendHydrationInFlight = true;
      try {
        await window.hydrateFromBackend();
      } finally {
        backendHydrationInFlight = false;
        if (backendHydrationPending) {
          backendHydrationPending = false;
          backendHydrationTimer = window.setTimeout(() => {
            backendHydrationTimer = null;
            void runBackendHydration();
          }, 0);
        }
      }
    }

    function scheduleBackendHydration() {
      if (backendHydrationTimer !== null) window.clearTimeout(backendHydrationTimer);
      backendHydrationTimer = window.setTimeout(() => {
        backendHydrationTimer = null;
        void runBackendHydration();
      }, 1000);
    }

    async function runActivityPageRefresh() {
      if (activityPageRefreshInFlight) {
        activityPageRefreshPending = true;
        return;
      }
      activityPageRefreshInFlight = true;
      try {
        await window.loadActivityPage({ reset: true });
      } finally {
        activityPageRefreshInFlight = false;
        if (activityPageRefreshPending) {
          activityPageRefreshPending = false;
          activityPageRefreshTimer = window.setTimeout(() => {
            activityPageRefreshTimer = null;
            void runActivityPageRefresh();
          }, 0);
        }
      }
    }

    function scheduleActivityPageRefresh() {
      if (activityPageRefreshTimer !== null) window.clearTimeout(activityPageRefreshTimer);
      activityPageRefreshTimer = window.setTimeout(() => {
        activityPageRefreshTimer = null;
        void runActivityPageRefresh();
      }, 1000);
    }

    function syncActivityRailHeight() {
      const rail = document.querySelector('[data-od-id="activity-rail"]');
      const delight = document.getElementById("delightBanner");
      if (!rail || !delight || !window.matchMedia("(min-width: 1181px)").matches) {
        rail?.style.removeProperty("--activity-rail-max-height");
        return;
      }
      const height = Math.ceil(delight.getBoundingClientRect().height);
      if (height > 0) rail.style.setProperty("--activity-rail-max-height", `${height}px`);
    }

    function scheduleActivityRailHeightSync() {
      if (activityRailHeightFrame) cancelAnimationFrame(activityRailHeightFrame);
      activityRailHeightFrame = requestAnimationFrame(() => {
        activityRailHeightFrame = 0;
        syncActivityRailHeight();
      });
    }

    function showFatal(error, context = "页面启动") {
      const message = error?.message || String(error || "未知错误");
      const banner = $("#fatalBanner");
      if (banner) {
        banner.textContent = `${context}出现问题：${message}`;
        banner.classList.add("is-open");
      }
      const status = $("#statusLabel");
      if (status) status.textContent = `${context}异常`;
      const summary = $("#runtimeSummary");
      if (summary) summary.textContent = message;
      console.error(context, error);
    }

    window.addEventListener("error", (event) => showFatal(event.error || event.message, "页面脚本"));
    window.addEventListener("unhandledrejection", (event) => showFatal(event.reason, "异步加载"));

    function storageGet(key) {
      try { return window.localStorage?.getItem(key) || ""; } catch { return ""; }
    }

    function storageSet(key, value) {
      try { window.localStorage?.setItem(key, value); } catch {}
    }

    const DISMISS_ON_RESHUFFLE_KEY = "openbiliclaw.dismissOnReshuffle";
    state.dismissOnReshuffle = storageGet(DISMISS_ON_RESHUFFLE_KEY) === "1";
    const DISPLAY_MODE_KEY = "openbiliclaw.displayMode";
    state.displayMode = storageGet(DISPLAY_MODE_KEY) || "card";
    const SIDE_DRAWER_OPEN_KEY = "openbiliclaw.sideDrawerOpen";
    const DELIGHT_QUEUE_LIMIT_KEY = "openbiliclaw.webui.delightQueueLimit";
    const STAR_REPO_URL = "https://github.com/whiteguo233/OpenBiliClaw";
    const STAR_REPO_SLUG = "whiteguo233/OpenBiliClaw";
    const STAR_COUNT_CACHE_KEY = "openbiliclaw.webui.starCount";
    const STAR_COUNT_TTL_MS = 12 * 60 * 60 * 1000;

    function formatStarCount(n) {
      if (typeof n !== "number" || !Number.isFinite(n)) return "";
      if (n >= 10000) return `${(n / 1000).toFixed(0)}k`;
      if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
      return String(n);
    }

    function showStarCount(n) {
      const el = $("#starCount");
      const text = formatStarCount(n);
      if (el && text) {
        el.textContent = text;
        el.hidden = false;
      }
    }

    async function loadStarCount() {
      const el = $("#starCount");
      if (!(el instanceof HTMLElement)) return;
      let cachedTime = 0;
      try {
        const raw = storageGet(STAR_COUNT_CACHE_KEY);
        if (raw) {
          const { n, t } = JSON.parse(raw);
          if (typeof n === "number") {
            showStarCount(n);
            cachedTime = typeof t === "number" ? t : 0;
          }
        }
      } catch {
        cachedTime = 0;
      }
      if (Date.now() - cachedTime < STAR_COUNT_TTL_MS) return;
      try {
        const res = await fetch(`https://api.github.com/repos/${STAR_REPO_SLUG}`, {
          headers: { Accept: "application/vnd.github+json" },
        });
        if (!res.ok) return;
        const data = await res.json();
        const n = data?.stargazers_count;
        if (typeof n === "number") {
          showStarCount(n);
          storageSet(STAR_COUNT_CACHE_KEY, JSON.stringify({ n, t: Date.now() }));
        }
      } catch {
        // Offline / rate-limited: keep the CTA visible without a count.
      }
    }

    function bindStarButton() {
      const button = $("#starButton");
      if (!(button instanceof HTMLElement)) return;
      button.addEventListener("click", () => {
        window.open(STAR_REPO_URL, "_blank", "noopener,noreferrer");
      });
      void loadStarCount();
    }

    function normalizeBackendHost(host) {
      const trimmed = String(host || "").trim();
      if (!trimmed) return "127.0.0.1";
      try { return new URL(trimmed).hostname || "127.0.0.1"; } catch { return trimmed.replace(/^https?:\/\//, "").replace(/\/.*$/, ""); }
    }

    function safeBind(selector, eventName, handler) {
	      const element = $(selector);
	      if (!element) { showFatal(new Error(`缺少元素 ${selector}`), "绑定交互"); return; }
	      element.addEventListener(eventName, handler);
	    }

	    function eventDelegation(parentSelector, childSelector, eventName, handler) {
	      const parent = $(parentSelector);
	      if (!parent) { showFatal(new Error(`缺少父元素 ${parentSelector}`), "绑定交互"); return; }
	      parent.addEventListener(eventName, (event) => {
	        const target = event.target.closest(childSelector);
	        if (target) handler(event);
	      });
	    }

    function locationApiDefault() {
      try {
        const loc = window.location;
        if (loc && /^https?:$/.test(loc.protocol) && loc.hostname) {
          return { host: loc.hostname, port: loc.port || (loc.protocol === "https:" ? "443" : "80") };
        }
      } catch { /* file:// or no window — fall through */ }
      return { host: "127.0.0.1", port: "8420" };
    }

    function getApiBase() {
      // Default to a *relative* same-origin path so the request carries the page
      // scheme/host/port exactly (correct under an HTTPS reverse proxy and PWA
      // launch) and the HttpOnly session cookie is sent automatically. An
      // explicit saved/typed backend setting still wins (cross-origin mode).
      const typedHost = ($("#backendHost")?.value || storageGet("openbiliclaw.webui.backendHost") || "").trim();
      const typedPort = String($("#backendPort")?.value || storageGet("openbiliclaw.webui.backendPort") || "").trim();
      if (!typedHost && !typedPort) {
        return "/api";
      }
      const def = locationApiDefault();
      const host = normalizeBackendHost(typedHost || def.host);
      const port = (typedPort || def.port).trim() || def.port;
      const proto = (typeof location !== "undefined" && location.protocol === "https:") ? "https" : "http";
      return `${proto}://${host}:${port}/api`;
    }

    function restoreBackendEndpoint() {
      const host = storageGet("openbiliclaw.webui.backendHost");
      const port = storageGet("openbiliclaw.webui.backendPort");
      if (host) setInput("backendHost", normalizeBackendHost(host));
      if (port) setInput("backendPort", port);
    }

    function persistBackendEndpoint() {
      const def = locationApiDefault();
      const host = normalizeBackendHost($("#backendHost")?.value || def.host);
      const port = String($("#backendPort")?.value || def.port).trim() || def.port;
      setInput("backendHost", host);
      setInput("backendPort", port);
      storageSet("openbiliclaw.webui.backendHost", host);
      storageSet("openbiliclaw.webui.backendPort", port);
      return { host, port };
    }

    function getDelightQueueLimit() {
      const raw = $("#delightQueueLimit")?.value || storageGet(DELIGHT_QUEUE_LIMIT_KEY) || "20";
      const limit = Number.parseInt(String(raw), 10);
      if (!Number.isFinite(limit)) return 20;
      return Math.max(1, Math.min(100, limit));
    }

    function restoreFrontendSettings(config = state.config || {}) {
      const configuredLimit = config.scheduler?.delight_queue_limit;
      const limit = configuredLimit || storageGet(DELIGHT_QUEUE_LIMIT_KEY) || "20";
      setInput("delightQueueLimit", String(limit));
      renderReshuffleToggle();
    }

    function persistFrontendSettings() {
      const limit = getDelightQueueLimit();
      setInput("delightQueueLimit", String(limit));
      storageSet(DELIGHT_QUEUE_LIMIT_KEY, String(limit));
      storageSet(DISMISS_ON_RESHUFFLE_KEY, state.dismissOnReshuffle ? "1" : "0");
      renderReshuffleToggle();
      return { delightQueueLimit: limit, dismissOnReshuffle: state.dismissOnReshuffle };
    }

    function getRuntimeStreamUrl() {
      const base = getApiBase();
      let url;
      if (base.startsWith("/")) {
        // relative same-origin base → build an absolute ws(s) URL from the page
        const proto = (typeof location !== "undefined" && location.protocol === "https:") ? "wss" : "ws";
        const host = (typeof location !== "undefined" && location.host) || "127.0.0.1:8420";
        url = `${proto}://${host}${base}/runtime-stream`;
      } else {
        url = `${base.replace(/^http/, "ws")}/runtime-stream`;
      }
      // cross-origin handshake can't send a cookie → carry the bearer token
      return appendToken(url);
    }

    // ── Password gate (login overlay) ────────────────────────────
    let _authOverlayShown = false;
    const SESSION_TOKEN_KEY = "openbiliclaw.session_token";

    // Cross-origin mode: the desktop UI points at a backend on a *different*
    // origin, so the same-origin cookie isn't sent. The server then issues a
    // finite bearer token (allowed_bearer_origins + ttl>0); we keep it in
    // sessionStorage and attach it as Authorization / ?token= (review r1#5).
    function isCrossOriginBase() {
      const base = getApiBase();
      if (!base || base.startsWith("/")) return false;
      try {
        return new URL(base).origin !== location.origin;
      } catch {
        return false;
      }
    }

    function getSessionToken() {
      if (!isCrossOriginBase()) return "";
      try {
        return sessionStorage.getItem(SESSION_TOKEN_KEY) || "";
      } catch {
        return "";
      }
    }

    function setSessionToken(token) {
      try {
        if (token) sessionStorage.setItem(SESSION_TOKEN_KEY, token);
        else sessionStorage.removeItem(SESSION_TOKEN_KEY);
      } catch { /* sessionStorage unavailable */ }
    }

    function withBearer(headers) {
      const token = getSessionToken();
      return token ? { ...(headers || {}), Authorization: `Bearer ${token}` } : (headers || {});
    }

    function appendToken(url) {
      const token = getSessionToken();
      if (!token) return url;
      return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
    }

    async function fetchAuthStatus() {
      try {
        const base = getApiBase() || DEFAULT_API_BASE;
        const res = await fetch(`${base}/auth/status`, {
          credentials: "same-origin",
          headers: withBearer(),
        });
        if (!res.ok) return { enabled: false, authenticated: true };
        return await res.json();
      } catch {
        return { enabled: false, authenticated: true };
      }
    }

    function handleAuthRequired() {
      // Mid-session token loss (expired / revoked): reload after re-login.
      showLoginOverlay();
    }

    function showLoginOverlay(onSuccess) {
      if (_authOverlayShown) return;
      _authOverlayShown = true;
      const overlay = document.createElement("div");
      overlay.id = "authOverlay";
      overlay.setAttribute("role", "dialog");
      overlay.setAttribute("aria-modal", "true");
      overlay.style.cssText =
        "position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;" +
        "background:rgba(20,28,46,0.55);backdrop-filter:blur(4px);";
      overlay.innerHTML =
        '<form id="authForm" autocomplete="off" style="width:min(360px,90vw);display:flex;flex-direction:column;gap:14px;' +
        'padding:28px 24px;background:#fff;border-radius:18px;box-shadow:0 18px 48px rgba(0,0,0,.22);">' +
        '<h2 style="margin:0;font-size:20px;color:#fb7299;text-align:center;">OpenBiliClaw</h2>' +
        '<p style="margin:0;font-size:14px;color:#60708c;text-align:center;">请输入访问密码</p>' +
        '<input id="authPassword" type="password" placeholder="密码" autocomplete="current-password" ' +
        'aria-label="访问密码" style="padding:12px 14px;font-size:15px;border:1px solid #e2e6ef;border-radius:10px;">' +
        '<button type="submit" style="padding:12px;font-size:15px;font-weight:600;color:#fff;background:#fb7299;' +
        'border:none;border-radius:10px;cursor:pointer;">登录</button>' +
        '<p id="authError" role="alert" hidden style="margin:0;font-size:13px;color:#ef4444;text-align:center;"></p>' +
        "</form>";
      document.body.appendChild(overlay);
      const input = overlay.querySelector("#authPassword");
      const button = overlay.querySelector("button");
      const errorEl = overlay.querySelector("#authError");
      input?.focus();

      const showError = (msg) => {
        if (!errorEl) return;
        errorEl.textContent = msg;
        errorEl.hidden = false;
        input?.select();
      };

      overlay.querySelector("#authForm")?.addEventListener("submit", async (event) => {
        event.preventDefault();
        const password = input?.value || "";
        if (!password) { showError("请输入密码"); return; }
        if (button) { button.disabled = true; button.textContent = "登录中…"; }
        if (errorEl) errorEl.hidden = true;
        try {
          const base = getApiBase() || DEFAULT_API_BASE;
          const res = await fetch(`${base}/auth/login`, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ password }),
          });
          const data = await res.json().catch(() => null);
          if (res.ok && data?.ok) {
            // Cross-origin bearer mode: the server returns a finite token here.
            if (data.token) setSessionToken(data.token);
            overlay.remove();
            _authOverlayShown = false;
            if (typeof onSuccess === "function") onSuccess();
            else location.reload();
            return;
          }
          if (res.status === 403) showError("此来源不被允许跨源登录（需配置 allowed_bearer_origins）");
          else if (res.status === 400) showError("跨源登录需设置有限有效期（session_ttl_hours>0）");
          else showError(res.status === 429 ? "尝试过于频繁，请稍后再试" : "密码错误");
        } catch {
          showError("无法连接后端，请稍后重试");
        } finally {
          if (button) { button.disabled = false; button.textContent = "登录"; }
        }
      });
    }

    function ensureAuthenticated() {
      return fetchAuthStatus().then((status) => {
        if (status && status.enabled && status.authenticated === false) {
          return new Promise((resolve) => showLoginOverlay(resolve));
        }
        return undefined;
      });
    }

    // 轻量 markdown 渲染：先转义防 XSS；支持 标题/粗斜体/行内与块级代码/引用/有序无序列表/链接/分隔线/段落。不依赖外部库。
    function renderMarkdown(src) {
      if (!src) return "";
      const esc = escapeHtml(src);
      const fenced = [];
      let s = esc.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) => {
        const idx = fenced.length;
        fenced.push('<pre class="md-pre"><code>' + code.replace(/\n$/, "") + "</code></pre>");
        return "@@FENCE@@" + idx + "@@";
      });
      const lines = s.split("\n");
      const out = [];
      let i = 0;
      let inList = null;
      const flushList = () => { if (inList) { out.push("</" + inList + ">"); inList = null; } };
      const inline = (text) => {
        const codes = [];
        text = text.replace(/`([^`]+)`/g, (m2, c) => { const k = codes.length; codes.push('<code class="md-code">' + c + "</code>"); return "@@IC@@" + k + "@@"; });
        text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m2, t, url) => {
          const safe = /^(https?:\/\/|\/|#|mailto:)/i.test(url) ? url : "#";
          return '<a class="md-link" href="' + safe + '" target="_blank" rel="noopener noreferrer">' + t + "</a>";
        });
        text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
        text = text.replace(/\*([^*]+)\*/g, "<em>$1</em>").replace(/_([^_]+)_/g, "<em>$1</em>");
        text = text.replace(/@@IC@@(\d+)@@/g, (m2, k) => codes[+k]);
        return text;
      };
      while (i < lines.length) {
        const line = lines[i];
        const fb = line.match(/^@@FENCE@@(\d+)@@$/);
        if (fb) { flushList(); out.push(fenced[+fb[1]]); i++; continue; }
        if (/^(---|\*\*\*|___)\s*$/.test(line)) { flushList(); out.push('<hr class="md-hr">'); i++; continue; }
        const h = line.match(/^(#{1,6})\s+(.*)$/);
        if (h) { flushList(); const lvl = h[1].length; out.push("<h" + lvl + ' class="md-h' + lvl + '">' + inline(h[2].trim()) + "</h" + lvl + ">"); i++; continue; }
        if (/^&gt;\s?/.test(line)) {
          flushList();
          const buf = [];
          while (i < lines.length && /^&gt;\s?/.test(lines[i])) { buf.push(inline(lines[i].replace(/^&gt;\s?/, ""))); i++; }
          out.push('<blockquote class="md-quote">' + buf.join("<br>") + "</blockquote>");
          continue;
        }
        const ul = line.match(/^[-*+]\s+(.*)$/);
        const ol = line.match(/^\d+\.\s+(.*)$/);
        if (ul || ol) {
          const type = ul ? "ul" : "ol";
          if (inList !== type) { flushList(); out.push("<" + type + ' class="md-' + type + '">'); inList = type; }
          out.push("<li>" + inline((ul ? ul[1] : ol[1]).trim()) + "</li>");
          i++; continue;
        }
        if (/^\s*$/.test(line)) { flushList(); i++; continue; }
        flushList();
        const para = [line];
        i++;
        while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,6})\s/.test(lines[i]) && !/^[-*+]\s/.test(lines[i]) && !/^\d+\.\s/.test(lines[i]) && !/^&gt;\s?/.test(lines[i]) && !/^(---|\*\*\*|___)\s*$/.test(lines[i]) && !/^@@FENCE@@\d+@@$/.test(lines[i])) {
          para.push(lines[i]); i++;
        }
        out.push('<p class="md-p">' + inline(para.join("<br>")) + "</p>");
      }
      flushList();
      return out.join("");
    }

    // Decode source-provided entities for display text only; every later HTML or attribute output must still escape by context.
    function decodeHtmlEntities(value) {
      return String(value ?? "").replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos|#39);/g, (match, entity) => {
        if (entity === "amp") return "&";
        if (entity === "lt") return "<";
        if (entity === "gt") return ">";
        if (entity === "quot") return '"';
        if (entity === "apos" || entity === "#39") return "'";
        if (entity.startsWith("#x")) {
          const codePoint = Number.parseInt(entity.slice(2), 16);
          return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
        }
        if (entity.startsWith("#")) {
          const codePoint = Number.parseInt(entity.slice(1), 10);
          return Number.isFinite(codePoint) ? String.fromCodePoint(codePoint) : match;
        }
        return match;
      });
    }

    function urlHostMatches(url, hostnames) {
      const text = String(url || "").trim();
      if (!text) return false;
      try {
        const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`;
        const host = new URL(candidate).hostname.toLowerCase();
        return hostnames.some((hostname) => host === hostname || host.endsWith(`.${hostname}`));
      } catch {
        return false;
      }
    }

    function normalizeSourcePlatform(item) {
      const explicit = String(item?.source_platform ?? item?.platform ?? "").trim().toLowerCase();
      if (platformAliases[explicit]) return platformAliases[explicit];
      const url = String(item?.content_url ?? "").trim().toLowerCase();
      if (url) {
        if (url.includes("bilibili.com") || url.includes("b23.tv")) return "bilibili";
        if (url.includes("xiaohongshu.com") || url.includes("xhslink.com")) return "xiaohongshu";
        if (url.includes("douyin.com")) return "douyin";
        if (url.includes("youtube.com") || url.includes("youtu.be")) return "youtube";
        if (urlHostMatches(url, ["x.com", "twitter.com"])) return "twitter";
        if (urlHostMatches(url, ["zhihu.com", "zhuanlan.zhihu.com"])) return "zhihu";
        return "web";
      }
      if (String(item?.bvid ?? "").trim()) return "bilibili";
      return explicit || "bilibili";
    }

    function normalizeRecommendation(item) {
      const contentId = String(item?.content_id ?? item?.bvid ?? "");
      return {
        id: Number(item?.id ?? Date.now()),
        bvid: String(item?.bvid ?? contentId),
        content_id: contentId,
        title: decodeHtmlEntities(item?.title ?? "未命名内容"),
        up: decodeHtmlEntities(item?.up_name ?? item?.up ?? "未知创作者"),
        cover_url: normalizeImageUrl(item?.cover_url ?? item?.cover ?? item?.pic ?? item?.thumbnail_url ?? item?.thumbnail ?? item?.image_url),
        content_url: String(item?.content_url ?? ""),
        topic: decodeHtmlEntities(item?.topic_label ?? item?.topic ?? "未归类"),
        platform: normalizeSourcePlatform(item),
        content_type: String(item?.content_type ?? "video").trim().toLowerCase() || "video",
        body_text: decodeHtmlEntities(item?.body_text ?? ""),
        duration: String(item?.duration ?? ""),
        presented: Boolean(item?.presented),
        feedback_type: String(item?.feedback_type ?? item?.feedback ?? ""),
        pool_status: String(item?.pool_status ?? item?.status ?? ""),
        reason: decodeHtmlEntities(item?.expression ?? item?.reason ?? "后端暂未返回解释。")
      };
    }

    function recommendationKey(item) {
      return String(item?.bvid || item?.content_id || item?.id || "");
    }

    function shouldRemoveRecommendationAfterFeedback(feedbackType) {
      const normalized = String(feedbackType || "").trim().toLowerCase();
      return normalized === "dislike" || normalized === "dismiss";
    }

    function isFeedbackedRecommendation(item) {
      const feedback = String(item?.feedback_type || item?.feedback || "").trim().toLowerCase();
      const poolStatus = String(item?.pool_status || item?.status || "").trim().toLowerCase();
      return shouldRemoveRecommendationAfterFeedback(feedback) || (poolStatus === "feedbacked" && !feedback);
    }

    function normalizeRecommendationList(items) {
      return window.asArray(items).map(normalizeRecommendation).filter((item) => !isFeedbackedRecommendation(item));
    }

    async function requestJson(path, options = {}) {
      try {
        return await requestJsonStrict(path, { ...options, timeoutMs: options.timeoutMs ?? 15000 });
      } catch {
        return null;
      }
    }

    async function requestJsonStrict(path, options = {}) {
      const base = options.baseUrl || getApiBase() || DEFAULT_API_BASE;
      const { baseUrl, timeoutMs = 60000, signal, ...fetchOptions } = options;
      // Same-origin: send the session cookie + CSRF header on EVERY request
      // (incl. GET) so state-changing GETs like /api/recommendations are
      // covered (§4.8). Cross-origin: attach the bearer token instead.
      fetchOptions.credentials = "same-origin";
      fetchOptions.headers = { ...(fetchOptions.headers || {}), "X-OBC-Auth": "1" };
      fetchOptions.headers = withBearer(fetchOptions.headers);
      const controller = signal ? null : new AbortController();
      const timeoutId = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
      try {
        // 绝对 URL（http(s)://…，如独立推荐流服务 127.0.0.1:8421）不再
        // 叠加 API base，避免拼出 "http://host:port/apihttp://…" 的坏地址。
        const url = /^https?:\/\//i.test(path) ? path : `${base}${path}`;
        const response = await fetch(url, { ...fetchOptions, signal: signal || controller?.signal });
        const contentType = response.headers.get("content-type") || "";
        const details = contentType.includes("application/json") ? await response.json().catch(() => null) : await response.text().catch(() => "");
        if (!response.ok) {
          if (response.status === 401) {
            setSessionToken("");  // drop a stale bearer token before re-login
            handleAuthRequired();
          }
          const error = new Error(configErrorMessage(details) || `${path} 请求失败：HTTP ${response.status}`);
          error.status = response.status;
          error.details = details;
          throw error;
        }
        return details;
      } catch (error) {
        if (error?.name === "AbortError") throw new Error(`${path} 请求超时，请稍后刷新确认是否已写入。`);
        throw error;
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
      }
    }

    function configErrorMessage(details) {
      if (!details) return "";
      if (typeof details === "string") return details;
      const issues = details.config?.issues || details.detail?.config?.issues;
      if (Array.isArray(issues) && issues.length) {
        return issues.map((issue) => `${issue.severity || "warning"}: ${issue.message || issue.code || JSON.stringify(issue)}`).join("\n");
      }
      if (Array.isArray(details.detail)) {
        return details.detail.map((item) => `${item.loc?.join(".") || "字段"}: ${item.msg || JSON.stringify(item)}`).join("\n");
      }
      return details.message || details.detail?.message || details.detail?.error || details.error || "";
    }

    function showToast(message) {
      const toast = $("#toast");
      toast.textContent = message;
      toast.classList.add("is-open");
      window.setTimeout(() => toast.classList.remove("is-open"), 2600);
    }

    function describeInitReason(reason) {
      if (!reason || reason === "none") return "";
      return INIT_REASON_TEXT[reason] || `未知初始化状态：${reason}`;
    }

    function initEnabledPlatforms(status) {
      const platforms = status?.prerequisites?.enabled_platforms;
      return Array.isArray(platforms) ? platforms.map(String) : [];
    }

    function initSourceLabels(keys) {
      const byKey = new Map(INIT_SOURCE_OPTIONS.map((opt) => [opt.key, opt.label]));
      return (Array.isArray(keys) ? keys : []).map((key) => byKey.get(key) || key);
    }

    function buildInitChecklist(status, selected = null) {
      const prereq = status?.prerequisites || {};
      const enabled = initEnabledPlatforms(status);
      const selectedSources = Array.isArray(selected) ? selected : null;
      // B 站登录只在勾选了 B 站时才是硬前置。
      const biliSelected = selectedSources ? selectedSources.includes("bilibili") : true;
      const embeddingRequired = Boolean(prereq.embedding_required);
      return [
        {
          key: "bilibili",
          label: biliSelected ? "B 站已登录" : "B 站已登录（未勾选 B 站，可跳过）",
          ok: Boolean(prereq.bilibili_logged_in),
          hard: biliSelected,
          hint: "在浏览器里登录 bilibili.com，扩展会自动把 Cookie 同步给后端；不想接 B 站也可以直接取消勾选。"
        },
        {
          key: "llm",
          label: "AI 服务可用",
          ok: Boolean(prereq.llm_ready),
          hard: true,
          hint: "到设置页填好 LLM provider 的 API Key，或确认本地 / 远端模型服务可达。"
        },
        {
          key: "embedding",
          label: embeddingRequired ? "向量模型可用" : "向量模型可用（推荐，非必须）",
          ok: Boolean(prereq.embedding_ready),
          hard: embeddingRequired,
          hint: embeddingRequired
            ? "本地 Ollama + bge-m3 需要完成一次真实向量请求；模型仍在下载或服务异常时请稍后重试。"
            : "未配置 embedding 时可以先初始化；推荐去重和语义检索会弱一些。"
        },
        {
          key: "platforms",
          label: selectedSources?.length
            ? `本次初始化来源：${initSourceLabels(selectedSources).join("、")}`
            : enabled.length
              ? `已启用来源：${initSourceLabels(enabled).join("、")}`
              : "数据来源：仅 B 站（可在设置里开启更多平台）",
          ok: true,
          hard: false,
          hint: ""
        }
      ];
    }

    function initProgressView(status) {
      const total = status?.total_stages || 4;
      const stages = Array.isArray(status?.stages) ? status.stages : [];
      const doneCount = stages.filter((stage) => stage.status === "ok").length;
      const running = Boolean(status?.running);
      const failedStage = stages.find((stage) => stage.status === "failed" || stage.status === "cancelled");
      const current = status?.current_stage || 0;
      const currentStage = stages.find((stage) => stage.n === current);
      const rawPct = ((doneCount + (running ? 0.5 : 0)) / total) * 100;
      const pct = Math.max(0, Math.min(100, Math.round(rawPct)));
      return {
        active: running,
        failed: Boolean(failedStage),
        pct: running ? Math.max(pct, 1) : pct,
        stageLabel: currentStage ? `${currentStage.n}/${total} ${currentStage.label}` : "",
        failedReason: failedStage?.reason || ""
      };
    }

    function initContentReadyFromRuntime(status = state.runtimeStatus) {
      const runtime = window.normalizeRuntimeStatus?.(status);
      return Boolean(runtime) && (
        runtime.pool_available_count > 0 ||
        runtime.recommendation_count > 0
      );
    }

    function initWaitingForFirstPool(status = state.initStatus) {
      return Boolean(status?.initialized) && !initContentReadyFromRuntime(state.runtimeStatus);
    }

    async function refreshRuntimeStatusForInitContent() {
      try {
        const runtime = await requestJsonStrict(ENDPOINTS.runtimeStatus, { timeoutMs: 60000 });
        state.runtimeStatus = normalizeRuntimeStatus(runtime);
      } catch {
        // Keep the last runtime snapshot; the init poll will retry.
      }
      return initContentReadyFromRuntime(state.runtimeStatus);
    }

    function selectedInitSourcesFromDom() {
      return Array.from(document.querySelectorAll("input[data-init-source]"))
        .filter((input) => input.checked)
        .map((input) => input.value);
    }

    function initChecklistMarkup(status, selected = null) {
      if (!status) {
        return '<li class="init-hint-row">点「开始初始化」会先检查 AI 服务 / 向量模型，以及所选平台的登录状态，通过才开始。</li>';
      }
      return buildInitChecklist(status, selected)
        .map((row) => {
          const mark = row.ok ? "✓" : row.hard ? "✗" : "•";
          const hint = !row.ok && row.hint ? `<p class="init-hint">${escapeHtml(row.hint)}</p>` : "";
          return `<li class="${row.ok ? "init-ok" : "init-missing"} ${row.hard ? "init-hard" : "init-soft"}"><div class="init-row"><span class="init-mark">${mark}</span><span>${escapeHtml(row.label)}</span></div>${hint}</li>`;
        })
        .join("");
    }

    function initSourcesMarkup() {
      const selected = state.initSelectedSources
        ? new Set(state.initSelectedSources)
        : new Set(INIT_SOURCE_OPTIONS.filter((opt) => opt.defaultChecked).map((opt) => opt.key));
      const rows = INIT_SOURCE_OPTIONS.map((opt) => {
        const checked = selected.has(opt.key) ? " checked" : "";
        const label = opt.defaultChecked ? `${opt.label}（推荐）` : opt.label;
        return `<label class="init-source-row"><input type="checkbox" value="${escapeHtml(opt.key)}" data-init-source="${escapeHtml(opt.key)}"${checked}><span>${escapeHtml(label)}</span></label>`;
      }).join("");
      return `<div class="init-sources"><p class="init-sources-title">选择初始化数据来源（至少一个）</p>${rows}<p class="init-sources-hint">${escapeHtml(INIT_SOURCE_LOGIN_HINT)}</p></div>`;
    }

    function initOnboardingPhase(status, progress) {
      if (state.initBusy) return "busy";
      if (Boolean(status?.initialized)) return initContentReadyFromRuntime(state.runtimeStatus) ? "completed" : "running";
      if (Boolean(status?.running)) return "running";
      if (progress.failed) return "failed";
      return "idle";
    }

    function updateInitOnboardingStatus(section, status, progress, reason, buttonLabel, buttonDisabled) {
      const checklist = section.querySelector(".init-checklist");
      if (checklist) checklist.innerHTML = initChecklistMarkup(status, state.initSelectedSources);
      const progressBox = section.querySelector(".init-progress");
      const progressFill = section.querySelector(".init-progress-fill");
      const progressText = progressBox?.querySelector("p");
      const progressLabel = progress.failed
        ? (describeInitReason(status?.reason) || progress.failedReason || "初始化未完成，请稍后重试。")
        : progress.active
          ? `${progress.stageLabel || "正在初始化"}（${progress.pct}%）`
          : "等待开始";
      if (progressBox) progressBox.hidden = !(Boolean(status?.running) || progress.failed);
      if (progressFill) progressFill.style.width = `${progress.pct}%`;
      if (progressText) progressText.textContent = progressLabel;
      const reasonText = section.querySelector(".init-reason");
      if (reasonText) {
        reasonText.hidden = !reason;
        reasonText.textContent = reason;
      }
      const startButton = section.querySelector('[data-init-action="start"]');
      if (startButton) {
        startButton.disabled = buttonDisabled;
        startButton.textContent = buttonLabel;
      }
    }

    function renderInitOnboarding() {
      if (!grid) return;
      const status = state.initStatus;
      const progress = initProgressView(status);
      const isRunning = Boolean(status?.running);
      const waitingForFirstPool = initWaitingForFirstPool(status);
      const displayProgress = waitingForFirstPool
        ? { ...progress, active: true, failed: false, pct: 95, stageLabel: "4/4 整理首轮内容池" }
        : progress;
      const alreadyInitialized = Boolean(status?.initialized) && !waitingForFirstPool;
      const showProgress = isRunning || displayProgress.failed || waitingForFirstPool;
      const reason = waitingForFirstPool
        ? (state.initReason || INIT_FIRST_POOL_WAIT_TEXT)
        : (state.initReason || describeInitReason(status?.reason) || status?.detail || "");
      const phase = initOnboardingPhase(status, displayProgress);
      const buttonLabel = state.initBusy
        ? "检查中…"
        : isRunning
          ? "初始化进行中…"
          : waitingForFirstPool
            ? "整理首轮内容…"
          : alreadyInitialized
            ? "已初始化"
            : displayProgress.failed
              ? "重试初始化"
              : "开始初始化";
      const buttonDisabled = state.initBusy || isRunning || waitingForFirstPool || alreadyInitialized;
      const existing = grid.querySelector(".init-onboarding");
      if (existing?.dataset.initPhase === phase && phase !== "idle" && phase !== "busy") {
        updateInitOnboardingStatus(existing, status, displayProgress, reason, buttonLabel, buttonDisabled);
        const loadMore = $("#loadMoreBtn");
        if (loadMore) loadMore.hidden = true;
        return;
      }
      grid.innerHTML = `
        <section class="init-onboarding" aria-label="引导初始化" data-init-phase="${escapeHtml(phase)}">
          <div class="init-onboarding-copy">
            <p class="eyebrow">Guided init</p>
            <h3>还没完成初始化</h3>
            <p class="video-meta">先检查 AI 服务和所选平台登录，通过后在这里一步步拉取数据、生成画像、补齐首轮内容池。B 站默认勾选但可取消，至少保留一个来源。</p>
          </div>
          ${isRunning ? "" : initSourcesMarkup()}
          <ul class="init-checklist">${initChecklistMarkup(status, state.initSelectedSources)}</ul>
          <div class="init-progress"${showProgress ? "" : " hidden"}>
            <div class="init-progress-track"><div class="init-progress-fill" style="width:${displayProgress.pct}%"></div></div>
            <p>${escapeHtml(displayProgress.failed ? (describeInitReason(status?.reason) || displayProgress.failedReason || "初始化未完成，请稍后重试。") : displayProgress.active ? `${displayProgress.stageLabel || "正在初始化"}（${displayProgress.pct}%）` : "等待开始")}</p>
          </div>
          <p class="init-reason"${reason ? "" : " hidden"}>${escapeHtml(reason)}</p>
          <div class="init-actions">
            <button class="small-btn primary" type="button" data-init-action="start"${buttonDisabled ? " disabled" : ""}>${escapeHtml(buttonLabel)}</button>
            <button class="small-btn" type="button" data-init-action="settings">打开设置</button>
          </div>
        </section>`;
      const loadMore = $("#loadMoreBtn");
      if (loadMore) loadMore.hidden = true;
      grid.querySelector('[data-init-action="start"]')?.addEventListener("click", () => {
        void handleDesktopStartInitClick();
      });
      grid.querySelector('[data-init-action="settings"]')?.addEventListener("click", () => {
        openSettingsPage("sources");
      });
      grid.querySelectorAll("input[data-init-source]").forEach((input) => {
        input.addEventListener("change", () => {
          state.initSelectedSources = selectedInitSourcesFromDom();
          // Refresh just the checklist so the B 站 row flips between hard
          // prerequisite and skippable hint as the checkbox changes.
          const checklist = grid.querySelector(".init-onboarding .init-checklist");
          if (checklist) {
            checklist.innerHTML = initChecklistMarkup(state.initStatus, state.initSelectedSources);
          }
        });
      });
    }

    function clearInitPolling() {
      if (initPollTimer !== null) {
        window.clearTimeout(initPollTimer);
        initPollTimer = null;
      }
    }

    function scheduleInitStatusRefresh(delayMs = INIT_STATUS_POLL_MS) {
      clearInitPolling();
      initPollTimer = window.setTimeout(() => {
        initPollTimer = null;
        void refreshInitStatus();
      }, delayMs);
    }

    async function refreshInitStatus({ schedule = true } = {}) {
      if (initRefreshInFlight) {
        initRefreshPending = true;
        return;
      }
      initRefreshInFlight = true;
      clearInitPolling();
      const wasInitialized = Boolean(state.initStatus?.initialized) && initContentReadyFromRuntime(state.runtimeStatus);
      try {
        const status = await requestJsonStrict(ENDPOINTS.initStatus, { timeoutMs: 60000 });
        state.initStatus = status;
        state.initReason = "";
        if (status?.initialized) {
          if (!(await refreshRuntimeStatusForInitContent())) {
            state.initReason = INIT_FIRST_POOL_WAIT_TEXT;
            window.renderAll();
            scheduleInitStatusRefresh(schedule ? INIT_STATUS_POLL_MS : INIT_STATUS_WATCHDOG_MS);
            return;
          }
          window.renderAll();
          clearInitPolling();
          initRefreshPending = false;
          if (!wasInitialized) {
            scheduleBackendHydration();
            showToast("初始化完成，正在加载推荐");
          }
          return;
        }
        window.renderAll();
        if (status?.running) {
          scheduleInitStatusRefresh(schedule ? INIT_STATUS_POLL_MS : INIT_STATUS_WATCHDOG_MS);
        } else if (!status?.running) {
          clearInitPolling();
        }
      } catch (error) {
        scheduleInitStatusRefresh(INIT_STATUS_POLL_MS);
        state.initReason = error?.message || "初始化状态读取失败。";
        window.renderAll();
      } finally {
        initRefreshInFlight = false;
        if (initRefreshPending) {
          initRefreshPending = false;
          void refreshInitStatus({ schedule });
        }
      }
    }

    async function handleDesktopStartInitClick() {
      const selected = selectedInitSourcesFromDom();
      state.initSelectedSources = selected;
      state.initBusy = true;
      state.initReason = "";
      window.renderAll();
      let status = null;
      try {
        status = await requestJsonStrict(ENDPOINTS.initStatus, { timeoutMs: 60000 });
        state.initStatus = status;
      } catch (error) {
        state.initReason = error?.message || "前置检查没拉到，稍后再试。";
        state.initBusy = false;
        window.renderAll();
        return;
      }
      if (status.initialized) {
        state.initBusy = false;
        if (await refreshRuntimeStatusForInitContent()) {
          state.initReason = "";
          scheduleBackendHydration();
        } else {
          state.initReason = INIT_FIRST_POOL_WAIT_TEXT;
          scheduleInitStatusRefresh(INIT_STATUS_POLL_MS);
        }
        window.renderAll();
        return;
      }
      if (status.running) {
        state.initBusy = false;
        window.renderAll();
        clearInitPolling();
        scheduleInitStatusRefresh(INIT_STATUS_START_POLL_MS);
        return;
      }
      if (!selected.length) {
        state.initReason = INIT_REASON_TEXT.no_sources_selected;
        state.initBusy = false;
        window.renderAll();
        return;
      }
      if (selected.includes("bilibili") && !status?.prerequisites?.bilibili_logged_in) {
        state.initReason = "还没检测到 B 站登录。先登录 bilibili.com，或取消勾选 B 站再开始。";
        state.initBusy = false;
        window.renderAll();
        return;
      }
      if (!status.can_start) {
        state.initReason = describeInitReason(status.reason) || status.detail || "以下条件未满足，无法开始初始化。";
        state.initBusy = false;
        window.renderAll();
        return;
      }
      try {
        const started = await requestJsonStrict(ENDPOINTS.startInit, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sources: selected }),
          timeoutMs: 60000
        });
        state.initStatus = { ...(state.initStatus || {}), ...started };
        state.initBusy = false;
        showToast("初始化已开始");
        window.renderAll();
        scheduleInitStatusRefresh(INIT_STATUS_START_POLL_MS);
      } catch (error) {
        const code = error?.details?.error || error?.details?.reason;
        state.initReason = describeInitReason(code) || error?.message || "初始化没能启动，请稍后重试。";
        state.initBusy = false;
        window.renderAll();
      }
    }

    function openPanel(id) { document.getElementById(id)?.classList.add("is-open"); }
    function closePanel(id) {
      const panel = document.getElementById(id);
      panel?.classList.remove("is-open", "from-mobile-menu");
      if (id === "messagesDrawer") {
        state.messageListSnapshot = null;
        state.messageListDomLocked = false;
      }
    }

    const MAIN_PAGE_IDS = ["homePage", "customFilterPage", "poolAllPage", "poolFilterPage", "observabilityPage", "interviewPage", "poolExplorePage", "xhsFeedPage", "zhihuFeedPage", "biliFeedPage", "youtubeFeedPage", "v2exFeedPage", "xiaoyuzhouFeedPage", "delightPage", "savedPage", "watchLaterPage", "profilePage", "chatPage", "diaryPage", "clonePage", "selfEvolutionPage", "libraryPage", "readArchivePage", "settingsPage", "topicsPage", "healthPage", "travelPage"];

    window.showMainPage = showMainPage;
    window.$ = $;
    window.safeBind = safeBind;
    window.eventDelegation = eventDelegation;
    window.closeFeedDropdown = closeFeedDropdown;
    window.closeMobileMenu = closeMobileMenu;
    window.closePanel = closePanel;
    window.openPanel = openPanel;
    window.showToast = showToast;
    // removeFeedback, sendFeedback moved to pool-explore.js
    window.requestJson = requestJson;
    window.escapeHtml = escapeHtml;
    window.platformLabelHtml = platformLabelHtml;
    window.ENDPOINTS = ENDPOINTS;
    // 供 pool-explore.js / profile.js 跨模块调用（运行时通过全局解析）
    window.contentUrl = contentUrl;
    window.trackRecommendationClick = trackRecommendationClick;
    window.isMobileViewport = isMobileViewport;
    window.recommendationKey = recommendationKey;
    window.shouldRemoveRecommendationAfterFeedback = shouldRemoveRecommendationAfterFeedback;
    window.refreshInitStatus = refreshInitStatus;
    window.filteredVideos = filteredVideos;
    window.currentCustomFilterPlatform = currentCustomFilterPlatform;
    window.warmCoverImages = warmCoverImages;
    window.decodeHtmlEntities = decodeHtmlEntities;
    window.normalizeImageUrl = normalizeImageUrl;
    window.imageProxyUrl = imageProxyUrl;
    function showMainPage(pageId) {
      MAIN_PAGE_IDS.forEach((id) => {
        const page = document.getElementById(id);
        if (!page) return;
        if (id === pageId) {
          page.removeAttribute("hidden");
        } else {
          page.setAttribute("hidden", "");
        }
      });
      const isFeedPage = ["xhsFeedPage", "zhihuFeedPage", "biliFeedPage", "youtubeFeedPage", "v2exFeedPage", "xiaoyuzhouFeedPage"].includes(pageId);
      document.body.classList.toggle("profile-page-open", pageId === "profilePage");
      document.body.classList.toggle("chat-page-open", pageId === "chatPage");
      document.body.classList.toggle("library-page-open", pageId === "libraryPage" || pageId === "readArchivePage");
      document.body.classList.toggle("pool-all-page-open", pageId === "poolAllPage" || pageId === "poolFilterPage");
      document.body.classList.toggle("custom-filter-page-open", pageId === "customFilterPage");
      document.body.classList.toggle("saved-page-open", pageId === "savedPage" || pageId === "watchLaterPage");
      document.body.classList.toggle("settings-page-open", pageId === "settingsPage");
      document.body.classList.toggle("observability-page-open", pageId === "observabilityPage");
      document.body.classList.toggle("interview-page-open", pageId === "interviewPage");
      document.body.classList.toggle("pool-explore-page-open", pageId === "poolExplorePage");
      document.body.classList.toggle("delight-page-open", pageId === "delightPage");
      document.body.classList.toggle("clone-page-open", pageId === "clonePage");
      document.body.classList.toggle("travel-page-open", pageId === "travelPage");
      document.body.classList.toggle("self-evolution-page-open", pageId === "selfEvolutionPage");
      const tabSync = { homePage: "homeBtn", customFilterPage: "customFilterBtn", poolAllPage: "poolAllBtn", poolExplorePage: "poolExploreBtn", poolFilterPage: "poolFilterBtn", delightPage: "delightTabBtn", savedPage: "favoritesBtn", watchLaterPage: "watchLaterBtn", diaryPage: "diaryBtn", clonePage: "cloneBtn", profilePage: "profileBtn", chatPage: "chatBtn", libraryPage: "libraryBtn", readArchivePage: "readArchiveBtn", settingsPage: "settingsBtn", travelPage: "travelBtn", topicsPage: "topicsBtn", healthPage: "healthBtn" };
      const activeTab = document.getElementById(tabSync[pageId]);
      document.querySelectorAll(".tab-btn").forEach((btn) => btn.classList.toggle("is-active", btn === activeTab));
      // 筛选下拉菜单：当前在筛选页面时高亮触发按钮和对应菜单项
      const isFilterPage = pageId === "customFilterPage" || pageId === "poolFilterPage";
      const filterTrigger = document.getElementById("filterDropdownTrigger");
      if (filterTrigger) filterTrigger.classList.toggle("is-active", isFilterPage);
      document.querySelectorAll(".filter-dropdown-item").forEach((item) => {
        item.classList.toggle("is-active", item === activeTab);
      });
      // 推荐流下拉菜单：当前在推荐流页面时高亮触发按钮和对应菜单项
      const feedTrigger = document.getElementById("feedDropdownTrigger");
      if (feedTrigger) feedTrigger.classList.toggle("is-active", isFeedPage);
      // 池子下拉菜单：当前在池子页面时高亮触发按钮
      const isPoolPage = pageId === "poolAllPage" || pageId === "poolExplorePage";
      const poolTrigger = document.getElementById("poolDropdownTrigger");
      if (poolTrigger) poolTrigger.classList.toggle("is-active", isPoolPage);
      // 我的下拉菜单：当前在收藏/稍后再看/画像/聊聊口味页面时高亮触发按钮
      const isMinePage = pageId === "savedPage" || pageId === "watchLaterPage" || pageId === "profilePage" || pageId === "chatPage" || pageId === "settingsPage";
      const mineTrigger = document.getElementById("mineDropdownTrigger");
      if (mineTrigger) mineTrigger.classList.toggle("is-active", isMinePage);
      // Feed meta count & FAB: only show on feed pages
      const feedMetaCount = document.getElementById("feedMetaCount");
      const fab = document.getElementById("fabRefreshBtn");
      if (feedMetaCount) {
        if (isFeedPage) {
          feedMetaCount.removeAttribute("hidden");
          if (fab) fab.removeAttribute("hidden");
        } else {
          feedMetaCount.setAttribute("hidden", "");
          if (fab) fab.setAttribute("hidden", "");
        }
      }
      // Delight topbar tools: only show on delight page
      const isDelightPage = pageId === "delightPage";
      const delightPill = document.getElementById("delightTopbarPill");
      const delightTag = document.getElementById("delightTopbarTag");
      const delightBtn = document.getElementById("delightRefreshBtn");
      const poolPill = document.querySelector(".pool-pill");
      const reshuffleToggle = document.querySelector(".reshuffle-toggle");
      const reshuffleBtn = document.getElementById("reshuffleBtn");
      if (delightPill) delightPill.hidden = !isDelightPage;
      if (delightTag) delightTag.hidden = !isDelightPage;
      if (delightBtn) delightBtn.hidden = !isDelightPage;
      if (poolPill) poolPill.style.display = isDelightPage ? "none" : "";
      if (reshuffleToggle) reshuffleToggle.style.display = isDelightPage ? "none" : "";
      if (reshuffleBtn) reshuffleBtn.style.display = isDelightPage ? "none" : "";
      // 面试页面专用工具
      const isInterviewPage = pageId === "interviewPage";
      const interviewBtn = document.getElementById("interviewRefreshBtn");
      if (interviewBtn) interviewBtn.hidden = !isInterviewPage;
      // 统一刷新按钮：各页面共用，根据当前页面绑定对应刷新函数
      const refreshMap = {
        observabilityPage: () => scheduleObservabilityRefresh(),
        poolAllPage: () => loadPoolAllItems(),
        poolFilterPage: () => loadPoolFilterItems(),
        poolExplorePage: () => { if (window.loadPoolExploreData) window.loadPoolExploreData(); },
        clonePage: () => loadCloneSites(),
        travelPage: () => { document.getElementById("travelRefreshBtn")?.click(); },
        selfEvolutionPage: () => { document.getElementById("selfEvoRefreshBtn")?.click(); },
      };
      const globalRefreshBtn = document.getElementById("globalRefreshBtn");
      if (globalRefreshBtn) {
        const refreshFn = refreshMap[pageId];
        if (refreshFn) {
          globalRefreshBtn.hidden = false;
          globalRefreshBtn.onclick = refreshFn;
        } else {
          globalRefreshBtn.hidden = true;
          globalRefreshBtn.onclick = null;
        }
      }
    }

    // ── Desktop page routing (independent URLs, no full reload) ──
    // Each top-level view gets its own URL (/web/library, /web/chat, …).
    // Clicking a tab uses pushState so the address bar stays in sync and the
    // page is bookmarkable / refresh-safe, while the backend serves the same
    // SPA shell for /web/{page} so direct links work too.
    const DESKTOP_PAGE_ROUTES = {
      home: () => openHomePage(),
      "custom-filter": () => openCustomFilterPage(),
      "pool-all": () => openPoolAllPage(),
      "pool-filter": () => openPoolFilterPage(),
      observability: () => openObservabilityPage(),
      interview: () => openInterviewPage(),
      "pool-explore": () => openPoolExplorePage(),
      delight: () => openDelightPage(),
      saved: () => openSavedPage(),
      watchLater: () => openWatchLaterPage(),
      profile: () => openProfilePage(),
      chat: () => openChatPage(),
      diary: () => openDiaryPage(),
      clone: () => openClonePage(),
      "self-evolution": () => { if (window.openSelfEvolutionPage) window.openSelfEvolutionPage(); },
      library: () => openLibraryPage(),
      "read-archive": () => openReadArchivePage(),
      settings: () => openSettingsPage("models"),
      travel: () => openTravelPage(),
    };
    window.DESKTOP_PAGE_ROUTES = DESKTOP_PAGE_ROUTES;

    function routeFromPath() {
      const match = (location.pathname || "/web").match(/^\/web\/([a-zA-Z0-9-]+)\/?$/);
      const page = match ? match[1] : "home";
      const params = new URLSearchParams(location.search);
      const opener = DESKTOP_PAGE_ROUTES[page] || DESKTOP_PAGE_ROUTES.home;
      opener(params);
    }

    window.navigateTo = navigateTo;
    function navigateTo(path) {
      if ((location.pathname + location.search) === path) return;
      history.pushState({ path }, "", path);
      routeFromPath();
    if (typeof window.__initSelfEvolution === "function") window.__initSelfEvolution();
    }

    window.addEventListener("popstate", () => routeFromPath());

    function syncTopbarHeight() {
      const topbar = document.querySelector(".topbar");
      if (!topbar) return;
      document.documentElement.style.setProperty("--topbar-height", `${Math.ceil(topbar.getBoundingClientRect().height)}px`);
    }

    function openHomePage() {
      showMainPage("homePage");
      renderFilters();
      renderVideos();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openCustomFilterPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("customFilterPage");
      // 自定义筛选：全部条件在页面内设置
      renderCustomFilterPanel();
      renderFilters();
      renderVideos();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openPoolAllPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("poolAllPage");
      loadPoolAllItems();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openPoolFilterPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("poolFilterPage");
      renderPoolFilterBar();
      loadPoolFilterItems();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openObservabilityPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("observabilityPage");
      loadObservabilityData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openInterviewPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("interviewPage");
      if (window.loadInterviewData) window.loadInterviewData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openPoolExplorePage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("poolExplorePage");
      state.poolExploreFilters = {};
      window.loadPoolExploreData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }


    function openDelightPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("delightPage");
      if (typeof window.renderDelightGrid === "function") window.renderDelightGrid();
      else console.warn("renderDelightGrid not ready — profile.js may have failed to load");
      // 队列还没就绪时立即单独拉取（pending-batch 本身 50ms 级），
      // 不等 hydrate 主链（runtime/notification/chat 等）全部完成再出卡。
      if (!state.delights.length && typeof window.fetchDelightQueue === "function") void window.fetchDelightQueue();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openProfilePage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("profilePage");
      renderProfileDetails();
      void window.refreshProfile().catch(() => {});
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openChatPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("chatPage");
      const input = document.getElementById("chatInput");
      window.scrollTo({ top: 0, behavior: "smooth" });
      window.setTimeout(() => input?.focus(), 100);
    }

    function openSettingsPage(panel = "models") {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((drawer) => closePanel(drawer.id));
      setActiveSettingsPanel(panel || "models");
      showMainPage("settingsPage");
      window.scrollTo({ top: 0, behavior: "smooth" });
      void window.renderSourcesStatus();
      void window.renderSourceCredentials();
      void loadSubscriptionList();
      void lanAuthControl?.reload();
      void bootAutostartControl?.reload();
      void window.refreshUpdateStatus();
    }

    // ── Reading library ───────────────────────────────────────────

    let _librarySourceFilter = "all";
    let _libraryStatusFilter = "all";
    let _libraryTagFilter = null;
    let _librarySearchQuery = "";
    const LIBRARY_PAGE_SIZE = 50;
    let _libraryLimit = LIBRARY_PAGE_SIZE;
    let _librarySearchBound = false;

    // Cross-platform source-type labels for the reading-library filter chips.
    const SOURCE_LABELS = {
      zhihu: "知乎", youtube: "YouTube", bilibili: "B站", douyin: "抖音",
      xiaohongshu: "小红书", rss: "RSS", xiaoyuzhou: "播客", v2ex: "V2EX",
      wechat: "公众号", reddit: "Reddit", other: "其他",
    };
    const VIDEO_SOURCE_TYPES = new Set(["youtube", "bilibili", "douyin"]);

    // ── 日记模块按需加载脚本清单（提前声明：顶层 routeFromPath() 会在
    //    app.js 解析早期调用 openDiaryPage，变量必须在其之前初始化）──
    var _diaryScriptsPromise = null;
    var DIARY_SCRIPTS = [
      "diary-insights.js",
      "diary-people.js",
      "diary-semantic.js",
      "diary-chat.js",
      "diary-reflection.js",
      "diary-knowledge.js",
      "diary-self-evolution.js",
      "diary-insights-center.js",
      "diary-enhanced-center.js",
    ];

    // ── 日记系统 Diary state（提前声明，避免 routeFromPath 初始化时访问未初始化变量）──
    const diaryState = {
      entries: [],
      total: 0,
      offset: 0,
      limit: 50,
      selectedId: null,
      editingId: null,
      search: "",
      moodFilter: "",
      sourceFilter: "",
      loading: false,
    };

    const MOOD_LABELS = {
      very_happy: "非常开心",
      happy: "开心",
      neutral: "平静",
      sad: "低落",
      very_sad: "非常低落",
      angry: "生气",
      anxious: "焦虑",
      unknown: "未标注",
    };

    const DIARY_SOURCE_LABELS = {
      manual: "手动",
      import_lele: "乐乐日记",
      import_text: "文本导入",
      import_markdown: "Markdown",
      api: "API",
    };
    let diaryEventsBound = false;

    // Render the source-type filter chips from the live article distribution so
    // newly synced platforms (bilibili/youtube/douyin/zhihu/...) appear without
    // a hardcoded list. Keeps the current _librarySourceFilter active.
    async function renderLibrarySourceFilters() {
      const box = document.getElementById("libraryFilters");
      if (!box) return;
      try {
        const res = await fetch("/api/reading/sources");
        if (!res.ok) throw new Error("HTTP " + res.status);
        const { sources } = await res.json();
        const chips = [`<button class="library-filter-btn${_librarySourceFilter === "all" ? " is-active" : ""}" data-filter="all" type="button">全部</button>`];
        (sources || []).forEach((s) => {
          const label = SOURCE_LABELS[s.source_type] || s.source_type;
          chips.push(
            `<button class="library-filter-btn${_librarySourceFilter === s.source_type ? " is-active" : ""}" data-filter="${s.source_type}" type="button">${label} ${s.count}</button>`
          );
        });
        box.innerHTML = chips.join("");
      } catch {
        /* keep whatever is rendered (or empty) on failure */
      }
    }

    function openLibraryPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((drawer) => closePanel(drawer.id));
      showMainPage("libraryPage");
      window.scrollTo({ top: 0, behavior: "smooth" });
      bindLibrarySearchOnce();
      void renderLibrarySourceFilters();
      void loadLibraryItems();
    }

    function syncLibraryPaging(total, shown) {
      const countEl = document.getElementById("libraryCount");
      const moreEl = document.getElementById("libraryMore");
      if (countEl) {
        countEl.textContent = total ? `共 ${total} 篇 · 已显示 ${shown} 篇` : "";
      }
      if (moreEl) moreEl.hidden = !(total && shown < total);
    }

    function buildLibraryCard(item, tags) {
      const status = item.status || "unread";
      const statusLabel = { unread: "未读", reading: "正在读", finished: "已读完" }[status] || "未读";
      const card = document.createElement("article");
      card.className = "video-card is-minimal";
      card.dataset.itemId = item.id;
      card.dataset.status = status;
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
          <p class="video-card-title" style="flex:1">${escapeHtml(item.title || "")}</p>
          <span class="library-card-status ${status}">${statusLabel}</span>
        </div>
        <div class="video-card-meta">
          <span class="video-card-author">${escapeHtml(item.author || "")}</span>
          <span class="video-card-tag">${escapeHtml(item.source_name || item.source_type || "")}</span>
        </div>
        <div class="video-card-summary">${escapeHtml((item.summary || "").slice(0, 200))}</div>
        ${tags.length ? `<div class="library-card-tags">${tags.map((t) => `<span class="library-card-tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
        <div class="library-card-actions">
          <button class="icon-btn" data-action="toggle-status" type="button" title="切换阅读状态">${status === "finished" ? "重读" : status === "reading" ? "标为已读" : "开始读"}</button>
          <button class="icon-btn" data-action="edit-tags" type="button" title="编辑标签">标签</button>
        </div>`;
      card.addEventListener("click", (e) => {
        const actionBtn = e.target.closest("[data-action]");
        if (actionBtn) {
          if (actionBtn.dataset.action === "toggle-status") {
            e.stopPropagation();
            toggleReadingStatus(item.id, status, card);
          } else if (actionBtn.dataset.action === "edit-tags") {
            e.stopPropagation();
            openTagEditor(item.id, tags);
          }
          return;
        }
        void openArticleReader(item.id);
      });
      return card;
    }

    function renderLibraryCards(items) {
      const grid = document.getElementById("libraryGrid");
      if (!grid) return;
      if (!items || !items.length) {
        grid.innerHTML = '<div class="empty-state">没有匹配的内容</div>';
        return;
      }
      const allTags = new Set();
      items.forEach((item) => {
        let tags = item.tags;
        if (typeof tags === "string") { try { tags = JSON.parse(tags); } catch { tags = []; } }
        if (Array.isArray(tags)) tags.forEach((t) => allTags.add(t));
      });
      const tagsEl = document.getElementById("libraryTags");
      const tagsList = document.getElementById("libraryTagsList");
      if (tagsEl && tagsList) {
        if (allTags.size) {
          tagsEl.hidden = false;
          tagsList.innerHTML = `<button class="library-tag${_libraryTagFilter === null ? " is-active" : ""}" data-tag="" type="button">全部</button>`
            + [...allTags].sort().map((t) =>
                `<button class="library-tag${_libraryTagFilter === t ? " is-active" : ""}" data-tag="${escapeHtml(t)}" type="button">${escapeHtml(t)}</button>`
              ).join("");
        } else {
          tagsEl.hidden = true;
        }
      }
      grid.replaceChildren(...items.map((item) => {
        let tags = item.tags;
        if (typeof tags === "string") { try { tags = JSON.parse(tags); } catch { tags = []; } }
        if (!Array.isArray(tags)) tags = [];
        return buildLibraryCard(item, tags);
      }));
    }

    function loadLibraryItems() {
      const grid = document.getElementById("libraryGrid");
      if (!grid) return;
      const q = (_librarySearchQuery || "").trim();
      if (q) { void loadLibrarySearch(q); return; }
      grid.innerHTML = '<div class="empty-state">加载中…</div>';

      const params = new URLSearchParams();
      if (_librarySourceFilter && _librarySourceFilter !== "all") params.set("source_type", _librarySourceFilter);
      if (_libraryStatusFilter && _libraryStatusFilter !== "all") params.set("status", _libraryStatusFilter);
      if (_libraryTagFilter) params.set("tag", _libraryTagFilter);
      params.set("limit", String(_libraryLimit));
      const countParams = new URLSearchParams(params);
      countParams.delete("limit");

      Promise.all([
        fetch(`/api/reading/items?${params}`)
          .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }),
        fetch(`/api/reading/count?${countParams}`)
          .then((r) => (r.ok ? r.json() : { total: 0 }))
          .catch(() => ({ total: 0 })),
      ])
        .then(([items, countData]) => {
          const total = Number(countData && countData.total) || 0;
          syncLibraryPaging(total, items ? items.length : 0);
          renderLibraryCards(items);
        })
        .catch(() => {
          grid.innerHTML = '<div class="empty-state">加载阅读库失败，请确认后端服务正常</div>';
        });
    }

    function loadLibrarySearch(q) {
      const grid = document.getElementById("libraryGrid");
      if (!grid) return;
      grid.innerHTML = '<div class="empty-state">搜索中…</div>';
      const params = new URLSearchParams();
      if (_librarySourceFilter && _librarySourceFilter !== "all") params.set("source_type", _librarySourceFilter);
      if (_libraryStatusFilter && _libraryStatusFilter !== "all") params.set("status", _libraryStatusFilter);
      if (_libraryTagFilter) params.set("tag", _libraryTagFilter);
      params.set("q", q);
      params.set("limit", String(_libraryLimit));
      fetch(`/api/reading/search?${params}`)
        .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then((items) => {
          const shown = items ? items.length : 0;
          const countEl = document.getElementById("libraryCount");
          if (countEl) countEl.textContent = `搜索“${q}” 找到 ${shown} 条`;
          const moreEl = document.getElementById("libraryMore");
          if (moreEl) moreEl.hidden = true;
          renderLibraryCards(items);
        })
        .catch(() => {
          grid.innerHTML = '<div class="empty-state">搜索失败，请确认后端服务正常</div>';
        });
    }

    function bindLibrarySearchOnce() {
      if (_librarySearchBound) return;
      _librarySearchBound = true;
      const input = document.getElementById("librarySearchInput");
      const clearBtn = document.getElementById("librarySearchClear");
      if (!input) return;
      let timer = null;
      input.addEventListener("input", () => {
        _librarySearchQuery = input.value;
        if (clearBtn) clearBtn.hidden = !input.value;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => loadLibraryItems(), 250);
      });
      if (clearBtn) clearBtn.addEventListener("click", () => {
        input.value = "";
        _librarySearchQuery = "";
        clearBtn.hidden = true;
        loadLibraryItems();
      });
    }

    // ── Read archive (已读库) ──────────────────────────────────────
    // 已读库 = articles 表里 source_type=read-archive 的条目，由
    // scripts/import_readlib_to_db.py 从 notes/已读库 文件存档导入。
    let _readArchivePlatform = "all";
    let _readArchiveQuery = "";
    let _readArchiveCounts = null;
    const READ_ARCHIVE_PAGE_SIZE = 24;
    let _readArchiveLimit = READ_ARCHIVE_PAGE_SIZE;
    let _readArchiveBound = false;
    const READ_ARCHIVE_PLATFORMS = ["知乎", "小红书", "V2EX"];

    function openReadArchivePage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((drawer) => closePanel(drawer.id));
      showMainPage("readArchivePage");
      window.scrollTo({ top: 0, behavior: "smooth" });
      bindReadArchiveOnce();
      void refreshReadArchiveCounts();
      loadReadArchiveItems();
    }

    function bindReadArchiveOnce() {
      if (_readArchiveBound) return;
      _readArchiveBound = true;
      const input = document.getElementById("readArchiveSearchInput");
      const clearBtn = document.getElementById("readArchiveSearchClear");
      if (input) {
        let timer = null;
        input.addEventListener("input", () => {
          _readArchiveQuery = input.value;
          if (clearBtn) clearBtn.hidden = !input.value;
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => loadReadArchiveItems(), 250);
        });
        input.addEventListener("keydown", (e) => {
          if (e.key === "Escape") { input.value = ""; _readArchiveQuery = ""; if (clearBtn) clearBtn.hidden = true; loadReadArchiveItems(); }
        });
      }
      if (clearBtn) clearBtn.addEventListener("click", () => {
        input.value = "";
        _readArchiveQuery = "";
        clearBtn.hidden = true;
        loadReadArchiveItems();
      });
      const moreBtn = document.getElementById("readArchiveMoreBtn");
      if (moreBtn) moreBtn.addEventListener("click", () => {
        _readArchiveLimit += READ_ARCHIVE_PAGE_SIZE;
        loadReadArchiveItems();
      });
    }

    async function refreshReadArchiveCounts() {
      try {
        const fetches = [fetch("/api/read-archive/count").then((r) => r.json())];
        READ_ARCHIVE_PLATFORMS.forEach((p) => {
          fetches.push(
            fetch(`/api/read-archive/count?tag=${encodeURIComponent(p)}`).then((r) => r.json())
          );
        });
        const results = await Promise.all(fetches);
        const counts = { all: Number(results[0].total) || 0 };
        READ_ARCHIVE_PLATFORMS.forEach((p, i) => { counts[p] = Number(results[i + 1].total) || 0; });
        _readArchiveCounts = counts;
        renderReadArchiveFilters();
      } catch { /* 筛选条保持现状 */ }
    }

    function renderReadArchiveFilters() {
      const box = document.getElementById("readArchiveFilters");
      if (!box) return;
      const entries = [["all", "全部"]].concat(READ_ARCHIVE_PLATFORMS.map((p) => [p, p]));
      box.innerHTML = entries.map(([key, label]) => {
        const n = _readArchiveCounts ? _readArchiveCounts[key] : "";
        return `<button class="library-filter-btn${_readArchivePlatform === key ? " is-active" : ""}" data-platform="${key}" type="button">${label}${n !== "" ? ` ${n}` : ""}</button>`;
      }).join("");
      box.querySelectorAll("[data-platform]").forEach((btn) => {
        btn.addEventListener("click", () => {
          _readArchivePlatform = btn.dataset.platform;
          _readArchiveLimit = READ_ARCHIVE_PAGE_SIZE;
          renderReadArchiveFilters();
          loadReadArchiveItems();
        });
      });
    }

    function loadReadArchiveItems() {
      const grid = document.getElementById("readArchiveGrid");
      if (!grid) return;
      const q = (_readArchiveQuery || "").trim();
      grid.innerHTML = '<div class="empty-state">加载中…</div>';
      const params = new URLSearchParams({ limit: String(_readArchiveLimit) });
      if (_readArchivePlatform !== "all") params.set("tag", _readArchivePlatform);
      const countParams = new URLSearchParams(params);
      countParams.delete("limit");
      if (q) params.set("q", q);
      const req = q
        ? fetch(`/api/read-archive/search?${params}`).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        : Promise.all([
            fetch(`/api/read-archive/items?${params}`).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }),
            fetch(`/api/read-archive/count?${countParams}`).then((r) => (r.ok ? r.json() : { total: 0 })).catch(() => ({ total: 0 })),
          ]).then(([items, countData]) => ({ items, total: countData.total || 0 }));
      req.then((data) => {
        const items = Array.isArray(data) ? data : (data.items || []);
        const total = Array.isArray(data) ? items.length : (Number(data.total) || 0);
        const countEl = document.getElementById("readArchiveCount");
        if (countEl) countEl.textContent = total ? (q ? `搜索“${q}” 找到 ${items.length} 条` : `共 ${total} 篇 · 已显示 ${items.length} 篇`) : "";
        const moreEl = document.getElementById("readArchiveMore");
        if (moreEl) moreEl.hidden = !(!q && total > items.length);
        renderReadArchiveCards(items);
      }).catch(() => {
        grid.innerHTML = '<div class="empty-state">加载已读库失败，请确认后端服务正常</div>';
      });
    }

    function renderReadArchiveCards(items) {
      const grid = document.getElementById("readArchiveGrid");
      if (!grid) return;
      if (!items || !items.length) {
        grid.innerHTML = '<div class="empty-state">没有匹配的内容</div>';
        return;
      }
      grid.innerHTML = "";
      items.forEach((item) => {
        let tags = item.tags;
        if (typeof tags === "string") { try { tags = JSON.parse(tags); } catch { tags = []; } }
        if (!Array.isArray(tags)) tags = [];
        grid.appendChild(buildReadArchiveCard(item, tags));
      });
    }

    function buildReadArchiveCard(item, tags) {
      const card = document.createElement("article");
      card.className = "video-card is-minimal";
      card.dataset.itemId = item.id;
      const date = (item.published_at || "").slice(0, 10);
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
          <p class="video-card-title" style="flex:1">${escapeHtml(item.title || "")}</p>
          <span class="library-card-status finished">已读</span>
        </div>
        <div class="video-card-meta">
          <span class="video-card-author">${escapeHtml(item.author || "")}</span>
          <span class="video-card-tag">${escapeHtml(item.source_name || "")}</span>
          ${date ? `<span>${escapeHtml(date)}</span>` : ""}
        </div>
        ${item.summary ? `<div class="video-card-summary">${escapeHtml(item.summary.slice(0, 160))}</div>` : ""}
        ${tags.length ? `<div class="library-card-tags">${tags.slice(0, 4).map((t) => `<span class="library-card-tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}`;
      card.addEventListener("click", () => { void openReadArchiveArticleReader(item.id); });
      return card;
    }

    async function openReadArchiveArticleReader(id) {
      const titleEl = document.getElementById("articleDrawerTitle");
      const sourceEl = document.getElementById("articleDrawerSource");
      const metaEl = document.getElementById("articleReaderMeta");
      const bodyEl = document.getElementById("articleReaderBody");
      const originBtn = document.getElementById("articleOpenOrigin");
      _currentArticleId = id;
      _suppressScroll = true;
      if (titleEl) titleEl.textContent = "载入中…";
      if (sourceEl) sourceEl.textContent = "已读库";
      if (metaEl) metaEl.textContent = "";
      if (bodyEl) bodyEl.innerHTML = '<p class="article-reader-placeholder">正在载入正文…</p>';
      openPanel("articleDrawer");
      try {
        const res = await fetch(`/api/read-archive/articles/${id}`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        const article = data.article || {};
        if (titleEl) titleEl.textContent = article.title || "文章";
        if (sourceEl) sourceEl.textContent = article.source_name || article.source_type || "已读库";
        if (metaEl) {
          metaEl.textContent = [article.author, article.published_at].filter(Boolean).join(" · ");
        }
        const text = (article.content_text || "").trim();
        if (bodyEl) {
          if (text) {
            bodyEl.innerHTML = renderMarkdown(text);
          } else {
            bodyEl.innerHTML = `<p class="article-reader-placeholder">${escapeHtml(article.summary || "这篇内容没有存档正文。")}</p>`;
          }
        }
        if (originBtn) {
          if (article.url && /^https?:\/\//i.test(article.url)) {
            originBtn.hidden = false;
            originBtn.dataset.url = article.url;
            originBtn.textContent = "打开原文";
          } else {
            originBtn.hidden = true;
          }
        }
      } catch {
        if (titleEl) titleEl.textContent = "加载失败";
        if (bodyEl) bodyEl.innerHTML = '<p class="article-reader-placeholder">无法加载已读库内容。</p>';
      } finally {
        _suppressScroll = false;
      }
    }

    let _currentArticleId = null;
    let _suppressScroll = false;

    async function openArticleReader(id) {
      const titleEl = document.getElementById("articleDrawerTitle");
      const sourceEl = document.getElementById("articleDrawerSource");
      const metaEl = document.getElementById("articleReaderMeta");
      const bodyEl = document.getElementById("articleReaderBody");
      const originBtn = document.getElementById("articleOpenOrigin");
      _currentArticleId = id;
      // 先抑制：占位符/正文渲染都会重置 scrollTop 并触发 scroll 事件，
      // 必须先关掉保存，否则会把已存位置污染成 0
      _suppressScroll = true;
      if (titleEl) titleEl.textContent = "载入中…";
      if (sourceEl) sourceEl.textContent = "Article";
      if (metaEl) metaEl.textContent = "";
      if (bodyEl) bodyEl.innerHTML = '<p class="article-reader-placeholder">正在载入正文…</p>';
      openPanel("articleDrawer");
      try {
        const res = await fetch(`/api/articles/${id}`);
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        const article = data.article || {};
        if (titleEl) titleEl.textContent = article.title || "文章";
        if (sourceEl) sourceEl.textContent = article.source_name || article.source_type || "Article";
        if (metaEl) {
          metaEl.textContent = [article.author, article.published_at]
            .filter(Boolean)
            .join(" · ");
        }
        const isVideo = VIDEO_SOURCE_TYPES.has(article.source_type);
        const text = (article.content_text || "").trim();
        if (bodyEl) {
          const savedPos = loadReaderPos(id);
          if (text) {
            bodyEl.innerHTML = renderMarkdown(text);
          } else {
            bodyEl.innerHTML = `<p class="article-reader-placeholder">${
              isVideo
                ? "这是一段视频，没有可阅读的正文，点击下方按钮打开观看。"
                : escapeHtml(
                    article.summary || "这篇内容没有存档正文，可以点下方「打开原文」查看。"
                  )
            }</p>`;
          }
          restoreReaderScroll(savedPos);
        }
        bindReaderProgress();
        if (originBtn) {
          // local:// 之类的占位链接(无原文的本地存档)不展示"打开原文"
          if (article.url && /^https?:\/\//i.test(article.url)) {
            originBtn.hidden = false;
            originBtn.dataset.url = article.url;
            originBtn.textContent = isVideo ? "打开视频" : "打开原文";
          } else {
            originBtn.hidden = true;
          }
        }
      } catch {
        if (bodyEl) {
          bodyEl.innerHTML =
            '<p class="article-reader-placeholder">正文加载失败，请检查后端服务。</p>';
        }
        _suppressScroll = false;
      }
    }

    // ── 阅读进度条 + 滚动记忆 ──────
    function readerPosKey(id) { return "obc:reader:pos:" + id; }
    function saveReaderPos(id, top) { try { localStorage.setItem(readerPosKey(id), String(top)); } catch (e) {} }
    function loadReaderPos(id) { try { const v = localStorage.getItem(readerPosKey(id)); return v ? Number(v) || 0 : 0; } catch (e) { return 0; } }
    function updateReaderProgress(pct) {
      const bar = document.getElementById("readerProgress");
      if (bar && bar.firstElementChild) bar.firstElementChild.style.width = Math.max(0, Math.min(100, pct * 100)) + "%";
    }
    let _readerScrollBound = false;
    function bindReaderProgress() {
      const el = document.getElementById("articleReader");
      if (!el) return;
      if (!_readerScrollBound) {
        el.addEventListener("scroll", () => {
          if (!_currentArticleId || _suppressScroll) return;
          const max = el.scrollHeight - el.clientHeight;
          const pct = max > 0 ? el.scrollTop / max : 0;
          updateReaderProgress(pct);
          saveReaderPos(_currentArticleId, el.scrollTop);
        }, { passive: true });
        _readerScrollBound = true;
      }
      const max = el.scrollHeight - el.clientHeight;
      updateReaderProgress(max > 0 ? el.scrollTop / max : 0);
    }
    // 恢复上次阅读位置：抽屉滑入动画期间布局可能未就绪（max=0），
    // 故用 rAF 轮询，直到可滚动再应用；并用 _currentArticleId 隔离，
    // 防止快速切换文章时把上一篇的待恢复位置误套到当前篇。
    function restoreReaderScroll(pos) {
      const el = document.getElementById("articleReader");
      if (!el) return;
      const targetId = _currentArticleId;
      _suppressScroll = true;
      let tries = 0;
      const apply = () => {
        if (_currentArticleId !== targetId) { _suppressScroll = false; return true; }
        const max = el.scrollHeight - el.clientHeight;
        if (max > 0) {
          if (pos > 0) {
            el.scrollTop = Math.min(pos, max);
            updateReaderProgress(el.scrollTop / max);
          }
          _suppressScroll = false;
          return true;
        }
        return false;
      };
      const tick = () => {
        if (apply()) return;
        if (++tries < 30) requestAnimationFrame(tick);
        else _suppressScroll = false;
      };
      requestAnimationFrame(tick);
    }

    async function setArticleStatus(status) {
      if (!_currentArticleId) return;
      try {
        const res = await fetch(`/api/reading/items/${_currentArticleId}/status`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status }),
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        showToast(
          status === "finished" ? "已标为读完" : status === "reading" ? "已标为正在读" : "已标为未读"
        );
        void loadLibraryItems();
      } catch {
        showToast("状态更新失败");
      }
    }

    function toggleReadingStatus(itemId, currentStatus, cardEl) {
      const nextStatus = { unread: "reading", reading: "finished", finished: "unread" }[currentStatus] || "unread";
      fetch(`/api/reading/items/${itemId}/status`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      })
        .then((r) => { if (!r.ok) throw new Error(); return r.json(); })
        .then(() => {
          cardEl.dataset.status = nextStatus;
          cardEl.querySelector(".library-card-status").className = `library-card-status ${nextStatus}`;
          const label = { unread: "未读", reading: "正在读", finished: "已读完" }[nextStatus];
          cardEl.querySelector(".library-card-status").textContent = label;
          const btn = cardEl.querySelector('[data-action="toggle-status"]');
          if (btn) btn.textContent = nextStatus === "finished" ? "重读" : nextStatus === "reading" ? "标为已读" : "开始读";
        })
        .catch(() => {});
    }

    function openTagEditor(itemId, currentTags) {
      const overlay = document.createElement("div");
      overlay.className = "tag-editor-overlay";
      overlay.innerHTML = `
        <div class="tag-editor">
          <h3>编辑标签</h3>
          <input class="tag-editor-input" id="tagEditorInput" value="${escapeHtml(currentTags.join(", "))}" placeholder="输入标签，用逗号分隔" autofocus>
          <div class="tag-editor-actions">
            <button class="secondary" id="tagEditorCancel" type="button">取消</button>
            <button class="primary" id="tagEditorSave" type="button">保存</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);

      const input = overlay.querySelector("#tagEditorInput");
      const save = () => {
        const tags = (input.value || "").split(",").map((t) => t.trim()).filter(Boolean);
        fetch(`/api/reading/items/${itemId}/tags`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tags }),
        })
          .then((r) => { if (!r.ok) throw new Error(); return r.json(); })
          .then(() => { overlay.remove(); void loadLibraryItems(); })
          .catch(() => { overlay.remove(); });
      };
      overlay.querySelector("#tagEditorSave").addEventListener("click", save);
      overlay.querySelector("#tagEditorCancel").addEventListener("click", () => overlay.remove());
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
      window.setTimeout(() => input?.focus(), 100);

      overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    }

    // ── Saved pages: 稍后再看 (watch-later) & 收藏 (favorites) ──────
    // The two are independent backend collections sharing one list UI.

    function watchLaterStatus(bvid) {
      return requestJson(`${ENDPOINTS.watchLater}/${encodeURIComponent(bvid)}`);
    }

    function favoriteStatus(bvid) {
      return requestJson(`${ENDPOINTS.favorites}/${encodeURIComponent(bvid)}`);
    }

    function updateSavedBadge(badgeId, total) {
      const badge = document.getElementById(badgeId);
      if (!badge) return;
      const n = Number(total) || 0;
      if (n > 0) {
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.removeAttribute("hidden");
      } else {
        badge.textContent = "";
        badge.setAttribute("hidden", "");
      }
    }

    // 收藏 / 稍后再看 共用同一套卡片外观（与首页 .video-card 对齐）：
    // 封面 + 两行标题 + 作者/平台，操作按钮默认隐藏、悬停才显形。
    function renderSavedList(grid, items, onRemove) {
      if (!grid) return;
      const rows = Array.isArray(items) ? items : [];
      if (!rows.length) {
        grid.replaceChildren();
        return;
      }
      grid.replaceChildren(...rows.map((item) => {
        const card = document.createElement("article");
        card.className = "saved-card";
        const url = contentUrl(item);
        const title = item.title || item.bvid || "";
        const author = item.up_name || item.author_name || "";
        const platform = platformName(item.source_platform || item.platform || "");
        const tag = platform || "原文";
        const cover = item.cover_url || item.cover || "";
        const coverHtml = cover
          ? `<img class="saved-card-cover" src="${escapeHtml(cover)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
          : `<div class="saved-card-cover is-placeholder" aria-hidden="true">${escapeHtml(String(title).slice(0, 1) || "文")}</div>`;
        card.innerHTML = `
          <div class="saved-card-media">
            ${coverHtml}
            <div class="saved-card-actions">
              <button class="saved-card-action" type="button" aria-label="打开原文" title="打开原文">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6"/><path d="M20 4l-8.5 8.5"/><path d="M18 14.5V19a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V8a1.5 1.5 0 0 1 1.5-1.5H10"/></svg>
              </button>
              <button class="saved-card-action is-remove" type="button" aria-label="移除" title="移除">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
              </button>
            </div>
          </div>
          <div class="saved-card-body">
            <h3 class="saved-card-title">${escapeHtml(title)}</h3>
            <div class="saved-card-meta">
              <span class="saved-card-author">${escapeHtml(author)}</span>
              <span class="saved-card-tag">${escapeHtml(tag)}</span>
            </div>
          </div>`;
        const openBtn = card.querySelector(".saved-card-action:not(.is-remove)");
        if (url) {
          card.querySelector(".saved-card-media").addEventListener("click", (e) => {
            if (e.target.closest(".saved-card-action")) return;
            window.open(url, "_blank", "noopener,noreferrer");
          });
          openBtn.addEventListener("click", () => window.open(url, "_blank", "noopener,noreferrer"));
        } else {
          openBtn.disabled = true;
        }
        card.querySelector(".is-remove").addEventListener("click", async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            await onRemove(item.bvid || item.item_key);
            card.remove();
          } catch {
            btn.disabled = false;
          }
        });
        return card;
      }));
    }

    // 收藏与稍后再看是两个互相独立的后端集合，现在各自拥有独立页面与 URL。
    const SAVED_LIST_VIEWS = {
      favorite: {
        pageId: "savedPage",
        grid: "savedList",
        empty: "savedEmpty",
        titleBadge: "favCountBadge",
        navBadge: "favoritesCountBadge",
        endpoint: () => ENDPOINTS.favorites,
        emptyText: "还没有收藏的内容，去推荐里点星标收藏吧。",
      },
      watch_later: {
        pageId: "watchLaterPage",
        grid: "watchLaterList",
        empty: "watchLaterEmpty",
        titleBadge: "wlCountBadge",
        navBadge: "watchLaterCountBadge",
        endpoint: () => ENDPOINTS.watchLater,
        emptyText: "还没有稍后再看的内容，去推荐里点时钟图标加入吧。",
      },
    };

    async function refreshSavedList(listKind) {
      const view = SAVED_LIST_VIEWS[listKind];
      if (!view) return;
      const endpoint = view.endpoint();
      const data = await requestJson(`${endpoint}?limit=100&offset=0`).catch(() => null);
      const items = data?.items || [];
      const total = data?.total || 0;
      const grid = document.getElementById(view.grid);
      const empty = document.getElementById(view.empty);
      if (!grid) return;
      if (!items.length) {
        grid.replaceChildren();
        if (empty) {
          empty.textContent = view.emptyText;
          empty.removeAttribute("hidden");
        }
      } else {
        if (empty) empty.setAttribute("hidden", "");
        renderSavedList(grid, items, async (itemKey) => {
          await requestJson(`${endpoint}/${encodeURIComponent(itemKey)}`, { method: "DELETE" });
          void refreshSavedList(listKind);
        });
      }
      // 页面标题徽章 + 顶栏导航徽章共用本次请求的 total，避免再打一次接口。
      updateSavedBadge(view.titleBadge, total);
      updateSavedBadge(view.navBadge, total);
      return total;
    }

    function openSavedListView(listKind) {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      const view = SAVED_LIST_VIEWS[listKind] || SAVED_LIST_VIEWS.favorite;
      showMainPage(view.pageId);
      void refreshSavedList(listKind);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openSavedPage() {
      openSavedListView("favorite");
    }

    function openWatchLaterPage() {
      openSavedListView("watch_later");
    }

    async function refreshWatchLater() {
      const data = await requestJson(`${ENDPOINTS.watchLater}?limit=100&offset=0`).catch(() => null);
      updateSavedBadge("watchLaterCountBadge", data?.total);
    }

    async function refreshFavorites() {
      const data = await requestJson(`${ENDPOINTS.favorites}?limit=100&offset=0`).catch(() => null);
      updateSavedBadge("favoritesCountBadge", data?.total);
    }

    // Re-sync the pressed state + count badge for all visible ☆/♥ toggles.
    function syncWatchLaterButtons() {
      requestJson(`${ENDPOINTS.watchLater}?limit=200&offset=0`).then((data) => {
        const saved = new Set((data?.items || []).map((it) => it.bvid));
        document.querySelectorAll('.video-card [data-action="watch-later"]').forEach((btn) => {
          const card = btn.closest(".video-card");
          const bvid = card?.dataset?.bvid;
          if (!bvid) return;
          const on = saved.has(bvid);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        updateSavedBadge("watchLaterCountBadge", data?.total);
      }).catch(() => {});
    }

    function syncFavoriteButtons() {
      requestJson(`${ENDPOINTS.favorites}?limit=200&offset=0`).then((data) => {
        const saved = new Set((data?.items || []).map((it) => it.bvid));
        document.querySelectorAll('.video-card [data-action="favorite"]').forEach((btn) => {
          const card = btn.closest(".video-card");
          const bvid = card?.dataset?.bvid;
          if (!bvid) return;
          const on = saved.has(bvid);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        });
        updateSavedBadge("favoritesCountBadge", data?.total);
      }).catch(() => {});
    }

    function setSideDrawerOpen(open, { persist = true } = {}) {
      const drawer = document.getElementById("sideDrawer");
      drawer?.classList.toggle("is-open", open);
      drawer?.setAttribute("aria-hidden", open ? "false" : "true");
      document.body.classList.toggle("side-drawer-open", open);
      const button = document.getElementById("sideDrawerBtn");
      if (button) {
        button.setAttribute("aria-expanded", open ? "true" : "false");
        button.setAttribute("aria-label", open ? "收起侧边菜单" : "展开侧边菜单");
      }
      if (persist) storageSet(SIDE_DRAWER_OPEN_KEY, open ? "1" : "0");
    }

    function openSideDrawer(options) {
      setSideDrawerOpen(true, options);
    }

    function closeSideDrawer(options) {
      setSideDrawerOpen(false, options);
    }

    function toggleSideDrawer() {
      const drawer = document.getElementById("sideDrawer");
      setSideDrawerOpen(!drawer?.classList.contains("is-open"));
    }

    function isMobileViewport() {
      return window.matchMedia?.("(max-width: 820px)").matches;
    }

    function syncMobileSearch() {
      const input = $("#mobileSearchInput");
      if (input && input.value !== state.query) input.value = state.query || "";
    }

    function openMobileMenu() {
      syncMobileSearch();
      window.renderRail();
      document.body.classList.add("mobile-menu-open");
      document.getElementById("mobileMenu")?.classList.add("is-open");
    }

    function closeMobileMenu() {
      document.body.classList.remove("mobile-menu-open");
      document.getElementById("mobileMenu")?.classList.remove("is-open");
    }

    function openMobilePanel(id, options = {}) {
      closeMobileMenu();
      if (id === "messagesDrawer") {
        window.hydrateInboxFromSpeculations(state.profile?.speculative_interests);
        window.hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
        state.messageListSnapshot = window.getRenderableMessages();
        returnToMessages();
        window.renderMessages();
        void window.refreshProfile().catch(() => {});
      }
      if (id === "activityDrawer") window.renderActivityHistory();
      const panel = document.getElementById(id);
      panel?.classList.add("from-mobile-menu");
      openPanel(id);
    }

    function openMobilePage(id, options = {}) {
      if (id === "profilePage") openProfilePage();
      if (id === "chatPage") openChatPage();
      if (id === "settingsPage") openSettingsPage(options.settingsPanel || "models");
    }

    function returnToMobileMenu(event) {
      const panel = event.target.closest(".drawer, .overlay");
      if (panel?.id) closePanel(panel.id);
      openMobileMenu();
    }

    function platformName(value) {
      return platformLabel[String(value || "").toLowerCase()] || String(value || "").trim();
    }

    function configuredSourceFilterLabels() {
      // 所有已定义的来源都显示在筛选按钮；如果禁用了，用户选了也会返回空，但至少能看到按钮
      // 修复：之前只显示后端配置 enabled 的，导致小红书等平台明明爬了内容却看不到按钮
      return sourceFilterDefinitions
        .map((source) => source.label);
    }

    function buildFilters() {
      const sourceSet = new Set(configuredSourceFilterLabels());
      for (const item of state.videos) {
        const label = platformName(item.platform);
        if (label) sourceSet.add(label);
      }
      const sources = sourceFilterOrder.filter((name) => sourceSet.has(name));
      const otherSources = [...sourceSet].filter((name) => !sourceFilterOrder.includes(name)).sort((a, b) => a.localeCompare(b, "zh-Hans-CN"));
      return ["全部", ...sources, ...otherSources];
    }

    function filteredVideos() {
      const q = state.query.trim().toLowerCase();
      const activePage = document.querySelector(".main-col:not([hidden])");
      const pageId = activePage?.id || "homePage";
      const isCustomPage = pageId === "customFilterPage";
      return state.videos.filter((item) => {
        const label = platformName(item.platform);
        let platformOk;
        if (isCustomPage) {
          // 自定义页：勾选的平台集合；不勾 = 不限
          platformOk = !state.customFilterSources || state.customFilterSources.size === 0
            || state.customFilterSources.has(item.platform);
        } else {
          // 首页：零筛选
          platformOk = true;
        }
        // 内容类型（仅自定义页）：勾选的类型集合；不勾 = 不限
        let typeOk = true;
        if (isCustomPage && state.customContentTypes && state.customContentTypes.size > 0) {
          typeOk = state.customContentTypes.has(String(item.content_type || "").toLowerCase());
        }
        // 关键词（仅自定义页）：匹配标题/作者/话题/理由/平台
        let keywordOk = true;
        const customKeyword = String(state.customKeyword || "").trim().toLowerCase();
        if (isCustomPage && customKeyword) {
          keywordOk = [item.title, item.up, item.author_name, item.up_name, item.topic, item.reason, label]
            .map((part) => String(part || ""))
            .join(" ")
            .toLowerCase()
            .includes(customKeyword);
        }
        const queryOk = !q || [item.title, item.up, item.topic, item.reason, label].join(" ").toLowerCase().includes(q);
        return platformOk && typeOk && keywordOk && queryOk;
      });
    }

    // Map the active platform filter label back to a backend source_platform
    // key (e.g. "B 站" -> "bilibili"). Returns null for "全部" so the
    // recommender serves a mixed batch.
    function platformKeyForFilter() {
      if (state.filter === "全部") return null;
      const def = sourceFilterDefinitions.find((source) => source.label === state.filter);
      return def ? def.key : null;
    }

    function setDismissOnReshuffle(enabled, { persist = true, toast = false } = {}) {
      state.dismissOnReshuffle = Boolean(enabled);
      if (persist) storageSet(DISMISS_ON_RESHUFFLE_KEY, state.dismissOnReshuffle ? "1" : "0");
      renderReshuffleToggle();
      if (toast) showToast(state.dismissOnReshuffle ? "换一批前会忽略当前显示的推荐" : "换一批不会自动忽略当前推荐");
    }

    function renderReshuffleToggle() {
      const toggles = [$("#dismissOnReshuffleToggle"), $("#dismissOnReshuffleSetting")];
      toggles.forEach((toggle) => {
        if (toggle && toggle.checked !== state.dismissOnReshuffle) toggle.checked = state.dismissOnReshuffle;
      });
      const settingText = $("#dismissOnReshuffleSettingText");
      if (settingText) settingText.textContent = state.dismissOnReshuffle ? "开启" : "关闭";
    }

    function renderViewTabs() {
      // 视图切换已迁移至独立路由：/web/recommendations /web/platform-filter /web/custom-filter
    }

    function buildCustomCheckbox(options, container, selectedSet, onChange) {
      // 通用勾选组渲染：options=[{key,label}]，selectedSet 为 Set
      container.replaceChildren();
      options.forEach((def) => {
        const labelEl = document.createElement("label");
        labelEl.className = `filter-checkbox${selectedSet.has(def.key) ? " is-checked" : ""}`;
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = selectedSet.has(def.key);
        input.addEventListener("change", () => {
          if (input.checked) {
            selectedSet.add(def.key);
            labelEl.classList.add("is-checked");
          } else {
            selectedSet.delete(def.key);
            labelEl.classList.remove("is-checked");
          }
          if (onChange) onChange();
        });
        labelEl.appendChild(input);
        labelEl.appendChild(document.createTextNode(" " + def.label));
        container.appendChild(labelEl);
      });
    }

    function renderCustomFilterPanel() {
      // 自定义筛选页面内面板：平台 + 内容类型 + 每批数量 + 关键词
      const platformGroup = $("#customPlatformGroup");
      const contentTypeGroup = $("#customContentTypeGroup");
      const limitGroup = $("#customLimitGroup");
      const keywordInput = $("#customKeywordInput");
      if (!platformGroup || !contentTypeGroup) return;

      // 默认状态：全部不勾（= 不限制）
      if (!state.customFilterSources) state.customFilterSources = new Set();
      if (!state.customContentTypes) state.customContentTypes = new Set();

      const syncHint = () => {
        const hint = $("#customFilterHint");
        if (!hint) return;
        const parts = [];
        const platformCount = state.customFilterSources.size;
        const typeCount = state.customContentTypes.size;
        const keyword = String(state.customKeyword || "").trim();
        if (platformCount === 0) parts.push("平台不限");
        else if (platformCount === sourceFilterDefinitions.length) parts.push("全部平台");
        else parts.push(`平台 ${platformCount} 项`);
        if (typeCount === 0) parts.push("类型不限");
        else parts.push(`类型 ${typeCount} 项`);
        parts.push(`每批 ${state.customLimit || 10} 条`);
        if (keyword) parts.push(`关键词“${keyword}”`);
        hint.textContent = parts.join(" · ");
      };

      buildCustomCheckbox(sourceFilterDefinitions, platformGroup, state.customFilterSources, syncHint);
      buildCustomCheckbox(contentTypeFilterDefinitions, contentTypeGroup, state.customContentTypes, syncHint);

      // 每批数量：单选 chips
      if (limitGroup) {
        const limitOptions = [10, 30, 50, 100, 200];
        limitGroup.replaceChildren();
        limitOptions.forEach((value) => {
          const btn = document.createElement("button");
          btn.className = `chip${Number(state.customLimit) === value ? " is-active" : ""}`;
          btn.type = "button";
          btn.textContent = `${value} 条`;
          btn.addEventListener("click", () => {
            state.customLimit = value;
            limitGroup.querySelectorAll(".chip").forEach((chip) => chip.classList.remove("is-active"));
            btn.classList.add("is-active");
            syncHint();
          });
          limitGroup.appendChild(btn);
        });
      }

      if (keywordInput && keywordInput.value !== (state.customKeyword || "")) {
        keywordInput.value = state.customKeyword || "";
      }
      if (keywordInput && !keywordInput._bound) {
        keywordInput._bound = true;
        keywordInput.addEventListener("input", () => {
          state.customKeyword = keywordInput.value || "";
          syncHint();
        });
        keywordInput.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            window.reshuffle?.();
          }
        });
      }
      syncHint();
    }

    function resetCustomFilters() {
      state.customFilterSources = new Set();
      state.customContentTypes = new Set();
      state.customKeyword = "";
      state.customLimit = 50;
      renderCustomFilterPanel();
    }

    function currentCustomFilterPlatform() {
      // 单个平台时透传给后端；多选或全不选 → null（混合推荐，客户端过滤）
      if (!state.customFilterSources || state.customFilterSources.size !== 1) {
        return null;
      }
      return Array.from(state.customFilterSources)[0];
    }

    function toggleFilterDropdown() {
      const menu = document.getElementById("filterDropdownMenu");
      const trigger = document.getElementById("filterDropdownTrigger");
      if (!menu || !trigger) return;
      const isOpen = !menu.hidden;
      if (isOpen) {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      } else {
        // position: fixed，动态计算触发按钮下方的位置
        const rect = trigger.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 4}px`;
        menu.style.left = `${rect.left}px`;
        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
      }
    }
    function closeFilterDropdown() {
      const menu = document.getElementById("filterDropdownMenu");
      const trigger = document.getElementById("filterDropdownTrigger");
      if (menu) menu.hidden = true;
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    }

    function toggleFeedDropdown() {
      const menu = document.getElementById("feedDropdownMenu");
      const trigger = document.getElementById("feedDropdownTrigger");
      if (!menu || !trigger) return;
      const isOpen = !menu.hidden;
      if (isOpen) {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      } else {
        const rect = trigger.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 4}px`;
        menu.style.left = `${rect.left}px`;
        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
      }
    }
    function closeFeedDropdown() {
      const menu = document.getElementById("feedDropdownMenu");
      const trigger = document.getElementById("feedDropdownTrigger");
      if (menu) menu.hidden = true;
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    }

    function togglePoolDropdown() {
      const menu = document.getElementById("poolDropdownMenu");
      const trigger = document.getElementById("poolDropdownTrigger");
      if (!menu || !trigger) return;
      const isOpen = !menu.hidden;
      if (isOpen) {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      } else {
        closeFilterDropdown();
        closeFeedDropdown();
        closeMineDropdown();
        const rect = trigger.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 4}px`;
        menu.style.left = `${rect.left}px`;
        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
      }
    }
    function closePoolDropdown() {
      const menu = document.getElementById("poolDropdownMenu");
      const trigger = document.getElementById("poolDropdownTrigger");
      if (menu) menu.hidden = true;
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    }

    function toggleMineDropdown() {
      const menu = document.getElementById("mineDropdownMenu");
      const trigger = document.getElementById("mineDropdownTrigger");
      if (!menu || !trigger) return;
      const isOpen = !menu.hidden;
      if (isOpen) {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      } else {
        closeFilterDropdown();
        closeFeedDropdown();
        closePoolDropdown();
        const rect = trigger.getBoundingClientRect();
        menu.style.top = `${rect.bottom + 4}px`;
        menu.style.left = `${rect.left}px`;
        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
      }
    }
    function closeMineDropdown() {
      const menu = document.getElementById("mineDropdownMenu");
      const trigger = document.getElementById("mineDropdownTrigger");
      if (menu) menu.hidden = true;
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    }

    function renderFilters() {
      const bar = $("#platformFilterBar");
      if (!bar) return;
      const filters = buildFilters();
      if (!filters.includes(state.filter)) state.filter = "全部";
      bar.replaceChildren(...filters.map((name) => {
        const btn = document.createElement("button");
        btn.className = `chip${state.filter === name ? " is-active" : ""}`;
        btn.type = "button";
        btn.textContent = name;
        btn.addEventListener("click", () => { state.filter = name; window.reshuffle?.(); });
        return btn;
      }));
    }

    function normalizeImageUrl(value) {
      const url = String(value || "").trim();
      if (!url) return "";
      if (url.startsWith("//")) return `https:${url}`;
      if (url.startsWith("http://")) return `https://${url.slice("http://".length)}`;
      return url;
    }

    function imageProxyUrl(value) {
      const url = normalizeImageUrl(value);
      if (!url) return "";
      try {
        new URL(url);
      } catch {
        return "";
      }
      const base = getApiBase() || DEFAULT_API_BASE;
      // cross-origin <img> can't send the cookie/header → carry the token in the query
      return appendToken(`${base}/image-proxy?url=${encodeURIComponent(url)}`);
    }

    // In cross-origin bearer mode the cover <img> carries the token in ?token=,
    // but a plain <img> sends no Origin so the backend would ignore it. Marking
    // it crossorigin makes the browser send Origin (and skip the cookie), so the
    // allowed-origin + ?token= path authorizes it. Same-origin mode omits this so
    // the cookie is still sent. See review r4#2.
    function imgCrossOriginAttr() {
      return isCrossOriginBase() ? ' crossorigin="anonymous"' : "";
    }

    function coverImg(item) {
      const url = imageProxyUrl(item.cover_url);
      if (!url) return "";
      // loading="eager" (not lazy): cover starts fetching the moment the card is
      // in the DOM, so a card scrolled into view never shows the gradient
      // placeholder while a native lazy <img> defers its fetch ("白一下再出来").
      return `<img src="${escapeHtml(url)}"${imgCrossOriginAttr()} alt="${escapeHtml(item.title)} 的封面" loading="eager" fetchpriority="auto" decoding="async" referrerpolicy="no-referrer">`;
    }

    // Warm the browser cache for a batch of cover images before their cards are
    // (re)rendered. Used by appendMore so newly loaded covers paint instantly
    // instead of flashing the placeholder while they download. Resolves on a
    // timeout so one slow cover can't stall the batch.
    const warmedCoverUrls = new Set();
    function warmCoverImages(items, { waitForDecode = false, timeoutMs = 4000 } = {}) {
      if (typeof Image === "undefined") return Promise.resolve();
      const pending = [];
      for (const item of items || []) {
        const src = imageProxyUrl(item?.cover_url);
        if (!src || warmedCoverUrls.has(src)) continue;
        warmedCoverUrls.add(src);
        const img = new Image();
        if (isCrossOriginBase()) img.crossOrigin = "anonymous";
        img.decoding = "async";
        const loaded = new Promise((resolve) => {
          img.onload = () => resolve();
          img.onerror = () => resolve();
        });
        img.src = src;
        let ready = loaded;
        if (typeof img.decode === "function") ready = img.decode().catch(() => {});
        if (waitForDecode) pending.push(ready);
      }
      if (!waitForDecode || pending.length === 0) return Promise.resolve();
      return Promise.race([Promise.all(pending), new Promise((resolve) => setTimeout(resolve, timeoutMs))]);
    }

    function contentUrl(item) {
      if (item.content_url) return item.content_url;
      if (item.platform === "bilibili" && item.bvid) return `https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}`;
      if (item.platform === "youtube" && item.content_id) return `https://www.youtube.com/watch?v=${encodeURIComponent(item.content_id)}`;
      if (item.platform === "twitter" && item.content_id) return `https://x.com/i/status/${encodeURIComponent(item.content_id)}`;
      if (item.platform === "xiaohongshu" && item.content_id) return `https://www.xiaohongshu.com/explore/${encodeURIComponent(item.content_id)}`;
      if (item.platform === "xiaohongshu" && item.bvid) return `https://www.xiaohongshu.com/explore/${encodeURIComponent(item.bvid)}`;
      return "";
    }

    function recommendationTextCardText(item) {
      return String(item.body_text || item.title || "先看文字也行").trim();
    }

    function recommendationIsTextCard(item) {
      const hasCover = Boolean(imageProxyUrl(item.cover_url));
      return textCardContentTypes.has(String(item.content_type || "").toLowerCase()) || !hasCover;
    }

    function recommendationCoverClass(item) {
      return recommendationIsTextCard(item) ? " is-text-card" : "";
    }

    function recommendationMediaHtml(item) {
      if (recommendationIsTextCard(item)) {
        return `<p class="cover-text">${escapeHtml(recommendationTextCardText(item))}</p>`;
      }
      return coverImg(item);
    }

    function recommendationMeta(item) {
      return [item.up, item.topic]
        .map((part) => String(part || "").trim())
        .filter(Boolean)
        .join(" · ");
    }

    /* ── MindBack-style card mode ─────────────────────────── */
    function mindbackCardHtml(item) {
      var cover = item.cover_url || "";
      var title = item.title || "";
      var up = item.up_name || item.author_name || "";
      var src = (item.source_platform || item.platform || "").toLowerCase();
      var srcLabel = platformName(src);
      var url = item.content_url || item.url || "";

      return [
        '<div class="mindback-cover-wrap">',
          cover ? '<img class="mindback-cover" src="' + escapeHtml(cover) + '" alt="" loading="lazy" onerror="this.parentElement.classList.add(\'no-cover\');this.remove()">' : '<div class="mindback-cover no-cover"></div>',
          url ? '<a class="mindback-origin-link" href="' + escapeHtml(url) + '" target="_blank" rel="noopener" title="原文" onclick="event.stopPropagation()">原文</a>' : "",
          '<span class="mindback-platform-badge">' + escapeHtml(srcLabel) + '</span>',
        '</div>',
        '<div class="mindback-body">',
          '<div class="mindback-title">' + escapeHtml(title) + '</div>',
          up ? '<div class="mindback-author">' + escapeHtml(up) + '</div>' : "",
        '</div>',
      ].join("");
    }

    function bindMindbackCardEvents(card, item) {
      var url = item.content_url || item.url || "";
      card.addEventListener("click", function () {
        if (url) { reportRecommendationClick(item, card); window.open(url, "_blank"); }
      });
    }

    function activeVideoGrid() {
      const page = document.querySelector(".main-col:not([hidden])");
      if (!page) return grid;
      const id = page.id;
      if (id === "customFilterPage") return $("#customVideoGrid") || grid;
      return grid;
    }

    function activeLoadMoreBtn() {
      const page = document.querySelector(".main-col:not([hidden])");
      if (!page) return $("#loadMoreBtn");
      const id = page.id;
      if (id === "customFilterPage") return $("#customLoadMoreBtn");
      return $("#loadMoreBtn");
    }

    function renderVideos() {
      if (window.shouldShowInitOnboarding?.(state.runtimeStatus)) {
        renderInitOnboarding();
        return;
      }
      const g = activeVideoGrid();
      const loadMore = activeLoadMoreBtn();
      if (loadMore) loadMore.hidden = false;
      const items = filteredVideos();
      if (!items.length) {
        const message = state.query.trim()
          ? `没有找到包含“${escapeHtml(state.query.trim())}”的推荐。`
          : state.videos.length
            ? "当前筛选下没有推荐。"
            : "当前列表里的推荐都已处理，可以换一批推荐或等待后端补货。";
        g.innerHTML = `<div class="empty-state">${message}</div>`;
        return;
      }
      g.classList.add("is-minimal");
      // Bulk query saved states to avoid N round trips
      const bvids = items.map((item) => item.bvid || item.id).filter(Boolean);
      // 一次批量查询替代 N×2 个请求（收藏 + 稍后看各一条），
      // 列表 200+ 条时原实现发 400+ 请求，页面打开被请求洪水拖到 10s+。
      const savedStatusPromise = bvids.length
        ? requestJson(`${ENDPOINTS.savedStatus}?bvids=${encodeURIComponent(bvids.join(","))}`, { timeoutMs: 10000 }).catch(() => ({}))
        : Promise.resolve({});
      g.replaceChildren(...items.map((item, i) => {
        const card = document.createElement("article");
        card.className = "video-card is-minimal";
        card.dataset.bvid = item.bvid || item.id;
        const platform = platformName(item.platform);
        const author = item.up_name || item.author_name || "";
        card.innerHTML = `
          <p class="video-card-title">${escapeHtml(item.title)}</p>
          <div class="video-card-meta">
            <span class="video-card-author">${escapeHtml(author)}</span>
            <span class="video-card-tag">${escapeHtml(platform)}</span>
          </div>
          ${item.quality_reason ? `<p class="video-card-reason">${escapeHtml(item.quality_reason)}</p>` : ""}
          <div class="video-card-actions">
            <button class="feedback-icon-btn" data-action="like" type="button" aria-label="喜欢" title="喜欢">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>
            </button>
            <button class="feedback-icon-btn" data-action="dislike" type="button" aria-label="不感兴趣" title="不感兴趣">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg>
            </button>
            <button class="feedback-icon-btn watch-later-btn" data-action="watch-later" type="button" aria-label="稍后再看" title="稍后再看" aria-pressed="false">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>
            </button>
            <button class="feedback-icon-btn favorite-btn" data-action="favorite" type="button" aria-label="收藏" title="收藏" aria-pressed="false">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>
            </button>
          </div>`;
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          const url = contentUrl(item);
          if (url) { window.openRecommendation(item, card); }
        });
        card.querySelectorAll("[data-action]").forEach((btn) => btn.addEventListener("click", () => window.handleCardAction(btn.dataset.action, item, card)));
        // Update saved state after bulk status promise resolves
        const bvid = item.bvid || item.id;
        const wlBtn = card.querySelector('[data-action="watch-later"]');
        const favBtn = card.querySelector('[data-action="favorite"]');
        savedStatusPromise.then((statusMap) => {
          const st = (statusMap && statusMap[bvid]) || {};
          if (wlBtn && st.watch_later) { wlBtn.setAttribute("aria-pressed", "true"); wlBtn.title = "取消稍后再看"; }
          if (favBtn && st.saved) { favBtn.setAttribute("aria-pressed", "true"); favBtn.title = "取消收藏"; }
        });
        return card;
      }));
      return;
    }

    function trackRecommendationClick(item) {
      const url = contentUrl(item);
      void requestJson(ENDPOINTS.click, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bvid: item.bvid,
          content_id: item.content_id || item.bvid,
          content_url: url || item.content_url,
          source_platform: item.platform,
          title: item.title,
          recommendation_id: item.id,
          topic_label: item.topic,
          up_name: item.up
        })
      }).catch(() => {});
    }

    /* ── 池子总览 ──────────────────────────────────────────────── */

    function loadPoolAllItems() {
      if (state.poolAllLoading) return;
      state.poolAllLoading = true;
      const grid = $("#poolAllGrid");
      if (grid) grid.innerHTML = `<div class="empty-state">正在加载池子数据…</div>`;
      requestJson(ENDPOINTS.poolAll + "?shuffle=true&limit=6", { timeoutMs: 60000 }).then((data) => {
        state.poolAllLoading = false;
        if (!data || !data.items) {
          if (grid) grid.innerHTML = `<div class="empty-state">加载失败，请稍后重试。</div>`;
          return;
        }
        state.poolAllItems = data.items;
        // 更新统计卡片
        const rawEl = $("#poolAllRaw");
        if (rawEl) rawEl.textContent = String(data.raw) + " (总" + data.total + ")";
        const availEl = $("#poolAllAvailable");
        if (availEl) availEl.textContent = String(data.available);
        const pendEl = $("#poolAllPending");
        if (pendEl) pendEl.textContent = String(data.pending);
        const totalEl = $("#poolAllTotal");
        if (totalEl) totalEl.textContent = String(data.total);
        renderPoolAllStatusBar();
        renderPoolAllItems();
      }).catch(() => {
        state.poolAllLoading = false;
        if (grid) grid.innerHTML = `<div class="empty-state">请求失败，请检查后端连接。</div>`;
      });
    }

    function renderPoolAllStatusBar() {
      const bar = $("#poolAllStatusBar");
      if (!bar || !state.poolAllItems.length) return;
      const counts = {};
      state.poolAllItems.forEach((item) => {
        const s = item.pool_status || "unknown";
        counts[s] = (counts[s] || 0) + 1;
      });
      const total = state.poolAllItems.length;
      const labels = { fresh: "待推荐", shown: "已展示", stale: "已过期", suppressed: "已抑制", feedbacked: "已反馈", pending: "处理中" };
      const colors = { fresh: "#4caf50", shown: "#2196f3", stale: "#ff9800", suppressed: "#9e9e9e", feedbacked: "#e91e63", pending: "#ff5722" };
      const order = ["fresh", "shown", "stale", "suppressed", "feedbacked", "pending"];
      bar.replaceChildren(
        ...order
          .filter((k) => counts[k] > 0)
          .map((k) => {
            const pct = ((counts[k] / total) * 100).toFixed(1);
            const chip = document.createElement("span");
            chip.className = "pool-status-chip";
            chip.innerHTML = `<span class="chip-dot" style="background:${colors[k] || '#888'}"></span>${labels[k] || k} <em>${counts[k]}</em><span class="chip-pct">${pct}%</span>`;
            return chip;
          })
      );
    }

    function renderPoolAllItems() {
      const grid = $("#poolAllGrid");
      if (!grid) return;
      const items = state.poolAllItems;
      if (!items.length) {
        grid.innerHTML = `<div class="empty-state">池子为空。</div>`;
        return;
      }
      const statusMeta = {
        fresh: { label: "待推荐", color: "#4caf50" },
        shown: { label: "已展示", color: "#2196f3" },
        stale: { label: "已过期", color: "#ff9800" },
        suppressed: { label: "已抑制", color: "#9e9e9e" },
        feedbacked: { label: "已反馈", color: "#e91e63" },
        pending: { label: "处理中", color: "#ff5722" },
      };
      // 切分批次：先渲染前 50 条，剩余用 IntersectionObserver 懒加载
      const batchSize = 50;
      const renderBatch = (start, end) => {
        const fragment = document.createDocumentFragment();
        items.slice(start, end).forEach((item) => {
          const card = document.createElement("article");
          const meta = statusMeta[item.pool_status] || { label: item.pool_status, color: "#888" };
          card.className = "video-card is-minimal pool-all-card";
          card.dataset.bvid = item.bvid;
          const platform = platformName(item.source_platform);
          const author = item.up_name || "";
          const hasReason = !!item.quality_reason;
          card.innerHTML = `
            <p class="video-card-title">${escapeHtml(item.title || "无标题")}</p>
            <div class="video-card-meta">
              <span class="video-card-author">${escapeHtml(author)}</span>
              <span class="video-card-tag">${escapeHtml(platform)}</span>
              <span class="pool-status-badge" style="--badge-bg:${meta.color}">${meta.label}</span>
            </div>
            ${hasReason ? `<p class="video-card-reason">${escapeHtml(item.quality_reason)}</p>` : ""}
            <div class="video-card-actions">
              <button class="feedback-icon-btn" data-action="open" type="button" aria-label="打开原文" title="打开原文">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
              </button>
            </div>`;
          card.addEventListener("click", (e) => {
            if (e.target.closest("[data-action]")) return;
            window.openRecommendation(item, card);
          });
          card.querySelector("[data-action]")?.addEventListener("click", (e) => {
            e.stopPropagation();
            window.openRecommendation(item, card);
          });
          fragment.appendChild(card);
        });
        grid.appendChild(fragment);
      };
      grid.replaceChildren();
      renderBatch(0, Math.min(batchSize, items.length));
      // 懒加载剩余
      if (items.length > batchSize) {
        const sentinel = document.createElement("div");
        sentinel.className = "pool-all-sentinel";
        grid.after(sentinel);
        let loaded = batchSize;
        const observer = new IntersectionObserver((entries) => {
          if (entries[0].isIntersecting && loaded < items.length) {
            const next = Math.min(loaded + batchSize, items.length);
            renderBatch(loaded, next);
            loaded = next;
            if (loaded >= items.length) {
              observer.disconnect();
              sentinel.remove();
            }
          }
        }, { rootMargin: "200px" });
        observer.observe(sentinel);
      }
    }

    // ── 池子筛选（按平台） ──────────────────────────────────────────────────

    function loadPoolFilterItems() {
      state.poolFilterLoading = true;
      const grid = $("#poolFilterGrid");
      if (grid) grid.innerHTML = `<div class="empty-state">正在加载池子数据…</div>`;
      const platform = state.poolFilterPlatform === "全部" ? null : state.poolFilterPlatform;
      // 获取平台对应的key，如果不能直接匹配则用label本身
      const def = platform && sourceFilterDefinitions.find((s) => s.label === platform);
      const platformKey = def ? def.key : platform;
      const params = new URLSearchParams();
      params.set("shuffle", "true");
      params.set("limit", "6");
      if (platformKey) params.set("platform", platformKey);
      requestJson(`${ENDPOINTS.poolAll}?${params.toString()}`, { timeoutMs: 60000 }).then((data) => {
        state.poolFilterLoading = false;
        if (!data || !data.items) {
          if (grid) grid.innerHTML = `<div class="empty-state">加载失败，请稍后重试。</div>`;
          return;
        }
        state.poolFilterItems = data.items;
        // 更新统计卡片
        const rawEl = $("#poolFilterRaw");
        if (rawEl) rawEl.textContent = String(data.raw) + " (总" + data.total + ")";
        const availEl = $("#poolFilterAvailable");
        if (availEl) availEl.textContent = String(data.available);
        const pendEl = $("#poolFilterPending");
        if (pendEl) pendEl.textContent = String(data.pending);
        const totalEl = $("#poolFilterTotal");
        if (totalEl) totalEl.textContent = String(data.total);
        renderPoolFilterItems();
      }).catch(() => {
        state.poolFilterLoading = false;
        if (grid) grid.innerHTML = `<div class="empty-state">请求失败，请检查后端连接。</div>`;
      });
    }

    function renderPoolFilterBar() {
      const bar = $("#poolFilterBar");
      if (!bar) return;
      const labels = configuredSourceFilterLabels();
      bar.replaceChildren(
        ...labels.map((name) => {
          const btn = document.createElement("button");
          btn.className = `chip${state.poolFilterPlatform === name ? " is-active" : ""}`;
          btn.type = "button";
          btn.textContent = name;
          btn.addEventListener("click", () => {
            state.poolFilterPlatform = name;
            renderPoolFilterBar();
            loadPoolFilterItems();
          });
          return btn;
        })
      );
    }

    function renderPoolFilterItems() {
      const grid = $("#poolFilterGrid");
      if (!grid) return;
      const items = state.poolFilterItems;
      if (!items.length) {
        grid.innerHTML = `<div class="empty-state">该平台下池子为空。</div>`;
        return;
      }
      const statusMeta = {
        fresh: { label: "待推荐", color: "#4caf50" },
        shown: { label: "已展示", color: "#2196f3" },
        stale: { label: "已过期", color: "#ff9800" },
        suppressed: { label: "已抑制", color: "#9e9e9e" },
        feedbacked: { label: "已反馈", color: "#e91e63" },
        pending: { label: "处理中", color: "#ff5722" },
      };
      grid.replaceChildren(
        ...items.map((item) => {
          const card = document.createElement("article");
          const meta = statusMeta[item.pool_status] || { label: item.pool_status, color: "#888" };
          card.className = "video-card is-minimal pool-all-card";
          card.dataset.bvid = item.bvid;
          const platform = platformName(item.source_platform);
          const author = item.up_name || "";
          const hasReason = !!item.quality_reason;
          card.innerHTML = `
            <p class="video-card-title">${escapeHtml(item.title || "无标题")}</p>
            <div class="video-card-meta">
              <span class="video-card-author">${escapeHtml(author)}</span>
              <span class="video-card-source">${escapeHtml(platform)}</span>
              <span class="pool-all-badge" style="background:${meta.color}">${meta.label}</span>
            </div>
            ${hasReason ? `<p class="video-card-reason">${escapeHtml(item.quality_reason)}</p>` : ""}
          `;
          card.addEventListener("click", () => window.openRecommendation(item, card));
          return card;
        })
      );
    }

    // ── 观测面板 ──────────────────────────────────────────────────────────

    function platformLabelHtml(key) {
      const labels = { bilibili: "B 站", xiaohongshu: "小红书", douyin: "抖音", youtube: "YouTube",
        twitter: "X", zhihu: "知乎", v2ex: "V2EX", reddit: "Reddit", wechat: "公众号",
        xiaoyuzhou: "小宇宙", rss: "RSS", user_favorite: "收藏" };
      return labels[key] || key;
    }

    function platformLabelClass(key) {
      const safe = String(key || "").toLowerCase().replace(/[^a-z0-9]/g, "-");
      return "obs-platform-badge obs-pb-" + safe;
    }

    function loadObservabilityData() {
      const body = $("#observabilityBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载观测数据…</div>`;
      requestJson("/observability", { timeoutMs: 60000 }).then((data) => {
        if (!data) {
          body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接。</div>`;
          return;
        }
        renderObservability(data, body);
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接。</div>`;
      });
    }

    function renderObservability(data, container) {
      const p = data.pipeline || {};
      const platforms = data.platforms || [];
      const scoreDist = data.score_distribution || [];
      const topicGroups = data.topic_groups || [];
      const llmUsage = data.llm_usage || {};
      const discCandidates = data.discovery_candidates || [];
      const runtime = data.runtime || {};
      const keywords = data.keywords || [];
      const evalStats = data.eval_stats || {};
      const eventStats = data.event_stats || {};
      const feedbackStats = data.feedback_stats || {};
      const exprCoverage = data.expression_coverage || {};
      const delightStats = data.delight_stats || {};
      const soulProfile = data.soul_profile || {};
      const schedulerLoops = data.scheduler_loops || [];
      const authSources = data.auth_sources || [];
      const styleDist = data.style_distribution || [];
      const satDist = data.satisfaction_distribution || [];
      const suppressedBreakdown = data.suppressed_breakdown || [];

      const tabs = [
        { id: "obs-tab-overview", label: "管道总览", section: "obs-section-overview" },
        { id: "obs-tab-discovery", label: "发现管道", section: "obs-section-discovery" },
        { id: "obs-tab-quality", label: "内容质量", section: "obs-section-quality" },
        { id: "obs-tab-behavior", label: "用户行为", section: "obs-section-behavior" },
        { id: "obs-tab-llm", label: "LLM 调用", section: "obs-section-llm" },
        { id: "obs-tab-health", label: "运行时健康", section: "obs-section-health" },
      ];

      container.innerHTML = `
        <div class="obs-tab-bar">
          ${tabs.map(t => `<button class="obs-tab is-active" data-obs-tab="${t.id}" data-obs-section="${t.section}">${t.label}</button>`).join("")}
        </div>
        <div class="obs-tab-content" id="obs-section-overview">${renderOverviewSection(p, platforms, runtime, suppressedBreakdown)}</div>
        <div class="obs-tab-content" id="obs-section-discovery" hidden>${renderDiscoverySection(discCandidates, keywords, evalStats, p)}</div>
        <div class="obs-tab-content" id="obs-section-quality" hidden>${renderQualitySection(scoreDist, topicGroups, exprCoverage, styleDist, p)}</div>
        <div class="obs-tab-content" id="obs-section-behavior" hidden>${renderBehaviorSection(eventStats, feedbackStats, satDist)}</div>
        <div class="obs-tab-content" id="obs-section-llm" hidden>${renderLLMSection(llmUsage)}</div>
        <div class="obs-tab-content" id="obs-section-health" hidden>${renderHealthSection(runtime, schedulerLoops, authSources, delightStats, soulProfile)}</div>
      `;

      container.querySelectorAll(".obs-tab").forEach(btn => {
        btn.addEventListener("click", () => {
          container.querySelectorAll(".obs-tab").forEach(b => b.classList.remove("is-active"));
          container.querySelectorAll(".obs-tab-content").forEach(s => s.hidden = true);
          btn.classList.add("is-active");
          const sec = document.getElementById(btn.dataset.obsSection);
          if (sec) sec.hidden = false;
        });
      });
    }

    function renderOverviewSection(p, platforms, runtime, suppressedBreakdown) {
      const suppressedTotal = suppressedBreakdown.reduce((s, r) => s + (r.total || 0), 0);
      const suppressedQualified = suppressedBreakdown.reduce((s, r) => s + (r.qualified || 0), 0);
      return `
        <div class="obs-section">
          <h3 class="obs-section-title">数据管道总览</h3>
          <div class="obs-stat-grid">
            <div class="obs-stat-card"><span class="obs-stat-val">${p.total_items}</span><span class="obs-stat-label">内容总量</span></div>
            <div class="obs-stat-card accent"><span class="obs-stat-val">${p.fresh}</span><span class="obs-stat-label">待推荐</span></div>
            <div class="obs-stat-card info"><span class="obs-stat-val">${p.shown}</span><span class="obs-stat-label">已展示</span></div>
            <div class="obs-stat-card warn"><span class="obs-stat-val">${p.stale}</span><span class="obs-stat-label">已过期</span></div>
            <div class="obs-stat-card muted"><span class="obs-stat-val">${p.suppressed}</span><span class="obs-stat-label">已抑制</span></div>
            <div class="obs-stat-card accent"><span class="obs-stat-val">${p.items_with_quality_score}</span><span class="obs-stat-label">已评分</span></div>
            <div class="obs-stat-card muted"><span class="obs-stat-val">${p.items_without_quality_score}</span><span class="obs-stat-label">未评分</span></div>
            <div class="obs-stat-card info"><span class="obs-stat-val">${p.avg_quality_score}</span><span class="obs-stat-label">平均分</span></div>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">各平台内容分布</h3>
          <div class="obs-platform-table-wrap">
            <table class="obs-platform-table">
              <thead><tr><th>平台</th><th>总量</th><th>待推荐</th><th>已展示</th><th>已过期</th><th>已抑制</th><th>已反馈</th></tr></thead>
              <tbody>${platforms.map(plat => {
                const pct = p.total_items > 0 ? ((plat.total / p.total_items) * 100).toFixed(1) : "0";
                return `<tr><td><span class="${platformLabelClass(plat.platform)}">${escapeHtml(platformLabelHtml(plat.platform))}</span></td>
                  <td class="obs-num">${plat.total} <span class="obs-pct">${pct}%</span></td>
                  <td class="obs-num">${plat.fresh}</td><td class="obs-num">${plat.shown}</td>
                  <td class="obs-num">${plat.stale}</td><td class="obs-num">${plat.suppressed}</td>
                  <td class="obs-num">${plat.feedbacked}</td></tr>`;
              }).join("")}</tbody>
            </table>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">配额管理中（被抑制）内容分析</h3>
          ${suppressedBreakdown.length ? `
          <div class="obs-meta" style="margin-bottom:10px">
            <span>共 <b>${suppressedTotal}</b> 条在配额管理中，其中 <b class="accent">${suppressedQualified}</b> 条资格齐全（过准入线+要素完整+可跳转），其余因相关性不足或未评估暂不适合推荐</span>
          </div>
          <div class="obs-platform-table-wrap">
            <table class="obs-platform-table">
              <thead><tr><th>平台</th><th>总量</th><th>资格齐全</th><th>相关性不足(&lt;0.60)</th><th>未评估</th></tr></thead>
              <tbody>${suppressedBreakdown.map(r => `
                <tr><td><span class="${platformLabelClass(r.platform)}">${escapeHtml(platformLabelHtml(r.platform))}</span></td>
                  <td class="obs-num">${r.total ?? 0}</td>
                  <td class="obs-num ${r.qualified > 0 ? "accent" : ""}">${r.qualified ?? 0}</td>
                  <td class="obs-num">${r.below_threshold ?? 0}</td>
                  <td class="obs-num">${r.unevaluated ?? 0}</td></tr>`).join("")}
              </tbody>
            </table>
          </div>` : `<div class="empty-state">当前没有配额管理中的内容</div>`}
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">运行时快照</h3>
          <div class="obs-stat-grid obs-small-grid">
            <div class="obs-stat-card"><span class="obs-stat-val">${runtime.recommendation_count || "—"}</span><span class="obs-stat-label">推荐次数</span></div>
            <div class="obs-stat-card accent"><span class="obs-stat-val">${runtime.pool_available_count || "—"}</span><span class="obs-stat-label">可用池</span></div>
            <div class="obs-stat-card info"><span class="obs-stat-val">${runtime.pool_raw_count || "—"}</span><span class="obs-stat-label">原始池</span></div>
            <div class="obs-stat-card"><span class="obs-stat-val">${runtime.pool_target_count || "—"}</span><span class="obs-stat-label">目标池大小</span></div>
            <div class="obs-stat-card warn"><span class="obs-stat-val">${runtime.pending_signal_events || "—"}</span><span class="obs-stat-label">待处理事件</span></div>
            <div class="obs-stat-card"><span class="obs-stat-val">${runtime.last_discovered_count || "—"}</span><span class="obs-stat-label">上次发现</span></div>
            <div class="obs-stat-card"><span class="obs-stat-val">${runtime.last_replenished_count || "—"}</span><span class="obs-stat-label">上次补货</span></div>
            <div class="obs-stat-card" style="grid-column:span 2"><span class="obs-stat-val" style="font-size:14px">${runtime.last_refresh_at || "—"}</span><span class="obs-stat-label">最后刷新</span></div>
          </div>
          ${(runtime.recent_pool_topics || []).length ? `<div class="obs-meta"><span>近期主题: ${runtime.recent_pool_topics.map(t => escapeHtml(String(t))).join("、")}</span></div>` : ""}
        </div>
      `;
    }

    function renderDiscoverySection(discCandidates, keywords, evalStats, p) {
      const maxDisc = Math.max(...discCandidates.map(x => x.count), 1);
      return `
        <div class="obs-row">
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">发现候选状态</h3>
            <div class="obs-bar-chart">${discCandidates.map(d => {
              const pct = (d.count / maxDisc * 100).toFixed(0);
              return `<div class="obs-bar-row"><span class="obs-bar-label">${escapeHtml(d.status)}</span>
                <div class="obs-bar-track"><div class="obs-bar-fill" style="width:${pct}%"></div></div>
                <span class="obs-bar-val">${d.count}</span></div>`;
            }).join("")}</div>
          </div>
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">评估统计</h3>
            <div class="obs-stat-grid obs-small-grid">
              <div class="obs-stat-card"><span class="obs-stat-val">${evalStats.total_candidates || 0}</span><span class="obs-stat-label">候选总数</span></div>
              <div class="obs-stat-card accent"><span class="obs-stat-val">${evalStats.candidates_accepted || 0}</span><span class="obs-stat-label">已准入</span></div>
              <div class="obs-stat-card info"><span class="obs-stat-val">${evalStats.acceptance_rate || 0}%</span><span class="obs-stat-label">准入率</span></div>
              <div class="obs-stat-card warn"><span class="obs-stat-val">${evalStats.total_eval_attempts || 0}</span><span class="obs-stat-label">总评估次数</span></div>
            </div>
            <div class="obs-meta">
              <span>待评估: ${p.discovery_candidates_pending}</span>
              <span>已评估待入库: ${p.discovery_candidates_evaluated}</span>
            </div>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">关键词状态（按平台）</h3>
          <div class="obs-platform-table-wrap">
            <table class="obs-platform-table">
              <thead><tr><th>平台</th><th>状态</th><th>数量</th></tr></thead>
              <tbody>${keywords.map(k => `<tr>
                <td><span class="${platformLabelClass(k.platform)}">${escapeHtml(platformLabelHtml(k.platform))}</span></td>
                <td><span class="pex-badge pex-badge-status">${escapeHtml(k.status)}</span></td>
                <td class="obs-num">${k.count}</td>
              </tr>`).join("")}</tbody>
            </table>
          </div>
          <div class="obs-meta">关键词生命周期: pending → claimed → executing → used/failed/expired</div>
        </div>
      `;
    }

    function renderQualitySection(scoreDist, topicGroups, exprCoverage, styleDist, p) {
      const maxScore = Math.max(...scoreDist.map(x => x.count), 1);
      const maxTopic = Math.max(...topicGroups.map(x => x.count), 1);
      const maxStyle = Math.max(...styleDist.map(x => x.count), 1);
      const withExpr = exprCoverage.with_expression || 0;
      const withoutExpr = exprCoverage.without_expression || 0;
      const exprTotal = withExpr + withoutExpr;
      const exprPct = exprTotal > 0 ? (withExpr / exprTotal * 100).toFixed(1) : 0;
      return `
        <div class="obs-section">
          <h3 class="obs-section-title">数据完整度</h3>
          <div class="obs-stat-grid obs-small-grid">
            <div class="obs-stat-card info"><span class="obs-stat-val">${withExpr} <span style="font-size:14px">(${exprPct}%)</span></span><span class="obs-stat-label">有推荐理由</span></div>
            <div class="obs-stat-card muted"><span class="obs-stat-val">${withoutExpr}</span><span class="obs-stat-label">无推荐理由</span></div>
            <div class="obs-stat-card accent"><span class="obs-stat-val">${exprCoverage.with_topic_group || 0}</span><span class="obs-stat-label">有主题标签</span></div>
            <div class="obs-stat-card accent"><span class="obs-stat-val">${exprCoverage.with_quality_score || 0}</span><span class="obs-stat-label">有质量评分</span></div>
          </div>
        </div>
        <div class="obs-row">
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">大模型评分分布</h3>
            <div class="obs-bar-chart">${scoreDist.map(s => {
              const pct = (s.count / maxScore * 100).toFixed(0);
              return `<div class="obs-bar-row"><span class="obs-bar-label">${escapeHtml(s.bucket)}</span>
                <div class="obs-bar-track"><div class="obs-bar-fill" style="width:${pct}%"></div></div>
                <span class="obs-bar-val">${s.count}</span></div>`;
            }).join("")}</div>
          </div>
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">主题标签分布</h3>
            <div class="obs-topic-list">${topicGroups.map(t => {
              const pct = (t.count / maxTopic * 100).toFixed(0);
              return `<div class="obs-topic-row"><span class="obs-topic-label">${escapeHtml(t.topic)}</span>
                <div class="obs-bar-track"><div class="obs-bar-fill obs-bar-topic" style="width:${pct}%"></div></div>
                <span class="obs-bar-val">${t.count}</span></div>`;
            }).join("")}</div>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">内容风格分布</h3>
          <div class="obs-bar-chart">${styleDist.map(s => {
            const pct = (s.count / maxStyle * 100).toFixed(0);
            return `<div class="obs-bar-row"><span class="obs-bar-label">${escapeHtml(s.style)}</span>
              <div class="obs-bar-track"><div class="obs-bar-fill obs-bar-topic" style="width:${pct}%"></div></div>
              <span class="obs-bar-val">${s.count}</span></div>`;
          }).join("")}</div>
        </div>
      `;
    }

    function renderBehaviorSection(eventStats, feedbackStats, satDist) {
      const eventTypes = eventStats.by_type || {};
      const eventPlatforms = eventStats.by_platform || {};
      const fbTypes = feedbackStats.by_type || {};
      const maxEvent = Math.max(...Object.values(eventTypes), 1);
      const maxSat = Math.max(...satDist.map(x => x.count), 1);
      return `
        <div class="obs-row">
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">行为事件类型</h3>
            <div class="obs-bar-chart">${Object.entries(eventTypes).map(([k, v]) => {
              const pct = (v / maxEvent * 100).toFixed(0);
              return `<div class="obs-bar-row"><span class="obs-bar-label">${escapeHtml(k)}</span>
                <div class="obs-bar-track"><div class="obs-bar-fill obs-bar-topic" style="width:${pct}%"></div></div>
                <span class="obs-bar-val">${v}</span></div>`;
            }).join("")}</div>
            <div class="obs-meta"><span>共 ${eventStats.total_events || 0} 条事件</span></div>
          </div>
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">事件来源平台</h3>
            <div class="obs-bar-chart">${Object.entries(eventPlatforms).map(([k, v]) => {
              const max = Math.max(...Object.values(eventPlatforms), 1);
              const pct = (v / max * 100).toFixed(0);
              return `<div class="obs-bar-row"><span class="obs-bar-label">${escapeHtml(platformLabelHtml(k))}</span>
                <div class="obs-bar-track"><div class="obs-bar-fill" style="width:${pct}%"></div></div>
                <span class="obs-bar-val">${v}</span></div>`;
            }).join("")}</div>
          </div>
        </div>
        <div class="obs-row">
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">用户满意度分布</h3>
            <div class="obs-bar-chart">${satDist.map(s => {
              const pct = (s.count / maxSat * 100).toFixed(0);
              return `<div class="obs-bar-row"><span class="obs-bar-label">${escapeHtml(s.satisfaction)}</span>
                <div class="obs-bar-track"><div class="obs-bar-fill" style="width:${pct}%"></div></div>
                <span class="obs-bar-val">${s.count}</span></div>`;
            }).join("")}</div>
          </div>
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">反馈统计</h3>
            <div class="obs-stat-grid obs-small-grid" style="margin-bottom:8px">
              <div class="obs-stat-card"><span class="obs-stat-val">${feedbackStats.total_feedback || 0}</span><span class="obs-stat-label">总反馈</span></div>
            </div>
            <div class="obs-bar-chart">${Object.entries(fbTypes).map(([k, v]) => {
              const max = Math.max(...Object.values(fbTypes), 1);
              const pct = (v / max * 100).toFixed(0);
              return `<div class="obs-bar-row"><span class="obs-bar-label">${escapeHtml(k)}</span>
                <div class="obs-bar-track"><div class="obs-bar-fill obs-bar-topic" style="width:${pct}%"></div></div>
                <span class="obs-bar-val">${v}</span></div>`;
            }).join("")}</div>
          </div>
        </div>
      `;
    }

    function renderLLMSection(llmUsage) {
      const callers = llmUsage.by_caller || [];
      return `
        <div class="obs-section">
          <h3 class="obs-section-title">LLM 调用统计（近7天）</h3>
          <div class="obs-stat-grid obs-small-grid">
            <div class="obs-stat-card accent"><span class="obs-stat-val">${llmUsage.today_calls ?? 0}</span><span class="obs-stat-label">今日调用</span></div>
            <div class="obs-stat-card warn"><span class="obs-stat-val">¥${llmUsage.today_cost_cny ?? 0}</span><span class="obs-stat-label">今日费用</span></div>
            <div class="obs-stat-card"><span class="obs-stat-val">${llmUsage.total_calls_7d ?? 0}</span><span class="obs-stat-label">7天调用</span></div>
            <div class="obs-stat-card warn"><span class="obs-stat-val">¥${llmUsage.total_cost_7d ?? 0}</span><span class="obs-stat-label">7天费用</span></div>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">调用方详情</h3>
          ${callers.length ? `<div class="obs-caller-table-wrap"><table class="obs-platform-table">
            <thead><tr><th>调用方</th><th>调用次数</th><th>费用</th><th>输入Token</th><th>输出Token</th></tr></thead>
            <tbody>${callers.map(c => `<tr>
              <td><code class="obs-caller">${escapeHtml(String(c.caller || "unknown"))}</code></td>
              <td class="obs-num">${c.calls}</td>
              <td class="obs-num">¥${c.cost_cny.toFixed ? c.cost_cny.toFixed(4) : c.cost_cny}</td>
              <td class="obs-num">${c.prompt_tokens}</td>
              <td class="obs-num">${c.completion_tokens}</td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="empty-state">暂无数据</div>`}
        </div>
      `;
    }

    function renderHealthSection(runtime, schedulerLoops, authSources, delightStats, soulProfile) {
      return `
        <div class="obs-row">
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">惊喜推荐 (Delight)</h3>
            <div class="obs-stat-grid obs-small-grid">
              <div class="obs-stat-card accent"><span class="obs-stat-val">${delightStats.delight_candidates || 0}</span><span class="obs-stat-label">候选数</span></div>
              <div class="obs-stat-card info"><span class="obs-stat-val">${delightStats.delight_notified || 0}</span><span class="obs-stat-label">已推送</span></div>
              <div class="obs-stat-card warn"><span class="obs-stat-val">${delightStats.pending_delight || 0}</span><span class="obs-stat-label">待推送</span></div>
              <div class="obs-stat-card"><span class="obs-stat-val" style="font-size:14px">${delightStats.last_delight_notification || "—"}</span><span class="obs-stat-label">上次推送</span></div>
            </div>
          </div>
          <div class="obs-section obs-half">
            <h3 class="obs-section-title">灵魂画像 (Soul)</h3>
            <div class="obs-stat-grid obs-small-grid">
              <div class="obs-stat-card info"><span class="obs-stat-val">${soulProfile.interest_tags_count || "—"}</span><span class="obs-stat-label">兴趣标签</span></div>
              <div class="obs-stat-card"><span class="obs-stat-val">${soulProfile.awareness_notes_count || "—"}</span><span class="obs-stat-label">感知笔记</span></div>
              <div class="obs-stat-card accent"><span class="obs-stat-val">${soulProfile.insight_hypotheses_count || "—"}</span><span class="obs-stat-label">洞察假设</span></div>
              <div class="obs-stat-card" style="grid-column:span 2"><span class="obs-stat-val" style="font-size:14px">${soulProfile.personality_traits || "—"}</span><span class="obs-stat-label">人格特征</span></div>
            </div>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">平台认证状态</h3>
          <div class="obs-platform-table-wrap">
            <table class="obs-platform-table">
              <thead><tr><th>平台</th><th>状态</th><th>Cookie 时长</th><th>最后验证</th><th>错误</th></tr></thead>
              <tbody>${authSources.length ? authSources.map(a => `<tr>
                <td><span class="${platformLabelClass(a.platform)}">${escapeHtml(a.label)}</span></td>
                <td><span class="pex-badge pex-badge-status">${escapeHtml(a.status)}</span></td>
                <td class="obs-num">${a.cookie_age_hours || "—"}h</td>
                <td style="font-size:12px">${a.last_ok_at || "—"}</td>
                <td style="font-size:12px;color:var(--text-secondary)">${escapeHtml(a.error || "")}</td>
              </tr>`).join("") : `<tr><td colspan="5" style="text-align:center;color:var(--text-secondary)">暂无认证信息</td></tr>`}</tbody>
            </table>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">调度循环状态</h3>
          ${schedulerLoops.length ? `<div class="obs-platform-table-wrap"><table class="obs-platform-table">
            <thead><tr><th>循环名称</th><th>间隔</th><th>上次运行</th><th>状态</th></tr></thead>
            <tbody>${schedulerLoops.map(l => `<tr>
              <td><code class="obs-caller">${escapeHtml(String(l.label || l.name || "unknown"))}</code></td>
              <td class="obs-num">${l.interval_seconds || "—"}s</td>
              <td style="font-size:12px">${l.last_tick_at || "—"}</td>
              <td><span class="pex-badge pex-badge-status">${escapeHtml(l.status || "unknown")}</span></td>
            </tr>`).join("")}</tbody></table></div>` : `<div class="empty-state">调度循环数据不可用（需要 runtime_controller.get_loop_health()）</div>`}
        </div>
      `;
    }

    // ── Subscription management ──────────────────────────────────
    let _activeSubTab = "rss";

    function setActiveSubTab(tabName) {
      _activeSubTab = tabName;
      document.querySelectorAll("[data-sub-tab]").forEach((tab) => {
        const isActive = tab.dataset.subTab === tabName;
        tab.classList.toggle("is-active", isActive);
      });
      renderSubscriptionList();
    }

    async function loadSubscriptionList() {
      const container = document.getElementById("subscriptionList");
      const loading = document.getElementById("subscriptionLoading");
      if (!container) return;
      if (loading) loading.textContent = "加载中...";
      try {
        const data = await requestJson(ENDPOINTS.subscriptions + "/stats");
        window._subscriptionData = data;
        renderSubscriptionList();
        if (loading) loading.textContent = "";
      } catch (e) {
        // Fallback to basic list
        try {
          const data = await requestJson(ENDPOINTS.subscriptions);
          window._subscriptionData = data;
          renderSubscriptionList();
        } catch (e2) {
          if (loading) loading.textContent = "加载失败: " + e2.message;
        }
      }
    }

    function formatLastFetchTime(t) {
      if (!t) return "";
      try {
        const d = new Date(t);
        if (isNaN(d.getTime())) return t;
        const now = new Date();
        const diffMs = now - d;
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return "刚刚";
        if (diffMin < 60) return `${diffMin} 分钟前`;
        const diffHour = Math.floor(diffMin / 60);
        if (diffHour < 24) return `${diffHour} 小时前`;
        const diffDay = Math.floor(diffHour / 24);
        if (diffDay < 7) return `${diffDay} 天前`;
        return d.toLocaleDateString("zh-CN");
      } catch {
        return t;
      }
    }

    function renderSubscriptionList() {
      const container = document.getElementById("subscriptionList");
      if (!container) return;
      const data = window._subscriptionData || { rss: [], xiaoyuzhou: [], wechat: [] };
      const items = data[_activeSubTab] || [];
      if (!items.length) {
        container.innerHTML = '<p class="settings-note-inline" style="color:var(--text-tertiary);">暂无订阅源，请在上方添加</p>';
        return;
      }
      container.innerHTML = items.map((item, idx) => {
        const lastFetched = formatLastFetchTime(item.last_fetched_at);
        const count = item.item_count != null ? item.item_count : "-";
        return `
        <div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border-subtle);">
          <div style="flex:1;min-width:0;">
            <div style="font-weight:500;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(item.name || "")}</div>
            <div style="font-size:12px;color:var(--text-tertiary, #888);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(item.url || "")}</div>
          </div>
          <div style="text-align:right;flex-shrink:0;font-size:12px;color:var(--text-tertiary, #888);line-height:1.4;">
            <div>${count} 条内容</div>
            <div>${lastFetched ? "上次: " + lastFetched : "暂无抓取"}</div>
          </div>
          <button class="pill-btn" style="flex-shrink:0;color:var(--danger, #e74c3c);" data-sub-del="${_activeSubTab}" data-sub-url="${escapeHtml(item.url || "")}">删除</button>
        </div>
      `}).join("");

      // Attach delete handlers
      container.querySelectorAll("[data-sub-del]").forEach((btn) => {
        btn.addEventListener("click", () => deleteSubscription(btn.dataset.subDel, btn.dataset.subUrl));
      });
    }

    async function deleteSubscription(sourceType, url) {
      if (!confirm(`确定删除此订阅源？\n${url}`)) return;
      try {
        const res = await fetch(ENDPOINTS.subscriptions, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_type: sourceType, url }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || "HTTP " + res.status);
        }
        setSubStatus("已删除", "success");
        await loadSubscriptionList();
      } catch (e) {
        setSubStatus("删除失败: " + e.message, "error");
      }
    }

    function setSubStatus(msg, type = "info") {
      const el = document.getElementById("subStatus");
      if (!el) return;
      el.textContent = msg;
      el.style.color = type === "error" ? "var(--danger, #e74c3c)" : type === "success" ? "var(--success, #27ae60)" : "var(--text-tertiary, #888)";
    }

    function escapeHtml(str) {
      const div = document.createElement("div");
      div.textContent = str;
      return div.innerHTML;
    }

    // Subscription tab switching
    document.querySelectorAll("[data-sub-tab]").forEach((tab) => {
      tab.addEventListener("click", () => setActiveSubTab(tab.dataset.subTab));
    });

    // Add subscription button
    document.getElementById("addSubBtn")?.addEventListener("click", async () => {
      const name = document.getElementById("subName")?.value?.trim();
      const url = document.getElementById("subUrl")?.value?.trim();
      if (!name || !url) {
        setSubStatus("请填写名称和 URL", "error");
        return;
      }
      const sourceType = _activeSubTab;
      try {
        const res = await fetch(ENDPOINTS.subscriptions, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_type: sourceType, name, url }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || "HTTP " + res.status);
        }
        document.getElementById("subName").value = "";
        document.getElementById("subUrl").value = "";
        setSubStatus("已添加", "success");
        await loadSubscriptionList();
      } catch (e) {
        setSubStatus("添加失败: " + e.message, "error");
      }
    });

    function setActiveSettingsPanel(panelName = "models") {
      document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
        const isActive = tab.dataset.settingsTab === panelName;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.settingsPanel !== panelName;
      });
    }

    document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      tab.addEventListener("click", () => setActiveSettingsPanel(tab.dataset.settingsTab));
    });

    function setActiveModelSettingsPanel(groupName = "llm", panelName = "default") {
      document.querySelectorAll(`[data-model-settings-tab][data-model-settings-group="${groupName}"]`).forEach((tab) => {
        const isActive = tab.dataset.modelSettingsTab === panelName;
        tab.classList.toggle("is-active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      document.querySelectorAll(`[data-model-settings-panel][data-model-settings-group="${groupName}"]`).forEach((panel) => {
        panel.hidden = panel.dataset.modelSettingsPanel !== panelName;
      });
    }

    document.querySelectorAll("[data-model-settings-tab]").forEach((tab) => {
      tab.addEventListener("click", () => setActiveModelSettingsPanel(tab.dataset.modelSettingsGroup, tab.dataset.modelSettingsTab));
    });

    function startChatPlaceholderRotation() {
      const input = $("#chatInput");
      if (!input || chatPlaceholderTimer) return;
      chatPlaceholderTimer = window.setInterval(() => {
        if (document.activeElement === input || input.value.trim()) return;
        chatPlaceholderIndex = (chatPlaceholderIndex + 1) % CHAT_PLACEHOLDERS.length;
        input.setAttribute("placeholder", CHAT_PLACEHOLDERS[chatPlaceholderIndex]);
      }, 5000);
    }

    safeBind("#sideDrawerBtn", "click", toggleSideDrawer);
    safeBind(".brand", "click", (event) => { event.preventDefault(); openHomePage(); });
    safeBind("#sideDrawerScrim", "click", closeSideDrawer);
    safeBind("#mobileMenuBtn", "click", openMobileMenu);
    safeBind("#mobileMenuClose", "click", closeMobileMenu);
    safeBind("#mobileSearchInput", "input", (event) => { state.query = event.target.value || ""; const desktopInput = $("#searchInput"); if (desktopInput) desktopInput.value = state.query; window.renderAll(); });
    safeBind("#mobileSearchForm", "submit", (event) => { event.preventDefault(); state.query = $("#mobileSearchInput")?.value || ""; const desktopInput = $("#searchInput"); if (desktopInput) desktopInput.value = state.query; window.renderAll(); closeMobileMenu(); });
    document.querySelectorAll("[data-mobile-panel]").forEach((button) => {
      button.addEventListener("click", () => openMobilePanel(button.dataset.mobilePanel, { settingsPanel: button.dataset.settings }));
    });
    document.querySelectorAll("[data-mobile-page]").forEach((button) => {
      button.addEventListener("click", () => {
        openMobilePage(button.dataset.mobilePage, { settingsPanel: button.dataset.settings });
      });
    });
    document.querySelectorAll("[data-mobile-back]").forEach((button) => {
      button.addEventListener("click", returnToMobileMenu);
    });

    safeBind("#profileBtn", "click", () => { closeMineDropdown(); navigateTo("/web/profile"); });
    safeBind("#diaryBtn", "click", () => navigateTo("/web/diary"));
    safeBind("#topicsBtn", "click", () => { window.navigateTo("/web/topics"); });
    safeBind("#healthBtn", "click", () => { window.navigateTo("/web/health"); });
    safeBind("#travelBtn", "click", () => { window.navigateTo("/web/travel"); });
    safeBind("#cloneBtn", "click", () => navigateTo("/web/clone"));
    safeBind("#homeBtn", "click", () => navigateTo("/web"));
    safeBind("#customFilterBtn", "click", () => { closeFilterDropdown(); navigateTo("/web/custom-filter"); });
    safeBind("#poolFilterBtn", "click", () => { closeFilterDropdown(); navigateTo("/web/pool-filter"); });
    // 筛选下拉菜单：合并自定义筛选 + 池子筛选
    safeBind("#filterDropdownTrigger", "click", (e) => {
      e.stopPropagation();
      closePoolDropdown();
      closeFeedDropdown();
      closeMineDropdown();
      toggleFilterDropdown();
    });
    document.addEventListener("click", (e) => {
      const dropdown = document.getElementById("filterDropdown");
      if (dropdown && !dropdown.contains(e.target)) closeFilterDropdown();
    });
    // 推荐流下拉菜单：合并6个平台推荐流
    safeBind("#feedDropdownTrigger", "click", (e) => {
      e.stopPropagation();
      closeFilterDropdown();
      closePoolDropdown();
      closeMineDropdown();
      toggleFeedDropdown();
    });
    document.addEventListener("click", (e) => {
      const dropdown = document.getElementById("feedDropdown");
      if (dropdown && !dropdown.contains(e.target)) closeFeedDropdown();
    });
    // 池子下拉菜单：合并池子总览 + 池子探索
    safeBind("#poolDropdownTrigger", "click", (e) => {
      e.stopPropagation();
      togglePoolDropdown();
    });
    document.addEventListener("click", (e) => {
      const dropdown = document.getElementById("poolDropdown");
      if (dropdown && !dropdown.contains(e.target)) closePoolDropdown();
    });
    // 我的下拉菜单：合并稍后再看 + 我的收藏 + 我的画像 + 聊聊口味
    safeBind("#mineDropdownTrigger", "click", (e) => {
      e.stopPropagation();
      toggleMineDropdown();
    });
    document.addEventListener("click", (e) => {
      const dropdown = document.getElementById("mineDropdown");
      if (dropdown && !dropdown.contains(e.target)) closeMineDropdown();
    });
    safeBind("#watchLaterBtn", "click", () => navigateTo("/web/watchLater"));
    safeBind("#favoritesBtn", "click", () => navigateTo("/web/saved"));
    safeBind("#profileMemoryMoreBtn", "click", () => window.loadMoreProfileMemory?.());
    safeBind("#chatBtn", "click", () => { closeMineDropdown(); navigateTo("/web/chat"); });
    safeBind("#libraryBtn", "click", () => navigateTo("/web/library"));
    safeBind("#readArchiveBtn", "click", () => navigateTo("/web/read-archive"));
    safeBind("#libraryPage", "click", (event) => {
      const filterBtn = event.target.closest(".library-filter-btn");
      if (filterBtn) {
        document.querySelectorAll(".library-filter-btn").forEach((b) => b.classList.remove("is-active"));
        filterBtn.classList.add("is-active");
        _librarySourceFilter = filterBtn.dataset.filter;
        _libraryTagFilter = null;
        document.querySelectorAll(".library-status-btn").forEach((b) => b.classList.remove("is-active"));
        document.querySelector('[data-status="all"]')?.classList.add("is-active");
        _libraryStatusFilter = "all";
        _libraryLimit = LIBRARY_PAGE_SIZE;
        void loadLibraryItems();
        return;
      }
      const statusBtn = event.target.closest(".library-status-btn");
      if (statusBtn) {
        document.querySelectorAll(".library-status-btn").forEach((b) => b.classList.remove("is-active"));
        statusBtn.classList.add("is-active");
        _libraryStatusFilter = statusBtn.dataset.status;
        _libraryTagFilter = null;
        _libraryLimit = LIBRARY_PAGE_SIZE;
        void loadLibraryItems();
        return;
      }
      const tagBtn = event.target.closest(".library-tag");
      if (tagBtn) {
        document.querySelectorAll(".library-tag").forEach((b) => b.classList.remove("is-active"));
        tagBtn.classList.add("is-active");
        _libraryTagFilter = tagBtn.dataset.tag || null;
        _libraryLimit = LIBRARY_PAGE_SIZE;
        void loadLibraryItems();
      }
    });
    safeBind("#libraryMoreBtn", "click", () => {
      _libraryLimit += LIBRARY_PAGE_SIZE;
      void loadLibraryItems();
    });
    safeBind("#articleDrawer", "click", (event) => {
      const statusBtn = event.target.closest("[data-article-status]");
      if (statusBtn) {
        void setArticleStatus(statusBtn.dataset.articleStatus);
        return;
      }
    });
    safeBind("#articleOpenOrigin", "click", () => {
      const url = document.getElementById("articleOpenOrigin")?.dataset.url;
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    });
    safeBind("#messagesBtn", "click", () => {
      closeSideDrawer();
      window.hydrateInboxFromSpeculations(state.profile?.speculative_interests);
      window.hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
      state.messageListSnapshot = window.getRenderableMessages();
      openPanel("messagesDrawer");
      returnToMessages();
      window.renderMessages();
      void window.refreshProfile().catch(() => {});
    });
    safeBind("#activityBtn", "click", () => { closeSideDrawer(); window.renderActivityHistory(); openPanel("activityDrawer"); });
    safeBind("#activityMoreBtn", "click", () => window.loadActivityPage());
    safeBind("#settingsBtn", "click", () => openSettingsPage("models"));
    safeBind("#openSettingsHero", "click", () => openSettingsPage("models"));
    function _cloneBtnLoading(btn, loading) {
      if (!btn) return;
      btn.disabled = loading;
      btn.textContent = loading ? "处理中…" : btn.dataset.label || btn.textContent;
    }
    safeBind("#cloneImportBtn", "click", async () => {
      const btn = document.getElementById("cloneImportBtn");
      if (btn?.disabled) return;
      btn.dataset.label = "扫描导入";
      _cloneBtnLoading(btn, true);
      try {
        const res = await fetch("/api/clone/import", { method: "POST" });
        const data = await res.json();
        if (data.ok) {
          await loadCloneSites();
        } else {
          alert("导入失败: " + (data.error || "未知错误"));
        }
      } catch (e) {
        alert("导入失败: " + e.message);
      } finally {
        _cloneBtnLoading(btn, false);
      }
    });
    bindStarButton();
    syncTopbarHeight();
    window.addEventListener("resize", syncTopbarHeight);
    document.getElementById("homeBtn")?.classList.add("is-active");
    ["#dismissOnReshuffleToggle", "#dismissOnReshuffleSetting"].forEach((selector) => {
      safeBind(selector, "change", (event) => {
        setDismissOnReshuffle(Boolean(event.target.checked), { toast: true });
      });
    });
    safeBind("#reshuffleBtn", "click", () => window.reshuffle?.());
    safeBind("#loadMoreBtn", "click", () => window.reshuffle?.());
    safeBind("#customLoadMoreBtn", "click", () => window.reshuffle?.());
    safeBind("#customApplyBtn", "click", () => window.reshuffle?.());
    safeBind("#customResetBtn", "click", () => {
      resetCustomFilters();
      showToast("已重置全部筛选条件");
    });
    safeBind("#poolAllBtn", "click", () => { closePoolDropdown(); navigateTo("/web/pool-all"); });
    safeBind("#poolFilterBtn", "click", () => navigateTo("/web/pool-filter"));
    safeBind("#delightRefreshBtn", "click", () => window.shuffleDelights());
    function setCoverVisible(show) {
      document.body.classList.toggle("no-cover", !show);
      localStorage.setItem("openbiliclaw.hideCover", show ? "0" : "1");
      [["#coverOnBtn", show], ["#coverOffBtn", !show]].forEach(([selector, active]) => {
        const btn = $(selector);
        if (!btn) return;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
      if (!show) showToast("封面已隐藏");
    }
    // 调节按钮已移除：固定显示封面（保留函数仅用于初始化 body 状态）
    setCoverVisible(true);
    const CARD_SIZES = ["md", "lg", "sm"];
    const SIZE_BTN_MAP = { sm: "#sizeSmBtn", md: "#sizeMdBtn", lg: "#sizeLgBtn" };
    function applyCardSize(size) {
      if (!CARD_SIZES.includes(size)) size = "md";
      document.body.classList.remove("card-sm", "card-md", "card-lg");
      document.body.classList.add(`card-${size}`);
      localStorage.setItem("openbiliclaw.cardSize", size);
      CARD_SIZES.forEach((key) => {
        const btn = $(SIZE_BTN_MAP[key]);
        if (!btn) return;
        const active = key === size;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }
    // 卡片尺寸调节已移除：固定中等卡片
    applyCardSize("md");
    const CARD_DISPLAY_MODES = ["card", "mindback", "list"];
    function setDisplayMode(mode) {
      if (!CARD_DISPLAY_MODES.includes(mode)) mode = "card";
      state.displayMode = mode;
      storageSet(DISPLAY_MODE_KEY, mode);
      grid.classList.remove("mindback-mode", "list-mode");
      if (mode === "mindback") grid.classList.add("mindback-mode");
      if (mode === "list") grid.classList.add("list-mode");
      const pairs = [["#modeCardBtn", "card"], ["#modeMindbackBtn", "mindback"], ["#modeListBtn", "list"]];
      pairs.forEach(([selector, key]) => {
        const btn = $(selector);
        if (!btn) return;
        const active = key === mode;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
      renderVideos();
    }
    // 显示模式调节已移除：固定网格卡片模式
    setDisplayMode("card");
    safeBind("#observabilityBtn", "click", () => navigateTo("/web/observability"));
    safeBind("#interviewBtn", "click", () => navigateTo("/web/interview"));
    const scheduleObservabilityRefresh = debounceAsync(() => loadObservabilityData(), 500);
    safeBind("#interviewRefreshBtn", "click", () => { if (window.loadInterviewData) window.loadInterviewData(); });
    safeBind("#poolExploreBtn", "click", () => { closePoolDropdown(); navigateTo("/web/pool-explore"); });
    safeBind("#delightTabBtn", "click", () => navigateTo("/web/delight"));
    safeBind("#resetFiltersBtn", "click", () => { state.query = ""; state.filter = "全部"; const input = $("#searchInput"); if (input) input.value = ""; window.reshuffle?.(); });
    safeBind("#searchInput", "input", (event) => { state.query = event.target.value || ""; window.renderAll(); });
    safeBind("#searchForm", "submit", (event) => { event.preventDefault(); state.query = $("#searchInput")?.value || ""; window.renderAll(); });
    window.addEventListener("resize", scheduleActivityRailHeightSync);
    safeBind("#chatForm", "submit", (event) => {
      event.preventDefault();
      const input = $("#chatInput");
      const text = input?.value?.trim() || "";
      if (!text) return;
      input.value = "";
      if (state.chatMode === "recommend") {
        sendChatRecommend(text);
      } else {
        window.sendChat(text);
      }
    });
    // 聊聊口味：模式切换（普通聊天 / 对话式推荐）
    state.chatMode = "normal";
    state.chatRecommendSessionId = null;
    safeBind("#chatModeNormal", "click", () => setChatMode("normal"));
    safeBind("#chatModeRecommend", "click", () => setChatMode("recommend"));
    function setChatMode(mode) {
      state.chatMode = mode;
      const normalBtn = $("#chatModeNormal");
      const recommendBtn = $("#chatModeRecommend");
      const input = $("#chatInput");
      const results = $("#chatRecommendResults");
      if (normalBtn) normalBtn.classList.toggle("is-active", mode === "normal");
      if (recommendBtn) recommendBtn.classList.toggle("is-active", mode === "recommend");
      if (normalBtn) normalBtn.setAttribute("aria-selected", String(mode === "normal"));
      if (recommendBtn) recommendBtn.setAttribute("aria-selected", String(mode === "recommend"));
      if (input) {
        input.placeholder = mode === "recommend"
          ? '说说你想看什么，比如"给我推荐几个广告算法视频"、"再来几个"、"讲讲第二个"'
          : "说说你最近怎么想——你是什么样的人、喜欢什么、讨厌什么，都可以直接说。";
      }
      if (results) results.hidden = mode !== "recommend";
    }
    async function sendChatRecommend(message) {
      state.chat.push({ role: "user", text: message });
      state.chat.push({ role: "agent", text: "正在为你推荐..." });
      window.renderChat();
      try {
        const result = await requestJson(ENDPOINTS.chatRecommend, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            session_id: state.chatRecommendSessionId,
            limit: 5
          })
        });
        if (result?.reply) {
          state.chatRecommendSessionId = result.session_id;
          state.chat[state.chat.length - 1] = { role: "agent", text: result.reply };
          // 渲染推荐结果卡片
          const resultsEl = $("#chatRecommendResults");
          if (resultsEl && Array.isArray(result.recommendations) && result.recommendations.length > 0) {
            resultsEl.hidden = false;
            let html = '<div class="section-head"><h3>推荐内容</h3></div><div class="card-grid">';
            result.recommendations.forEach((item) => {
              const platform = item.source_platform || item.platform || "";
              const author = item.up_name || item.author || "";
              const url = item.content_url || item.url || "#";
              const reason = item.expression || item.reason || "";
              html += `
                <article class="video-card is-minimal">
                  <p class="video-card-title"><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(item.title || "无标题")}</a></p>
                  <div class="video-card-meta">
                    ${author ? `<span class="video-card-author">${escapeHtml(author)}</span>` : ""}
                    ${platform ? `<span class="video-card-tag">${escapeHtml(platform)}</span>` : ""}
                  </div>
                  ${reason ? `<p class="video-card-reason">${escapeHtml(reason)}</p>` : ""}
                </article>
              `;
            });
            html += "</div>";
            resultsEl.innerHTML = html;
          }
        } else {
          state.chat[state.chat.length - 1] = { role: "agent", text: "推荐出了点问题，稍后再试。" };
        }
      } catch (e) {
        console.error("Chat recommend failed:", e);
        state.chat[state.chat.length - 1] = { role: "agent", text: "推荐出了点问题，稍后再试。" };
      }
      window.renderChat();
    }
    safeBind("#messageChatBackBtn", "click", () => window.returnToMessages?.());
    safeBind("#messageChatForm", "submit", (event) => {
      event.preventDefault();
      const input = $("#messageChatInput");
      const text = input?.value?.trim() || "";
      if (!text) return;
      input.value = "";
      if (state.messageChatDomain && (state.messageChatScope === "probe" || state.messageChatScope === "avoidance_probe")) {
        const probeType = state.messageChatScope === "avoidance_probe" ? "avoidance.probe" : "interest.probe";
        state.handledProbeKeys.add(window.probeKey(probeType, state.messageChatDomain));
      }
      window.sendChat(text, {
        contextPrefix: state.messageChatPrompt,
        scope: state.messageChatScope,
        subjectId: state.messageChatDomain,
        subjectTitle: state.messageChatSubjectTitle
      });
    });
    safeBind("#llmProvider", "change", () => window.applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), default_provider: $("#llmProvider")?.value || "" } }));
    safeBind("#llmFallbackProvider", "change", () => window.applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), fallback_provider: $("#llmFallbackProvider")?.value || "" } }));
    safeBind("#embeddingFallbackProvider", "change", () => window.applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), embedding: { ...(state.config?.llm?.embedding || {}), fallback_provider: $("#embeddingFallbackProvider")?.value || "" } } }));
    safeBind("#probeLlm", "click", () => { void window.runLlmConfigProbe(); });
    safeBind("#probeEmbedding", "click", () => { void window.runEmbeddingConfigProbe(); });
    lanAuthControl = window.initLanAuthControl?.();
    bootAutostartControl = window.initBootAutostartControl?.();
    Object.values(window.SOURCE_ENABLE_SELECT_IDS || {}).forEach((id) => {
      safeBind(`#${id}`, "change", () => window.renderSourcesStatusRows(state.sourceStatus));
    });
    safeBind("#suggestSharesBtn", "click", async () => {
      const result = await requestJson(ENDPOINTS.sourceShareSuggestion, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled_sources: { bilibili: $("#bilibiliEnabled").value === "on", xiaohongshu: $("#xhsEnabled").value === "on", douyin: $("#douyinEnabled").value === "on", youtube: $("#youtubeEnabled").value === "on", twitter: $("#twitterEnabled").value === "on", zhihu: $("#zhihuEnabled").value === "on" }, configured_shares: window.buildConfigUpdate().scheduler.pool_source_shares }) });
      const shares = result?.pool_source_shares || result?.shares || result?.suggested_shares;
      if (shares) {
        setInput("shareBilibili", shares.bilibili);
        setInput("shareXhs", shares.xiaohongshu);
        setInput("shareDouyin", shares.douyin);
        setInput("shareYoutube", shares.youtube);
        if (shares.twitter !== undefined) setInput("shareTwitter", shares.twitter);
        if (shares.zhihu !== undefined) setInput("shareZhihu", shares.zhihu);
        showToast("已应用来源占比建议");
      } else {
        showToast("没有拿到占比建议");
      }
    });
    safeBind("#settingsForm", "submit", async (event) => {
      event.preventDefault();
      const submitBtn = $("#settingsForm button[type='submit']");
      const previousText = submitBtn?.textContent || "保存配置";
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "保存中…";
      }
      const endpoint = persistBackendEndpoint();
      const frontend = persistFrontendSettings();
      if ($("#configStatus")) $("#configStatus").value = `正在保存到 ${endpoint.host}:${endpoint.port}，惊喜队列加载 ${frontend.delightQueueLimit} 条，换一批忽略当前${frontend.dismissOnReshuffle ? "已开启" : "已关闭"}，后端热重载可能需要几秒。`;
      try {
        const payload = window.buildConfigUpdate();
        const result = await requestJsonStrict(ENDPOINTS.config.replace("?reveal_keys=true", ""), {
          method: "PUT",
          timeoutMs: 60000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (result?.config) window.applyConfig(result.config);
        const message = result?.message || "配置已保存。";
        const suffix = result?.restart_required ? "\n当前配置需要重启后端后完全生效。" : result?.reloaded === false ? "\n后端返回未热重载，请检查运行状态。" : "";
        if ($("#configStatus")) $("#configStatus").value = `${message}${suffix}`;
        showToast(result?.restart_required ? "配置已保存，需要重启后端" : "配置已保存");
        void window.hydrateFromBackend();
        void window.refreshUpdateStatus();
      } catch (error) {
        const message = configErrorMessage(error.details) || error.message || "未知错误";
        if ($("#configStatus")) $("#configStatus").value = `保存失败：\n${message}`;
        showToast("保存失败：请查看配置状态");
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = previousText;
        }
      }
    });
    // routeFromPath() 在脚本解析早期执行，页面级状态必须在此之前声明（避免 TDZ）
    let _travelLoaded = { flights: false, overview: false, doc: false };
    routeFromPath();
    if (typeof window.__initSelfEvolution === "function") window.__initSelfEvolution();
    restoreBackendEndpoint();
    restoreFrontendSettings();
    setSideDrawerOpen(!isMobileViewport() && storageGet(SIDE_DRAWER_OPEN_KEY) !== "0", { persist: false });
    startChatPlaceholderRotation();
    ensureAuthenticated()
      .then(() => new Promise(r => setTimeout(r, 0)))
      .then(() => {
        try { window.renderAll?.(); } catch (e) { console.error("首屏渲染失败", e); }
      })
      .then(() => window.hydrateFromBackend())
      .then(() => window.connectRuntimeStream?.())
      .catch((error) => {
        console.error("后端数据加载失败", error);
        $("#statusLabel").textContent = "后端数据加载失败";
        $("#runtimeSummary").textContent = error?.message || "页面已保留离线数据，可打开设置检查 FastAPI 地址。";
        showToast("后端数据加载失败，页面已保留离线数据");
      });
    // ===== Self-Evolution (自进化) =====

    // ── 日记系统 Diary 函数 ──────────────────────────────────────────

    function openDiaryPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("diaryPage");
      diaryState.offset = 0;
      diaryState.selectedId = null;
      loadDiaryScripts().then(() => {
        loadDiaryStats();
        loadDiaryList();
        bindDiaryEvents();
      });
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openClonePage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("clonePage");
      loadCloneSites();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    // ── 旅行预算页面 ──────────────────────────────────────────────

    function openTravelPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("travelPage");
      window.scrollTo({ top: 0, behavior: "smooth" });
      // 子 tab 切换
      document.querySelectorAll(".travel-subtab").forEach((tab) => {
        tab.onclick = () => {
          document.querySelectorAll(".travel-subtab").forEach((t) => t.classList.remove("active"));
          tab.classList.add("active");
          const view = tab.dataset.travelView;
          document.getElementById("travelFlightsView").hidden = view !== "flights";
          document.getElementById("travelOverviewView").hidden = view !== "overview";
          document.getElementById("travelDocView").hidden = view !== "doc";
        };
      });
      document.getElementById("travelRefreshBtn").onclick = () => {
        _travelLoaded = { flights: false, overview: false, doc: false };
        loadTravelFlights();
        loadTravelOverview();
        loadTravelDoc();
      };
      if (!_travelLoaded.flights) loadTravelFlights();
      if (!_travelLoaded.overview) loadTravelOverview();
      if (!_travelLoaded.doc) loadTravelDoc();
    }

    async function loadTravelFlights() {
      const grid = document.getElementById("travelFlightsGrid");
      const loading = document.getElementById("travelFlightsLoading");
      const alertsEl = document.getElementById("travelAlerts");
      if (!grid) return;
      loading.hidden = false;
      grid.innerHTML = "";
      alertsEl.hidden = true;
      try {
        const res = await fetch("/api/travel/flights");
        const data = await res.json();
        _travelLoaded.flights = true;
        // 降价提醒
        if (data.alerts && data.alerts.length) {
          alertsEl.innerHTML = data.alerts.map((a) =>
            `<div class="travel-alert">🔥 ${a.route} ${a.date} 降价至 ¥${a.price}（${a.flight}），较基线降 ¥${a.drop}</div>`
          ).join("");
          alertsEl.hidden = false;
        }
        // 航线卡片（沿用推荐流小白卡 video-card.is-minimal）
        grid.innerHTML = (data.routes || []).map((r) => {
          const price = r.lowest_price ? `<span class="travel-flight-price">¥${Number(r.lowest_price).toLocaleString()}</span>` : '<span class="travel-flight-price muted">未取到</span>';
          const child = r.child_price ? `<div class="travel-flight-child">儿童 ¥${r.child_price}</div>` : "";
          const flight = r.lowest_flight || "";
          const time = r.departure_time || "";
          return `<div class="video-card is-minimal travel-flight-card">
            <div class="video-card-title">${escapeHtml(r.dep_city)} → ${escapeHtml(r.arr_city)}</div>
            <div class="travel-flight-date">${escapeHtml(r.date)}</div>
            ${price}
            <div class="video-card-footer travel-flight-meta">${escapeHtml(flight)} ${escapeHtml(time)}</div>
            ${child}
          </div>`;
        }).join("");
      } catch (e) {
        grid.innerHTML = `<div class="travel-error">加载失败：${e.message}</div>`;
      } finally {
        loading.hidden = true;
      }
    }

    async function loadTravelOverview() {
      const loading = document.getElementById("travelOverviewLoading");
      const totalsBody = document.querySelector("#travelTotalsTable tbody");
      const plansBody = document.querySelector("#travelPlansTable tbody");
      if (!totalsBody) return;
      loading.hidden = false;
      try {
        const res = await fetch("/api/travel/overview");
        const data = await res.json();
        _travelLoaded.overview = true;
        totalsBody.innerHTML = (data.totals || []).map((t) =>
          `<tr><td>${t.item}</td><td><strong>${t.amount}</strong></td><td class="travel-note">${t.note}</td></tr>`
        ).join("");
        plansBody.innerHTML = (data.plans || []).map((p) =>
          `<tr><td>${p.item}</td><td>${p.plan_a}</td><td>${p.plan_b}</td><td>${p.plan_c}</td></tr>`
        ).join("");
      } catch (e) {
        totalsBody.innerHTML = `<tr><td colspan="3" class="travel-error">加载失败：${e.message}</td></tr>`;
      } finally {
        loading.hidden = true;
      }
    }

    async function loadTravelDoc() {
      const loading = document.getElementById("travelDocLoading");
      const content = document.getElementById("travelDocContent");
      if (!content) return;
      loading.hidden = false;
      try {
        const res = await fetch("/api/travel/doc");
        const data = await res.json();
        _travelLoaded.doc = true;
        // 简单 markdown 渲染（标题、表格、列表、粗体）
        content.innerHTML = renderSimpleMarkdown(data.content || "");
      } catch (e) {
        content.innerHTML = `<div class="travel-error">加载失败：${e.message}</div>`;
      } finally {
        loading.hidden = true;
      }
    }

    function renderSimpleMarkdown(text) {
      const lines = text.split("\n");
      let html = "";
      let inTable = false;
      let inList = false;
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("|") && trimmed.includes("---")) { continue; }
        if (trimmed.startsWith("|")) {
          const cells = trimmed.split("|").slice(1, -1).map((c) => c.trim());
          if (!inTable) { html += "<table class='travel-md-table'><thead><tr>"; html += cells.map((c) => `<th>${c}</th>`).join(""); html += "</tr></thead><tbody>"; inTable = true; }
          else { html += "<tr>"; html += cells.map((c) => `<td>${c}</td>`).join(""); html += "</tr>"; }
          continue;
        } else if (inTable) { html += "</tbody></table>"; inTable = false; }
        if (trimmed.startsWith("### ")) { if (inList) { html += "</ul>"; inList = false; } html += `<h4>${trimmed.slice(4)}</h4>`; }
        else if (trimmed.startsWith("## ")) { if (inList) { html += "</ul>"; inList = false; } html += `<h3>${trimmed.slice(3)}</h3>`; }
        else if (trimmed.startsWith("# ")) { if (inList) { html += "</ul>"; inList = false; } html += `<h2>${trimmed.slice(2)}</h2>`; }
        else if (trimmed.startsWith("- ")) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${trimmed.slice(2)}</li>`; }
        else if (trimmed.startsWith("> ")) { if (inList) { html += "</ul>"; inList = false; } html += `<blockquote>${trimmed.slice(2)}</blockquote>`; }
        else if (trimmed === "") { if (inList) { html += "</ul>"; inList = false; } }
        else { if (inList) { html += "</ul>"; inList = false; } html += `<p>${trimmed}</p>`; }
      }
      if (inTable) html += "</tbody></table>";
      if (inList) html += "</ul>";
      // 粗体
      html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
      return html;
    }

    let _cloneData = { sites: [], stats: null };

    async function loadCloneSites() {
      const grid = document.getElementById("cloneGrid");
      const empty = document.getElementById("cloneEmpty");
      const loading = document.getElementById("cloneLoading");
      if (!grid) return;
      if (loading) loading.hidden = false;
      grid.innerHTML = "";
      try {
        const [sitesRes, statsRes] = await Promise.all([
          fetch("/api/clone/sites?limit=200").then(r => r.json()),
          fetch("/api/clone/stats").then(r => r.json()),
        ]);
        _cloneData.sites = sitesRes.ok ? sitesRes.items : [];
        _cloneData.stats = statsRes.ok ? statsRes.stats : null;
        // 如果站点为空，自动扫描导入已有站点
        if (!_cloneData.sites.length && !_cloneData._autoImported) {
          _cloneData._autoImported = true;
          try {
            const impRes = await fetch("/api/clone/import", { method: "POST" });
            const impData = await impRes.json();
            if (impData.ok && impData.count > 0) {
              // 重新加载
              const [sitesRes2, statsRes2] = await Promise.all([
                fetch("/api/clone/sites?limit=200").then(r => r.json()),
                fetch("/api/clone/stats").then(r => r.json()),
              ]);
              _cloneData.sites = sitesRes2.ok ? sitesRes2.items : [];
              _cloneData.stats = statsRes2.ok ? statsRes2.stats : null;
            }
          } catch (_) { /* 静默失败，用户可手动导入 */ }
        }
        renderCloneSites();
      } catch (e) {
        grid.innerHTML = `<div class="clone-empty"><p>加载失败: ${e.message}</p></div>`;
      } finally {
        if (loading) loading.hidden = true;
      }
    }

    function renderCloneSites() {
      const grid = document.getElementById("cloneGrid");
      const empty = document.getElementById("cloneEmpty");
      const stats = _cloneData.stats;
      if (stats) {
        const el = (id) => document.getElementById(id);
        const setText = (id, val) => { const e = el(id); if (e) e.textContent = val; };
        setText("cloneTotalCount", stats.total_sites || 0);
        setText("cloneTotalSize", formatBytes(stats.total_size_bytes || 0));
        setText("cloneTotalFiles", (stats.total_files || 0).toLocaleString());
      }
      if (!_cloneData.sites.length) {
        if (grid) grid.innerHTML = "";
        if (empty) empty.hidden = false;
        return;
      }
      if (empty) empty.hidden = true;
      if (grid) grid.innerHTML = _cloneData.sites.map(site => renderCloneCard(site)).join("");
    }

    function renderCloneCard(site) {
      const previewUrl = site.local_path ? `/clone/sites/${site.local_path}` : "#";
      const size = formatBytes(site.size_bytes || 0);
      const fileCount = (site.file_count || 0).toLocaleString();
      const statusBadge = site.status === "cloned" ? "" : `<span class="clone-status-badge clone-status-${site.status}">${site.status}</span>`;
      const tags = (site.tags || []).map(t => `<span class="clone-tag">${t}</span>`).join("");
      return `
        <a class="clone-card" href="${previewUrl}" target="_blank" rel="noopener">
          <div class="clone-card-body">
            <h3 class="clone-card-title">${escHtml(site.name)}</h3>
            ${statusBadge}
            ${site.description ? `<p class="clone-card-desc">${escHtml(site.description)}</p>` : ""}
            <div class="clone-card-meta">
              <span>${size}</span>
              <span>${fileCount} 文件</span>
            </div>
            ${tags ? `<div class="clone-card-tags">${tags}</div>` : ""}
          </div>
          <div class="clone-card-footer">
            <span class="clone-card-category">${site.category || "website"}</span>
            <span class="clone-card-date">${site.created_at ? new Date(site.created_at).toLocaleDateString() : ""}</span>
          </div>
        </a>
      `;
    }

    function formatBytes(bytes) {
      if (!bytes || bytes === 0) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      let i = 0;
      let size = bytes;
      while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
      return size.toFixed(i > 0 ? 1 : 0) + " " + units[i];
    }

    function escHtml(str) {
      const div = document.createElement("div");
      div.appendChild(document.createTextNode(str || ""));
      return div.innerHTML;
    }

    function loadDiaryScripts() {
      if (_diaryScriptsPromise) return _diaryScriptsPromise;
      _diaryScriptsPromise = (async () => {
        for (const name of DIARY_SCRIPTS) {
          if (document.querySelector(`script[data-diary-script="${name}"]`)) continue;
          await new Promise((resolve, reject) => {
            const el = document.createElement("script");
            const ver = window.__ASSET_VERSION || "";
            el.src = `/web/assets/js/${name}${ver ? `?v=${ver}` : ""}`;
            el.dataset.diaryScript = name;
            el.onload = resolve;
            el.onerror = reject;
            document.head.appendChild(el);
          });
        }
        // 动态加载的脚本不会触发 DOMContentLoaded，手动执行各自 init
        if (typeof window.__initDiaryInsights === "function") window.__initDiaryInsights();
        if (typeof window.__initDiaryPeople === "function") window.__initDiaryPeople();
        if (typeof window.__initDiarySemantic === "function") window.__initDiarySemantic();
        if (typeof window.__initDiaryChat === "function") window.__initDiaryChat();
        if (typeof window.__initDiaryEnhancedCenter === "function") window.__initDiaryEnhancedCenter();
        // 其余模块（反思/知识/自进化/洞察/记忆中心）在切换子 Tab 时按需初始化
      })();
      return _diaryScriptsPromise;
    }

    function bindDiaryEvents() {
      if (diaryEventsBound) return;
      diaryEventsBound = true;

      safeBind("#diaryNewBtn", "click", () => openDiaryEditor());
      safeBind("#diaryImportBtn", "click", () => openDiaryImport());
      safeBind("#diaryAnalyzeBtn", "click", () => batchAnalyzeDiary());
      safeBind("#diaryLoadMoreBtn", "click", loadMoreDiary);
      safeBind("#diaryResetFilterBtn", "click", resetDiaryFilters);

      // 搜索
      let searchTimer;
      const searchInput = document.getElementById("diarySearchInput");
      if (searchInput) {
        searchInput.addEventListener("input", () => {
          clearTimeout(searchTimer);
          searchTimer = setTimeout(() => {
            diaryState.search = searchInput.value.trim();
            diaryState.offset = 0;
            loadDiaryList();
          }, 400);
        });
      }

      // 筛选
      const moodFilter = document.getElementById("diaryMoodFilter");
      if (moodFilter) {
        moodFilter.addEventListener("change", () => {
          diaryState.moodFilter = moodFilter.value;
          diaryState.offset = 0;
          loadDiaryList();
        });
      }
      const sourceFilter = document.getElementById("diarySourceFilter");
      if (sourceFilter) {
        sourceFilter.addEventListener("change", () => {
          diaryState.sourceFilter = sourceFilter.value;
          diaryState.offset = 0;
          loadDiaryList();
        });
      }

      // 编辑器
      safeBind("#diaryEditorCloseBtn", "click", closeDiaryEditor);
      safeBind("#diaryEditorCancelBtn", "click", closeDiaryEditor);
      safeBind("#diaryEditorOverlay", "click", closeDiaryEditor);
      safeBind("#diaryEditorSaveBtn", "click", saveDiaryEntry);

      // 详情操作
      safeBind("#diaryEditBtn", "click", () => {
        if (diaryState.selectedId) openDiaryEditor(diaryState.selectedId);
      });
      safeBind("#diaryAnalyzeOneBtn", "click", () => {
        if (diaryState.selectedId) analyzeDiaryEntry(diaryState.selectedId);
      });
      safeBind("#diaryDeleteBtn", "click", () => {
        if (diaryState.selectedId) deleteDiaryEntry(diaryState.selectedId);
      });

      // 导入
      safeBind("#diaryImportCloseBtn", "click", closeDiaryImport);
      safeBind("#diaryImportCancelBtn", "click", closeDiaryImport);
      safeBind("#diaryImportOverlay", "click", closeDiaryImport);
      safeBind("#diaryImportConfirmBtn", "click", confirmDiaryImport);
    }

    async function loadDiaryStats() {
      try {
        const resp = await fetch("/api/diary/stats");
        const data = await resp.json();
        if (data.ok && data.stats) {
          const s = data.stats;
          document.getElementById("diaryStatTotal").textContent = s.total_entries;
          document.getElementById("diaryStatWords").textContent = formatNumber(s.total_words);
          document.getElementById("diaryStatAvg").textContent = s.avg_words_per_entry;
          document.getElementById("diaryStatAnalyzed").textContent = s.analyzed_count;
          const range = s.earliest_date && s.latest_date ? `${s.earliest_date} ~ ${s.latest_date}` : "—";
          document.getElementById("diaryStatRange").textContent = range;
        }
      } catch (e) {
        console.error("加载日记统计失败:", e);
      }
    }

    async function loadDiaryList() {
      if (diaryState.loading) return;
      diaryState.loading = true;
      const params = new URLSearchParams({
        limit: diaryState.limit,
        offset: diaryState.offset,
        sort_by: "entry_date",
        sort_order: "DESC",
      });
      if (diaryState.search) params.set("search", diaryState.search);
      if (diaryState.moodFilter) params.set("mood", diaryState.moodFilter);
      if (diaryState.sourceFilter) params.set("source", diaryState.sourceFilter);

      try {
        const resp = await fetch(`/api/diary?${params}`);
        const data = await resp.json();
        if (data.ok) {
          if (diaryState.offset === 0) {
            diaryState.entries = data.items;
          } else {
            diaryState.entries = diaryState.entries.concat(data.items);
          }
          diaryState.total = data.total;
          renderDiaryList();
          document.getElementById("diaryListCount").textContent = `${data.total} 篇`;
          const loadMoreBtn = document.getElementById("diaryLoadMoreBtn");
          if (loadMoreBtn) {
            loadMoreBtn.hidden = diaryState.entries.length >= data.total;
          }
        }
      } catch (e) {
        console.error("加载日记列表失败:", e);
      } finally {
        diaryState.loading = false;
      }
    }

    function renderDiaryList() {
      const listEl = document.getElementById("diaryList");
      if (!listEl) return;
      if (diaryState.entries.length === 0) {
        listEl.innerHTML = '<div style="padding:40px 20px;text-align:center;color:var(--muted);font-size:13px;">暂无日记，点击"写日记"开始记录</div>';
        return;
      }
      listEl.innerHTML = diaryState.entries.map((entry) => {
        const isActive = entry.id === diaryState.selectedId;
        const preview = entry.content.replace(/\n/g, " ").substring(0, 80);
        const moodLabel = MOOD_LABELS[entry.mood] || entry.mood;
        const sourceLabel = DIARY_SOURCE_LABELS[entry.source] || entry.source;
        return `
          <div class="diary-list-item ${isActive ? "active" : ""}" data-id="${entry.id}">
            <div class="diary-list-item-date">${entry.entry_date}${entry.title ? " · " + escapeHtml(entry.title) : ""}</div>
            <div class="diary-list-item-preview">${escapeHtml(preview)}${entry.content.length > 80 ? "..." : ""}</div>
            <div class="diary-list-item-meta">
              <span class="diary-list-item-mood">${moodLabel}</span>
              <span class="diary-list-item-source">${sourceLabel}</span>
              <span style="margin-left:auto;color:var(--meta);">${entry.word_count}字</span>
            </div>
          </div>
        `;
      }).join("");

      listEl.querySelectorAll(".diary-list-item").forEach((item) => {
        item.addEventListener("click", () => {
          const id = parseInt(item.dataset.id);
          selectDiaryEntry(id);
        });
      });
    }

    async function selectDiaryEntry(id) {
      diaryState.selectedId = id;
      renderDiaryList();
      await loadDiaryDetail(id);
    }

    async function loadDiaryDetail(id) {
      const emptyEl = document.getElementById("diaryDetailEmpty");
      const contentEl = document.getElementById("diaryDetailContent");
      try {
        const resp = await fetch(`/api/diary/${id}`);
        const data = await resp.json();
        if (data.ok && data.entry) {
          const entry = data.entry;
          emptyEl.hidden = true;
          contentEl.hidden = false;

          document.getElementById("diaryDetailDate").textContent = entry.entry_date;
          document.getElementById("diaryDetailMood").textContent = MOOD_LABELS[entry.mood] || entry.mood;
          document.getElementById("diaryDetailSource").textContent = DIARY_SOURCE_LABELS[entry.source] || entry.source;
          document.getElementById("diaryDetailTitle").textContent = entry.title || "(无标题)";
          document.getElementById("diaryDetailBody").textContent = entry.content;

          const tagsEl = document.getElementById("diaryDetailTags");
          if (entry.tags && entry.tags.length > 0) {
            tagsEl.innerHTML = entry.tags.map((t) => `<span class="diary-detail-tag">${escapeHtml(t)}</span>`).join("");
            tagsEl.style.display = "flex";
          } else {
            tagsEl.innerHTML = "";
            tagsEl.style.display = "none";
          }

          // 分析结果
          if (data.analysis) {
            renderDiaryAnalysis(data.analysis);
          } else {
            document.getElementById("diaryAnalysisSection").hidden = true;
          }
        }
      } catch (e) {
        console.error("加载日记详情失败:", e);
      }
    }

    function renderDiaryAnalysis(analysis) {
      const section = document.getElementById("diaryAnalysisSection");
      section.hidden = false;
      document.getElementById("diaryAnalysisSummary").textContent = analysis.summary || "暂无摘要";

      const keyPointsEl = document.getElementById("diaryAnalysisKeyPoints");
      keyPointsEl.innerHTML = (analysis.key_points || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("") || "<li>暂无</li>";

      const emotionsEl = document.getElementById("diaryAnalysisEmotions");
      const emotions = analysis.emotions || {};
      emotionsEl.innerHTML = Object.entries(emotions).map(([k, v]) =>
        `<div class="diary-analysis-emotion-item"><span>${escapeHtml(k)}</span><span>${(v * 100).toFixed(0)}%</span></div>`
      ).join("") || '<span style="color:var(--muted);font-size:13px;">暂无</span>';

      const themesEl = document.getElementById("diaryAnalysisThemes");
      themesEl.innerHTML = (analysis.themes || []).map((t) => `<span class="diary-analysis-theme-tag">${escapeHtml(t)}</span>`).join("") || '<span style="color:var(--muted);font-size:13px;">暂无</span>';

      const peopleEl = document.getElementById("diaryAnalysisPeople");
      peopleEl.innerHTML = (analysis.people_mentioned || []).map((p) => `<span class="diary-analysis-person-tag">${escapeHtml(p)}</span>`).join("") || '<span style="color:var(--muted);font-size:13px;">暂无</span>';

      document.getElementById("diaryAnalysisInsight").textContent = analysis.growth_insight || "暂无成长洞察";
    }

    function openDiaryEditor(id = null) {
      diaryState.editingId = id;
      const modal = document.getElementById("diaryEditorModal");
      const titleEl = document.getElementById("diaryEditorTitle");
      const dateInput = document.getElementById("diaryEditorDate");
      const titleInput = document.getElementById("diaryEditorTitleInput");
      const contentInput = document.getElementById("diaryEditorContent");
      const tagsInput = document.getElementById("diaryEditorTags");
      const moodInput = document.getElementById("diaryEditorMood");

      if (id) {
        titleEl.textContent = "编辑日记";
        const entry = diaryState.entries.find((e) => e.id === id);
        if (entry) {
          dateInput.value = entry.entry_date;
          titleInput.value = entry.title || "";
          contentInput.value = entry.content;
          tagsInput.value = (entry.tags || []).join(", ");
          moodInput.value = entry.mood;
        }
      } else {
        titleEl.textContent = "写日记";
        dateInput.value = new Date().toISOString().split("T")[0];
        titleInput.value = "";
        contentInput.value = "";
        tagsInput.value = "";
        moodInput.value = "unknown";
      }
      modal.hidden = false;
      setTimeout(() => contentInput.focus(), 100);
    }

    function closeDiaryEditor() {
      document.getElementById("diaryEditorModal").hidden = true;
      diaryState.editingId = null;
    }

    async function saveDiaryEntry() {
      const date = document.getElementById("diaryEditorDate").value;
      const title = document.getElementById("diaryEditorTitleInput").value.trim();
      const content = document.getElementById("diaryEditorContent").value.trim();
      const tagsStr = document.getElementById("diaryEditorTags").value.trim();
      const mood = document.getElementById("diaryEditorMood").value;

      if (!content) {
        alert("日记内容不能为空");
        return;
      }
      if (!date) {
        alert("请选择日期");
        return;
      }

      const tags = tagsStr ? tagsStr.split(/[,，]/).map((t) => t.trim()).filter(Boolean) : [];
      const payload = { entry_date: date, title, content, tags, mood, source: "manual" };

      try {
        let resp;
        if (diaryState.editingId) {
          resp = await fetch(`/api/diary/${diaryState.editingId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
        } else {
          resp = await fetch("/api/diary", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
        }
        const data = await resp.json();
        if (data.ok) {
          closeDiaryEditor();
          diaryState.offset = 0;
          await loadDiaryStats();
          await loadDiaryList();
          if (data.entry) {
            selectDiaryEntry(data.entry.id);
          }
        } else {
          alert("保存失败: " + (data.error || "未知错误"));
        }
      } catch (e) {
        alert("保存失败: " + e.message);
      }
    }

    async function deleteDiaryEntry(id) {
      if (!confirm("确定要删除这篇日记吗？此操作不可撤销。")) return;
      try {
        const resp = await fetch(`/api/diary/${id}`, { method: "DELETE" });
        const data = await resp.json();
        if (data.ok) {
          diaryState.selectedId = null;
          document.getElementById("diaryDetailEmpty").hidden = false;
          document.getElementById("diaryDetailContent").hidden = true;
          diaryState.offset = 0;
          await loadDiaryStats();
          await loadDiaryList();
        }
      } catch (e) {
        alert("删除失败: " + e.message);
      }
    }

    async function analyzeDiaryEntry(id) {
      const btn = document.getElementById("diaryAnalyzeOneBtn");
      const originalText = btn.textContent;
      btn.textContent = "分析中...";
      btn.disabled = true;
      try {
        const resp = await fetch(`/api/diary/${id}/analyze?force=true`, { method: "POST" });
        const data = await resp.json();
        if (data.ok && data.analysis) {
          renderDiaryAnalysis(data.analysis);
          await loadDiaryStats();
        } else {
          alert("分析失败: " + (data.error || "未知错误"));
        }
      } catch (e) {
        alert("分析失败: " + e.message);
      } finally {
        btn.textContent = originalText;
        btn.disabled = false;
      }
    }

    async function batchAnalyzeDiary() {
      if (!confirm("将对所有未分析的日记进行批量 AI 分析，可能需要较长时间，确定继续吗？")) return;
      const btn = document.getElementById("diaryAnalyzeBtn");
      const originalText = btn.textContent;
      btn.textContent = "批量分析中...";
      btn.disabled = true;
      try {
        const resp = await fetch("/api/diary/analyze-batch?limit=100&concurrency=3", { method: "POST" });
        const data = await resp.json();
        if (data.ok) {
          alert(`批量分析完成：成功 ${data.success} 篇，失败 ${data.failed} 篇`);
          await loadDiaryStats();
          if (diaryState.selectedId) await loadDiaryDetail(diaryState.selectedId);
        } else {
          alert("批量分析失败: " + (data.error || "未知错误"));
        }
      } catch (e) {
        alert("批量分析失败: " + e.message);
      } finally {
        btn.textContent = originalText;
        btn.disabled = false;
      }
    }

    function openDiaryImport() {
      document.getElementById("diaryImportModal").hidden = false;
      document.getElementById("diaryImportPath").value = "";
      document.getElementById("diaryImportFormat").value = "auto";
      document.getElementById("diaryImportResult").hidden = true;
    }

    function closeDiaryImport() {
      document.getElementById("diaryImportModal").hidden = true;
    }

    async function confirmDiaryImport() {
      const path = document.getElementById("diaryImportPath").value.trim();
      const format = document.getElementById("diaryImportFormat").value;
      const resultEl = document.getElementById("diaryImportResult");

      if (!path) {
        resultEl.textContent = "请输入文件路径";
        resultEl.className = "diary-import-result error";
        resultEl.hidden = false;
        return;
      }

      const btn = document.getElementById("diaryImportConfirmBtn");
      btn.textContent = "导入中...";
      btn.disabled = true;

      try {
        const resp = await fetch("/api/diary/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_path: path, format }),
        });
        const data = await resp.json();
        if (data.ok) {
          resultEl.textContent = `导入成功！共导入 ${data.imported} 篇日记`;
          resultEl.className = "diary-import-result success";
          resultEl.hidden = false;
          diaryState.offset = 0;
          await loadDiaryStats();
          await loadDiaryList();
          setTimeout(() => closeDiaryImport(), 1500);
        } else {
          resultEl.textContent = "导入失败: " + (data.error || "未知错误");
          resultEl.className = "diary-import-result error";
          resultEl.hidden = false;
        }
      } catch (e) {
        resultEl.textContent = "导入失败: " + e.message;
        resultEl.className = "diary-import-result error";
        resultEl.hidden = false;
      } finally {
        btn.textContent = "开始导入";
        btn.disabled = false;
      }
    }

    function loadMoreDiary() {
      diaryState.offset += diaryState.limit;
      loadDiaryList();
    }

    function resetDiaryFilters() {
      diaryState.search = "";
      diaryState.moodFilter = "";
      diaryState.sourceFilter = "";
      diaryState.offset = 0;
      document.getElementById("diarySearchInput").value = "";
      document.getElementById("diaryMoodFilter").value = "";
      document.getElementById("diarySourceFilter").value = "";
      loadDiaryList();
    }

    function formatNumber(n) {
      if (n >= 10000) return (n / 10000).toFixed(1) + "w";
      if (n >= 1000) return (n / 1000).toFixed(1) + "k";
      return n.toString();
    }

    function escapeHtml(str) {
      const div = document.createElement("div");
      div.textContent = str;
      return div.innerHTML;
    }

    // ── setInput helper (used by both core and extracted modules) ──
    function setInput(id, value) {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = value;
    }

    // ── Expose shared state for extracted modules ──
    window.OBC = {
      state,
      $,
      grid,
      ENDPOINTS,
      DEFAULT_API_BASE,
      sourceFilterDefinitions,
      sourceFilterOrder,
      contentTypeFilterDefinitions,
      platformLabel,
      platformAliases,
      textCardContentTypes,
      INIT_SOURCE_OPTIONS,
      INIT_SOURCE_LOGIN_HINT,
      INIT_REASON_TEXT,
      INIT_STATUS_POLL_MS,
      CHAT_PLACEHOLDERS,
      DISMISS_ON_RESHUFFLE_KEY,
      DISPLAY_MODE_KEY,
      SIDE_DRAWER_OPEN_KEY,
      DELIGHT_QUEUE_LIMIT_KEY,
      showFatal,
      storageGet,
      storageSet,
      getApiBase,
      getRuntimeStreamUrl,
      getSessionToken,
      setSessionToken,
      isCrossOriginBase,
      withBearer,
      requestJson,
      requestJsonStrict,
      showToast,
      configErrorMessage,
      escapeHtml,
      safeBind,
      eventDelegation,
      navigateTo,
      showMainPage,
      DESKTOP_PAGE_ROUTES,
      routeFromPath,
      syncTopbarHeight,
      setInput,
      renderReshuffleToggle,
      persistFrontendSettings,
      restoreFrontendSettings,
      initWaitingForFirstPool,
      scheduleActivityRailHeightSync,
      renderViewTabs,
      renderFilters,
      renderVideos,
      renderInitOnboarding,
      normalizeRecommendationList,
      scheduleBackendHydration,
      scheduleActivityPageRefresh,
      scheduleDelightQueueRefresh,
      getDelightQueueLimit,
      normalizeBackendHost,
      restoreBackendEndpoint,
      persistBackendEndpoint,
      // Page opener functions
      openHomePage,
      openCustomFilterPage,
      openPoolAllPage,
      openPoolFilterPage,
      openObservabilityPage,
      openInterviewPage,
      openDelightPage,
      openSavedPage,
      openWatchLaterPage,
      openProfilePage,
      openChatPage,
      openDiaryPage,
      openClonePage,
      openLibraryPage,
      openReadArchivePage,
      openSettingsPage,
      openPoolExplorePage,
    };

})();
