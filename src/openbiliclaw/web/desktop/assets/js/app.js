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
    ENDPOINTS.userFeedback = "/api/user-feedback";
    ENDPOINTS.userFeedbackBatch = "/api/user-feedback/batch";

    ENDPOINTS.interestTags = "/api/interest-tags";
    ENDPOINTS.viewRecord = "/api/view-record";
    ENDPOINTS.viewDwell = "/api/view-dwell";
    ENDPOINTS.viewHistory = "/api/view-history";

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

    const scheduleDelightQueueRefresh = debounceAsync(() => fetchDelightQueue(), 1000);

    async function runBackendHydration() {
      if (backendHydrationInFlight) {
        backendHydrationPending = true;
        return;
      }
      backendHydrationInFlight = true;
      try {
        await hydrateFromBackend();
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
        await loadActivityPage({ reset: true });
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
      return asArray(items).map(normalizeRecommendation).filter((item) => !isFeedbackedRecommendation(item));
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
      const runtime = normalizeRuntimeStatus(status);
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
            renderAll();
            scheduleInitStatusRefresh(schedule ? INIT_STATUS_POLL_MS : INIT_STATUS_WATCHDOG_MS);
            return;
          }
          renderAll();
          clearInitPolling();
          initRefreshPending = false;
          if (!wasInitialized) {
            scheduleBackendHydration();
            showToast("初始化完成，正在加载推荐");
          }
          return;
        }
        renderAll();
        if (status?.running) {
          scheduleInitStatusRefresh(schedule ? INIT_STATUS_POLL_MS : INIT_STATUS_WATCHDOG_MS);
        } else if (!status?.running) {
          clearInitPolling();
        }
      } catch (error) {
        scheduleInitStatusRefresh(INIT_STATUS_POLL_MS);
        state.initReason = error?.message || "初始化状态读取失败。";
        renderAll();
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
      renderAll();
      let status = null;
      try {
        status = await requestJsonStrict(ENDPOINTS.initStatus, { timeoutMs: 60000 });
        state.initStatus = status;
      } catch (error) {
        state.initReason = error?.message || "前置检查没拉到，稍后再试。";
        state.initBusy = false;
        renderAll();
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
        renderAll();
        return;
      }
      if (status.running) {
        state.initBusy = false;
        renderAll();
        clearInitPolling();
        scheduleInitStatusRefresh(INIT_STATUS_START_POLL_MS);
        return;
      }
      if (!selected.length) {
        state.initReason = INIT_REASON_TEXT.no_sources_selected;
        state.initBusy = false;
        renderAll();
        return;
      }
      if (selected.includes("bilibili") && !status?.prerequisites?.bilibili_logged_in) {
        state.initReason = "还没检测到 B 站登录。先登录 bilibili.com，或取消勾选 B 站再开始。";
        state.initBusy = false;
        renderAll();
        return;
      }
      if (!status.can_start) {
        state.initReason = describeInitReason(status.reason) || status.detail || "以下条件未满足，无法开始初始化。";
        state.initBusy = false;
        renderAll();
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
        renderAll();
        scheduleInitStatusRefresh(INIT_STATUS_START_POLL_MS);
      } catch (error) {
        const code = error?.details?.error || error?.details?.reason;
        state.initReason = describeInitReason(code) || error?.message || "初始化没能启动，请稍后重试。";
        state.initBusy = false;
        renderAll();
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

    const MAIN_PAGE_IDS = ["homePage", "customFilterPage", "poolAllPage", "poolFilterPage", "observabilityPage", "poolExplorePage", "xhsFeedPage", "zhihuFeedPage", "biliFeedPage", "youtubeFeedPage", "v2exFeedPage", "xiaoyuzhouFeedPage", "agentRecommendPage", "delightPage", "savedPage", "watchLaterPage", "profilePage", "chatPage", "diaryPage", "clonePage", "selfEvolutionPage", "libraryPage", "readArchivePage", "settingsPage"];

    window.showMainPage = showMainPage;
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
      document.body.classList.toggle("profile-page-open", pageId === "profilePage");
      document.body.classList.toggle("chat-page-open", pageId === "chatPage");
      document.body.classList.toggle("library-page-open", pageId === "libraryPage" || pageId === "readArchivePage");
      document.body.classList.toggle("pool-all-page-open", pageId === "poolAllPage" || pageId === "poolFilterPage");
      document.body.classList.toggle("custom-filter-page-open", pageId === "customFilterPage");
      document.body.classList.toggle("saved-page-open", pageId === "savedPage" || pageId === "watchLaterPage");
      document.body.classList.toggle("settings-page-open", pageId === "settingsPage");
      const tabSync = { homePage: "homeBtn", customFilterPage: "customFilterBtn", poolAllPage: "poolAllBtn", poolExplorePage: "poolExploreBtn", poolFilterPage: "poolFilterBtn", delightPage: "delightTabBtn", savedPage: "favoritesBtn", watchLaterPage: "watchLaterBtn", diaryPage: "diaryBtn", clonePage: "cloneBtn", profilePage: "profileBtn", chatPage: "chatBtn", libraryPage: "libraryBtn", readArchivePage: "readArchiveBtn", settingsPage: "settingsBtn" };
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
      const feedPageIds = ["xhsFeedPage", "zhihuFeedPage", "biliFeedPage", "youtubeFeedPage", "v2exFeedPage", "xiaoyuzhouFeedPage"];
      const isFeedPage = feedPageIds.includes(pageId);
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
      "pool-explore": () => openPoolExplorePage(),
      "xhs-feed": () => openXhsFeedPage(),
      "zhihu-feed": () => openZhihuFeedPage(),
      "bili-feed": () => openBiliFeedPage(),
      "youtube-feed": () => openYoutubeFeedPage(),
      "v2ex-feed": () => openV2exFeedPage(),
      "xiaoyuzhou-feed": () => openXiaoyuzhouFeedPage(),
      "agent-recommend": () => openAgentRecommendPage(),
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
    };

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

    function openPoolExplorePage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("poolExplorePage");
      state.poolExploreFilters = {};
      loadPoolExploreData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openXhsFeedPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("xhsFeedPage");
      loadXhsFeedData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openZhihuFeedPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("zhihuFeedPage");
      loadZhihuFeedData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openBiliFeedPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("biliFeedPage");
      loadBiliFeedData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openYoutubeFeedPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("youtubeFeedPage");
      loadYoutubeFeedData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openV2exFeedPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("v2exFeedPage");
      loadV2exFeedData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openXiaoyuzhouFeedPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("xiaoyuzhouFeedPage");
      loadXiaoyuzhouFeedData();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openAgentRecommendPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("agentRecommendPage");
      // Focus the input
      setTimeout(() => {
        const input = $("#agentRecommendInput");
        if (input) input.focus();
      }, 100);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openDelightPage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("delightPage");
      renderDelightGrid();
      // 队列还没就绪时立即单独拉取（pending-batch 本身 50ms 级），
      // 不等 hydrate 主链（runtime/notification/chat 等）全部完成再出卡。
      if (!state.delights.length) void fetchDelightQueue();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function openProfilePage() {
      closeMobileMenu();
      document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
      showMainPage("profilePage");
      renderProfileDetails();
      void refreshProfile().catch(() => {});
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
      void renderSourcesStatus();
      void renderSourceCredentials();
      void loadSubscriptionList();
      void lanAuthControl?.reload();
      void bootAutostartControl?.reload();
      void refreshUpdateStatus();
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
      renderRail();
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
        hydrateInboxFromSpeculations(state.profile?.speculative_interests);
        hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
        state.messageListSnapshot = getRenderableMessages();
        returnToMessages();
        renderMessages();
        void refreshProfile().catch(() => {});
      }
      if (id === "activityDrawer") renderActivityHistory();
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
            reshuffle();
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
        btn.addEventListener("click", () => { state.filter = name; reshuffle(); });
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
      if (shouldShowInitOnboarding(state.runtimeStatus)) {
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
          if (url) { openRecommendation(item, card); }
        });
        card.querySelectorAll("[data-action]").forEach((btn) => btn.addEventListener("click", () => handleCardAction(btn.dataset.action, item, card)));
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
            openRecommendation(item, card);
          });
          card.querySelector("[data-action]")?.addEventListener("click", (e) => {
            e.stopPropagation();
            openRecommendation(item, card);
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
          card.addEventListener("click", () => openRecommendation(item, card));
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

    // ── 池子探索 ──────────────────────────────────────────────────────────

    function loadPoolExploreData() {
      const body = $("#poolExploreBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载筛选条件…</div>`;

      // 先获取观测数据中的 topic_group 列表
      requestJson("/observability", { timeoutMs: 30000 }).then((obs) => {
        const topicGroups = (obs?.topic_groups || []).map(t => t.topic);
        renderPoolExploreFilters(body, topicGroups);
        doPoolExploreQuery(body);
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接。</div>`;
      });
    }

    function renderPoolExploreFilters(container, topicGroups) {
      const f = state.poolExploreFilters || {};
      const platforms = ["bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "v2ex", "reddit", "wechat", "xiaoyuzhou", "rss"];
      const statuses = ["fresh", "shown", "stale", "suppressed", "feedbacked"];

      container.innerHTML = `
        <div class="obs-section">
          <h3 class="obs-section-title">筛选条件</h3>
          <div class="pex-filter-grid">
            <div class="pex-filter-group">
              <label class="pex-filter-label">平台</label>
              <select class="pex-filter-select" id="pexPlatform">
                <option value="">全部</option>
                ${platforms.map(p => `<option value="${p}"${f.platform === p ? " selected" : ""}>${platformLabelHtml(p)}</option>`).join("")}
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">状态</label>
              <select class="pex-filter-select" id="pexStatus">
                <option value="">全部</option>
                ${statuses.map(s => `<option value="${s}"${f.status === s ? " selected" : ""}>${s}</option>`).join("")}
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">最低评分</label>
              <select class="pex-filter-select" id="pexMinScore">
                <option value="">不限</option>
                <option value="0"${f.min_score === 0 ? " selected" : ""}>0+ (全部)</option>
                <option value="0.2"${f.min_score === 0.2 ? " selected" : ""}>0.2+</option>
                <option value="0.4"${f.min_score === 0.4 ? " selected" : ""}>0.4+</option>
                <option value="0.6"${f.min_score === 0.6 ? " selected" : ""}>0.6+</option>
                <option value="0.8"${f.min_score === 0.8 ? " selected" : ""}>0.8+</option>
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">评分状态</label>
              <select class="pex-filter-select" id="pexScored">
                <option value="">全部</option>
                <option value="scored"${f.scored_only ? " selected" : ""}>已评分</option>
                <option value="unscored"${f.unscored_only ? " selected" : ""}>未评分</option>
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">内容链接</label>
              <select class="pex-filter-select" id="pexHasUrl">
                <option value="">全部</option>
                <option value="yes"${f.has_url === true ? " selected" : ""}>有链接</option>
                <option value="no"${f.has_url === false ? " selected" : ""}>无链接</option>
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">主题标签</label>
              <select class="pex-filter-select" id="pexTopicGroup">
                <option value="">全部</option>
                ${topicGroups.map(t => `<option value="${t}"${f.topic_group === t ? " selected" : ""}>${escapeHtml(t)}</option>`).join("")}
              </select>
            </div>
          </div>
          <div class="pex-filter-actions">
            <button class="pill-btn dark" id="pexApplyBtn" type="button">应用筛选</button>
            <button class="pill-btn" id="pexResetBtn" type="button">重置</button>
            <span class="pex-filter-hint" id="pexFilterHint">共 <strong id="pexTotalCount">—</strong> 条匹配</span>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">筛选结果</h3>
          <div class="card-grid" id="pexResultGrid">
            <div class="empty-state">点击"应用筛选"查看结果</div>
          </div>
          <div class="pool-all-footer">随机展示 6 条，共 <span id="pexResultTotal">—</span> 条匹配</div>
        </div>
      `;

      safeBind("#pexApplyBtn", "click", () => {
        readPoolExploreFilters();
        doPoolExploreQuery(container);
      });
      safeBind("#pexResetBtn", "click", () => {
        state.poolExploreFilters = {};
        loadPoolExploreData();
      });
    }

    function readPoolExploreFilters() {
      const f = {};
      const plat = $("#pexPlatform")?.value;
      if (plat) f.platform = plat;
      const status = $("#pexStatus")?.value;
      if (status) f.status = status;
      const minScore = $("#pexMinScore")?.value;
      if (minScore !== "" && minScore !== undefined) f.min_score = parseFloat(minScore);
      const scored = $("#pexScored")?.value;
      if (scored === "scored") f.scored_only = true;
      if (scored === "unscored") f.unscored_only = true;
      const hasUrl = $("#pexHasUrl")?.value;
      if (hasUrl === "yes") f.has_url = true;
      if (hasUrl === "no") f.has_url = false;
      const topic = $("#pexTopicGroup")?.value;
      if (topic) f.topic_group = topic;
      state.poolExploreFilters = f;
    }

    function doPoolExploreQuery(container) {
      const f = state.poolExploreFilters || {};
      const params = new URLSearchParams();
      params.set("shuffle", "true");
      params.set("limit", "6");
      if (f.platform) params.set("platform", f.platform);
      if (f.status) params.set("status", f.status);
      if (f.min_score !== undefined && f.min_score !== null) params.set("min_score", String(f.min_score));
      if (f.scored_only) params.set("scored_only", "true");
      if (f.unscored_only) params.set("unscored_only", "true");
      if (f.has_url === true || f.has_url === false) params.set("has_url", f.has_url ? "true" : "false");
      if (f.topic_group) params.set("topic_group", f.topic_group);

      const hint = $("#pexFilterHint");
      const grid = $("#pexResultGrid");
      const totalEl = $("#pexResultTotal");
      if (hint) hint.innerHTML = "查询中…";
      if (grid) grid.innerHTML = `<div class="empty-state">正在查询…</div>`;

      requestJson(`${ENDPOINTS.poolAll}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (hint) hint.innerHTML = `共 <strong>${total}</strong> 条匹配`;
        if (totalEl) totalEl.textContent = String(total);
        if (grid) {
          if (!items.length) {
            grid.innerHTML = `<div class="empty-state">没有匹配的内容</div>`;
          } else {
            grid.replaceChildren(
              ...items.map((item) => {
                const card = document.createElement("div");
                card.className = "video-card is-minimal";
                card.innerHTML = poolExploreCardHtml(item);
                card.addEventListener("click", (e) => {
                  if (e.target.closest("[data-action]")) return;
                  openRecommendation(item, card);
                });
                card.querySelector("[data-action]")?.addEventListener("click", (e) => {
                  e.stopPropagation();
                  openRecommendation(item, card);
                });
                return card;
              })
            );
          }
        }
      }).catch(() => {
        if (hint) hint.innerHTML = "查询失败";
        if (grid) grid.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
      });
    }

    // ── 小红书推荐流 ──────────────────────────────────────────────────────

    function loadXhsFeedData(bust = false) {
      const body = $("#xhsFeedBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

      const params = new URLSearchParams();
      params.set("source", "xhs-feed");
      if (bust) params.set("_", String(Date.now()));
      params.set("shuffle", "true");
      params.set("limit", "40");

      requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (!items.length) {
          body.innerHTML = `
            <div class="obs-section">
              <div class="empty-state">
                <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
              </div>
            </div>`;
          return;
        }
        body.innerHTML = `
	          <div class="obs-section">
	            <div class="xhs-feed-meta">
	              <div>
	                <span>共 <strong>${total}</strong> 条推荐内容</span>
	                <span class="xhs-feed-tag">xhs-feed</span>
	              </div>
	              <button class="pill-btn dark" id="xhsFeedRefreshBtn" type="button">换一批</button>
	            </div>
	            <div class="card-grid" id="xhsFeedGrid"></div>
	          </div>`;
        const grid = $("#xhsFeedGrid");
        if (!grid) return;
        grid.replaceChildren(
          ...items.map((item) => {
            const card = document.createElement("div");
            card.className = "video-card is-minimal";
            card.innerHTML = xhsFeedCardHtml(item);
            card.addEventListener("click", (e) => {
              if (e.target.closest("[data-action]")) return;
              openRecommendation(item, card);
            });
            card.querySelector("[data-action]")?.addEventListener("click", (e) => {
              e.stopPropagation();
              openRecommendation(item, card);
            });
            return card;
          })
        );
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
      });
    }

    function xhsFeedCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const url = escapeHtml(item.content_url || "");
      const status = item.pool_status || "";
      const score = item.quality_score || 0;
      const topic = escapeHtml(item.topic_group || "");

      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml("xiaohongshu")}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          <div class="video-card-meta">
            <span class="video-card-author">${author}</span>
            <span class="video-card-platform">${platformLabelHtml("xiaohongshu")}</span>
          </div>
          <div class="video-card-footer">
            <span class="video-card-status ${status}">${status}</span>
            ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
            ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    // ── 知乎推荐流 ──────────────────────────────────────────────────────

    function loadZhihuFeedData(bust = false) {
      const body = $("#zhihuFeedBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

      const params = new URLSearchParams();
      params.set("source", "zhihu-feed");
      if (bust) params.set("_", String(Date.now()));
      params.set("shuffle", "true");
      params.set("limit", "20");

      requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (!items.length) {
          body.innerHTML = `
            <div class="obs-section">
              <div class="empty-state">
                <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
              </div>
            </div>`;
          return;
        }
        body.innerHTML = `
	          <div class="obs-section">
	            <div class="xhs-feed-meta">
	              <div>
	                <span>共 <strong>${total}</strong> 条推荐内容</span>
	                <span class="xhs-feed-tag zhihu-feed-tag">zhihu-feed</span>
	              </div>
	              <button class="pill-btn dark" id="zhihuFeedRefreshBtn" type="button">换一批</button>
	            </div>
	            <div class="card-grid" id="zhihuFeedGrid"></div>
	          </div>`;
        const grid = $("#zhihuFeedGrid");
        if (!grid) return;
        grid.replaceChildren(
          ...items.map((item) => {
            const card = document.createElement("div");
            card.className = "video-card is-minimal";
            card.innerHTML = zhihuFeedCardHtml(item);
            card.addEventListener("click", (e) => {
              if (e.target.closest("[data-action]")) return;
              openRecommendation(item, card);
            });
            card.querySelector("[data-action]")?.addEventListener("click", (e) => {
              e.stopPropagation();
              openRecommendation(item, card);
            });
            return card;
          })
        );
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
      });
    }

    function zhihuFeedCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const url = escapeHtml(item.content_url || "");
      const status = item.pool_status || "";
      const score = item.quality_score || 0;
      const topic = escapeHtml(item.topic_group || "");
      const excerpt = escapeHtml((item.body_text || "").slice(0, 120));

      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml("zhihu")}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          ${excerpt ? `<p class="video-card-excerpt">${excerpt}</p>` : ""}
          <div class="video-card-meta">
            <span class="video-card-author">${author}</span>
            <span class="video-card-platform">${platformLabelHtml("zhihu")}</span>
          </div>
          <div class="video-card-footer">
            <span class="video-card-status ${status}">${status}</span>
            ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
            ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    function loadBiliFeedData(bust = false) {
      const body = $("#biliFeedBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

      const params = new URLSearchParams();
      params.set("source", "bili-feed");
      if (bust) params.set("_", String(Date.now()));
      params.set("shuffle", "true");
      params.set("limit", "20");

      requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (!items.length) {
          body.innerHTML = `
            <div class="obs-section">
              <div class="empty-state">
                <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
              </div>
            </div>`;
          return;
        }
        body.innerHTML = `
          <div class="obs-section">
            <div class="xhs-feed-meta">
              <div>
                <span>共 <strong>${total}</strong> 条推荐内容</span>
                <span class="xhs-feed-tag bili-feed-tag">bili-feed</span>
              </div>
              <button class="pill-btn dark" id="biliFeedRefreshBtn" type="button">换一批</button>
            </div>
            <div class="card-grid" id="biliFeedGrid"></div>
          </div>`;
        const grid = $("#biliFeedGrid");
        if (!grid) return;
        grid.replaceChildren(
          ...items.map((item) => {
            const card = document.createElement("div");
            card.className = "video-card is-minimal";
            card.innerHTML = biliFeedCardHtml(item);
            card.addEventListener("click", (e) => {
              if (e.target.closest("[data-action]")) return;
              openRecommendation(item, card);
            });
            card.querySelector("[data-action]")?.addEventListener("click", (e) => {
              e.stopPropagation();
              openRecommendation(item, card);
            });
            return card;
          })
        );
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
      });
    }

    function biliFeedCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const url = escapeHtml(item.content_url || "");
      const status = item.pool_status || "";
      const score = item.quality_score || 0;
      const topic = escapeHtml(item.topic_group || "");
      const viewCount = item.view_count || 0;
      const duration = item.duration || 0;
      const durStr = duration ? `${Math.floor(duration / 60)}:${String(duration % 60).padStart(2, "0")}` : "";
      const viewStr = viewCount >= 10000 ? `${(viewCount / 10000).toFixed(1)}万` : String(viewCount);

      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml("bilibili")}</div>
          ${durStr ? `<span class="video-card-duration">${durStr}</span>` : ""}
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          <div class="video-card-meta">
            <span class="video-card-author">${author}</span>
            <span class="video-card-platform">${platformLabelHtml("bilibili")}</span>
          </div>
          <div class="video-card-footer">
            <span class="video-card-status ${status}">${status}</span>
            ${viewCount > 0 ? `<span class="video-card-views">${viewStr}播放</span>` : ""}
            ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
            ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    function loadYoutubeFeedData(bust = false) {
      const body = $("#youtubeFeedBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

      const params = new URLSearchParams();
      params.set("source", "youtube-feed");
      if (bust) params.set("_", String(Date.now()));
      params.set("shuffle", "true");
      params.set("limit", "20");

      requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (!items.length) {
          body.innerHTML = `
            <div class="obs-section">
              <div class="empty-state">
                <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
              </div>
            </div>`;
          return;
        }
        body.innerHTML = `
          <div class="obs-section">
            <div class="xhs-feed-meta">
              <div>
                <span>共 <strong>${total}</strong> 条推荐内容</span>
                <span class="xhs-feed-tag youtube-feed-tag">youtube-feed</span>
              </div>
              <button class="pill-btn dark" id="youtubeFeedRefreshBtn" type="button">换一批</button>
            </div>
            <div class="card-grid" id="youtubeFeedGrid"></div>
          </div>`;
        const grid = $("#youtubeFeedGrid");
        if (!grid) return;
        grid.replaceChildren(
          ...items.map((item) => {
            const card = document.createElement("div");
            card.className = "video-card is-minimal";
            card.innerHTML = youtubeFeedCardHtml(item);
            card.addEventListener("click", (e) => {
              if (e.target.closest("[data-action]")) return;
              openRecommendation(item, card);
            });
            card.querySelector("[data-action]")?.addEventListener("click", (e) => {
              e.stopPropagation();
              openRecommendation(item, card);
            });
            return card;
          })
        );
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
      });
    }

    function youtubeFeedCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const url = escapeHtml(item.content_url || "");
      const status = item.pool_status || "";
      const score = item.quality_score || 0;
      const topic = escapeHtml(item.topic_group || "");
      const viewCount = item.view_count || 0;
      const viewStr = viewCount >= 10000 ? `${(viewCount / 10000).toFixed(1)}万` : String(viewCount);

      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml("youtube")}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          <div class="video-card-meta">
            <span class="video-card-author">${author}</span>
            <span class="video-card-platform">${platformLabelHtml("youtube")}</span>
          </div>
          <div class="video-card-footer">
            <span class="video-card-status ${status}">${status}</span>
            ${viewCount > 0 ? `<span class="video-card-views">${viewStr}播放</span>` : ""}
            ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
            ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    function loadV2exFeedData(bust = false) {
      const body = $("#v2exFeedBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

      const params = new URLSearchParams();
      params.set("source", "v2ex-feed");
      if (bust) params.set("_", String(Date.now()));
      params.set("shuffle", "true");
      params.set("limit", "20");

      requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (!items.length) {
          body.innerHTML = `
            <div class="obs-section">
              <div class="empty-state">
                <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
              </div>
            </div>`;
          return;
        }
        body.innerHTML = `
          <div class="obs-section">
            <div class="xhs-feed-meta">
              <div>
                <span>共 <strong>${total}</strong> 条推荐内容</span>
                <span class="xhs-feed-tag v2ex-feed-tag">v2ex-feed</span>
              </div>
              <button class="pill-btn dark" id="v2exFeedRefreshBtn" type="button">换一批</button>
            </div>
            <div class="card-grid" id="v2exFeedGrid"></div>
          </div>`;
        const grid = $("#v2exFeedGrid");
        if (!grid) return;
        grid.replaceChildren(
          ...items.map((item) => {
            const card = document.createElement("div");
            card.className = "video-card is-minimal";
            card.innerHTML = v2exFeedCardHtml(item);
            card.addEventListener("click", (e) => {
              if (e.target.closest("[data-action]")) return;
              openRecommendation(item, card);
            });
            card.querySelector("[data-action]")?.addEventListener("click", (e) => {
              e.stopPropagation();
              openRecommendation(item, card);
            });
            return card;
          })
        );
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
      });
    }

    function v2exFeedCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const url = escapeHtml(item.content_url || "");
      const status = item.pool_status || "";
      const score = item.quality_score || 0;
      const topic = escapeHtml(item.topic_group || "");
      const replies = item.like_count || 0;
      const excerpt = escapeHtml((item.body_text || "").slice(0, 120));

      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml("v2ex")}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          ${excerpt ? `<p class="video-card-excerpt">${excerpt}</p>` : ""}
          <div class="video-card-meta">
            <span class="video-card-author">${author}</span>
            <span class="video-card-platform">${platformLabelHtml("v2ex")}</span>
          </div>
          <div class="video-card-footer">
            <span class="video-card-status ${status}">${status}</span>
            ${replies > 0 ? `<span class="video-card-views">${replies}回复</span>` : ""}
            ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
            ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    function loadXiaoyuzhouFeedData(bust = false) {
      const body = $("#xiaoyuzhouFeedBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

      const params = new URLSearchParams();
      params.set("source", "xiaoyuzhou-feed");
      if (bust) params.set("_", String(Date.now()));
      params.set("shuffle", "true");
      params.set("limit", "20");

      requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (!items.length) {
          body.innerHTML = `
            <div class="obs-section">
              <div class="empty-state">
                <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
              </div>
            </div>`;
          return;
        }
        body.innerHTML = `
          <div class="obs-section">
            <div class="xhs-feed-meta">
              <div>
                <span>共 <strong>${total}</strong> 条推荐内容</span>
                <span class="xhs-feed-tag xiaoyuzhou-feed-tag">小宇宙</span>
              </div>
              <button class="pill-btn dark" id="xiaoyuzhouFeedRefreshBtn" type="button">换一批</button>
            </div>
            <div class="card-grid" id="xiaoyuzhouFeedGrid"></div>
          </div>`;
        const grid = $("#xiaoyuzhouFeedGrid");
        if (!grid) return;
        grid.replaceChildren(
          ...items.map((item) => {
            const card = document.createElement("div");
            card.className = "video-card is-minimal";
            card.innerHTML = xiaoyuzhouFeedCardHtml(item);
            card.addEventListener("click", (e) => {
              if (e.target.closest("[data-action]")) return;
              openRecommendation(item, card);
            });
            card.querySelector("[data-action]")?.addEventListener("click", (e) => {
              e.stopPropagation();
              openRecommendation(item, card);
            });
            return card;
          })
        );
      }).catch(() => {
        body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
      });
    }

    function xiaoyuzhouFeedCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const url = escapeHtml(item.content_url || "");
      const status = item.pool_status || "";
      const score = item.quality_score || 0;
      const duration = item.like_count || 0;
      const minutes = duration > 0 ? Math.round(duration / 60) : 0;
      const excerpt = escapeHtml((item.body_text || "").slice(0, 120));

      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml("xiaoyuzhou")}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          ${excerpt ? `<p class="video-card-excerpt">${excerpt}</p>` : ""}
          <div class="video-card-meta">
            <span class="video-card-author">${author}</span>
            <span class="video-card-platform">${platformLabelHtml("xiaoyuzhou")}</span>
          </div>
          <div class="video-card-footer">
            <span class="video-card-status ${status}">${status}</span>
            ${minutes > 0 ? `<span class="video-card-views">${minutes}分钟</span>` : ""}
            ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    // Session context for multi-turn agent recommendation
    let _agentSessionId = localStorage.getItem("agentSessionId") || "";
    if (!_agentSessionId) {
      _agentSessionId = "sess_" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("agentSessionId", _agentSessionId);
    }

    function updateAgentSessionContext(data) {
      const ctx = $("#agentSessionContext");
      const ctxText = $("#agentSessionContextText");
      if (!ctx || !ctxText) return;
      const sc = data?.session_context || "";
      if (sc) {
        ctxText.textContent = sc;
        ctx.style.display = "flex";
      } else {
        ctx.style.display = "none";
      }
    }

    function resetAgentSession() {
      _agentSessionId = "sess_" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);
      localStorage.setItem("agentSessionId", _agentSessionId);
      const ctx = $("#agentSessionContext");
      if (ctx) ctx.style.display = "none";
    }

    function loadAgentRecommendData(query) {
      const body = $("#agentRecommendBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在搜索「${escapeHtml(query)}」…</div>`;

      // Save to search history
      saveAgentSearchHistory(query);

      const params = new URLSearchParams();
      params.set("q", query);
      params.set("limit", "20");
      params.set("shuffle", "true");
      params.set("session_id", _agentSessionId);

      requestJson(`${ENDPOINTS.poolAll.replace("/pool/all", "/agent-recommend")}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        // Update session context in UI
        updateAgentSessionContext(data);
        if (!items.length) {
          body.innerHTML = `
            <div class="obs-section">
              <div class="empty-state" style="padding: 40px 20px; text-align: center;">
                <p style="font-size: 16px; margin-bottom: 8px;">没有找到相关内容</p>
                <p style="font-size: 13px; color: var(--text-secondary);">试试其他关键词，比如「科技」「AI」「搞笑」「美食」</p>
              </div>
            </div>`;
          return;
        }

        // Count platform distribution
        const platformCounts = {};
        for (const item of items) {
          const p = item.source_platform || "unknown";
          platformCounts[p] = (platformCounts[p] || 0) + 1;
        }
        const platformStatsHtml = Object.entries(platformCounts)
          .sort((a, b) => b[1] - a[1])
          .map(([p, c]) => `<span class="agent-platform-stat">${platformLabelHtml(p)} ${c}</span>`)
          .join("");

        body.innerHTML = `
          <div class="obs-section">
            <div class="agent-recommend-meta">
              <div class="agent-recommend-meta-left">
                <span>搜索「<strong>${escapeHtml(query)}</strong>」共 <strong>${total}</strong> 条内容</span>
                <div class="agent-platform-stats">${platformStatsHtml}</div>
              </div>
              <div class="agent-recommend-meta-right">
                <button class="pill-btn dark" id="agentRecommendRefreshBtn" type="button" data-query="${escapeHtml(query)}">换一批</button>
              </div>
            </div>
            <div class="card-grid" id="agentRecommendGrid"></div>
          </div>`;
        const grid = $("#agentRecommendGrid");
        if (!grid) return;
        grid.replaceChildren(
          ...items.map((item) => {
            const card = document.createElement("div");
            card.className = "video-card is-minimal";
            card.__itemData = item; // store for feedback button
            card.innerHTML = agentRecommendCardHtml(item, query);
            card.addEventListener("click", (e) => {
              if (e.target.closest("[data-action]") || e.target.closest(".feedback-btn") || e.target.closest(".video-card-link")) return;
              // Track view + dwell (implicit feedback)
              beginDwellTracking(item);
              const url = item.content_url;
              if (url) window.open(url, "_blank", "noopener,noreferrer");
            });
            return card;
          })
        );
      }).catch(() => {
        body.innerHTML = `<div class="empty-state" style="padding: 40px 20px; text-align: center;">
          <p style="font-size: 16px; margin-bottom: 8px;">请求失败</p>
          <p style="font-size: 13px; color: var(--text-secondary);">请检查后端连接后重试</p>
          <button class="pill-btn dark" style="margin-top: 12px;" onclick="loadAgentRecommendData('${escapeHtml(query)}')">重试</button>
        </div>`;
      });
    }

    function agentRecommendCardHtml(item, query) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const url = escapeHtml(item.content_url || "");
      const status = item.pool_status || "";
      const score = item.quality_score || 0;
      const platform = item.source_platform || "";
      const topic = escapeHtml(item.topic_group || "");
      const excerpt = escapeHtml((item.body_text || "").slice(0, 120));

      // Highlight keywords in title
      let highlightedTitle = title;
      if (query) {
        const keywords = query.split(/[,，、\s]+/).filter(Boolean);
        for (const kw of keywords) {
          if (kw.length < 2) continue;
          const escapedKw = kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          highlightedTitle = highlightedTitle.replace(
            new RegExp(escapedKw, "gi"),
            (match) => `<mark class="kw-highlight">${escapeHtml(match)}</mark>`
          );
        }
      }

      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml(platform)}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${highlightedTitle}</p>
          ${excerpt ? `<p class="video-card-excerpt">${excerpt}</p>` : ""}
          <div class="video-card-meta">
            <span class="video-card-author">${author}</span>
            <span class="video-card-platform">${platformLabelHtml(platform)}</span>
          </div>
          <div class="video-card-footer">
            <span class="video-card-status ${status}">${status}</span>
            ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
            ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
          </div>
        </div>
        <div class="video-card-actions">
          <button class="feedback-btn like-btn" data-bvid="${escapeHtml(item.bvid)}" data-action="like" title="喜欢">👍</button>
          <button class="feedback-btn dislike-btn" data-bvid="${escapeHtml(item.bvid)}" data-action="dislike" title="不喜欢">👎</button>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    // --- Agent search history ---
    function getAgentSearchHistory() {
      try {
        return JSON.parse(localStorage.getItem("agentSearchHistory") || "[]");
      } catch { return []; }
    }

    function saveAgentSearchHistory(query) {
      const q = query.trim();
      if (!q) return;
      let history = getAgentSearchHistory();
      history = history.filter((h) => h !== q);
      history.unshift(q);
      if (history.length > 10) history = history.slice(0, 10);
      try {
        localStorage.setItem("agentSearchHistory", JSON.stringify(history));
      } catch { /* ignore */ }
      renderAgentSearchHistory();
    }

    function renderAgentSearchHistory() {
      const history = getAgentSearchHistory();
      const container = $("#recentSearchTags");
      const hints = $("#agentRecentHints");
      if (!container || !hints) return;
      if (history.length === 0) {
        hints.style.display = "none";
        return;
      }
      hints.style.display = "";
      container.innerHTML = history
        .map((h) => `<span class="agent-hint" data-hint="${escapeHtml(h)}">${escapeHtml(h)}</span>`)
        .join("");
    }

    // --- Implicit feedback: dwell-time tracking (停留时长) ---
    const DWELL_CAP_SECONDS = 1800; // cap at 30 min per view
    const DWELL_MIN_SECONDS = 1;    // ignore accidental clicks

    function getPendingDwell() {
      try { return JSON.parse(sessionStorage.getItem("pendingDwell") || "null"); }
      catch { return null; }
    }

    function setPendingDwell(entry) {
      try { sessionStorage.setItem("pendingDwell", JSON.stringify(entry)); }
      catch { /* ignore */ }
    }

    function clearPendingDwell() {
      try { sessionStorage.removeItem("pendingDwell"); }
      catch { /* ignore */ }
    }

    function sendDwellReport(bvid, dwellSeconds, useBeacon) {
      if (!bvid || dwellSeconds < DWELL_MIN_SECONDS) return;
      const payload = JSON.stringify({ bvid, dwell_seconds: Math.round(dwellSeconds) });
      if (useBeacon && navigator.sendBeacon) {
        try {
          navigator.sendBeacon(ENDPOINTS.viewDwell, new Blob([payload], { type: "application/json" }));
          return;
        } catch { /* fall through to fetch */ }
      }
      void requestJson(ENDPOINTS.viewDwell, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
      }).catch(() => {});
    }

    // Settle the pending view: compute dwell since open and report it.
    function finalizePendingDwell(useBeacon = false) {
      const pending = getPendingDwell();
      if (!pending || !pending.openedAt) { clearPendingDwell(); return; }
      clearPendingDwell();
      const dwellSeconds = Math.min((Date.now() - pending.openedAt) / 1000, DWELL_CAP_SECONDS);
      sendDwellReport(pending.bvid, dwellSeconds, useBeacon);
    }

    // Record the view immediately, then track dwell until user returns.
    function beginDwellTracking(item) {
      if (!item || !item.bvid) return;
      finalizePendingDwell();
      void requestJson(ENDPOINTS.viewRecord, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bvid: item.bvid,
          title: item.title || "",
          source_platform: item.source_platform || item.platform || "",
          topic_group: item.topic_group || item.topic || item.topic_label || "",
          content_url: item.content_url || "",
          up_name: item.up_name || item.up || "",
          quality_score: item.quality_score || 0,
        }),
      }).catch(() => {});
      setPendingDwell({ bvid: item.bvid, openedAt: Date.now() });
    }

    // User returns to this tab → settle the pending dwell.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") finalizePendingDwell();
    });
    // Page closed with a pending view → best-effort beacon report.
    window.addEventListener("pagehide", () => finalizePendingDwell(true));

    function sendFeedback(bvid, action, item) {
      const payload = { bvid, action };
      if (item) {
        payload.source_platform = item.source_platform || "";
        payload.title = item.title || "";
        payload.topic_group = item.topic_group || "";
        payload.body_text = (item.body_text || "").slice(0, 200);
      }
      requestJson(ENDPOINTS.userFeedback, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      }).then((res) => {
        if (res && res.ok) {
          // Toggle visual state
          const btn = document.querySelector(`.feedback-btn[data-bvid="${bvid}"][data-action="${action}"]`);
          if (btn) btn.classList.add("is-active");
          // Also remove the opposite active state
          const opposite = action === "like" ? "dislike" : "like";
          const oppBtn = document.querySelector(`.feedback-btn[data-bvid="${bvid}"][data-action="${opposite}"]`);
          if (oppBtn) oppBtn.classList.remove("is-active");
        }
      }).catch(() => {});
    }

    function removeFeedback(bvid, action) {
      requestJson(`${ENDPOINTS.userFeedback}?bvid=${encodeURIComponent(bvid)}&action=${action}`, {
        method: "DELETE",
      }).then((res) => {
        if (res && res.ok) {
          const btn = document.querySelector(`.feedback-btn[data-bvid="${bvid}"][data-action="${action}"]`);
          if (btn) btn.classList.remove("is-active");
        }
      }).catch(() => {});
    }

    function loadInterestTags() {
      const container = $("#agentInterestTags");
      if (!container) return;
      requestJson(ENDPOINTS.interestTags + "?limit=20", { timeoutMs: 10000 })
        .then((data) => {
          const tags = data?.tags || [];
          if (!tags.length) {
            container.style.display = "none";
            return;
          }
          container.style.display = "";
          container.innerHTML = tags.map((t) => {
            const platforms = t.source_platforms?.length
              ? t.source_platforms.map((p) => platformLabelHtml(p)).join(" ")
              : "";
            return `<span class="interest-tag" title="${t.count} 次点赞${platforms ? ' · ' + t.source_platforms.join(', ') : ''}">
              ${escapeHtml(t.tag)} <small>${t.weight}</small>
              ${platforms ? `<span class="interest-tag-platforms">${platforms}</span>` : ""}
            </span>`;
          }).join("");
        })
        .catch(() => { container.style.display = "none"; });
    }

    function poolExploreCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const author = escapeHtml(item.up_name || item.author_name || "");
      const platform = escapeHtml(item.source_platform || "");
      const platLabel = platformLabelHtml(platform);
      const status = item.pool_status || "";
      const score = item.quality_score ? item.quality_score.toFixed(3) : "—";
      const topic = escapeHtml(item.topic_group || "");
      return `
        <p class="video-card-title">${title}</p>
        <div class="video-card-meta">
          <span class="pex-badge pex-badge-${platform}">${platLabel}</span>
          ${status ? `<span class="pool-status-badge">${status}</span>` : ""}
          ${author ? `<span class="video-card-author">${author}</span>` : ""}
        </div>
        <div class="video-card-stats" style="margin-top:4px;font-size:11px;color:var(--text-secondary)">
          评分: ${score}${topic ? ` · ${topic}` : ""}
        </div>
        <div class="video-card-actions">
          <button class="feedback-icon-btn" data-action="open" type="button" aria-label="打开原文" title="打开原文">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          </button>
        </div>
      `;
    }

    function openRecommendation(item, card) {
      const url = contentUrl(item);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      trackRecommendationClick(item);
      beginDwellTracking(item);
      const statusLine = card?.querySelector(".status-line");
      if (statusLine) statusLine.textContent = url ? "已打开真实内容链接，点击信号会在后台记录。" : "后端没有返回可打开链接；点击信号会在后台记录。";
      showToast(url ? `打开：${item.title}` : "后端没有返回可打开链接");
    }

    async function submitFeedback(item, feedback_type, note = "") {
      return await requestJsonStrict(ENDPOINTS.feedback, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendation_id: item.id, feedback_type, note }),
        timeoutMs: 30000
      });
    }

    function recommendationRemoveDelay() {
      return isMobileViewport() ? 1000 : 2400;
    }

    function removeRecommendationCard(item, card, message, delayMs = recommendationRemoveDelay()) {
      const key = recommendationKey(item);
      window.setTimeout(() => {
        if (card) card.classList.add("is-removing");
        window.setTimeout(() => {
          state.videos = state.videos.filter((video) => recommendationKey(video) !== key);
          renderAll();
          if (message) showToast(message);
        }, card ? 180 : 0);
      }, card ? delayMs : 0);
    }

    function finishRecommendationFeedback(card, feedbackType = "") {
      if (!card) return;
      delete card.dataset.feedbackPending;
      card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
      const normalized = String(feedbackType || "").trim().toLowerCase();
      if (normalized !== "like") return;
      const button = card.querySelector('[data-action="like"]');
      if (!button) return;
      button.setAttribute("aria-pressed", "true");
      button.classList.add("is-active");
      button.disabled = true;
    }

    const sendIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M4 12 20 4l-5 16-3.2-6.8L4 12Z"/><path d="m11.8 13.2 3.7-3.7"/></svg>';

    function openCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.action = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送");
      button.setAttribute("title", "发送");
      requestAnimationFrame(() => card.querySelector(".comment-field input")?.focus());
    }

    function closeCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.action = "comment";
      button.textContent = "聊一聊";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
    }

    // Collapse an open composer back to the 聊一聊 button when focus leaves it
    // (user clicked 聊一聊 then changed their mind). The typed draft stays in the
    // input, so reopening restores it. Deferred so a click on the send / cancel
    // button — which blurs the input first in some browsers — still wins.
    function autoCollapseComposer(container, event, closeFn) {
      if (!container || !container.classList.contains("is-composing")) return;
      const next = event.relatedTarget;
      if (next && container.contains(next)) return;
      window.setTimeout(() => {
        if (!container.classList.contains("is-composing")) return;
        if (container.contains(document.activeElement)) return;
        closeFn();
      }, 120);
    }

    function openDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button || !state.delight) return;
      shell?.classList.add("is-composing");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.delight = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送");
      button.setAttribute("title", "发送");
      scheduleActivityRailHeightSync();
      requestAnimationFrame(() => $("#delightCommentInput")?.focus());
    }

    function closeDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button) return;
      shell?.classList.remove("is-composing");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.delight = "chat";
      button.textContent = "聊一聊";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
      scheduleActivityRailHeightSync();
    }

    async function handleCardAction(action, item, card) {
      const status = card.querySelector(".status-line");
      if (card.dataset.feedbackPending === "true") return;
      if (action === "open") return openRecommendation(item, card);
      if (action === "comment") { openCardComposer(card); return; }
      if (action === "cancel-comment") { closeCardComposer(card); return; }
      if (action === "watch-later") {
        const btn = card.querySelector('[data-action="watch-later"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "\u7A0D\u540E\u518D\u770B" : "\u53D6\u6D88\u6536\u85CF";
        try {
          const bvid = item.bvid || item.id;
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.watchLater}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.watchLater, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u7A0D\u540E\u518D\u770B" : "\u7A0D\u540E\u518D\u770B";
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (action === "favorite") {
        const btn = card.querySelector('[data-action="favorite"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "\u6536\u85CF" : "\u53D6\u6D88\u6536\u85CF";
        try {
          const bvid = item.bvid || item.id;
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.favorites}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.favorites, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u6536\u85CF" : "\u6536\u85CF";
        } finally {
          btn.disabled = false;
        }
        return;
      }
      card.dataset.feedbackPending = "true";
      card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = true; });
      try {
        if (action === "send-comment") {
          const input = card.querySelector(".comment-field input");
          const note = input.value.trim();
          if (!note) {
            delete card.dataset.feedbackPending;
            card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
            status.textContent = "先写一句想聊的内容，再提交这条反馈。";
            input?.focus();
            return;
          }
          await submitFeedback(item, "comment", note);
          if (input) input.value = "";
          closeCardComposer(card);
          item.feedback_type = "comment";
          status.textContent = "已提交聊天线索，推荐会继续保留在当前列表。";
          finishRecommendationFeedback(card, "comment");
          showToast("已提交聊天线索");
          return;
        }
        const feedbackType = action === "like" ? "like" : action === "dismiss" ? "dismiss" : "dislike";
        await submitFeedback(item, feedbackType);
        const feedbackCopy = {
          like: ["已记录喜欢，推荐会继续保留在当前列表。", "已记录喜欢"],
          dislike: ["已记录不感兴趣，几秒后从当前列表移除。", "已记录不感兴趣"],
          dismiss: ["已忽略这条推荐，几秒后从当前列表移除。", "已忽略推荐"]
        }[feedbackType];
        status.textContent = feedbackCopy[0];
        if (shouldRemoveRecommendationAfterFeedback(feedbackType)) {
          removeRecommendationCard(item, card, feedbackCopy[1]);
          return;
        }
        item.feedback_type = feedbackType;
        finishRecommendationFeedback(card, feedbackType);
        showToast(feedbackCopy[1]);
      } catch (error) {
        delete card.dataset.feedbackPending;
        card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
        status.textContent = configErrorMessage(error?.details) || error?.message || "反馈提交失败，请稍后重试。";
        showToast(status.textContent);
      }
    }

    function renderRail() {
      const profile = state.profile;
      const portraitText = profile?.personality_portrait ? valueList(profile.personality_portrait) : "偏好结构化解释、长视频和跨学科桥接，对“为什么”比“是什么”更敏感。";
      if ($("#profilePortrait")) $("#profilePortrait").textContent = portraitText;
      if ($("#mobileProfilePortrait")) $("#mobileProfilePortrait").textContent = portraitText;
      const chips = [
        ...asArray(profile?.core_traits),
        ...asArray(profile?.cognitive_style),
        ...asArray(profile?.likes).map((item) => typeof item === "object" ? item.domain || item.name || item.title || valueList(item) : item)
      ].map(valueList).filter((text) => text && text.length <= 10 && !/[，。；、,.]/.test(text)).slice(0, 8);
      const chipTexts = chips.length ? chips : ["长解释", "机制控", "跨平台", "反信息茧房"];
      ["#profileChips", "#mobileProfileChips"].forEach((selector) => {
        const target = $(selector);
        if (!target) return;
        target.replaceChildren(...chipTexts.map((text) => {
          const chip = document.createElement("span"); chip.className = "chip"; chip.textContent = text; return chip;
        }));
      });
      const mbtiText = formatPersonalityType(profile?.mbti || profile?.personality_type) || "—";
      const opennessText = formatPercent(profile?.exploration_openness ?? profile?.openness) || "—";
      const depthText = formatPercent(profile?.style?.depth_preference ?? profile?.depth_preference ?? profile?.deep_preference ?? profile?.long_video_affinity) || "—";
      [["#railMbti", mbtiText], ["#mobileRailMbti", mbtiText], ["#railOpenness", opennessText], ["#mobileRailOpenness", opennessText], ["#railDepth", depthText], ["#mobileRailDepth", depthText]].forEach(([selector, value]) => {
        const target = $(selector);
        if (target) target.textContent = value;
      });
      const activityItems = state.activityItems.length ? state.activityItems : asArray(state.activity?.items);
      const activityHtml = activityItems.length
        ? activityItems.slice(0, 5).map((item) => `<div class="activity-item"><p>${escapeHtml(typeof item === "object" ? item.summary || item.detail || item.kind || valueList(item) : item)}</p></div>`).join("")
        : `<div class="empty-state">还没有新的动态；实时流收到 activity.added 后会自动刷新。</div>`;
      ["#activityList", "#mobileActivityList"].forEach((selector) => {
        const target = $(selector);
        if (target) target.innerHTML = activityHtml;
      });
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(getRenderableMessages().length);
    }

    function renderActivityHistory() {
      const list = $("#activityHistory");
      if (!list) return;
      if (!state.activityItems.length) {
        list.innerHTML = `<div class="empty-state">暂无历史动态。</div>`;
      } else {
        list.innerHTML = state.activityItems.map((item) => `<article class="activity-item"><p class="eyebrow">${escapeHtml(item.kind || "activity")}</p><h3>${escapeHtml(item.summary || "后台动态")}</h3><p class="video-meta">${escapeHtml(item.detail || item.created_at || "")}</p></article>`).join("");
      }
      const more = $("#activityMoreBtn");
      if (more) more.disabled = !state.activityHasMore;
    }

    async function loadActivityPage({ reset = false } = {}) {
      const cursor = reset ? "" : state.activityCursor;
      const query = new URLSearchParams({ limit: "10" });
      if (cursor) query.set("before", cursor);
      const payload = await requestJson(`${ENDPOINTS.activityFeed}?${query.toString()}`);
      if (!payload) { showToast("动态加载失败：后端不可用"); return; }
      const items = Array.isArray(payload.items) ? payload.items : [];
      state.activity = payload;
      state.activityItems = reset ? items : state.activityItems.concat(items);
      state.activityCursor = payload.next_cursor || payload.next || "";
      state.activityHasMore = Boolean(payload.has_more && state.activityCursor);
      renderRail();
      renderActivityHistory();
    }

    function formatPercent(value) {
      if (value == null || value === "") return "";
      if (typeof value === "string" && value.trim().endsWith("%")) return value.trim();
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      const normalized = Math.abs(number) <= 1 ? number * 100 : number;
      return `${Math.round(normalized)}%`;
    }

    function score01(value, fallback = 0.5) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.max(0, Math.min(1, Math.abs(number) <= 1 ? number : number / 100));
    }

    function formatPersonality(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      const type = value.type || value.mbti || value.name || value.label;
      const confidence = formatPercent(value.confidence);
      if (type && confidence) return `${type}（置信度 ${confidence}）`;
      if (type) return String(type);
      return valueList(value);
    }

    function formatPersonalityType(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      return String(value.type || value.mbti || value.name || value.label || "");
    }

    function formatProfileObject(value) {
      const preferred = value.domain || value.summary || value.name || value.title || value.label || value.value || value.text || value.reason || value.hypothesis || value.observation;
      if (preferred) return String(preferred);
      return Object.entries(value)
        .filter(([, val]) => val != null && val !== "")
        .map(([key, val]) => {
          if (key === "confidence") return `置信度 ${formatPercent(val)}`;
          if (key === "dimensions" && typeof val === "object") return "维度已在 MBTI 图表中展示";
          return `${key}: ${valueList(val)}`;
        })
        .filter(Boolean)
        .join(" / ");
    }

    function valueList(value) {
      if (value == null || value === "") return "";
      if (Array.isArray(value)) return value.map((item) => valueList(item)).filter(Boolean).join("、");
      if (typeof value === "object") return formatProfileObject(value);
      return String(value);
    }

    function asArray(value) {
      if (value == null || value === "") return [];
      if (Array.isArray(value)) return value;
      if (typeof value === "object") {
        if (Array.isArray(value.items)) return value.items;
        if (Array.isArray(value.domains)) return value.domains;
        if (Array.isArray(value.values)) return value.values;
        return Object.entries(value).map(([key, val]) => {
          if (val == null || val === "" || val === false) return "";
          if (val === true) return key;
          if (typeof val === "object" && !Array.isArray(val)) return { name: key, ...val };
          return `${key}: ${valueList(val)}`;
        }).filter(Boolean);
      }
      return String(value).split(/[、,\n]+/).map((item) => item.trim()).filter(Boolean);
    }

    function formatTimeAgo(dateStr) {
      if (!dateStr) return "";
      try {
        const date = new Date(dateStr.replace(" ", "T") + (dateStr.includes("Z") ? "" : "Z"));
        if (isNaN(date.getTime())) return dateStr;
        const now = new Date();
        const diffMs = now.getTime() - date.getTime();
        const diffMin = Math.floor(diffMs / 60000);
        const diffHour = Math.floor(diffMs / 3600000);
        const diffDay = Math.floor(diffMs / 86400000);
        if (diffMin < 1) return "刚刚";
        if (diffMin < 60) return `${diffMin} 分钟前`;
        if (diffHour < 24) return `${diffHour} 小时前`;
        if (diffDay < 7) return `${diffDay} 天前`;
        const month = date.getMonth() + 1;
        const day = date.getDate();
        return `${month}月${day}日`;
      } catch {
        return dateStr;
      }
    }

    function firstValue(...values) {
      return values.find((value) => value != null && value !== "" && (!Array.isArray(value) || value.length));
    }

    function chipsHtml(value, fallback = "这部分还在慢慢补。") {
      const items = Array.isArray(value) ? value.map(valueList).filter(Boolean) : valueList(value).split("、").filter(Boolean);
      if (!items.length) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-chip-list">${items.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>`;
    }

    function paragraphsHtml(value, fallback = "这部分还在观察，先不急着下结论。") {
      const text = valueList(value);
      if (!text) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-portrait-copy">${String(text).split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => `<p class="video-meta">${escapeHtml(line)}</p>`).join("")}</div>`;
    }

    function profileItem(title, html, extraClass = "") {
      return `<article class="profile-item ${extraClass}"><h3>${escapeHtml(title)}</h3>${html}</article>`;
    }

    function profileLayer(label, items) {
      const body = items.filter(Boolean).join("");
      if (!body) return "";
      return `<div class="profile-layer"><div class="profile-layer-label">${escapeHtml(label)}</div>${body}</div>`;
    }

    function dimensionData(mbti, key) {
      if (!mbti?.dimensions) return null;
      return mbti.dimensions[key] || mbti.dimensions[`${key[0]}_${key[1]}`] || mbti.dimensions[key.toLowerCase()] || mbti.dimensions[`${key[0].toLowerCase()}_${key[1].toLowerCase()}`];
    }

    function normalizedPole(rawPole, key) {
      const pole = String(rawPole || "").trim().toUpperCase();
      if (pole.includes(key[0])) return key[0];
      if (pole.includes(key[1])) return key[1];
      return "";
    }

    function mbtiAxisHtml(mbti, config) {
      const dim = dimensionData(mbti, config.key);
      if (!dim) return "";
      const pole = normalizedPole(dim.pole, config.key) || config.key[1];
      const strength = score01(dim.strength, 0.5);
      const marker = pole === config.key[0] ? 50 - strength * 50 : 50 + strength * 50;
      const start = Math.min(50, marker);
      const width = Math.abs(marker - 50);
      return `<div class="mbti-axis">
        <span class="mbti-axis-side${pole === config.key[0] ? " is-active" : ""}">${config.left}<span> ${config.leftName}</span></span>
        <div class="mbti-axis-track" style="--start:${start}%;--width:${width}%;--marker:${marker}%"><span class="mbti-axis-fill"></span><span class="mbti-axis-marker"></span></div>
        <span class="mbti-axis-side${pole === config.key[1] ? " is-active" : ""}">${config.right}<span> ${config.rightName}</span></span>
        <span class="mbti-axis-pct">${escapeHtml(pole)} ${Math.round(strength * 100)}%</span>
      </div>`;
    }

    function mbtiHtml(value) {
      if (!value) return `<p class="video-meta">MBTI 还没推断出来，再多看一阵。</p>`;
      if (typeof value !== "object") return `<p class="video-meta">${escapeHtml(value)}</p>`;
      const type = value.type || value.mbti || value.name || "—";
      const axes = [
        { key: "EI", left: "E", right: "I", leftName: "外向", rightName: "内向" },
        { key: "SN", left: "S", right: "N", leftName: "实感", rightName: "直觉" },
        { key: "TF", left: "T", right: "F", leftName: "思考", rightName: "情感" },
        { key: "JP", left: "J", right: "P", leftName: "判断", rightName: "知觉" }
      ].map((config) => mbtiAxisHtml(value, config)).filter(Boolean).join("");
      return `<div class="mbti-block"><div class="mbti-type-row"><span class="mbti-type-label">${escapeHtml(type)}</span>${value.confidence ? `<span class="mbti-confidence">整体可信度 ${formatPercent(value.confidence)}</span>` : ""}</div>${axes ? `<div class="mbti-dimensions">${axes}</div>` : ""}</div>`;
    }

    function interestTreeHtml(value, fallback) {
      const domains = asArray(value);
      if (!domains.length) return `<p class="video-meta">${escapeHtml(fallback)}</p>`;
      return `<div class="profile-interest-tree">${domains.map((item) => {
        if (typeof item !== "object") return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${escapeHtml(item)}</span></div></div>`;
        const title = item.domain || item.name || item.title || valueList(item);
        const weight = item.weight != null ? `<span class="profile-domain-weight">${formatPercent(item.weight)}</span>` : "";
        const specifics = asArray(item.specifics).map((s) => s?.name || s?.label || valueList(s)).filter(Boolean);
        return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${escapeHtml(title)}</span>${weight}</div>${specifics.length ? `<div class="profile-chip-list">${specifics.map((s) => `<span class="chip">${escapeHtml(s)}</span>`).join("")}</div>` : ""}</div>`;
      }).join("")}</div>`;
    }

    function meterHtml(label, value) {
      const score = score01(value);
      return `<div class="profile-meter"><div class="profile-meter-head"><span>${escapeHtml(label)}</span><strong>${Math.round(score * 100)}%</strong></div><div class="profile-meter-track"><div class="profile-meter-fill" style="width:${score * 100}%"></div></div></div>`;
    }

    function styleHtml(style) {
      if (!style || typeof style !== "object" || Array.isArray(style)) return paragraphsHtml(style, "内容口味还在继续归拢。");
      const textRows = [
        ["偏好时长", style.preferred_duration],
        ["偏好节奏", style.preferred_pace]
      ].filter(([, value]) => value).map(([label, value]) => `<div class="profile-context-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      const bars = [
        ["质量敏感度", style.quality_sensitivity],
        ["幽默偏好", style.humor_preference],
        ["深度偏好", style.depth_preference]
      ].filter(([, value]) => value != null).map(([label, value]) => meterHtml(label, value)).join("");
      return `<div class="profile-bars profile-style-bars">${textRows}${bars}</div>`;
    }

    function contextHtml(context) {
      if (!context || typeof context !== "object" || Array.isArray(context)) return paragraphsHtml(context, "使用场景还在继续观察。");
      const rows = [
        ["工作日", context.weekday_patterns],
        ["周末", context.weekend_patterns],
        ["一天中的时段", context.time_of_day_patterns],
        ["观看会话", context.session_type]
      ].filter(([, value]) => value).map(([label, value]) => `<div class="profile-context-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
      return rows ? `<div class="profile-context">${rows}</div>` : paragraphsHtml("", "使用场景还在继续观察。");
    }

    function speculativeHtml(items, options = {}) {
      const isAvoidance = options.kind === "avoidance";
      const probeType = isAvoidance ? "avoidance.probe" : "interest.probe";
      const list = asArray(items).filter((item) => {
        if (typeof item !== "object") return !state.handledProbeKeys.has(probeKey(probeType, item));
        const domain = item.domain || item.name || item.title;
        if (!domain || state.handledProbeKeys.has(probeKey(probeType, domain))) return false;
        const status = String(item.status || "active").trim().toLowerCase();
        return status === "active" || status === "pending";
      });
      if (!list.length) return `<p class="video-meta">${isAvoidance ? "阿B 暂时没有待确认的避雷方向。" : "阿B 还没有正在试探的新方向。"}</p>`;
      const statusLabels = { active: "待确认", pending: "待观察", confirmed: "已确认", deprecated: "已弃", rejected: "已排除" };
      const fallbackTitle = isAvoidance ? "猜测避雷" : "猜测兴趣";
      return `<div class="speculative-list">${list.map((item) => {
        if (typeof item !== "object") return `<div class="speculative-item"><div class="spec-header"><span class="spec-domain">${escapeHtml(item)}</span></div></div>`;
        const domain = item.domain || item.name || item.title || fallbackTitle;
        const status = item.status || "active";
        const count = Number(item.confirmation_count ?? 0);
        const threshold = Number(item.confirmation_threshold ?? 3);
        const progress = `${count}/${threshold} 次确认`;
        const confidence = score01(item.confidence, 0);
        const specifics = asArray(item.specifics).map((s) => ({
          name: s?.name || s?.label || valueList(s),
          count: Number(s?.confirmation_count ?? 0)
        })).filter((s) => s.name);
        return `<div class="speculative-item is-status-${escapeHtml(status)}" data-spec-domain="${escapeHtml(domain)}">
          <div class="spec-header">
            <span class="spec-domain">${escapeHtml(domain)}</span>
            ${statusLabels[status] ? `<span class="spec-status">${escapeHtml(statusLabels[status])}</span>` : ""}
            <span class="spec-progress">${escapeHtml(progress)}</span>
          </div>
          ${confidence > 0 ? `<div class="spec-confidence-row"><div class="spec-confidence-bar"><div class="spec-confidence-fill" style="width:${Math.round(confidence * 100)}%"></div></div><span class="spec-confidence-label">置信度 ${Math.round(confidence * 100)}%</span></div>` : ""}
          ${item.reason ? `<p class="video-meta">${escapeHtml(item.reason)}</p>` : ""}
          ${specifics.length ? `<div class="spec-specifics">${specifics.map((s) => `<span class="spec-specific-chip">${escapeHtml(s.name)}${s.count > 0 ? `<span class="spec-specific-count">${s.count}</span>` : ""}</span>`).join("")}</div>` : ""}
          <p class="spec-help">${isAvoidance ? `置信度表示阿B认为你会避开这个方向的把握；确认次数来自后端累计的避雷确认信号，达到 ${threshold} 次后会进入更稳定的避雷画像。` : `置信度表示阿B认为你会喜欢这个方向的把握；确认次数来自后端累计的正向确认信号（包括但不限于这里的“喜欢”），达到 ${threshold} 次后会进入更稳定的兴趣画像。`}</p>
          ${status === "active" && domain ? `<div class="spec-actions"><button class="probe-btn is-confirm" type="button" data-spec-response="confirm" data-spec-type="${isAvoidance ? "avoidance.probe" : "interest.probe"}">${isAvoidance ? "确实不喜欢" : "喜欢"}</button><button class="probe-btn is-reject" type="button" data-spec-response="reject" data-spec-type="${isAvoidance ? "avoidance.probe" : "interest.probe"}">${isAvoidance ? "不是" : "不喜欢"}</button></div>` : ""}
        </div>`;
      }).join("")}</div>`;
    }

    function memoryHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">阿B 还在继续观察，过一阵这里会更具体。</p>`;
      return `<div class="profile-card-list">${list.slice(0, 8).map((item) => {
        if (typeof item !== "object") return `<div class="profile-memory"><p class="video-meta">${escapeHtml(item)}</p></div>`;
        const meta = item.sourceLabel || item.source_label || item.source || item.created_at || "";
        const details = asArray([item.contextLine || item.context_line, item.impact, item.reasoning, item.evidence]).filter(Boolean).map((line) => `<p class="video-meta">${escapeHtml(valueList(line))}</p>`).join("");
        return `<div class="profile-memory"><div class="profile-memory-head"><strong>${escapeHtml(item.summary || item.title || "近期记忆")}</strong>${meta ? `<span class="profile-memory-meta">${escapeHtml(meta)}</span>` : ""}</div>${details}</div>`;
      }).join("")}</div>`;
    }

    function insightsHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">当前没有需要特别展示的活跃洞察。</p>`;
      return `<div class="profile-card-list">${list.map((item, idx) => {
        if (typeof item !== "object") return `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item)}</span></div></div>`;
        const evidenceItems = asArray(item.evidence).map((e) => String(e || "").trim()).filter(Boolean);
        const evidenceHtml = evidenceItems.length
          ? `<details class="profile-insight-evidence"><summary>证据 · ${evidenceItems.length} 条</summary><ul>${evidenceItems.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul></details>`
          : "";
        const hypothesis = item.hypothesis || "";
        const actions = hypothesis
          ? `<div class="insight-actions"><button class="pill-btn" type="button" data-insight-action="confirm" data-insight-idx="${idx}">准</button><button class="pill-btn" type="button" data-insight-action="reject" data-insight-idx="${idx}">不准</button></div>`
          : "";
        return `<div class="profile-insight" data-insight-idx="${idx}"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(hypothesis || item.observation || valueList(item))}</span><span class="profile-confidence">${formatPercent(item.confidence)}</span></div>${evidenceHtml}${item.validated ? `<p class="video-meta">已验证</p>` : ""}${actions}</div>`;
      }).join("")}</div>`;
    }

    function awarenessHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">近期观察还在沉淀。</p>`;
      return `<div class="profile-card-list">${list.map((item) => typeof item === "object" ? `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item.observation || valueList(item))}</span>${item.date ? `<span class="profile-confidence">${escapeHtml(item.date)}</span>` : ""}</div>${item.trend ? `<p class="video-meta">趋势：${escapeHtml(item.trend)}</p>` : ""}${item.emotion_guess ? `<p class="video-meta">情绪猜测：${escapeHtml(item.emotion_guess)}</p>` : ""}</div>` : `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${escapeHtml(item)}</span></div></div>`).join("")}</div>`;
    }

    function loadUserFeedbackAndViews() {
      // Load interest tags from likes
      requestJson(ENDPOINTS.interestTags + "?limit=20", { timeoutMs: 10000 })
        .then((data) => {
          const tags = data?.tags || [];
          const container = $("#profileInterestTagsContainer");
          const tagsEl = $("#profileInterestTags");
          if (!container || !tagsEl) return;
          if (!tags.length) {
            container.style.display = "none";
            return;
          }
          container.style.display = "";
          tagsEl.innerHTML = tags.map((t) => {
            const platforms = t.source_platforms?.length
              ? t.source_platforms.map((p) => platformLabelHtml(p)).join(" ")
              : "";
            return `<span class="interest-tag" title="${t.count} 次点赞${platforms ? ' · ' + t.source_platforms.join(', ') : ''}">
              ${escapeHtml(t.tag)} <small>${t.weight}</small>
              ${platforms ? `<span class="interest-tag-platforms">${platforms}</span>` : ""}
            </span>`;
          }).join("");
        })
        .catch(() => {
          const container = $("#profileInterestTagsContainer");
          if (container) container.style.display = "none";
        });

      // Load recent view history
      requestJson(ENDPOINTS.viewHistory + "?limit=30", { timeoutMs: 10000 })
        .then((views) => {
          const el = $("#profileViewHistory");
          if (!el) return;
          if (!views || !views.length) {
            el.innerHTML = `<p class="video-meta">还没有浏览记录，去 Agent 推荐页面逛逛吧。</p>`;
            return;
          }
          el.innerHTML = `<div class="view-history-list">${views.map((v) => {
            const title = escapeHtml(v.title || "无标题");
            const platform = platformLabelHtml(v.source_platform || "");
            const author = escapeHtml(v.up_name || "");
            const time = v.viewed_at ? formatTimeAgo(v.viewed_at) : "";
            const url = v.content_url || "";
            const topic = escapeHtml(v.topic_group || "");
            return `<div class="view-history-item">
              <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="view-history-link">${title}</a>
              <div class="view-history-meta">
                ${platform ? `<span class="pex-badge pex-badge-${v.source_platform || ''}">${platform}</span>` : ""}
                ${author ? `<span class="view-history-author">${escapeHtml(author)}</span>` : ""}
                ${topic ? `<span class="view-history-topic">${escapeHtml(topic)}</span>` : ""}
                ${time ? `<span class="view-history-time">${escapeHtml(time)}</span>` : ""}
              </div>
            </div>`;
          }).join("")}</div>`;
        })
        .catch(() => {
          const el = $("#profileViewHistory");
          if (el) el.innerHTML = `<p class="video-meta">浏览记录加载失败。</p>`;
        });
    }

    function updateProfileMemoryButton() {
      const button = $("#profileMemoryMoreBtn");
      if (!button) return;
      button.hidden = !state.profileCognitionHasMore;
      button.disabled = !state.profileCognitionHasMore;
    }

    function syncProfileCognitionState(profile) {
      const cursor = profile?.next_cognition_cursor || profile?.next_cursor || "";
      state.profileCognitionCursor = cursor;
      state.profileCognitionHasMore = Boolean(profile?.has_more_cognition_updates && cursor);
      updateProfileMemoryButton();
    }

    function renderProfileDetails() {
      const profile = state.profile;
      if (!profile) {
        $("#profileDetails").innerHTML = profileItem("画像还没攒起来", paragraphsHtml("后端未连接或画像尚未初始化。连接 FastAPI 后会展示完整画像。"));
        state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      if (state.editingProfile) {
        $("#profileDetails").innerHTML = renderProfileEditPanel();
        bindProfileEditActions();
        state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      syncProfileCognitionState(profile);
      // Load user feedback and view history for profile page
      loadUserFeedbackAndViews();
      const html = [
        profileItem("这会儿的你", paragraphsHtml(profile.personality_portrait || profile.summary), "profile-portrait-block"),
        profileLayer("Core — 比较稳定的底色", [
          profileItem("核心特质", chipsHtml(profile.core_traits, "这部分还在慢慢补。")),
          profileItem("深层需求", chipsHtml(profile.deep_needs, "这块还要再多看一点。")),
          profileItem("MBTI / 人格推断", mbtiHtml(firstValue(profile.mbti, profile.personality_type)))
        ]),
        profileLayer("Values — 你在内容里长期在找什么", [
          profileItem("价值偏好", chipsHtml(firstValue(profile.values, profile.value_preferences), "价值偏好还在继续归拢。")),
          profileItem("内在驱动力", chipsHtml(firstValue(profile.motivational_drivers, profile.intrinsic_drives, profile.motivations), "这块还要再多看一点。"))
        ]),
        profileLayer("Interest — 你最近在看什么", [
          profileItem("感兴趣的方向", interestTreeHtml(profile.likes, "再刷一阵，这里会更准。")),
          profileItem("明显会避开", interestTreeHtml(profile.dislikes, "这块还在继续确认，先别急着下死结论。")),
          profileItem("常看的 UP 主", chipsHtml(firstValue(profile.favorite_up_users, profile.favorite_creators, profile.creators, profile.up_names), "常看的 UP 主还在统计。"))
        ]),
        profileLayer("Role — 这阵子的状态", [
          profileItem("大致处在什么阶段", paragraphsHtml(profile.life_stage, "这块还在观察，先不急着定论。")),
          profileItem("这阵子更像在经历什么", paragraphsHtml(firstValue(profile.current_phase, profile.current_stage), "这阵子的变化还在继续看。"))
        ]),
        profileLayer("Surface — 你怎么看内容", [
          profileItem("认知风格", chipsHtml(profile.cognitive_style, "这层还在继续归拢。")),
          profileItem("内容口味", styleHtml(firstValue(profile.style, profile.content_style, profile.content_preferences))),
          profileItem("使用场景", contextHtml(firstValue(profile.context, profile.current_context))),
          profileItem("探索开放度", meterHtml("愿意走出既有兴趣圈", firstValue(profile.exploration_openness, profile.openness)))
        ]),
        profileLayer("Feedback — 显式反馈和浏览记录", [
          profileItem("点赞标签聚合", `<div id="profileInterestTagsContainer" style="display:none;"><div id="profileInterestTags" class="profile-interest-tags"></div></div>`, "feedback-section"),
          profileItem("近期浏览记录", `<div id="profileViewHistory" class="profile-view-history">正在加载...</div>`, "feedback-section")
        ]),
        profileLayer("Speculate — 阿B 在试探的方向", [
          profileItem("猜测兴趣", speculativeHtml(profile.speculative_interests)),
          profileItem("猜测避雷", speculativeHtml(profile.speculative_avoidances, { kind: "avoidance" })),
          profileItem("阿B 最近新记住了什么", memoryHtml(firstValue(profile.recent_cognition_updates, profile.recent_memories)))
        ]),
        profileLayer("Signals — 正在推断中", [
          profileItem("当前活跃的洞察", insightsHtml(profile.active_insights)),
          profileItem("近期观察到的", awarenessHtml(profile.recent_awareness))
        ])
      ].join("");
      const profileEditBar = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="enter">✏️ 编辑画像</button></div>`;
      $("#profileDetails").innerHTML = profileEditBar + html;
      bindSpeculativeActions();
      bindInsightActions();
      bindProfileEditToggle();
    }

    function bindInsightActions() {
      document.querySelectorAll("[data-insight-action]").forEach((button) => {
        button.addEventListener("click", () => respondInsightFeedback(button));
      });
    }

    async function respondInsightFeedback(button) {
      const signal = button.dataset.insightAction;
      const idx = Number(button.dataset.insightIdx);
      const insight = state.profile?.active_insights?.[idx];
      const hypothesis = insight && insight.hypothesis;
      if (!signal || !hypothesis) return;
      const row = button.closest(".profile-insight");
      row?.querySelectorAll("[data-insight-action]").forEach((btn) => { btn.disabled = true; });
      try {
        await requestJson(ENDPOINTS.insightFeedback, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hypothesis, signal }),
        });
        showToast(signal === "confirm" ? "已确认这条洞察" : "已记下，会少推这类");
        setTimeout(() => { void refreshProfile(); }, 1200);
      } catch (error) {
        row?.querySelectorAll("[data-insight-action]").forEach((btn) => { btn.disabled = false; });
        showToast("没存上，稍后再试");
      }
    }

    // ── Editable profile (Phase 3, desktop) ──────────────────────
    const PROFILE_EDIT_LABELS = {
      personality_portrait: "人格素描",
      "core.core_traits": "核心特质",
      "core.deep_needs": "深层需求",
      "values_layer.values": "价值偏好",
      "values_layer.motivational_drivers": "内在驱动力",
      likes: "感兴趣的方向",
      dislikes: "明显会避开",
      "interest.favorite_up_users": "常看的 UP 主",
      "role.life_stage": "大致处在什么阶段",
      "role.current_phase": "这阵子更像在经历什么",
      "surface.cognitive_style": "认知风格",
      "surface.exploration_openness": "探索开放度",
      "surface.style.quality_sensitivity": "质量敏感度",
      "surface.style.humor_preference": "幽默偏好",
      "surface.style.depth_preference": "深度偏好"
    };
    const PROFILE_EDIT_ORDER = [
      "personality_portrait",
      "core.core_traits",
      "core.deep_needs",
      "values_layer.values",
      "values_layer.motivational_drivers",
      "likes",
      "dislikes",
      "interest.favorite_up_users",
      "role.life_stage",
      "role.current_phase",
      "surface.cognitive_style",
      "surface.exploration_openness",
      "surface.style.quality_sensitivity",
      "surface.style.humor_preference",
      "surface.style.depth_preference"
    ];

    function bindProfileEditToggle() {
      const btn = document.querySelector('#profileDetails [data-profile-edit-toggle="enter"]');
      if (btn) btn.addEventListener("click", () => { void enterProfileEdit(); });
    }

    async function enterProfileEdit() {
      state.editingProfile = true;
      state.profileEditState = null;
      renderProfileDetails();
      state.profileEditState = await requestJson(ENDPOINTS.profileEditState);
      renderProfileDetails();
    }

    async function exitProfileEdit() {
      state.editingProfile = false;
      state.profileEditState = null;
      const fresh = await requestJson(ENDPOINTS.profile);
      if (fresh) state.profile = fresh;
      renderProfileDetails();
    }

    async function applyProfileEdit(payload) {
      const res = await requestJson(ENDPOINTS.profileEdit, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res && res.edit_state && res.edit_state.initialized) {
        state.profileEditState = res.edit_state;
      } else {
        const refreshed = await requestJson(ENDPOINTS.profileEditState);
        if (refreshed) state.profileEditState = refreshed;
        if (!res) showToast("修改未保存：请检查输入或后端状态");
      }
      renderProfileDetails();
    }

    function profileEditTextField(path, label, field) {
      const pinned = Boolean(field.pinned);
      const rows = path === "personality_portrait" ? 4 : 2;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <textarea class="edit-text-input" data-edit-text="${escapeHtml(path)}" rows="${rows}">${escapeHtml(field.value || "")}</textarea>
          ${field.ai_suggestion ? `<p class="edit-drift-hint">AI 当前想更新为：${escapeHtml(field.ai_suggestion)}</p>` : ""}
          <div class="edit-field-actions">
            <button class="pill-btn primary" type="button" data-edit-save="${escapeHtml(path)}">保存</button>
            ${pinned ? `<button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button>` : ""}
          </div>
        </div>`;
    }

    function profileEditScalarField(path, label, field) {
      const pinned = Boolean(field.pinned);
      const pct = Math.round((Number(field.value) || 0) * 100);
      const aiPct = typeof field.ai_suggestion === "number" ? Math.round(field.ai_suggestion * 100) : null;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-scalar-row">
            <input class="edit-scalar-input" type="range" min="0" max="100" step="1" value="${pct}" data-edit-scalar="${escapeHtml(path)}" />
            <span class="edit-scalar-value" data-edit-scalar-value="${escapeHtml(path)}">${pct}%</span>
          </div>
          ${aiPct !== null ? `<p class="edit-drift-hint">AI 当前想更新为：${aiPct}%</p>` : ""}
          <div class="edit-field-actions">
            <button class="pill-btn primary" type="button" data-edit-save-scalar="${escapeHtml(path)}">保存</button>
            ${pinned ? `<button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button>` : ""}
          </div>
        </div>`;
    }

    function profileEditListField(path, label, field) {
      const items = Array.isArray(field.items) ? field.items : [];
      const edited = (field.added?.length || 0) > 0 || (field.removed?.length || 0) > 0;
      const chips = items.length
        ? items.map((it) => `<span class="edit-chip">${escapeHtml(it)}<button class="edit-chip-remove" type="button" data-edit-remove="${escapeHtml(path)}" data-edit-value="${escapeHtml(it)}">✕</button></span>`).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-chip-list">${chips}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${escapeHtml(path)}" placeholder="添加一项" />
            <button class="pill-btn" type="button" data-edit-add="${escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function profileEditInterestField(path, label, field) {
      const domains = Array.isArray(field.domains) ? field.domains : [];
      const edited = (field.removed_domains?.length || 0) > 0 || domains.some((d) => d?.user_added);
      const chips = domains.length
        ? domains.map((d) => `<span class="edit-chip">${escapeHtml(d.domain)}${d.user_added ? " ＋" : ""}<button class="edit-chip-remove" type="button" data-edit-remove="${escapeHtml(path)}" data-edit-value="${escapeHtml(d.domain)}">✕</button></span>`).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      const placeholder = path === "dislikes" ? "添加要避开的领域" : "添加感兴趣的领域";
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-chip-list">${chips}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${escapeHtml(path)}" placeholder="${escapeHtml(placeholder)}" />
            <button class="pill-btn" type="button" data-edit-add="${escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function renderProfileEditPanel() {
      const editState = state.profileEditState;
      let html = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="exit">✓ 完成</button></div>`;
      if (!editState) {
        html += `<p class="video-meta">加载中…</p>`;
        return html;
      }
      if (!editState.initialized || !editState.fields) {
        html += `<p class="video-meta">画像还没攒起来，回到首页推荐区点「开始初始化」后再回来编辑。</p>`;
        return html;
      }
      html += `<p class="video-meta profile-edit-note">标签 / 兴趣类增删即时生效；文本与滑杆类改完点「保存」才生效。改动都不会被后续自动重建覆盖，删错了点「恢复 AI 建议」即可。</p>`;
      for (const path of PROFILE_EDIT_ORDER) {
        const field = editState.fields[path];
        if (!field || typeof field !== "object") continue;
        const label = PROFILE_EDIT_LABELS[path] || path;
        if (field.type === "text") html += profileEditTextField(path, label, field);
        else if (field.type === "scalar") html += profileEditScalarField(path, label, field);
        else if (field.type === "list") html += profileEditListField(path, label, field);
        else if (field.type === "interest") html += profileEditInterestField(path, label, field);
      }
      return html;
    }

    function bindProfileEditActions() {
      const root = $("#profileDetails");
      if (!root) return;
      root.querySelector('[data-profile-edit-toggle="exit"]')?.addEventListener("click", () => { void exitProfileEdit(); });
      root.querySelectorAll("[data-edit-remove]").forEach((btn) => {
        btn.addEventListener("click", () => void applyProfileEdit({ target: btn.dataset.editRemove, op: "remove", value: btn.dataset.editValue }));
      });
      root.querySelectorAll("[data-edit-reset]").forEach((btn) => {
        btn.addEventListener("click", () => void applyProfileEdit({ target: btn.dataset.editReset, op: "reset" }));
      });
      root.querySelectorAll("[data-edit-add]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editAdd;
          const input = root.querySelector(`[data-edit-add-input="${path}"]`);
          const value = input?.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: path, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-add-input]").forEach((input) => {
        input.addEventListener("keydown", (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          const value = input.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: input.dataset.editAddInput, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-save]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editSave;
          const textarea = root.querySelector(`[data-edit-text="${path}"]`);
          const value = textarea?.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: path, op: "set", value });
        });
      });
      root.querySelectorAll("[data-edit-scalar]").forEach((input) => {
        input.addEventListener("input", () => {
          const out = root.querySelector(`[data-edit-scalar-value="${input.dataset.editScalar}"]`);
          if (out) out.textContent = `${input.value}%`;
        });
      });
      root.querySelectorAll("[data-edit-save-scalar]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editSaveScalar;
          const input = root.querySelector(`[data-edit-scalar="${path}"]`);
          if (!input) return;
          void applyProfileEdit({ target: path, op: "set", value: Number(input.value) / 100 });
        });
      });
    }

    async function loadMoreProfileMemory() {
      if (!state.profileCognitionCursor) return;
      const button = $("#profileMemoryMoreBtn");
      if (button) button.disabled = true;
      const query = new URLSearchParams({ cursor: state.profileCognitionCursor });
      const nextPage = await requestJson(`${ENDPOINTS.profile}?${query.toString()}`);
      if (!nextPage) {
        showToast("近期记忆加载失败：后端不可用");
        updateProfileMemoryButton();
        return;
      }
      const current = Array.isArray(state.profile?.recent_cognition_updates) ? state.profile.recent_cognition_updates : [];
      const incoming = Array.isArray(nextPage.recent_cognition_updates) ? nextPage.recent_cognition_updates : [];
      state.profile = {
        ...(state.profile || {}),
        ...nextPage,
        recent_cognition_updates: current.concat(incoming)
      };
      syncProfileCognitionState(state.profile);
      renderProfileDetails();
      showToast(incoming.length ? `已加载 ${incoming.length} 条近期记忆` : "没有更多近期记忆");
    }

    function messageType(msg) {
      const type = msg?.type === "probe" ? "interest.probe" : (msg?.type || "interest.probe");
      return type === "avoidance" ? "avoidance.probe" : type;
    }

    function isAvoidanceProbe(type) {
      return messageType({ type }) === "avoidance.probe";
    }

    function isChallengeProbe(item) {
      const mode = String(item?.probe_mode || "").toLowerCase();
      return Boolean(item?.challenge) || mode === "lateral" || mode === "bridge" || mode === "wildcard";
    }

    function probeKey(type, domain) {
      const normalizedDomain = String(domain || "").trim().toLowerCase();
      return normalizedDomain ? `${messageType({ type })}:${normalizedDomain}` : "";
    }

    function messageKey(msg) {
      const type = messageType(msg);
      if (type === "interest.probe" || type === "avoidance.probe") {
        return probeKey(type, msg?.domain || msg?.title);
      }
      return `${type}:${msg?.bvid || msg?.domain || msg?.title || msg?.reason || ""}`;
    }

    function normalizeMessageItem(item) {
      if (!item) return null;
      const type = messageType(item);
      if (type === "delight") {
        return null;
      }
      if (type === "notification") {
        const bvid = item.bvid || item.id || item.recommendation_id;
        if (!bvid) return null;
        return {
          type: "notification",
          bvid: String(bvid),
          title: item.title || "有一条值得通知你的推荐",
          reason: item.reason || item.expression || "这条推荐达到了通知阈值。",
          content_url: item.content_url || (item.bvid ? `https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}` : "")
        };
      }
      const domain = item.domain || item.name || item.title;
      if (!domain) return null;
      const probeType = type === "avoidance.probe" || item.kind === "avoidance" ? "avoidance.probe" : "interest.probe";
      if (state.handledProbeKeys.has(probeKey(probeType, domain))) return null;
      const status = String(item.status || "active").trim().toLowerCase();
      if (status !== "active" && status !== "pending") return null;
      return {
        type: probeType,
        domain: String(domain),
        reason: item.reason || item.message || item.description || (probeType === "avoidance.probe" ? "后端希望确认这个避雷方向。" : "后端希望确认这个兴趣方向。"),
        specifics: asArray(item.specifics || item.examples || item.children).map((s) => s?.name || s?.label || valueList(s)).filter(Boolean),
        probe_mode: item.probe_mode || "",
        challenge: Boolean(item.challenge),
        chat_status: item.chat_status || item.status_text || "",
        chat_reply: item.chat_reply || item.reply || ""
      };
    }

    function syncMessageCount() {
      const count = getRenderableMessages(state.messageListSnapshot && isMessagesDrawerOpen() ? state.messageListSnapshot : state.messages).length;
      if (state.runtimeStatus) state.runtimeStatus.unread_count = count;
      const metric = $("#metricUnread");
      if (metric) metric.textContent = String(count);
      const dot = $("#messagesDot");
      if (dot) dot.hidden = count <= 0;
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(count);
      return count;
    }

    function getRenderableMessages(source = state.messages) {
      const seen = new Set();
      const items = [];
      for (const raw of source || []) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push(item);
      }
      return items;
    }

    function isMessagesDrawerOpen() {
      return Boolean($("#messagesDrawer")?.classList.contains("is-open"));
    }

    function hydrateInboxFromSpeculations(speculations, type = "interest.probe") {
      if (speculations == null || speculations === "") return;
      const normalizedType = messageType({ type });
      const items = asArray(speculations);
      const active = items.filter((item) => item && item.domain && (!item.status || item.status === "active") && !state.handledProbeKeys.has(probeKey(normalizedType, item.domain)));
      const activeKeys = new Set(active.map((item) => probeKey(normalizedType, item.domain)));
      const preserveCurrentProbeList = isMessagesDrawerOpen();
      state.messages = state.messages.filter((msg) => {
        if (messageType(msg) !== normalizedType) return true;
        const domain = String(msg.domain || "");
        if (!domain || state.handledProbeKeys.has(probeKey(normalizedType, domain))) return false;
        if (state.resolvingMessageKeys.has(messageKey(msg))) return true;
        return preserveCurrentProbeList || activeKeys.has(probeKey(normalizedType, domain));
      });
      const existing = new Set(state.messages.filter((msg) => messageType(msg) === normalizedType).map((msg) => probeKey(normalizedType, msg.domain)));
      for (const item of active) {
        const domain = String(item.domain);
        const key = probeKey(normalizedType, domain);
        if (!key || state.handledProbeKeys.has(key) || existing.has(key)) continue;
        state.messages.push(normalizeMessageItem({ ...item, type: normalizedType }));
        existing.add(key);
      }
      syncMessageCount();
    }

    function isMessageListLocked() {
      return Boolean(document.querySelector("#messageList .message-item.is-resolving, #messageList .message-item.is-resolved, #messageList .message-item.is-dismissing"));
    }

    function renderMessages() {
      const list = $("#messageList");
      if (state.messageListDomLocked || isMessageListLocked()) {
        syncMessageCount();
        return;
      }
      const source = state.messageListSnapshot && isMessagesDrawerOpen() ? state.messageListSnapshot : state.messages;
      const messages = getRenderableMessages(source);
      if (state.messageListSnapshot && isMessagesDrawerOpen()) state.messageListSnapshot = messages;
      else state.messages = messages;
      syncMessageCount();
      if (!messages.length) {
        list.innerHTML = `<div class="empty-state">暂无通知。兴趣确认、避雷确认和待通知候选都会出现在这里。</div>`;
        return;
      }
      list.replaceChildren(...messages.map((msg) => {
        const el = document.createElement("article");
        const key = messageKey(msg);
        const resolvedResult = state.resolvedMessageResults.get(key);
        el.className = "message-item";
        el.dataset.messageKey = key;
        if (messageType(msg) === "notification") {
          el.classList.add("is-notification");
          el.innerHTML = `<p class="eyebrow">待通知候选</p><h3>${escapeHtml(msg.title)}</h3><p class="video-meta">${escapeHtml(msg.reason)}</p><div class="message-note">这类消息来自后端挑出的高置信推荐，用于插件通知；标记已通知后不会反复出现。</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="通知候选状态"><button class="feedback-icon-btn" data-notification-msg="dismiss" type="button" aria-label="标记已通知" title="标记已通知"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg></button></div><div class="message-primary-actions"><button class="small-btn" data-notification-msg="view">去看看</button></div></div>`;
          el.querySelectorAll("[data-notification-msg]").forEach((btn) => btn.addEventListener("click", () => respondNotification(msg, btn.dataset.notificationMsg, el)));
        } else {
          const isAvoidance = messageType(msg) === "avoidance.probe";
          const isChallenge = !isAvoidance && isChallengeProbe(msg);
          el.classList.add(isAvoidance ? "is-avoidance-probe" : isChallenge ? "is-challenge-probe" : "is-interest-probe");
          const eyebrow = isAvoidance ? "避雷确认" : isChallenge ? "挑战探针" : "兴趣确认";
          const actionsLabel = isAvoidance ? "确认或排除这个避雷方向" : isChallenge ? "确认或排除这个挑战方向" : "确认或排除这个兴趣";
          const confirmLabel = isAvoidance ? "确实不喜欢" : "喜欢";
          const rejectLabel = isAvoidance ? "不是" : "不喜欢";
          const kindCopy = isAvoidance
            ? "想少看这类，就确认这是雷点；如果阿B猜错了，点不是。"
            : isChallenge
              ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就点不喜欢。"
            : "想继续探索这个方向，就点喜欢；不准就点不喜欢。";
          el.innerHTML = `<p class="eyebrow">${eyebrow}</p><div class="message-note probe-kind-copy">${escapeHtml(kindCopy)}</div><h3>${escapeHtml(msg.domain)}</h3><p class="video-meta">${escapeHtml(msg.reason)}</p><div class="profile-chip-row">${asArray(msg.specifics).map((s) => `<span class="chip">${escapeHtml(s)}</span>`).join("")}</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="${actionsLabel}"><button class="feedback-icon-btn" data-probe="confirm" type="button" aria-label="${confirmLabel}" title="${confirmLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg></button><span class="feedback-separator" aria-hidden="true">/</span><button class="feedback-icon-btn" data-probe="reject" type="button" aria-label="${rejectLabel}" title="${rejectLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg></button></div><div class="message-primary-actions"><button class="small-btn" data-probe="chat">多聊聊</button></div></div>`;
          if (resolvedResult) {
            el.classList.add("is-resolved");
            const resolvedActions = el.querySelector(".message-card-actions");
            if (resolvedActions) resolvedActions.outerHTML = `<div class="message-note is-success">${escapeHtml(resolvedResult)}</div>`;
          } else {
            el.querySelectorAll("[data-probe]").forEach((btn) => btn.addEventListener("click", () => respondProbe(msg, btn.dataset.probe, el)));
          }
        }
        return el;
      }));
    }

    async function respondNotification(msg, response, el) {
      if (response === "view" && msg.content_url) window.open(msg.content_url, "_blank", "noopener,noreferrer");
      await requestJson(ENDPOINTS.notificationSent, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: msg.bvid }) });
      state.messages = state.messages.filter((item) => !(messageType(item) === "notification" && String(item.bvid) === String(msg.bvid)));
      renderMessages();
      if (el) el.remove();
      showToast(response === "view" ? "已打开并标记这条通知" : "已标记这条通知");
    }

    function collapseMessageItem(key, fallbackEl, onDone) {
      const target = fallbackEl?.isConnected ? fallbackEl : Array.from(document.querySelectorAll("#messageList .message-item")).find((item) => item.dataset.messageKey === key);
      const finish = () => { onDone?.(); };
      if (!target) {
        finish();
        return;
      }
      target.style.height = `${target.getBoundingClientRect().height}px`;
      target.style.minHeight = "0px";
      target.style.overflow = "hidden";
      target.style.transition = `height 240ms var(--ease-standard), opacity 180ms var(--ease-standard), padding 240ms var(--ease-standard), border-width 240ms var(--ease-standard)`;
      target.getBoundingClientRect();
      target.classList.add("is-dismissing");
      target.style.height = "0px";
      window.setTimeout(() => {
        target.remove();
        finish();
      }, 260);
    }

    function appendInlineChatBubble(container, role, text) {
      if (!container) return null;
      const bubble = document.createElement("div");
      bubble.className = `inline-chat-bubble ${role}${role === "reply" ? " inline-chat-reply" : ""}`;
      bubble.textContent = text;
      container.appendChild(bubble);
      return bubble;
    }

    function messageProbeChatPrompt(msg, isAvoidance) {
      return msg.domain
        ? `我想多聊聊「${msg.domain}」这个${isAvoidance ? "避雷" : "兴趣"}方向。`
        : `我想多聊聊这个${isAvoidance ? "避雷" : "兴趣"}方向。`;
    }

    async function pollInlineMessageChatTurn(turnId, chatArea, thinking, startedAt = Date.now()) {
      const showReply = (text, tone = "reply") => {
        thinking?.remove();
        appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), tone, text);
        chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = false; });
        chatArea.querySelector(".inline-chat-input")?.focus();
      };
      try {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`);
        if (latest?.status === "completed" || latest?.reply) {
          showReply(latest.reply || "后端已完成这轮聊天。");
          return;
        }
        if (latest?.status === "failed" || Date.now() - startedAt > 180000) {
          showReply(latest?.error || "聊天处理超时，稍后可以在历史里继续查看。", "error");
          return;
        }
      } catch {
        // Keep polling below; transient disconnects should not collapse the inline composer.
      }
      window.setTimeout(() => pollInlineMessageChatTurn(turnId, chatArea, thinking, startedAt), 1200);
    }

    function openInlineMessageProbeChat(msg, el) {
      if (!el) return;
      const existing = el.querySelector(".inline-chat-area");
      if (existing) {
        existing.querySelector(".inline-chat-input")?.focus();
        return;
      }
      const probeType = messageType(msg);
      const isAvoidance = probeType === "avoidance.probe";
      const domain = String(msg.domain || "");
      const prompt = messageProbeChatPrompt(msg, isAvoidance);
      const actions = el.querySelector(".message-card-actions");
      if (actions) actions.hidden = true;
      const chatArea = document.createElement("div");
      chatArea.className = "inline-chat-area";
      chatArea.innerHTML = `
        <div class="inline-chat-turns" aria-live="polite"></div>
        <div class="inline-chat-compose">
          <textarea class="inline-chat-input" rows="2" placeholder="${escapeHtml(isAvoidance ? `聊聊你为什么想避开「${domain || "这个方向"}」…` : `聊聊你对「${domain || "这个方向"}」的想法…`)}"></textarea>
          <button class="inline-chat-send" type="button">发送</button>
          <button class="inline-chat-cancel" type="button">返回</button>
        </div>`;
      actions?.insertAdjacentElement("afterend", chatArea);
      const input = chatArea.querySelector(".inline-chat-input");
      const sendBtn = chatArea.querySelector(".inline-chat-send");
      const cancelBtn = chatArea.querySelector(".inline-chat-cancel");
      const closeComposer = () => {
        chatArea.remove();
        if (actions) actions.hidden = false;
      };
      const submit = async () => {
        const message = input?.value?.trim() || "";
        if (!message) {
          input?.focus();
          return;
        }
        chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = true; });
        appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "user", message);
        const thinking = appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "thinking", "阿B 正在结合这条探针思考…");
        const turnId = createClientTurnId(isAvoidance ? "avoidance-probe" : "probe");
        try {
          const turn = await requestJsonStrict(ENDPOINTS.chatTurns, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              turn_id: turnId,
              session: "webui",
              scope: isAvoidance ? "avoidance_probe" : "probe",
              subject_id: domain,
              subject_title: domain || (isAvoidance ? "这个避雷方向" : "这个兴趣方向"),
              message: `${prompt}\n\n${message}`
            })
          });
          if (input) input.value = "";
          void pollInlineMessageChatTurn(turn?.turn_id || turnId, chatArea, thinking);
        } catch (error) {
          thinking?.remove();
          appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "error", error?.message || "后台正忙，等一下再聊。");
          chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = false; });
          input?.focus();
        }
      };
      sendBtn?.addEventListener("click", () => void submit());
      cancelBtn?.addEventListener("click", closeComposer);
      input?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          void submit();
        }
        if (event.key === "Escape") closeComposer();
      });
      window.setTimeout(() => input?.focus(), 40);
    }

    async function respondProbe(msg, response, el) {
      if (!el) return;
      const actions = el.querySelector(".message-card-actions");
      if (response === "chat") {
        openInlineMessageProbeChat(msg, el);
        showToast("已在这条消息里打开聊天输入");
        return;
      }
      const key = messageKey(msg);
      state.messageListDomLocked = true;
      if (!state.messageListSnapshot && isMessagesDrawerOpen()) state.messageListSnapshot = getRenderableMessages();
      el.style.minHeight = `${el.getBoundingClientRect().height}px`;
      el.classList.add("is-resolving");
      state.resolvingMessageKeys.add(key);
      actions?.querySelectorAll("button").forEach((button) => { button.disabled = true; });
      const probeType = messageType(msg);
      const domain = msg.domain || "";
      const handledKey = probeKey(probeType, domain);
      if (handledKey) state.handledProbeKeys.add(handledKey);
      try {
        const isAvoidance = probeType === "avoidance.probe";
        const endpoint = isAvoidance ? ENDPOINTS.avoidanceProbeRespond : ENDPOINTS.interestProbeRespond;
        const apiResp = await requestJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domain: msg.domain, response, message: "" }) });
        if (apiResp && apiResp.ok === false) {
          state.resolvingMessageKeys.delete(key);
          state.messages = state.messages.filter((item) => messageKey(item) !== key);
          if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((item) => messageKey(item) !== key);
          state.messageListDomLocked = false;
          renderMessages();
          void refreshProfile();
          return;
        }
        const result = isAvoidance
          ? response === "confirm" ? "已确认避雷方向，后续会减少类似内容。" : "已搁置，暂时不作为避雷方向。"
          : response === "confirm" ? "已确认，后续推荐会提高权重。" : "已搁置，后续会少试探这个方向。";
        state.resolvedMessageResults.set(key, result);
        el.classList.remove("is-resolving");
        el.classList.add("is-resolved");
        if (actions) {
          actions.classList.add("is-result");
          actions.innerHTML = `<div class="message-action-result" title="${escapeHtml(result)}">${escapeHtml(result)}</div>`;
        }
        showToast(isAvoidance
          ? response === "confirm" ? "已确认这个避雷方向" : "已搁置这个避雷方向"
          : response === "confirm" ? "已确认这个兴趣方向" : "已搁置这个兴趣方向");
        setTimeout(() => {
          collapseMessageItem(key, el, () => {
            state.resolvingMessageKeys.delete(key);
            state.resolvedMessageResults.delete(key);
            state.messages = state.messages.filter((item) => messageKey(item) !== key);
            if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((item) => messageKey(item) !== key);
            state.messageListDomLocked = false;
            renderMessages();
            void refreshProfile();
          });
        }, 1800);
      } catch (error) {
        state.resolvingMessageKeys.delete(key);
        state.resolvedMessageResults.delete(key);
        state.messageListDomLocked = false;
        el.classList.remove("is-resolving");
        el.style.minHeight = "";
        if (handledKey) state.handledProbeKeys.delete(handledKey);
        actions?.querySelectorAll("button").forEach((button) => { button.disabled = false; });
        showToast(`确认反馈失败：${error.message || "后端不可用"}`);
      }
    }

    function bindSpeculativeActions() {
      document.querySelectorAll("[data-spec-response]").forEach((button) => {
        button.addEventListener("click", () => respondSpeculativeInterest(button));
      });
    }

    async function respondSpeculativeInterest(button) {
      const row = button.closest("[data-spec-domain]");
      const domain = row?.dataset.specDomain;
      const response = button.dataset.specResponse;
      if (!domain || !response) return;
      row.querySelectorAll("[data-spec-response]").forEach((btn) => { btn.disabled = true; });
      const type = button.dataset.specType || "interest.probe";
      const key = probeKey(type, domain);
      if (key) state.handledProbeKeys.add(key);
      try {
        const isAvoidance = isAvoidanceProbe(type);
        const endpoint = isAvoidance ? ENDPOINTS.avoidanceProbeRespond : ENDPOINTS.interestProbeRespond;
        const payload = { domain, response, message: "" };
        if (!isAvoidance) payload.surface = "profile";
        const apiResp = await requestJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        if (apiResp && apiResp.ok === false) {
          row.remove();
          state.messages = state.messages.filter((msg) => messageKey(msg) !== key);
          if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((msg) => messageKey(msg) !== key);
          renderMessages();
          void refreshProfile();
          return;
        }
        const result = isAvoidance
          ? (response === "confirm" ? `好，「${escapeHtml(domain)}」会作为避雷方向处理。` : `好，「${escapeHtml(domain)}」不记成避雷。`)
          : (response === "confirm" ? `好，「${escapeHtml(domain)}」记住了。` : `好，「${escapeHtml(domain)}」先不看了。`);
        row.innerHTML = `<p class="spec-result">${result}</p>`;
        state.messages = state.messages.filter((msg) => messageKey(msg) !== key);
        if (state.messageListSnapshot) state.messageListSnapshot = state.messageListSnapshot.filter((msg) => messageKey(msg) !== key);
        renderMessages();
        showToast(isAvoidance
          ? response === "confirm" ? "已确认这个避雷方向" : "已排除这个避雷方向"
          : response === "confirm" ? "已确认这个猜测兴趣" : "已排除这个猜测兴趣");
        setTimeout(() => { void refreshProfile(); }, 1200);
      } catch (error) {
        if (key) state.handledProbeKeys.delete(key);
        row.querySelectorAll("[data-spec-response]").forEach((btn) => { btn.disabled = false; });
        showToast(`确认反馈失败：${error.message || "后端不可用"}`);
      }
    }

    function createClientTurnId(prefix = "webui") {
      const suffix = window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      return `${prefix}-${suffix}`;
    }

    function normalizeDelightTurn(turn) {
      if (!turn) return null;
      const message = String(turn.message ?? turn.user_message ?? "");
      const reply = String(turn.reply ?? turn.assistant_message ?? "");
      const status = String(turn.status || (reply ? "completed" : "pending"));
      const turnId = String(turn.turn_id ?? turn.id ?? "");
      if (!turnId && !message && !reply) return null;
      return {
        turn_id: turnId,
        message,
        reply,
        status,
        error: String(turn.error ?? "")
      };
    }

    function delightTurnList(turns) {
      return asArray(turns).map(normalizeDelightTurn).filter(Boolean);
    }

    function upsertDelightTurn(turns, nextTurn) {
      const normalized = normalizeDelightTurn(nextTurn);
      const existing = delightTurnList(turns);
      if (!normalized) return existing;
      const index = existing.findIndex((turn) => turn.turn_id && turn.turn_id === normalized.turn_id);
      if (index < 0) return [...existing, normalized];
      return existing.map((turn, turnIndex) => turnIndex === index ? normalized : turn);
    }

    function mergeDelightTurnLists(currentTurns, incomingTurns) {
      let merged = delightTurnList(currentTurns);
      for (const turn of delightTurnList(incomingTurns)) merged = upsertDelightTurn(merged, turn);
      return merged;
    }

    function mergeDelightItem(current, incoming) {
      if (!current) return incoming;
      return {
        ...current,
        ...incoming,
        chat_turn_id: incoming.chat_turn_id || current.chat_turn_id || "",
        chat_reply: incoming.chat_reply || current.chat_reply || "",
        chat_draft: incoming.chat_draft || current.chat_draft || "",
        response_message: incoming.response_message || current.response_message || "",
        turns: mergeDelightTurnLists(current.turns, incoming.turns)
      };
    }

    function renderDelightTurns(delight) {
      const area = $("#delightTurns");
      if (!area) return;
      area.replaceChildren();
      const turns = delightTurnList(delight?.turns);
      if (!turns.length && !delight?.chat_reply) {
        area.hidden = true;
        scheduleActivityRailHeightSync();
        return;
      }
      area.hidden = false;
      if (!turns.length && delight?.chat_reply) {
        const bubble = document.createElement("div");
        bubble.className = "delight-turn-bubble is-assistant";
        bubble.textContent = delight.chat_reply;
        area.append(bubble);
        scheduleActivityRailHeightSync();
        return;
      }
      for (const turn of turns) {
        if (turn.message) {
          const userBubble = document.createElement("div");
          userBubble.className = "delight-turn-bubble is-user";
          userBubble.textContent = turn.message;
          area.append(userBubble);
        }
        const assistantBubble = document.createElement("div");
        const status = String(turn.status || "pending");
        assistantBubble.className = `delight-turn-bubble is-assistant${status === "pending" ? " is-thinking" : ""}${status === "failed" ? " is-error" : ""}`;
        assistantBubble.textContent = status === "pending"
          ? "阿B 正在品你这句话…"
          : status === "failed"
            ? turn.error || "这句还没发出去，稍后再试。"
            : turn.reply || "后端已完成这轮聊天。";
        area.append(assistantBubble);
      }
      scheduleActivityRailHeightSync();
    }

    function updateDelightState(bvid, updates) {
      const key = String(bvid || "");
      if (!key) return null;
      let current = null;
      state.delights = state.delights.map((item) => {
        if (String(item.bvid || "") !== key) return item;
        current = { ...item, ...updates };
        return current;
      });
      if (state.delight && String(state.delight.bvid || "") === key) {
        state.delight = { ...state.delight, ...updates };
        current = state.delight;
      }
      if (current && state.delight && String(state.delight.bvid || "") === key) {
        renderDelightTurns(state.delight);
        if ($("#delightStatus")) $("#delightStatus").textContent = state.delight.response_message || "";
      }
      return current;
    }

    function applyTurnToDelight(turn) {
      const subjectId = String(turn?.subject_id || turn?.bvid || "");
      if (!turn || (turn.scope && turn.scope !== "delight") || !subjectId) return null;
      const existing = state.delights.find((item) => String(item.bvid || "") === subjectId)
        || (state.delight && String(state.delight.bvid || "") === subjectId ? state.delight : null);
      const entry = normalizeDelightTurn(turn);
      if (!entry) return null;
      const status = String(entry.status || "pending");
      const updates = {
        chat_turn_id: entry.turn_id,
        turns: upsertDelightTurn(existing?.turns, entry),
        response_message: status === "completed" ? "这句已经记下，后面会更会试探。" : status === "failed" ? "这句还没发出去，稍后再试。" : "阿B 正在品你这句话。"
      };
      if (status === "completed") {
        updates.chat_reply = entry.reply || existing?.chat_reply || "";
        updates.chat_draft = "";
      }
      return updateDelightState(subjectId, updates);
    }

    function pollChatTurnUntilSettled(turnId, fallbackTurn) {
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`);
        if (latest) {
          const scopedTurn = { ...fallbackTurn, ...latest, scope: latest.scope || "delight", subject_id: latest.subject_id || fallbackTurn.subject_id };
          applyTurnToDelight(scopedTurn);
          if (latest.status === "completed" || latest.status === "failed") return;
        }
        if (Date.now() - startedAt > 180000) {
          applyTurnToDelight({ ...fallbackTurn, status: "failed", error: "聊天处理超时，稍后可以在历史里继续查看。" });
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function respondDelight(delight, response, el = null) {
      if (!delight) return;
      if (response === "chat") { openDelightComposer(); return; }
      if (response === "cancel-comment") { closeDelightComposer(); return; }
      if (response === "watch-later") {
        const btn = el?.closest("[data-action]") || document.querySelector('[data-delight="watch-later"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        try {
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.watchLater}/${encodeURIComponent(delight.bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.watchLater, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: delight.bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "favorite") {
        const btn = el?.closest("[data-action]") || document.querySelector('[data-delight="favorite"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        try {
          if (wasSaved) {
            await requestJson(`${ENDPOINTS.favorites}/${encodeURIComponent(delight.bvid)}`, { method: "DELETE" });
          } else {
            await requestJson(ENDPOINTS.favorites, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: delight.bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "send-comment") {
        const input = $("#delightCommentInput");
        const note = input?.value?.trim() || "";
        if (!note) {
          if ($("#delightStatus")) $("#delightStatus").textContent = "先写一句想聊的内容，再提交这轮对话。";
          input?.focus();
          return;
        }
        const turnId = createClientTurnId("delight");
        const pendingTurn = { turn_id: turnId, session: "webui", scope: "delight", subject_id: delight.bvid, subject_title: delight.title || "", message: note, reply: "", status: "pending", error: "" };
        applyTurnToDelight(pendingTurn);
        if (input) input.value = "";
        closeDelightComposer();
        try {
          const turn = await requestJsonStrict(ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pendingTurn) });
          const scopedTurn = { ...pendingTurn, ...(turn || {}), scope: turn?.scope || "delight", subject_id: turn?.subject_id || delight.bvid };
          applyTurnToDelight(scopedTurn);
          if (scopedTurn.turn_id && scopedTurn.status !== "completed" && scopedTurn.status !== "failed") pollChatTurnUntilSettled(scopedTurn.turn_id, scopedTurn);
          showToast("已提交聊天线索");
        } catch (error) {
          applyTurnToDelight({ ...pendingTurn, status: "failed", error: error.message || "聊天提交失败，请稍后再试。" });
          if (input) input.value = note;
          showToast(`聊天提交失败：${error.message || "后端不可用"}`);
        }
        return;
      }
      if (response === "view") {
        const url = delight.content_url || (delight.bvid ? `https://www.bilibili.com/video/${encodeURIComponent(delight.bvid)}` : "");
        if (url) window.open(url, "_blank", "noopener,noreferrer");
        trackRecommendationClick(delight);
        // 浏览过即已读：上报 view 让后端标记 delight_notified，下次重灌不再出现。
        // fire-and-forget，不阻塞打开内容；当场卡片仍保留。
        requestJson(ENDPOINTS.delightRespond, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bvid: delight.bvid, response: "view", title: delight.title || "", message: "" })
        }).catch(() => {});
        showToast(url ? "已打开惊喜推荐" : "后端没有返回可打开链接");
        return;
      }
      const feedbackToast = response === "like" ? "惊喜推荐已喜欢" : response === "dislike" ? "这类惊喜先少来点" : "已忽略这条惊喜推荐";
      const toastImmediately = response === "like" || response === "dislike";
      if (toastImmediately) showToast(feedbackToast);
      await requestJson(ENDPOINTS.delightRespond, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bvid: delight.bvid,
          response,
          title: delight.title,
          message: ""
        })
      });
      if (response === "like") {
        updateDelightState(delight.bvid, { response_message: "好，这类多来点。" });
        if (el) el.closest(".video-card")?.classList.add("is-liked");
      }
      if (response === "dislike" || response === "dismiss") {
        state.delights = state.delights.filter((item) => item.bvid !== delight.bvid);
        state.delightPage = null;
        renderDelightGrid();
      }
      if (!toastImmediately) showToast(feedbackToast);
    }

    function openMessageChat(msg) {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      const input = $("#messageChatInput");
      state.messageScrollTop = panel?.scrollTop || 0;
      const type = messageType(msg);
      const isAvoidance = type === "avoidance.probe";
      state.messageChatDomain = msg.domain || "";
      state.messageChatScope = isAvoidance ? "avoidance_probe" : "probe";
      openPanel("messagesDrawer");
      drawer?.classList.add("is-chatting");
      if (view) view.hidden = false;
      const title = $("#messageChatTitle");
      const context = $("#messageChatContext");
      const prompt = msg.domain
        ? `我想多聊聊「${msg.domain}」这个${isAvoidance ? "避雷" : "兴趣"}方向。`
        : `我想多聊聊这个${isAvoidance ? "避雷" : "兴趣"}方向。`;
      state.messageChatPrompt = prompt;
      state.messageChatSubjectTitle = msg.domain || (isAvoidance ? "这个避雷方向" : "这个兴趣方向");
      if (title) title.textContent = msg.domain ? `聊聊${isAvoidance ? "避雷" : "兴趣"}「${msg.domain}」` : `聊聊这个${isAvoidance ? "避雷" : "兴趣"}`;
      if (context) context.textContent = msg.reason || `这轮对话会沿用消息里的${isAvoidance ? "避雷" : "兴趣"}上下文。`;
      if (input) {
        input.value = "";
        input.placeholder = "继续写你想补充的问题、偏好或例子";
      }
      renderChat();
      if (panel) panel.scrollTop = 0;
      window.setTimeout(() => input?.focus(), 80);
    }

    function returnToMessages() {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      drawer?.classList.remove("is-chatting");
      if (view) view.hidden = true;
      state.messageChatDomain = "";
      state.messageChatPrompt = "";
      state.messageChatScope = "probe";
      state.messageChatSubjectTitle = "";
      window.setTimeout(() => {
        if (panel) panel.scrollTop = state.messageScrollTop || 0;
      }, 0);
    }

    function chatHtml(messages) {
      return messages.map((msg) => {
        const refs = Array.isArray(msg.references) ? msg.references.filter((item) => item && item.title) : [];
        // Only agent turns carry references, and only when the reply was
        // actually grounded in the crawled library.
        const badge = refs.length
          ? `<div class="chat-refs" title="${escapeHtml(refs.map((item) => item.title).join("\n"))}">已参考 ${refs.length} 篇收藏</div>`
          : "";
        return `<div class="chat-bubble ${msg.role === "user" ? "user" : "agent"}">${escapeHtml(msg.text)}${badge}</div>`;
      }).join("");
    }

    function renderChat() {
      const chatLog = $("#chatLog");
      if (chatLog) {
        chatLog.innerHTML = chatHtml(state.chat);
        chatLog.scrollTop = chatLog.scrollHeight;
      }
      const messageChatLog = $("#messageChatLog");
      if (messageChatLog) {
        const baseMessages = state.messageChatPrompt
          ? state.chat.filter((msg) => msg.text !== "你可以直接告诉我最近想多看什么、少看什么，或者评价一条推荐为什么准/不准。")
          : state.chat;
        const messages = state.messageChatPrompt ? [{ role: "user", text: state.messageChatPrompt }, ...baseMessages] : baseMessages;
        messageChatLog.innerHTML = chatHtml(messages);
        messageChatLog.scrollTop = messageChatLog.scrollHeight;
      }
    }

    async function sendChat(message, options = {}) {
      const payloadMessage = options.contextPrefix ? `${options.contextPrefix}\n\n${message}` : message;
      state.chat.push({ role: "user", text: message });
      state.chat.push({ role: "agent", text: "正在提交给后端，并等待 durable chat turn 完成。" });
      renderChat();
      const payload = {
        session: "webui",
        scope: options.scope || "chat",
        subject_id: options.subjectId || "",
        subject_title: options.subjectTitle || "",
        message: payloadMessage
      };
      const turn = await requestJson(ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!turn?.turn_id) {
        state.chat[state.chat.length - 1] = { role: "agent", text: "当前没有连上后端，聊天没有提交成功。请检查 FastAPI 地址后重试。" };
        renderChat();
        showToast("聊天提交失败：后端不可用");
        return;
      }
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await requestJson(`${ENDPOINTS.chatTurns}/${encodeURIComponent(turn.turn_id)}`);
        if (latest?.status === "completed" || latest?.reply) {
          state.chat[state.chat.length - 1] = {
            role: "agent",
            text: latest.reply || "后端已完成这轮聊天。",
            references: Array.isArray(latest?.references) ? latest.references : []
          };
          renderChat();
          return;
        }
        if (latest?.status === "failed" || Date.now() - startedAt > 180000) {
          state.chat[state.chat.length - 1] = { role: "agent", text: latest?.error || "聊天处理超时，稍后可以在历史里继续查看。" };
          renderChat();
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function refreshRecommendations() {
      const result = await requestJson(ENDPOINTS.refresh, { method: "POST" });
      if (result) {
        showToast("已请求后端开始补货");
        await hydrateFromBackend();
      } else {
        showToast("刷新失败：请检查后端连接");
      }
    }

    async function dismissVisibleRecommendationsBeforeReshuffle() {
      const visibleItems = filteredVideos().filter((item) => item?.id != null);
      if (!visibleItems.length) return { total: 0, ok: 0, failed: 0 };
      showToast(`正在忽略当前显示的 ${visibleItems.length} 张推荐…`);
      const results = await Promise.allSettled(visibleItems.map((item) => submitFeedback(item, "dismiss")));
      const dismissedKeys = new Set();
      results.forEach((result, index) => {
        if (result.status === "fulfilled") dismissedKeys.add(recommendationKey(visibleItems[index]));
      });
      if (dismissedKeys.size) {
        state.videos = state.videos.filter((item) => !dismissedKeys.has(recommendationKey(item)));
      }
      return { total: visibleItems.length, ok: dismissedKeys.size, failed: visibleItems.length - dismissedKeys.size };
    }

    async function reshuffle(platformOverride) {
      const reshuffleButton = $("#reshuffleBtn");
      const dismissToggle = $("#dismissOnReshuffleToggle");
      if (reshuffleButton) reshuffleButton.disabled = true;
      if (dismissToggle) dismissToggle.disabled = true;
      try {
        const dismissResult = state.dismissOnReshuffle ? await dismissVisibleRecommendationsBeforeReshuffle() : null;
        // 根据当前页面决定平台参数：
        // - 首页 / 全部推荐 → null（零筛选）
        // - 按平台 → 当前选中的平台filter
        // - 自定义筛选 → 勾选的平台（仅一个平台时透传，多选/全不选传null由客户端过滤）
        let platform;
        if (platformOverride !== undefined) {
          platform = platformOverride;
        } else {
          const activePage = document.querySelector(".main-col:not([hidden])");
          const pageId = activePage?.id || "homePage";
          if (pageId === "customFilterPage") {
            platform = currentCustomFilterPlatform();
          } else {
            platform = null;
          }
        }
        // 根据当前页面决定批量大小：
        // - 自定义筛选 → 用户在页面里设置的每批数量
        // - 首页 / 按平台 → 默认 10
        let batchLimit = 10;
        if (platformOverride === undefined) {
          const activePage = document.querySelector(".main-col:not([hidden])");
          const pageId = activePage?.id || "homePage";
          if (pageId === "customFilterPage" && state.customLimit) {
            batchLimit = state.customLimit;
          }
        }
        const params = new URLSearchParams();
        if (platform) params.set("platform", platform);
        if (batchLimit && batchLimit !== 10) params.set("limit", String(batchLimit));
        const url = params.size ? `${ENDPOINTS.reshuffle}?${params.toString()}` : ENDPOINTS.reshuffle;
        const payload = await requestJson(url, { method: "POST" });
        if (payload?.items?.length) {
          state.videos = normalizeRecommendationList(payload.items);
          renderAll();
          if (dismissResult?.ok) {
            const failedText = dismissResult.failed ? `，${dismissResult.failed} 张忽略失败` : "";
            showToast(`已忽略 ${dismissResult.ok} 张当前推荐并换一批${failedText}`);
          } else {
            showToast("已换一批推荐");
          }
        } else {
          renderAll();
          // Distinguish "pool is drained" (200 + empty items) from a real
          // connectivity failure (requestJson resolves to null). Reporting a
          // drained pool as a connection error sent people hunting for a
          // backend problem that did not exist.
          if (payload) {
            showToast("暂时没有新内容了，后台正在补货，稍后再试");
          } else {
            showToast("换一批失败：请检查后端连接");
          }
        }
      } finally {
        if (reshuffleButton) reshuffleButton.disabled = false;
        if (dismissToggle) dismissToggle.disabled = false;
      }
    }

    async function appendMore() {
      const payload = await requestJson(ENDPOINTS.append, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ excluded_bvids: state.videos.map((v) => v.bvid) }) });
      if (payload?.items?.length) {
        const freshItems = normalizeRecommendationList(payload.items);
        state.videos = state.videos.concat(freshItems);
        renderAll();
        // Keep decoding off the interaction path: slow first-miss covers should
        // not delay the new recommendation cards from appearing.
        void warmCoverImages(freshItems, { waitForDecode: true }).catch(() => {});
        showToast(freshItems.length ? "已加载更多推荐" : "后端返回的内容都已反馈过");
      } else {
        showToast("加载更多失败：后端没有返回新候选");
      }
    }

    function normalizeRuntimeStatus(status) {
      if (!status) return null;
      const previous = state.runtimeStatus || {};
      const incomingType = String(status.type || status.runtime_event_type || "");
      const merged = { ...previous, ...status };
      let manualRefreshState = status.manual_refresh_state != null
        ? String(status.manual_refresh_state || "idle")
        : String(previous.manual_refresh_state || "");
      if (status.manual_refresh_state == null) {
        if (incomingType === "refresh.started" || incomingType === "refresh.strategy") manualRefreshState = "running";
        if (incomingType === "refresh.pool_updated") manualRefreshState = "success";
        if (incomingType === "refresh.failed") manualRefreshState = "failed";
      }
      return {
        initialized: merged.initialized !== false,
        recommendation_count: Number(merged.recommendation_count ?? 0),
        pending_signal_events: Number(merged.pending_signal_events ?? 0),
        last_refresh_at: String(merged.last_refresh_at ?? ""),
        last_notification_at: String(merged.last_notification_at ?? ""),
        unread_count: Number(merged.unread_count ?? state.messages.length ?? 0),
        pool_available_count: Number(merged.pool_available_count ?? merged.pool_available ?? merged.available_count ?? 0),
        pool_pending_count: Number(merged.pool_pending_count ?? 0),
        pool_target_count: Number(merged.pool_target_count ?? state.config?.scheduler?.pool_target_count ?? 0),
        last_discovered_count: Number(merged.last_discovered_count ?? 0),
        last_replenished_count: Number(merged.last_replenished_count ?? 0),
        recent_pool_topics: Array.isArray(merged.recent_pool_topics) ? merged.recent_pool_topics.map(String).filter(Boolean) : [],
        manual_refresh_state: manualRefreshState || "idle",
        manual_refresh_message: String(merged.manual_refresh_message || ""),
        runtime_event_type: incomingType || String(merged.runtime_event_type || ""),
        live_summary: String(merged.live_summary || merged.message || merged.state || "")
      };
    }

    function hasPostInitRuntimeSignals(runtime) {
      return Boolean(runtime) && (
        runtime.recommendation_count > 0 ||
        runtime.pool_available_count > 0 ||
        runtime.pool_pending_count > 0 ||
        runtime.last_replenished_count > 0 ||
        runtime.last_discovered_count > 0
      );
    }

    function shouldShowInitOnboarding(status) {
      const runtime = normalizeRuntimeStatus(status);
      if (initWaitingForFirstPool(state.initStatus)) return true;
      if (Boolean(state.initStatus?.running)) return true;
      return Boolean(status) && runtime.initialized === false && !hasPostInitRuntimeSignals(runtime);
    }

    function getPoolStatusSummary(status) {
      const runtime = normalizeRuntimeStatus(status);
      if (!runtime || !runtime.initialized) return null;
      const sufficient = runtime.pool_target_count > 0 && runtime.pool_available_count >= runtime.pool_target_count;
      if (runtime.manual_refresh_state === "running") {
        return runtime.pool_available_count > 0
          ? { available: `还有 ${runtime.pool_available_count} 条可换`, replenished: "后台继续在找更多", topics: "可以先换一批，新的随时进" }
          : { available: "暂无可换库存", replenished: "正在补货", topics: "后台还在继续给你找新的" };
      }
      return {
        available: `还有 ${runtime.pool_available_count} 条可换`,
        replenished: runtime.last_replenished_count > 0
          ? `刚补进 ${runtime.last_replenished_count} 条`
          : runtime.last_discovered_count > 0
            ? "这轮找到了内容"
            : sufficient
              ? "这会儿先不补货"
              : "这轮还没补进",
        topics: runtime.recent_pool_topics.length > 0
          ? runtime.recent_pool_topics.join(" / ")
          : runtime.last_discovered_count > 0
            ? "但可立即换的库存还没变"
            : sufficient
              ? "先把这一池给你慢慢换开"
              : "还在继续摸你的口味"
      };
    }

    function configuredSourceCount() {
      const sources = state.config?.sources;
      if (!sources || typeof sources !== "object") return 0;
      const shares = state.config?.scheduler?.pool_source_shares || {};
      return Object.entries(sources).reduce((count, [key, value]) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) return count;
        if (Object.prototype.hasOwnProperty.call(value, "enabled")) {
          return count + (value.enabled !== false ? 1 : 0);
        }
        if (Object.prototype.hasOwnProperty.call(shares, key)) {
          return count + (Number(shares[key] ?? 0) > 0 ? 1 : 0);
        }
        return count;
      }, 0);
    }

    function syncSourceMetric() {
      const count = configuredSourceCount();
      $("#metricSources").textContent = count ? String(count) : "—";
    }

    function getPoolRefreshLabel(runtime) {
      if (!runtime) return "—";
      if (runtime.manual_refresh_message) return runtime.manual_refresh_message;
      if (runtime.manual_refresh_state === "running") return runtime.pool_available_count > 0 ? "后台继续补货中" : "正在补货";
      if (runtime.manual_refresh_state === "success") return "刚同步完成";
      if (runtime.manual_refresh_state === "failed") return "刷新失败";
      if (runtime.pending_signal_events > 0) return `已记下 ${runtime.pending_signal_events} 个新动作`;
      if (runtime.runtime_event_type === "refresh.pool_updated") return "刚同步推荐池";
      return runtime.pool_available_count > 0 ? "可直接换一批" : "等待后台补货";
    }

    function renderPoolStatus(status = state.runtimeStatus) {
      const runtime = normalizeRuntimeStatus(status);
      const summary = getPoolStatusSummary(runtime);
      $("#poolAvailable").textContent = summary?.available || "后端未初始化";
      $("#poolReplenished").textContent = summary?.replenished || "—";
      $("#poolTopics").textContent = summary?.topics || "—";
      $("#poolRefreshState").textContent = getPoolRefreshLabel(runtime);
    }

    function applyRuntimeStatus(payload) {
      if (!payload) return;
      state.runtimeStatus = normalizeRuntimeStatus(payload);
      const summary = getPoolStatusSummary(state.runtimeStatus);
      $("#statusLabel").textContent = state.runtimeStatus.initialized === false ? "后端未初始化" : "已连接本地后端";
      $("#metricPool").textContent = String(state.runtimeStatus.pool_available_count);
      syncMessageCount();
      syncSourceMetric();
      $("#runtimeSummary").textContent = state.runtimeStatus.live_summary || summary?.available || "后端在线，推荐池与采集运行时可读取。";
      renderPoolStatus(state.runtimeStatus);
    }

    function setInput(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    function setCookieOverrideInput(id, currentCookie, platformLabel) {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = "";
      const hasCookie = Boolean(String(currentCookie || "").trim());
      el.placeholder = hasCookie
        ? `已保存${platformLabel} Cookie；留空保存不会覆盖，需要更换时粘贴新的 Cookie`
        : `未保存${platformLabel} Cookie；需要手动覆盖时粘贴 Cookie`;
    }

    function getInput(id) {
      return document.getElementById(id)?.value?.trim() || "";
    }

    function getIntInput(id, fallback) {
      const value = Number.parseInt(getInput(id), 10);
      return Number.isFinite(value) ? value : fallback;
    }

    function getFloatInput(id, fallback) {
      const value = Number.parseFloat(getInput(id));
      return Number.isFinite(value) ? value : fallback;
    }

    const ZHIHU_SOURCE_MODE_FIELDS = [
      ["search", "zhihuModeSearch"],
      ["hot", "zhihuModeHot"],
      ["feed", "zhihuModeFeed"],
      ["creator", "zhihuModeCreator"],
      ["related", "zhihuModeRelated"],
    ];

    function setZhihuSourceModes(rawModes) {
      const fallbackModes = ZHIHU_SOURCE_MODE_FIELDS.map(([mode]) => mode);
      const selected = new Set(
        (Array.isArray(rawModes) && rawModes.length > 0 ? rawModes : fallbackModes)
          .map((mode) => String(mode).trim())
          .filter(Boolean),
      );
      for (const [mode, id] of ZHIHU_SOURCE_MODE_FIELDS) {
        const el = document.getElementById(id);
        if (el) el.checked = selected.has(mode);
      }
    }

    function collectZhihuSourceModes() {
      const selected = ZHIHU_SOURCE_MODE_FIELDS
        .filter(([, id]) => document.getElementById(id)?.checked === true)
        .map(([mode]) => mode);
      return selected.length > 0 ? selected : ["search"];
    }

    function joinPath(directory, filename) {
      const dir = String(directory || "").trim();
      const name = String(filename || "").trim();
      if (!dir) return name;
      if (!name) return dir;
      return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}/${name}`;
    }

    function resolveLogPath(loggingConfig) {
      if (loggingConfig?.file_path) return loggingConfig.file_path;
      return joinPath(loggingConfig?.directory || "logs", loggingConfig?.filename || "openbiliclaw.log");
    }

    function splitLogPath(rawPath, currentLogging) {
      const fallback = { directory: "logs", filename: "openbiliclaw.log" };
      const trimmed = String(rawPath || "").trim();
      if (!trimmed) return fallback;
      if (currentLogging && trimmed === resolveLogPath(currentLogging)) {
        return { directory: currentLogging.directory || fallback.directory, filename: currentLogging.filename || fallback.filename };
      }
      const normalized = trimmed.replaceAll("\\", "/").replace(/\/+$/, "");
      const slashIndex = normalized.lastIndexOf("/");
      if (slashIndex === -1) return { directory: fallback.directory, filename: normalized || fallback.filename };
      return { directory: normalized.slice(0, slashIndex) || "/", filename: normalized.slice(slashIndex + 1) || fallback.filename };
    }

    function setSelect(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    // Unified per-source login / cookie status (GET /api/sources/status),
    // rendered with separate scheduling and credential/plugin states.
    const SOURCE_STATUS_KEYS = [
      "bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu",
      "v2ex", "rss", "reddit", "wechat", "xiaoyuzhou",
    ];
    const CURRENT_CREDENTIAL_KEYS = [
      "bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu",
      "v2ex", "rss", "reddit", "wechat", "xiaoyuzhou",
    ];
    const SOURCE_ENABLE_SELECT_IDS = {
      bilibili: "bilibiliEnabled",
      xiaohongshu: "xhsEnabled",
      douyin: "douyinEnabled",
      youtube: "youtubeEnabled",
      twitter: "twitterEnabled",
      zhihu: "zhihuEnabled"
    };
    const SOURCE_ACCESS_STATE = {
      ok: { tone: "ready", label: "接入可用" },
      ready: { tone: "ready", label: "接入可用" },
      no_auth: { tone: "public", label: "无需登录" },
      unverified: { tone: "pending", label: "状态待验证" },
      missing: { tone: "warning", label: "需要登录" },
      missing_cookie: { tone: "warning", label: "缺少 Cookie" },
      rate_limited: { tone: "warning", label: "频率受限" },
      partial: { tone: "warning", label: "部分可用" },
      stale: { tone: "warning", label: "需要刷新" },
      expired_cookie: { tone: "danger", label: "Cookie 失效" },
      blocked: { tone: "danger", label: "接入受阻" }
    };

    function setSourceBadge(badge, text, tone) {
      if (!badge) return;
      badge.textContent = text;
      badge.dataset.tone = tone;
    }

    function getPendingSourceEnabled(key, item) {
      const select = document.getElementById(SOURCE_ENABLE_SELECT_IDS[key]);
      const currentEnabled = select ? select.value === "on" : Boolean(item?.enabled);
      const savedEnabled = typeof item?.enabled === "boolean" ? item.enabled : currentEnabled;
      return {
        currentEnabled,
        savedEnabled,
        pending: currentEnabled !== savedEnabled
      };
    }

    function renderSourcesStatusRows(data) {
      const list = $("#sourceStatusList");
      if (!list) return;
      SOURCE_STATUS_KEYS.forEach((key) => {
        const row = list.querySelector(`[data-source-status="${key}"]`);
        if (!row) return;
        const sourceBadge = row.querySelector(".source-source-badge");
        const accessBadge = row.querySelector(".source-access-badge");
        const detail = row.querySelector(".src-detail");
        const item = data?.[key];
        if (!item) {
          setSourceBadge(sourceBadge, "来源：状态未知", "muted");
          setSourceBadge(accessBadge, "接入：后端未连接", "muted");
          if (detail) detail.textContent = "暂时无法读取来源接入状态，请确认后端服务可用。";
          row.classList.remove("source-row-unsaved");
          row.dataset.sourceEnabled = "unknown";
          row.dataset.accessTone = "muted";
          return;
        }
        const enableState = getPendingSourceEnabled(key, item);
        const accessState = SOURCE_ACCESS_STATE[item.state] || { tone: "muted", label: "状态未知" };
        const sourceLabel = enableState.pending
          ? `来源：${enableState.currentEnabled ? "将启用" : "将停用"}，保存后生效`
          : `来源：${enableState.savedEnabled ? "启用" : "停用"}`;
        setSourceBadge(sourceBadge, sourceLabel, enableState.pending ? "pending" : enableState.savedEnabled ? "enabled" : "disabled");
        setSourceBadge(accessBadge, `接入：${accessState.label}`, accessState.tone);
        const detailPrefix = enableState.pending ? "开关已改动，保存配置后才会进入/退出调度。 " : "";
        if (detail) detail.textContent = detailPrefix + (item.detail || "暂无更多状态细节。");
        row.classList.toggle("source-row-unsaved", enableState.pending);
        row.dataset.sourceEnabled = enableState.currentEnabled ? "true" : "false";
        row.dataset.accessTone = accessState.tone;
      });
    }

    async function renderSourcesStatus() {
      let data = null;
      try { data = await requestJson("/sources/status"); } catch { data = null; }
      state.sourceStatus = data;
      renderSourcesStatusRows(data);
    }

    function renderSourceCredentialRows(data) {
      const list = $("#sourceCredentialList");
      if (!list) return;
      CURRENT_CREDENTIAL_KEYS.forEach((key) => {
        const row = list.querySelector(`[data-source-credential="${key}"]`);
        if (!row) return;
        const summary = row.querySelector(".source-credential-summary");
        const value = row.querySelector(".source-credential-value");
        const item = data?.[key];
        if (!item) {
          row.dataset.available = "false";
          if (summary) summary.textContent = "状态暂不可用";
          if (value) value.value = "暂时无法读取当前 Cookie / 登录凭据。";
          return;
        }
        row.dataset.available = item.available ? "true" : "false";
        if (summary) {
          summary.textContent = item.available
            ? `${item.label || "Cookie"} 已保存，展开查看`
            : item.detail || "当前没有可展示 Cookie";
        }
        if (value) {
          value.value = item.value || item.detail || "当前没有可展示 Cookie / 登录凭据。";
        }
      });
    }

    async function renderSourceCredentials() {
      let data = null;
      try { data = await requestJson(ENDPOINTS.sourceCredentials); } catch { data = null; }
      state.sourceCredentials = data;
      renderSourceCredentialRows(data);
    }

    // Login happens outside this page (user signs into a platform in another
    // tab), so a one-shot render on settings open goes stale — re-poll while
    // the status list is actually visible.
    setInterval(() => {
      if (document.hidden) return;
      const list = $("#sourceStatusList");
      if (!list || list.offsetParent === null) return;
      void renderSourcesStatus();
    }, 30000);

    // LAN password-gate control. The web UI is served from 127.0.0.1, so it is a
    // trusted-local client (same-origin loopback) and may manage /api/auth/admin,
    // exactly like the extension's popup-auth-control.
    let lanAuthControl = null;
    let bootAutostartControl = null;

    function initLanAuthControl() {
      const checkbox = $("#authEnabled");
      const password = $("#authPassword");
      const passwordField = $("#authPasswordField");
      const saveRow = $("#authSaveRow");
      const saveBtn = $("#authSave");
      const hint = $("#authHint");
      if (!checkbox) return { reload: async () => {} };
      let current = null;
      const setHint = (msg) => { if (hint) hint.textContent = msg; };
      function syncEditing() {
        const can = Boolean(current && current.can_manage);
        const enabling = checkbox.checked;
        if (passwordField) passwordField.hidden = !(can && enabling);
        if (saveRow) saveRow.hidden = !(can && enabling);
      }
      function applyServerState() {
        const can = Boolean(current && current.can_manage);
        checkbox.checked = Boolean(current && current.enabled);
        checkbox.disabled = !can;
        syncEditing();
        if (!current) setHint("无法读取后端鉴权状态。");
        else if (!can) setHint(current.env_managed ? "由环境变量管理，请改环境变量并重启后端。" : "仅本机 / 浏览器插件可修改此设置。");
        else if (current.enabled) setHint("已开启：局域网 / 远程设备访问需要登录密码（本机与插件免登录）。");
        else setHint("已关闭：局域网访问无需密码。");
      }
      async function load() {
        current = await requestJson("/auth/status");
        applyServerState();
        return current;
      }
      async function apply(enabled) {
        const pwd = password ? String(password.value || "") : "";
        if (enabled && !pwd.trim()) { setHint("请输入要设置的访问密码。"); if (password?.focus) password.focus(); return; }
        setHint("保存中…");
        try {
          const payload = enabled ? { enabled: true, password: pwd } : { enabled: false };
          const result = await requestJsonStrict("/auth/admin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
          if (result && result.ok === false) { setHint("保存失败，请重试。"); await load(); return; }
          if (password) password.value = "";
          await load();
        } catch (err) {
          const status = err?.status;
          if (status === 403) setHint("仅本机 / 插件可修改此设置。");
          else if (status === 409) setHint("由环境变量管理，无法在此修改。");
          else if (status === 400) setHint("开启密码门禁需要先设置密码。");
          else setHint("无法连接后端或保存失败，请重试。");
          await load();
        }
      }
      checkbox.addEventListener("change", () => {
        if (!checkbox.checked) void apply(false);
        else { syncEditing(); if (password?.focus) password.focus(); }
      });
      saveBtn?.addEventListener("click", () => void apply(true));
      void load();
      return { reload: load };
    }

    // Boot autostart control — mirrors the extension's popup-autostart-control.
    function initBootAutostartControl() {
      const checkbox = $("#autostartEnabled");
      const hint = $("#autostartHint");
      if (!checkbox) return { reload: async () => {} };
      let current = null;
      let busy = false;
      const setHint = (msg) => { if (hint) hint.textContent = msg; };
      function disabledHint(status) {
        const reason = status?.reason || "";
        if (reason === "env_managed") return "检测到环境变量配置，登录会话可能拿不到这些值；请先写入 config.toml。";
        if (reason === "shadowed") return "config.local.toml 正在覆盖开关，无法在此修改。";
        if (reason === "unsupported_docker_runtime") return "当前在 Docker / 容器环境中，不能注册桌面登录自启动。";
        if (reason === "unsupported_platform") return "当前平台暂不支持开机自启动。";
        if (reason === "local_only") return "仅本机 / 浏览器插件可修改此设置。";
        return "当前环境不能在这里修改开机自启动。";
      }
      function enabledHint(status) {
        const ollama = status?.manage_ollama ? "；本机 Ollama 配置会在需要时顺带拉起" : "";
        if (status?.registered === false) return `配置已开启，但系统注册缺失；下次后端启动会尝试修复${ollama}。`;
        return `已开启：下次登录系统会拉起后端，不启停当前进程${ollama}。`;
      }
      function activeHint(status) {
        if (!status) return "无法读取开机自启动状态。";
        if (!status.can_manage) return disabledHint(status);
        if (status.enabled) return enabledHint(status);
        return "已关闭：不会注册登录自启动；当前后端进程不受影响。";
      }
      function applyServerState() {
        const can = Boolean(current && current.can_manage);
        checkbox.checked = Boolean(current && current.enabled);
        checkbox.disabled = busy || !can;
        setHint(activeHint(current));
      }
      async function load() {
        current = await requestJson("/autostart-status");
        applyServerState();
        return current;
      }
      async function apply(enabled) {
        busy = true;
        checkbox.disabled = true;
        setHint(enabled ? "正在开启开机自启动…" : "正在关闭开机自启动…");
        try {
          const result = await requestJsonStrict("/autostart/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: Boolean(enabled) }) });
          current = result || current;
          busy = false;
          applyServerState();
          await load();
        } catch (err) {
          busy = false;
          const status = err?.status;
          current = err?.details || current;
          if (status === 403) setHint("仅本机 / 浏览器插件可修改此设置。");
          else if (status === 409) setHint(disabledHint(current));
          else setHint("无法连接后端或保存失败，请重试。");
          await load();
        }
      }
      checkbox.addEventListener("change", () => void apply(Boolean(checkbox.checked)));
      void load();
      return { reload: load };
    }

    function applyConfig(config) {
      if (!config || typeof config !== "object") return;
      state.config = config;
      const scheduler = config.scheduler || {};
      setSelect("schedulerEnabled", scheduler.enabled === false ? "off" : "on");
      setSelect("pauseDisconnect", scheduler.pause_on_extension_disconnect === false ? "keep" : "pause");
      setInput("extensionDisconnectGrace", scheduler.extension_disconnect_grace_seconds);
      setInput("poolTarget", scheduler.pool_target_count);
      setInput("accountSyncInterval", scheduler.account_sync_interval_hours);
      setInput("refreshCheckInterval", scheduler.refresh_check_interval_seconds);
      setInput("signalEventThreshold", scheduler.signal_event_threshold);
      setInput("feedbackBatchThreshold", scheduler.feedback_batch_threshold);
      setInput("trendingRefreshHours", scheduler.trending_refresh_hours);
      setInput("exploreRefreshHours", scheduler.explore_refresh_hours);
      setInput("discoveryLimit", scheduler.discovery_limit);
      setInput("proactivePushInterval", scheduler.proactive_push_interval_seconds);
      setInput("speculatorIdleInterval", scheduler.speculator_idle_interval_minutes);
      setSelect("autoUpdate", scheduler.auto_update_enabled === true ? "on" : "off");
      setInput("autoUpdateInterval", scheduler.auto_update_check_interval_hours);
      setInput("shareBilibili", scheduler.pool_source_shares?.bilibili);
      setInput("shareXhs", scheduler.pool_source_shares?.xiaohongshu);
      setInput("shareDouyin", scheduler.pool_source_shares?.douyin);
      setInput("shareYoutube", scheduler.pool_source_shares?.youtube);
      setInput("shareTwitter", scheduler.pool_source_shares?.twitter);
      setInput("shareZhihu", scheduler.pool_source_shares?.zhihu);
      setInput("speculationInterval", scheduler.speculation_interval_minutes);
      setInput("speculationTtl", scheduler.speculation_ttl_days);
      setInput("speculationCooldown", scheduler.speculation_cooldown_days);
      setInput("speculationThreshold", scheduler.speculation_confirmation_threshold);
      setInput("speculationMaxActive", scheduler.speculation_max_active);
      setInput("speculationMaxPrimary", scheduler.speculation_max_primary_interests);
      setInput("speculationMaxSecondary", scheduler.speculation_max_secondary_interests);

      const discovery = config.discovery || {};
      setSelect("multimodalEvaluationEnabled", discovery.multimodal_evaluation_enabled ? "on" : "off");
      setInput("multimodalBatchSize", discovery.multimodal_batch_size);
      setInput("multimodalImageMaxPx", discovery.multimodal_image_max_px);
      setInput("multimodalImageQuality", discovery.multimodal_image_quality);
      setInput("multimodalImageTimeout", discovery.multimodal_image_timeout_seconds);
      const multimodalStatus = $("#multimodalEvaluationStatus");
      if (multimodalStatus) {
        multimodalStatus.textContent = discovery.multimodal_evaluation_enabled ? "开启" : "关闭";
      }

      setSelect("language", config.language || "zh");
      setInput("dataDir", config.data_dir);
      setInput("storageDbPath", config.storage?.db_path);

      const llm = config.llm || {};
      const provider = llm.default_provider || llm.provider;
      setSelect("llmProvider", provider);
      const fallbackProvider = llm.fallback_provider || "";
      setSelect("llmFallbackProvider", fallbackProvider);
      setInput("llmConcurrency", llm.concurrency ?? 3);
      setInput("llmTimeout", llm.timeout);
      setSelect("llmAuthMode", llm.openai?.auth_mode || "api_key");
      if (provider) {
        setInput("llmModel", llm[provider]?.model);
        setInput("llmApiKey", llm[provider]?.api_key);
        setInput("llmBaseUrl", llm[provider]?.base_url);
      }
      if (fallbackProvider) {
        setSelect("llmFallbackAuthMode", llm[fallbackProvider]?.auth_mode || "api_key");
        setInput("llmFallbackModel", llm[fallbackProvider]?.model);
        setInput("llmFallbackApiKey", llm[fallbackProvider]?.api_key);
        setInput("llmFallbackBaseUrl", llm[fallbackProvider]?.base_url);
      } else {
        setSelect("llmFallbackAuthMode", "api_key");
        setInput("llmFallbackModel", "");
        setInput("llmFallbackApiKey", "");
        setInput("llmFallbackBaseUrl", "");
      }
      setInput("openrouterReferer", llm.openrouter?.http_referer);
      setInput("openrouterTitle", llm.openrouter?.x_title);
      setSelect("deepseekReasoning", llm.deepseek?.reasoning_effort || "");
      setSelect("embeddingProvider", llm.embedding?.provider || "");
      const embeddingFallbackProvider = llm.embedding?.fallback_provider || "";
      setSelect("embeddingFallbackProvider", embeddingFallbackProvider);
      setInput("embeddingModel", llm.embedding?.model);
      setInput("embeddingApiKey", llm.embedding?.api_key);
      setInput("embeddingBaseUrl", llm.embedding?.base_url);
      setInput("embeddingOutputDimensionality", llm.embedding?.output_dimensionality ?? 1024);
      setInput("embeddingSimilarity", llm.embedding?.similarity_threshold);
      if (embeddingFallbackProvider) {
        setInput("embeddingFallbackModel", llm[embeddingFallbackProvider]?.model);
        setInput("embeddingFallbackApiKey", llm[embeddingFallbackProvider]?.api_key);
        setInput("embeddingFallbackBaseUrl", llm[embeddingFallbackProvider]?.base_url);
      } else {
        setInput("embeddingFallbackModel", "");
        setInput("embeddingFallbackApiKey", "");
        setInput("embeddingFallbackBaseUrl", "");
      }
      setSelect("moduleSoulProvider", llm.soul?.provider || "");
      setInput("moduleSoulModel", llm.soul?.model);
      setSelect("moduleDiscoveryProvider", llm.discovery?.provider || "");
      setInput("moduleDiscoveryModel", llm.discovery?.model);
      setSelect("moduleRecommendationProvider", llm.recommendation?.provider || "");
      setInput("moduleRecommendationModel", llm.recommendation?.model);
      setSelect("moduleEvaluationProvider", llm.evaluation?.provider || "");
      setInput("moduleEvaluationModel", llm.evaluation?.model);

      setSelect("biliAuth", config.bilibili?.auth_method || "cookie");
      setCookieOverrideInput("biliCookie", config.bilibili?.cookie, " B 站");
      setInput("biliBrowserExecutable", config.bilibili?.browser_executable);
      setSelect("biliBrowserHeaded", config.bilibili?.browser_headed === true ? "on" : "off");
      setSelect("bilibiliEnabled", config.sources?.bilibili?.enabled === false ? "off" : "on");
      setInput("sourcesBrowserCdp", config.sources?.browser?.cdp_url);
      setSelect("sourcesBrowserHeaded", config.sources?.browser?.headed === true ? "on" : "off");
      setSelect("xhsEnabled", config.sources?.xiaohongshu?.enabled === true ? "on" : "off");
      setInput("xhsDailySearchBudget", config.sources?.xiaohongshu?.daily_search_budget);
      setInput("xhsDailyCreatorBudget", config.sources?.xiaohongshu?.daily_creator_budget);
      setInput("xhsTaskInterval", config.sources?.xiaohongshu?.task_interval_seconds);
      setSelect("douyinEnabled", config.sources?.douyin?.enabled === true ? "on" : "off");
      setCookieOverrideInput("douyinCookie", config.sources?.douyin?.cookie, "抖音");
      setInput("douyinCookieEnv", config.sources?.douyin?.cookie_env);
      setInput("douyinDailySearchBudget", config.sources?.douyin?.daily_search_budget);
      setInput("douyinDailyHotBudget", config.sources?.douyin?.daily_hot_budget);
      setInput("douyinDailyFeedBudget", config.sources?.douyin?.daily_feed_budget);
      setInput("douyinRequestInterval", config.sources?.douyin?.request_interval_seconds);
      setSelect("youtubeEnabled", config.sources?.youtube?.enabled === true ? "on" : "off");
      setInput("youtubeDailySearchBudget", config.sources?.youtube?.daily_search_budget);
      setInput("youtubeDailyTrendingBudget", config.sources?.youtube?.daily_trending_budget);
      setInput("youtubeDailyChannelBudget", config.sources?.youtube?.daily_channel_budget);
      setInput("youtubeRequestInterval", config.sources?.youtube?.request_interval_seconds);
      setInput("youtubeMinInterval", config.sources?.youtube?.min_interval_minutes);
      setSelect("twitterEnabled", config.sources?.twitter?.enabled === true ? "on" : "off");
      setCookieOverrideInput("twitterCookie", config.sources?.twitter?.cookie, " X");
      setInput("twitterCookieEnv", config.sources?.twitter?.cookie_env);
      setInput("twitterDailySearchBudget", config.sources?.twitter?.daily_search_budget);
      setInput("twitterDailyFeedBudget", config.sources?.twitter?.daily_feed_budget);
      setInput("twitterDailyCreatorBudget", config.sources?.twitter?.daily_creator_budget);
      setInput("twitterRequestInterval", config.sources?.twitter?.request_interval_seconds);
      setInput("twitterMinInterval", config.sources?.twitter?.min_interval_minutes);
      setSelect("zhihuEnabled", config.sources?.zhihu?.enabled === true ? "on" : "off");
      setZhihuSourceModes(config.sources?.zhihu?.source_modes);
      setInput("zhihuDailySearchBudget", config.sources?.zhihu?.daily_search_budget);
      setInput("zhihuDailyHotBudget", config.sources?.zhihu?.daily_hot_budget);
      setInput("zhihuDailyFeedBudget", config.sources?.zhihu?.daily_feed_budget);
      setInput("zhihuDailyCreatorBudget", config.sources?.zhihu?.daily_creator_budget);
      setInput("zhihuDailyRelatedBudget", config.sources?.zhihu?.daily_related_budget);
      setInput("zhihuRequestInterval", config.sources?.zhihu?.request_interval_seconds);
      setInput("zhihuMinInterval", config.sources?.zhihu?.min_interval_minutes);
      void renderSourcesStatus();
      void renderSourceCredentials();

      setSelect("logLevel", config.logging?.level || "INFO");
      setSelect("logFileLevel", config.logging?.file_level || "DEBUG");
      setInput("logPath", resolveLogPath(config.logging));
      setInput("logMaxFileSize", config.logging?.max_file_size_mb);
      setInput("logBackupCount", config.logging?.backup_count);
      setInput("logAggregateBudget", config.logging?.aggregate_budget_mb);
      setInput("logUnmanagedTruncate", config.logging?.unmanaged_truncate_mb);
      setInput("logUnmanagedMaxAge", config.logging?.unmanaged_max_age_days);

      if ($("#configStatus")) $("#configStatus").value = "配置已从后端加载。";
      if (state.runtimeStatus) applyRuntimeStatus(state.runtimeStatus);
      restoreFrontendSettings();
    }

    function normalizeDelight(item) {
      if (!item) return null;
      // 后端 pending-batch 对喜欢过的候选下发 state="liked"，重灌后恢复
      // 「已喜欢」文案，让用户看出这条已经表过态。
      const serverState = String(item.state ?? "");
      const fallbackMessage = serverState === "liked" ? "好，这类多来点。" : "";
      return {
        type: "delight",
        bvid: String(item.bvid ?? item.content_id ?? ""),
        title: decodeHtmlEntities(item.title ?? "发现了一条你可能会意外喜欢的内容"),
        reason: decodeHtmlEntities(item.delight_reason ?? item.reason ?? item.delight_hook ?? item.message ?? "这条来自后端高惊喜分候选。"),
        cover_url: normalizeImageUrl(item.cover_url ?? item.cover ?? item.pic ?? item.thumbnail_url ?? item.thumbnail ?? item.image_url),
        content_url: String(item.content_url ?? ""),
        source_platform: String(item.source_platform ?? item.platform ?? "bilibili"),
        chat_turn_id: String(item.chat_turn_id ?? ""),
        chat_reply: String(item.chat_reply ?? item.reply ?? ""),
        chat_draft: String(item.chat_draft ?? ""),
        state: serverState,
        response_message: String(item.response_message ?? "") || fallbackMessage,
        turns: delightTurnList(item.turns)
      };
    }

    function renderDelightCover(delight) {
      const thumb = $("#delightBanner .thumb");
      if (!thumb) return;
      const url = imageProxyUrl(delight?.cover_url);
      thumb.replaceChildren();
      thumb.classList.toggle("has-image", Boolean(url));
      if (!url) return;
      const image = document.createElement("img");
      if (isCrossOriginBase()) image.crossOrigin = "anonymous";
      image.alt = "";
      image.loading = "eager";
      image.fetchPriority = "high";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.src = url;
      image.addEventListener("error", () => {
        image.remove();
        thumb.classList.remove("has-image");
      });
      thumb.append(image);
    }

    // 惊喜页：一页 6 张卡片网格 + 换一换。展示层从 state.delights 取一页，
    // 换一换只在展示层打乱取页，不破坏实时流合并的队列本身。
    const DELIGHT_PAGE_SIZE = 6;

    function delightCardHtml(item) {
      const title = escapeHtml(item.title || "无标题");
      const reason = escapeHtml(item.reason || item.delight_reason || "");
      const platform = String(item.source_platform || "bilibili");
      const url = escapeHtml(item.content_url || "");
      const liked = item.state === "liked" ? " is-liked" : "";
      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${platformLabelHtml(platform)}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          <div class="video-card-meta">
            <span class="video-card-author">${reason.slice(0, 48)}</span>
            <span class="video-card-platform">${platformLabelHtml(platform)}</span>
          </div>
          <div class="video-card-footer${liked}">
            <button class="feedback-icon-btn" data-action="like" type="button" aria-label="喜欢" title="喜欢">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>
            </button>
            <button class="feedback-icon-btn" data-action="dismiss" type="button" aria-label="忽略" title="忽略">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18M9.84 9.91A3 3 0 0 0 12 15c.82 0 1.57-.33 2.11-.87M6.5 6.65A10.45 10.45 0 0 0 2.46 12C3.73 16.06 7.52 19 12 19c1.99 0 3.84-.58 5.4-1.58M11 5.05c.33-.03.66-.05 1-.05 4.48 0 8.27 2.94 9.54 7a10.5 10.5 0 0 1-1.19 2.5"/></svg>
            </button>
            <button class="feedback-icon-btn watch-later-btn" data-action="watch-later" type="button" aria-label="稍后再看" title="稍后再看">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>
            </button>
            <button class="feedback-icon-btn favorite-btn" data-action="favorite" type="button" aria-label="收藏" title="收藏">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>
            </button>
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    function renderDelightGrid() {
      const grid = $("#delightGrid");
      if (!grid) return;
      const all = state.delights || [];
      const count = $("#delightCount");
      if (count) count.textContent = `${all.length} 条候选`;
      if (!all.length) {
        grid.innerHTML = `
          <div class="obs-section">
            <div class="empty-state">
              <p>暂无惊喜候选，后端产生新的高惊喜候选后会出现在这里。</p>
            </div>
          </div>`;
        scheduleActivityRailHeightSync();
        return;
      }
      const page = Array.isArray(state.delightPage) && state.delightPage.length
        ? state.delightPage
        : all.slice(0, DELIGHT_PAGE_SIZE);
      grid.replaceChildren(
        ...page.map((item) => {
          const card = document.createElement("div");
          card.className = "video-card is-minimal";
          card.innerHTML = delightCardHtml(item);
          card.addEventListener("click", (e) => {
            if (e.target.closest("[data-action]")) return;
            respondDelight(item, "view");
          });
          card.querySelectorAll("[data-action]").forEach((btn) => {
            btn.addEventListener("click", (e) => {
              e.stopPropagation();
              respondDelight(item, btn.dataset.action, btn);
            });
          });
          return card;
        })
      );
      scheduleActivityRailHeightSync();
    }

    function shuffleDelights() {
      const all = (state.delights || []).slice();
      if (!all.length) return;
      for (let i = all.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [all[i], all[j]] = [all[j], all[i]];
      }
      state.delightPage = all.slice(0, DELIGHT_PAGE_SIZE);
      renderDelightGrid();
    }

    function applyDelights(payload) {
      const hasQueuePayload = Array.isArray(payload?.items) || Boolean(payload?.item);
      if (!hasQueuePayload) return;
      const items = Array.isArray(payload?.items) ? payload.items : payload.item ? [payload.item] : [];
      const normalized = items.map(normalizeDelight).filter(Boolean);
      const existingByBvid = new Map(state.delights.map((item) => [String(item.bvid || ""), item]));
      state.delights = [];
      for (const item of normalized) {
        const key = String(item.bvid || "");
        if (!key) continue;
        const existingIndex = state.delights.findIndex((current) => String(current.bvid || "") === key);
        const merged = mergeDelightItem(existingByBvid.get(key) || state.delights[existingIndex], item);
        if (existingIndex >= 0) state.delights[existingIndex] = merged;
        else state.delights.push(merged);
      }
      state.delightPage = null;
      renderDelightGrid();
    }

    function mergeMessages(items) {
      for (const raw of items) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!state.messages.some((msg) => messageKey(msg) === key)) state.messages.push(item);
      }
      renderMessages();
      applyRuntimeStatus({ unread_count: getRenderableMessages().length });
    }

    async function fetchDelightQueue() {
      const payload = await requestJson(ENDPOINTS.delightBatch);
      applyDelights(payload);
    }

    function handleRuntimeEvent(event) {
      if (!event?.type) return;
      applyRuntimeStatus({ ...event, live_summary: event.message || event.live_summary || event.type });
      // refresh.pool_updated / recommendation.reshuffled are pool-status signals, not
      // list-replacement signals: hydrating here would wipe locally appended cards
      // (/api/recommendations only returns the latest top window). Header/pool counts
      // still update via applyRuntimeStatus above; user-initiated 换一批 / 加载更多 replace
      // the list explicitly. Matches recommend.js + popup.js (fix 79042ce).
      if (["config_reloaded"].includes(event.type)) scheduleBackendHydration();
      if (["init_progress", "init_failed", "init_completed"].includes(event.type)) {
        void refreshInitStatus({ schedule: event.type === "init_progress" });
      }
      if (event.type === "refresh.pool_updated" && Boolean(state.initStatus?.initialized)) {
        void refreshInitStatus({ schedule: false });
      }
      if (event.type === "activity.added") scheduleActivityPageRefresh();
      if (
        event.type === "profile_updated" ||
        event.type === "interest.confirmed" ||
        event.type === "interest.rejected" ||
        event.type === "interest.chat" ||
        event.type === "avoidance.confirmed" ||
        event.type === "avoidance.rejected" ||
        event.type === "avoidance.chat"
      ) void refreshProfile();
      if (event.type === "delight.candidate" && event.bvid) {
        const delight = normalizeDelight(event);
        if (delight) {
          const key = String(delight.bvid || "");
          const existingIndex = state.delights.findIndex((item) => String(item.bvid || "") === key);
          if (existingIndex >= 0) {
            state.delights[existingIndex] = mergeDelightItem(state.delights[existingIndex], delight);
          } else {
            state.delights.push(delight);
          }
          state.delightPage = null;
          renderDelightGrid();
        }
      }
      if (
        event.type === "backend_update_available" ||
        event.type === "backend_restart_pending" ||
        event.type === "backend_update_failed"
      ) void refreshUpdateStatus();
      if (event.type === "backend_update_available") {
        const newVersion = event.latest_version ? `v${event.latest_version}` : "新版本";
        // desktop-v* tags = installer releases for frozen bundles; guide the
        // user to download instead of implying an in-place update will happen.
        showToast(String(event.latest_tag || "").startsWith("desktop-v")
          ? `发现新版安装包 ${newVersion}，请前往 GitHub Releases 下载升级`
          : `发现后端新版本 ${newVersion}`);
      }
      if (event.type === "delight.refreshed") scheduleDelightQueueRefresh();
      if (event.type === "notification.pending" && event.bvid) mergeMessages([{ ...event, type: "notification" }]);
      if (event.type === "interest.probe" && event.domain) mergeMessages([{ type: "interest.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个兴趣方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
      if (event.type === "avoidance.probe" && event.domain) mergeMessages([{ type: "avoidance.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个避雷方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
    }

    function connectRuntimeStream() {
      if (state.runtimeSocket) state.runtimeSocket.close();
      try {
        const socket = new WebSocket(getRuntimeStreamUrl());
        state.runtimeSocket = socket;
        socket.addEventListener("open", () => { $("#statusLabel").textContent = "实时连接中"; });
        socket.addEventListener("message", (event) => {
          try { handleRuntimeEvent(JSON.parse(event.data)); } catch {}
        });
        socket.addEventListener("close", () => {
          if (state.runtimeSocket === socket) window.setTimeout(connectRuntimeStream, 3000);
        });
        socket.addEventListener("error", () => { $("#statusLabel").textContent = "实时流断开"; });
      } catch {
        $("#statusLabel").textContent = "实时流不可用";
      }
    }

    async function refreshProfile() {
      const payload = await requestJson(ENDPOINTS.profile);
      const profile = payload?.profile || payload;
      if (profile && profile.initialized !== false) {
        state.profile = profile;
        hydrateInboxFromSpeculations(profile.speculative_interests);
        hydrateInboxFromSpeculations(profile.speculative_avoidances, "avoidance.probe");
        renderRail();
        renderProfileDetails();
        renderMessages();
      }
    }

    async function hydrateFromBackend() {
      // 先并行获取推荐和健康检查，尽快渲染
      // 每次打开/刷新首页都重新换一批（POST /recommendations/reshuffle 会从池子
      // serve 新一批并写入历史；GET 是幂等的，刷新会一直看到同一批，不符合预期）。
      const [health, recs] = await Promise.all([
        requestJson(ENDPOINTS.health).catch(() => null),
        requestJson(ENDPOINTS.reshuffle, { method: "POST" }).catch(() => null)
      ]);
      if (health) $("#statusLabel").textContent = "已连接本地后端";
      let recommendationItems = Array.isArray(recs) ? recs : asArray(recs?.items);
      // 池子为空（罕见，如首次部署/刚耗尽）时，回退到 GET 的 bootstrap 逻辑保证首屏有内容
      if (!recommendationItems.length) {
        const fallback = await requestJson(ENDPOINTS.recommendations).catch(() => null);
        recommendationItems = Array.isArray(fallback) ? fallback : asArray(fallback?.items);
      }
      state.videos = normalizeRecommendationList(recommendationItems);
      // 推荐先渲染，其他数据后台加载
      renderAll();

      // 后台加载其余数据，不阻塞页面展示
      const [runtime, activity, profile, delights, notification, chatTurns, delightChatTurns, config, initStatus] = await Promise.all([
        requestJson(ENDPOINTS.runtimeStatus).catch(() => null),
        requestJson(`${ENDPOINTS.activityFeed}?limit=5`).catch(() => null),
        requestJson(ENDPOINTS.profile).catch(() => null),
        requestJson(ENDPOINTS.delightBatch).catch(() => null),
        requestJson(ENDPOINTS.notificationPending).catch(() => null),
        requestJson(`${ENDPOINTS.chatTurns}?session=webui&scope=chat&limit=20`).catch(() => null),
        requestJson(`${ENDPOINTS.chatTurns}?session=webui&scope=delight&limit=80`).catch(() => null),
        requestJson(ENDPOINTS.config).catch(() => null),
        requestJson(ENDPOINTS.initStatus).catch(() => null)
      ]);
      if (initStatus) state.initStatus = initStatus;
      if (activity) {
        state.activity = activity;
        state.activityItems = asArray(activity.items);
        state.activityCursor = activity.next_cursor || activity.next || "";
        state.activityHasMore = Boolean(activity.has_more && state.activityCursor);
      }
      const profilePayload = profile?.profile || profile;
      if (profilePayload && profilePayload.initialized !== false) {
        state.profile = profilePayload;
        hydrateInboxFromSpeculations(profilePayload.speculative_interests);
        hydrateInboxFromSpeculations(profilePayload.speculative_avoidances, "avoidance.probe");
      }
      const chatItems = Array.isArray(chatTurns) ? chatTurns : asArray(chatTurns?.items);
      if (chatItems.length) {
        state.chat = chatItems.flatMap((turn) => [
          { role: "user", text: turn.message || turn.user_message || "" },
          { role: "agent", text: turn.reply || turn.assistant_message || turn.status || "等待后端回复中。" }
        ]).filter((item) => item.text);
      }
      const effectiveRuntime = await requestJson(ENDPOINTS.runtimeStatus).catch(() => runtime?.status || runtime);
      applyRuntimeStatus(effectiveRuntime?.status || effectiveRuntime);
      applyDelights(delights);
      const delightChatItems = Array.isArray(delightChatTurns) ? delightChatTurns : asArray(delightChatTurns?.items);
      for (const turn of delightChatItems.filter(Boolean)) applyTurnToDelight({ ...turn, scope: turn.scope || "delight" });
      if (notification?.item) mergeMessages([{ ...notification.item, type: "notification" }]);
      applyConfig(config?.config || config);
      // 后台数据加载完成后，重新渲染非推荐部分
      renderAll();
    }

    function renderAll() {
      const steps = [renderViewTabs, renderReshuffleToggle, renderFilters, renderVideos, syncSourceMetric, renderRail, renderProfileDetails, renderMessages, renderChat, renderPoolStatus];
      for (const step of steps) {
        try { step(); } catch (error) { showFatal(error, step.name || "渲染"); }
      }
      scheduleActivityRailHeightSync();
    }

    function buildConfigUpdate() {
      const provider = $("#llmProvider").value;
      const fallbackProvider = getInput("llmFallbackProvider");
      const llmProviderConfig = { model: getInput("llmModel") };
      if (provider === "openai") llmProviderConfig.auth_mode = getInput("llmAuthMode") || "api_key";
      if (getInput("llmApiKey")) llmProviderConfig.api_key = getInput("llmApiKey");
      if (getInput("llmBaseUrl")) llmProviderConfig.base_url = getInput("llmBaseUrl");
      const llmFallbackConfig = { model: getInput("llmFallbackModel") };
      if (fallbackProvider === "openai") llmFallbackConfig.auth_mode = getInput("llmFallbackAuthMode") || "api_key";
      if (getInput("llmFallbackApiKey")) llmFallbackConfig.api_key = getInput("llmFallbackApiKey");
      if (getInput("llmFallbackBaseUrl")) llmFallbackConfig.base_url = getInput("llmFallbackBaseUrl");
      const logPath = splitLogPath(getInput("logPath"), state.config?.logging);
      const embeddingFallbackProvider = getInput("embeddingFallbackProvider");
      const embeddingFallbackConfig = { model: getInput("embeddingFallbackModel") };
      if (getInput("embeddingFallbackApiKey")) embeddingFallbackConfig.api_key = getInput("embeddingFallbackApiKey");
      if (getInput("embeddingFallbackBaseUrl")) embeddingFallbackConfig.base_url = getInput("embeddingFallbackBaseUrl");
      const embedding = {
        provider: $("#embeddingProvider").value,
        fallback_enabled: Boolean(embeddingFallbackProvider),
        fallback_provider: embeddingFallbackProvider,
        model: getInput("embeddingModel"),
        output_dimensionality: Math.max(0, getIntInput("embeddingOutputDimensionality", 1024)),
        similarity_threshold: getFloatInput("embeddingSimilarity", 0.82)
      };
      if (getInput("embeddingApiKey")) embedding.api_key = getInput("embeddingApiKey");
      if (getInput("embeddingBaseUrl")) embedding.base_url = getInput("embeddingBaseUrl");
      const cookie = getInput("biliCookie");
      const douyinCookie = getInput("douyinCookie");
      const twitterCookie = getInput("twitterCookie");
      const llm = {
        ...(state.config?.llm || {}),
        default_provider: provider,
        fallback_enabled: Boolean(fallbackProvider),
        fallback_provider: fallbackProvider,
        concurrency: getIntInput("llmConcurrency", 3),
        timeout: getIntInput("llmTimeout", 60),
        [provider]: { ...(state.config?.llm?.[provider] || {}), ...llmProviderConfig },
        embedding: { ...(state.config?.llm?.embedding || {}), ...embedding },
        soul: { ...(state.config?.llm?.soul || {}), provider: getInput("moduleSoulProvider"), model: getInput("moduleSoulModel") },
        discovery: { ...(state.config?.llm?.discovery || {}), provider: getInput("moduleDiscoveryProvider"), model: getInput("moduleDiscoveryModel") },
        recommendation: { ...(state.config?.llm?.recommendation || {}), provider: getInput("moduleRecommendationProvider"), model: getInput("moduleRecommendationModel") },
        evaluation: { ...(state.config?.llm?.evaluation || {}), provider: getInput("moduleEvaluationProvider"), model: getInput("moduleEvaluationModel") }
      };
      if (fallbackProvider && fallbackProvider !== provider) {
        llm[fallbackProvider] = {
          ...(state.config?.llm?.[fallbackProvider] || {}),
          ...llmFallbackConfig
        };
      }
      if (embeddingFallbackProvider) {
        llm[embeddingFallbackProvider] = {
          ...(llm[embeddingFallbackProvider] || state.config?.llm?.[embeddingFallbackProvider] || {}),
          ...embeddingFallbackConfig
        };
      }
      if (getInput("openrouterReferer") || getInput("openrouterTitle")) {
        llm.openrouter = {
          ...(llm.openrouter || {}),
          http_referer: getInput("openrouterReferer"),
          x_title: getInput("openrouterTitle")
        };
      }
      const deepseekReasoning = getInput("deepseekReasoning");
      llm.deepseek = {
        ...(llm.deepseek || state.config?.llm?.deepseek || {}),
        reasoning_effort: deepseekReasoning
      };
      return {
        language: getInput("language") || "zh",
        data_dir: getInput("dataDir"),
        llm,
        bilibili: {
          auth_method: $("#biliAuth").value,
          ...(cookie ? { cookie } : {}),
          browser_executable: getInput("biliBrowserExecutable"),
          browser_headed: $("#biliBrowserHeaded").value === "on"
        },
        sources: {
          browser: {
            cdp_url: getInput("sourcesBrowserCdp"),
            headed: $("#sourcesBrowserHeaded").value === "on"
          },
          bilibili: {
            enabled: $("#bilibiliEnabled").value === "on"
          },
          xiaohongshu: {
            enabled: $("#xhsEnabled").value === "on",
            daily_search_budget: getIntInput("xhsDailySearchBudget", 0),
            daily_creator_budget: getIntInput("xhsDailyCreatorBudget", 0),
            task_interval_seconds: getIntInput("xhsTaskInterval", 45)
          },
          douyin: {
            enabled: $("#douyinEnabled").value === "on",
            mode: "direct",
            ...(douyinCookie ? { cookie: douyinCookie } : {}),
            cookie_env: getInput("douyinCookieEnv"),
            daily_search_budget: getIntInput("douyinDailySearchBudget", 0),
            daily_hot_budget: getIntInput("douyinDailyHotBudget", 0),
            daily_feed_budget: getIntInput("douyinDailyFeedBudget", 0),
            request_interval_seconds: getIntInput("douyinRequestInterval", 2)
          },
          youtube: {
            enabled: $("#youtubeEnabled").value === "on",
            daily_search_budget: getIntInput("youtubeDailySearchBudget", 0),
            daily_trending_budget: getIntInput("youtubeDailyTrendingBudget", 0),
            daily_channel_budget: getIntInput("youtubeDailyChannelBudget", 0),
            request_interval_seconds: getIntInput("youtubeRequestInterval", 2),
            min_interval_minutes: getIntInput("youtubeMinInterval", 60)
          },
          twitter: {
            enabled: $("#twitterEnabled").value === "on",
            mode: "cookie",
            ...(twitterCookie ? { cookie: twitterCookie } : {}),
            cookie_env: getInput("twitterCookieEnv"),
            daily_search_budget: getIntInput("twitterDailySearchBudget", 0),
            daily_feed_budget: getIntInput("twitterDailyFeedBudget", 0),
            daily_creator_budget: getIntInput("twitterDailyCreatorBudget", 0),
            request_interval_seconds: getIntInput("twitterRequestInterval", 3),
            min_interval_minutes: getIntInput("twitterMinInterval", 60)
          },
          zhihu: {
            enabled: $("#zhihuEnabled").value === "on",
            source_modes: collectZhihuSourceModes(),
            daily_search_budget: getIntInput("zhihuDailySearchBudget", 0),
            daily_hot_budget: getIntInput("zhihuDailyHotBudget", 0),
            daily_feed_budget: getIntInput("zhihuDailyFeedBudget", 0),
            daily_creator_budget: getIntInput("zhihuDailyCreatorBudget", 0),
            daily_related_budget: getIntInput("zhihuDailyRelatedBudget", 0),
            request_interval_seconds: getIntInput("zhihuRequestInterval", 3),
            min_interval_minutes: getIntInput("zhihuMinInterval", 60)
          }
        },
        scheduler: {
          enabled: $("#schedulerEnabled").value === "on",
          pause_on_extension_disconnect: $("#pauseDisconnect").value === "pause",
          extension_disconnect_grace_seconds: getIntInput("extensionDisconnectGrace", 90),
          pool_target_count: getIntInput("poolTarget", 300),
          account_sync_interval_hours: getIntInput("accountSyncInterval", 6),
          refresh_check_interval_seconds: getIntInput("refreshCheckInterval", 60),
          signal_event_threshold: getIntInput("signalEventThreshold", 6),
          feedback_batch_threshold: getIntInput("feedbackBatchThreshold", 3),
          trending_refresh_hours: getIntInput("trendingRefreshHours", 3),
          explore_refresh_hours: getIntInput("exploreRefreshHours", 12),
          discovery_limit: getIntInput("discoveryLimit", 30),
          delight_queue_limit: getDelightQueueLimit(),
          proactive_push_interval_seconds: getIntInput("proactivePushInterval", 120),
          speculator_idle_interval_minutes: getIntInput("speculatorIdleInterval", 30),
          pool_source_shares: {
            bilibili: getIntInput("shareBilibili", 5),
            xiaohongshu: getIntInput("shareXhs", 1),
            douyin: getIntInput("shareDouyin", 1),
            youtube: getIntInput("shareYoutube", 1),
            twitter: getIntInput("shareTwitter", 1),
            zhihu: getIntInput("shareZhihu", 1)
          },
          speculation_interval_minutes: getIntInput("speculationInterval", 10),
          speculation_ttl_days: getIntInput("speculationTtl", 3),
          speculation_cooldown_days: getIntInput("speculationCooldown", 7),
          speculation_confirmation_threshold: getIntInput("speculationThreshold", 3),
          speculation_max_active: getIntInput("speculationMaxActive", 5),
          speculation_max_primary_interests: getIntInput("speculationMaxPrimary", 15),
          speculation_max_secondary_interests: getIntInput("speculationMaxSecondary", 60),
          auto_update_enabled: $("#autoUpdate").value === "on",
          auto_update_check_interval_hours: getIntInput("autoUpdateInterval", 6)
        },
        discovery: {
          ...(state.config?.discovery || {}),
          multimodal_evaluation_enabled: $("#multimodalEvaluationEnabled").value === "on",
          multimodal_batch_size: getIntInput("multimodalBatchSize", 8),
          multimodal_image_max_px: getIntInput("multimodalImageMaxPx", 384),
          multimodal_image_quality: getIntInput("multimodalImageQuality", 72),
          multimodal_image_timeout_seconds: getIntInput("multimodalImageTimeout", 6)
        },
        storage: { db_path: getInput("storageDbPath") },
        logging: {
          level: getInput("logLevel") || "INFO",
          file_level: getInput("logFileLevel") || "DEBUG",
          directory: logPath.directory,
          filename: logPath.filename,
          file_path: getInput("logPath"),
          max_file_size_mb: getIntInput("logMaxFileSize", 100),
          backup_count: getIntInput("logBackupCount", 1),
          aggregate_budget_mb: getIntInput("logAggregateBudget", 500),
          unmanaged_truncate_mb: getIntInput("logUnmanagedTruncate", 200),
          unmanaged_max_age_days: getIntInput("logUnmanagedMaxAge", 30)
        }
      };
    }

    const UPDATE_REASON_TEXT = {
      dirty_worktree: "代码目录有未提交改动，更新被阻止",
      unsupported_install_mode: "当前安装方式不支持自动更新",
      untrusted_remote: "git 远端不在允许列表，更新被阻止",
      branch_not_fast_forwardable: "本地代码与发布版本分叉，无法快进更新",
      merge_or_rebase_in_progress: "代码目录正在合并 / 变基，更新暂缓",
      github_rate_limited: "GitHub API 限流，请稍后再试",
      github_unreachable: "无法访问 GitHub 检查更新",
      missing_target_tag: "远端未找到目标版本标签",
      dependency_sync_failed: "更新后依赖安装失败",
      restart_failed: "更新后重启失败",
      no_backend_tag_yet: "远端暂无后端发布标签",
      prerelease_ignored: "仅有预发布版本，已忽略",
      already_applying: "正在更新中"
    };

    function formatUpdateCheckTime(iso) {
      if (!iso) return "";
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return "";
      return date.toLocaleString("zh-CN", { hour12: false });
    }

    function describeUpdateStatus(backend) {
      const reasonKey = backend.reason && backend.reason !== "none" ? String(backend.reason) : "";
      const reasonText = UPDATE_REASON_TEXT[reasonKey] || reasonKey;
      const current = backend.current_version ? `v${backend.current_version}` : "";
      const latest = backend.latest_version ? `v${backend.latest_version}` : "";
      const checkedAt = formatUpdateCheckTime(backend.last_check_at);
      const suffix = checkedAt ? `（${checkedAt} 检查）` : "";
      switch (backend.state) {
        case "disabled":
          return { text: `自动更新未开启${current ? `，当前版本 ${current}` : ""}。`, tone: "" };
        case "checking":
          return { text: "正在检查更新…", tone: "" };
        case "up_to_date":
          return { text: `已是最新版本${current ? ` ${current}` : ""}${reasonText ? `（${reasonText}）` : ""}${suffix}`, tone: "success" };
        case "update_available":
          return { text: `发现新版本 ${latest}（当前 ${current}），${backend.auto_update_enabled ? "将在下个检查周期自动更新" : "开启自动更新后将自动升级"}${suffix}`, tone: "" };
        case "applying":
          return { text: `正在更新到 ${latest || "新版本"}…`, tone: "" };
        case "restart_pending":
          return { text: "更新完成，等待后端重启生效。", tone: "success" };
        case "blocked":
          return { text: `更新被阻止：${reasonText || "未知原因"}${suffix}`, tone: "error" };
        case "unsupported":
          return { text: reasonText || "当前安装方式不支持自动更新。", tone: "error" };
        case "error":
          return { text: `更新检查出错：${reasonText || backend.last_error || "未知错误"}${suffix}`, tone: "error" };
        default:
          return { text: `尚未检查更新${current ? `，当前版本 ${current}` : ""}。`, tone: "" };
      }
    }

    // Frozen desktop bundles can't self-apply — the backend runs a check-only
    // loop against desktop-v* installer tags and the UI guides the user to
    // download the new installer instead.
    function describeFrozenUpdateStatus(backend) {
      const reasonKey = backend.reason && backend.reason !== "none" ? String(backend.reason) : "";
      const reasonText = UPDATE_REASON_TEXT[reasonKey] || reasonKey;
      const current = backend.current_version ? `v${backend.current_version}` : "";
      const latest = backend.latest_version ? `v${backend.latest_version}` : "";
      const checkedAt = formatUpdateCheckTime(backend.last_check_at);
      const suffix = checkedAt ? `（${checkedAt} 检查）` : "";
      switch (backend.state) {
        case "checking":
          return { text: "正在检查新版安装包…", tone: "" };
        case "up_to_date":
          return { text: `当前安装包已是最新${current ? ` ${current}` : ""}${suffix}`, tone: "success" };
        case "update_available":
          return { text: `发现新版安装包 ${latest}（当前 ${current}），桌面安装包不支持自动更新，请下载新版安装包完成升级${suffix}`, tone: "" };
        case "error":
          return { text: `检查新版安装包出错：${reasonText || backend.last_error || "未知错误"}${suffix}`, tone: "error" };
        default:
          return { text: `桌面安装包不支持自动应用更新；后台会定期检查新版安装包并在这里提醒下载${current ? `（当前 ${current}）` : ""}。`, tone: "" };
      }
    }

    function renderUpdateStatus(backend) {
      const line = $("#updateStatusLine");
      const actions = $("#updateActions");
      const checkBtn = $("#updateCheckBtn");
      const applyBtn = $("#updateApplyBtn");
      const downloadLink = $("#updateDownloadLink");
      if (!line) return;
      if (!backend || typeof backend !== "object") {
        line.hidden = true;
        if (actions) actions.hidden = true;
        return;
      }
      const mode = String(backend.install_mode || "");
      // Older backends predate install_mode — keep the toggle usable there.
      const unsupportedInstall = Boolean(mode) && mode !== "git";
      const isFrozen = mode === "frozen";
      const toggle = $("#autoUpdate");
      const interval = $("#autoUpdateInterval");
      // The toggle governs auto-apply, which non-git installs can never do —
      // frozen check-reminders run unconditionally on the backend side.
      if (toggle) toggle.disabled = unsupportedInstall;
      if (interval) interval.disabled = unsupportedInstall;
      if (isFrozen) {
        const { text, tone } = describeFrozenUpdateStatus(backend);
        line.dataset.tone = tone;
        line.textContent = text;
      } else if (unsupportedInstall) {
        line.dataset.tone = "error";
        line.textContent = "当前安装方式不支持自动更新（需要 git 克隆的安装目录）。";
      } else {
        const { text, tone } = describeUpdateStatus(backend);
        line.dataset.tone = tone;
        line.textContent = text;
      }
      line.hidden = false;
      // 立即检查 works on git checkouts AND frozen bundles (check-only there);
      // 立即应用 only when a newer tag is ready to fast-forward on git; the
      // download link replaces 立即应用 on frozen when a new installer exists.
      const lockActions = unsupportedInstall && !isFrozen;
      if (actions) actions.hidden = lockActions;
      if (checkBtn) checkBtn.disabled = lockActions || backend.state === "checking" || backend.state === "applying";
      if (applyBtn) {
        const canApply = !unsupportedInstall && backend.state === "update_available" && Boolean(backend.latest_tag);
        applyBtn.hidden = !canApply;
        applyBtn.disabled = !canApply || backend.state === "applying";
        if (canApply) applyBtn.dataset.tag = String(backend.latest_tag);
      }
      if (downloadLink) {
        const showDownload = isFrozen && backend.state === "update_available";
        downloadLink.hidden = !showDownload;
        if (showDownload) {
          downloadLink.href = backend.latest_tag
            ? `https://github.com/whiteguo233/OpenBiliClaw/releases/tag/${encodeURIComponent(String(backend.latest_tag))}`
            : "https://github.com/whiteguo233/OpenBiliClaw/releases";
        }
      }
    }

    async function refreshUpdateStatus() {
      const line = $("#updateStatusLine");
      wireUpdateActions();
      try {
        const payload = await requestJson(ENDPOINTS.updateStatus);
        renderUpdateStatus(payload?.backend || null);
      } catch {
        if (line) line.hidden = true;
      }
    }

    // Wire the 立即检查 / 立即应用 buttons once. Manual check runs /api/update/check
    // (ignores the auto-update toggle); apply posts the latest tag and the backend
    // fast-forwards + restarts — the runtime-stream events refresh the line live.
    function wireUpdateActions() {
      const checkBtn = $("#updateCheckBtn");
      const applyBtn = $("#updateApplyBtn");
      if (checkBtn && !checkBtn.dataset.wired) {
        checkBtn.dataset.wired = "1";
        checkBtn.addEventListener("click", async () => {
          const prev = checkBtn.textContent;
          checkBtn.disabled = true;
          checkBtn.textContent = "检查中…";
          try {
            const payload = await requestJsonStrict(ENDPOINTS.updateCheck, {
              method: "POST",
              timeoutMs: 60000,
              headers: { "Content-Type": "application/json" },
              body: "{}"
            });
            renderUpdateStatus(payload?.backend || null);
          } catch (error) {
            showToast("检查更新失败：" + (error?.message || "未知错误"));
          } finally {
            checkBtn.textContent = prev;
            checkBtn.disabled = false;
          }
        });
      }
      if (applyBtn && !applyBtn.dataset.wired) {
        applyBtn.dataset.wired = "1";
        applyBtn.addEventListener("click", async () => {
          const tag = applyBtn.dataset.tag || "";
          if (!tag) return;
          const prev = applyBtn.textContent;
          applyBtn.disabled = true;
          applyBtn.textContent = "应用中…";
          try {
            const body = await requestJsonStrict(ENDPOINTS.updateApply, {
              method: "POST",
              timeoutMs: 60000,
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ target: "backend", tag })
            });
            if (body?.accepted) {
              showToast("已开始更新，后端将在完成后自动重启…");
            } else {
              const reason = body?.reason;
              showToast("更新未开始：" + (UPDATE_REASON_TEXT[reason] || reason || "未知原因"));
            }
          } catch (error) {
            const reason = error?.details?.reason;
            showToast("更新未开始：" + (UPDATE_REASON_TEXT[reason] || reason || error?.message || "未知原因"));
          } finally {
            applyBtn.textContent = prev;
            applyBtn.disabled = false;
            void refreshUpdateStatus();
          }
        });
      }
    }

    function formatProbeResult(result) {
      const ok = Boolean(result?.ok);
      const provider = result?.provider ? ` ${result.provider}` : "";
      const model = result?.model ? ` / ${result.model}` : "";
      const latency = Number.isFinite(Number(result?.latency_ms)) && Number(result.latency_ms) > 0
        ? ` (${Math.round(Number(result.latency_ms))}ms)`
        : "";
      const detail = result?.message || result?.error || (ok ? "服务可用" : "服务不可用");
      return `${ok ? "可用" : "不可用"}${provider}${model}${latency}: ${detail}`;
    }

    async function probeConfigService(kind, config) {
      return await requestJsonStrict(ENDPOINTS.configProbe, {
        method: "POST",
        timeoutMs: 35000,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, config })
      });
    }

    function renderProbeResult(statusEl, result) {
      if (!statusEl) return;
      statusEl.dataset.tone = result?.ok ? "success" : "error";
      statusEl.textContent = formatProbeResult(result);
      const configStatus = $("#configStatus");
      if (configStatus) configStatus.value = formatProbeResult(result);
    }

    function renderProbePending(statusEl, label) {
      if (!statusEl) return;
      statusEl.dataset.tone = "pending";
      statusEl.textContent = `${label} 探测中…`;
    }

    async function runLlmConfigProbe() {
      const button = $("#probeLlm");
      const statusEl = $("#probeLlmStatus");
      if (button) button.disabled = true;
      renderProbePending(statusEl, "LLM");
      try {
        const result = await probeConfigService("llm", buildConfigUpdate());
        renderProbeResult(statusEl, result);
      } catch (error) {
        renderProbeResult(statusEl, {
          ok: false,
          error: configErrorMessage(error?.details) || error?.message || "LLM 探测失败"
        });
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runEmbeddingConfigProbe() {
      const button = $("#probeEmbedding");
      const statusEl = $("#probeEmbeddingStatus");
      if (button) button.disabled = true;
      renderProbePending(statusEl, "Embedding");
      try {
        const result = await probeConfigService("embedding", buildConfigUpdate());
        renderProbeResult(statusEl, result);
      } catch (error) {
        renderProbeResult(statusEl, {
          ok: false,
          error: configErrorMessage(error?.details) || error?.message || "Embedding 探测失败"
        });
      } finally {
        if (button) button.disabled = false;
      }
    }

    document.addEventListener("click", (event) => {
      const closeId = event.target?.dataset?.close;
      if (closeId) closePanel(closeId);
    });

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
    safeBind("#mobileSearchInput", "input", (event) => { state.query = event.target.value || ""; const desktopInput = $("#searchInput"); if (desktopInput) desktopInput.value = state.query; renderAll(); });
    safeBind("#mobileSearchForm", "submit", (event) => { event.preventDefault(); state.query = $("#mobileSearchInput")?.value || ""; const desktopInput = $("#searchInput"); if (desktopInput) desktopInput.value = state.query; renderAll(); closeMobileMenu(); });
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
    safeBind("#profileMemoryMoreBtn", "click", loadMoreProfileMemory);
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
      hydrateInboxFromSpeculations(state.profile?.speculative_interests);
      hydrateInboxFromSpeculations(state.profile?.speculative_avoidances, "avoidance.probe");
      state.messageListSnapshot = getRenderableMessages();
      openPanel("messagesDrawer");
      returnToMessages();
      renderMessages();
      void refreshProfile().catch(() => {});
    });
    safeBind("#activityBtn", "click", () => { closeSideDrawer(); renderActivityHistory(); openPanel("activityDrawer"); });
    safeBind("#activityMoreBtn", "click", () => loadActivityPage());
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
    safeBind("#cloneRefreshBtn", "click", async () => {
      const btn = document.getElementById("cloneRefreshBtn");
      if (btn?.disabled) return;
      btn.dataset.label = "刷新";
      _cloneBtnLoading(btn, true);
      await loadCloneSites();
      _cloneBtnLoading(btn, false);
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
    safeBind("#reshuffleBtn", "click", reshuffle);
    safeBind("#loadMoreBtn", "click", reshuffle);
    safeBind("#customLoadMoreBtn", "click", reshuffle);
    safeBind("#customApplyBtn", "click", () => reshuffle());
    safeBind("#customResetBtn", "click", () => {
      resetCustomFilters();
      showToast("已重置全部筛选条件");
    });
    safeBind("#poolAllBtn", "click", () => { closePoolDropdown(); navigateTo("/web/pool-all"); });
    safeBind("#poolFilterBtn", "click", () => navigateTo("/web/pool-filter"));
    safeBind("#poolAllRefreshBtn", "click", loadPoolAllItems);
    safeBind("#poolFilterRefreshBtn", "click", loadPoolFilterItems);
    safeBind("#delightRefreshBtn", "click", () => shuffleDelights());
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
    const scheduleObservabilityRefresh = debounceAsync(() => loadObservabilityData(), 500);
    safeBind("#observabilityRefreshBtn", "click", () => scheduleObservabilityRefresh());
    safeBind("#poolExploreBtn", "click", () => { closePoolDropdown(); navigateTo("/web/pool-explore"); });
    safeBind("#poolExploreRefreshBtn", "click", () => loadPoolExploreData());
    safeBind("#xhsFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/xhs-feed"); });
    eventDelegation("#xhsFeedBody", "#xhsFeedRefreshBtn", "click", () => loadXhsFeedData(true));
    safeBind("#zhihuFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/zhihu-feed"); });
    safeBind("#biliFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/bili-feed"); });
    eventDelegation("#zhihuFeedBody", "#zhihuFeedRefreshBtn", "click", () => loadZhihuFeedData(true));
    eventDelegation("#biliFeedBody", "#biliFeedRefreshBtn", "click", () => loadBiliFeedData(true));
    safeBind("#youtubeFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/youtube-feed"); });
    eventDelegation("#youtubeFeedBody", "#youtubeFeedRefreshBtn", "click", () => loadYoutubeFeedData(true));
    safeBind("#v2exFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/v2ex-feed"); });
    eventDelegation("#v2exFeedBody", "#v2exFeedRefreshBtn", "click", () => loadV2exFeedData(true));
    safeBind("#xiaoyuzhouFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/xiaoyuzhou-feed"); });
    eventDelegation("#xiaoyuzhouFeedBody", "#xiaoyuzhouFeedRefreshBtn", "click", () => loadXiaoyuzhouFeedData(true));
    safeBind("#agentRecommendBtn", "click", () => {
      navigateTo("/web/agent-recommend");
      setTimeout(() => loadInterestTags(), 200);
    });
    safeBind("#agentRecommendSearchBtn", "click", () => {
      const input = $("#agentRecommendInput");
      if (input && input.value.trim()) {
        loadAgentRecommendData(input.value.trim());
      }
    });
    safeBind("#agentRecommendInput", "keydown", (e) => {
      if (e.key === "Enter" && e.target.value.trim()) {
        loadAgentRecommendData(e.target.value.trim());
      }
      if (e.key === "Escape") {
        e.target.value = "";
        const clearBtn = $("#agentRecommendClearBtn");
        if (clearBtn) clearBtn.style.display = "none";
      }
    });
    safeBind("#agentRecommendInput", "input", (e) => {
      const clearBtn = $("#agentRecommendClearBtn");
      if (clearBtn) clearBtn.style.display = e.target.value.trim() ? "inline-block" : "none";
    });
    safeBind("#agentRecommendClearBtn", "click", () => {
      const input = $("#agentRecommendInput");
      if (input) {
        input.value = "";
        input.focus();
      }
      const clearBtn = $("#agentRecommendClearBtn");
      if (clearBtn) clearBtn.style.display = "none";
    });
    safeBind("#agentResetSessionBtn", "click", () => {
      resetAgentSession();
      // Clear the input and search results
      const input = $("#agentRecommendInput");
      if (input) input.value = "";
      const body = $("#agentRecommendBody");
      if (body) body.innerHTML = "";
      showToast("已重置筛选条件");
    });
    eventDelegation("#agentRecommendBody", "#agentRecommendRefreshBtn", "click", (e) => {
      const query = e.target.getAttribute("data-query") || "";
      if (query) loadAgentRecommendData(query);
    });
    eventDelegation("#agentRecommendPage", ".agent-hint", "click", (e) => {
      const hint = e.target.getAttribute("data-hint") || "";
      if (hint) {
        const input = $("#agentRecommendInput");
        if (input) input.value = hint;
        const clearBtn = $("#agentRecommendClearBtn");
        if (clearBtn) clearBtn.style.display = "inline-block";
        loadAgentRecommendData(hint);
      }
    });
    // Render search history on page init
    renderAgentSearchHistory();

    // Feedback button event delegation
    eventDelegation("#agentRecommendPage", ".feedback-btn", "click", (e) => {
      e.stopPropagation();
      const btn = e.target.closest(".feedback-btn");
      if (!btn) return;
      const bvid = btn.getAttribute("data-bvid") || "";
      const action = btn.getAttribute("data-action") || "";
      if (!bvid || !action) return;
      if (btn.classList.contains("is-active")) {
        // Already active -> remove (toggle off)
        removeFeedback(bvid, action);
      } else {
        // Find the item data from the card
        const card = btn.closest(".video-card");
        let item = null;
        if (card) {
          const idx = Array.from(card.parentNode?.children || []).indexOf(card);
          const grid = card.closest("#agentRecommendGrid");
          if (grid) {
            // We stored items in the card's __itemData property
            item = card.__itemData;
          }
        }
        sendFeedback(bvid, action, item);
      }
    });
    safeBind("#delightTabBtn", "click", () => navigateTo("/web/delight"));
    safeBind("#resetFiltersBtn", "click", () => { state.query = ""; state.filter = "全部"; const input = $("#searchInput"); if (input) input.value = ""; reshuffle(); });
    safeBind("#searchInput", "input", (event) => { state.query = event.target.value || ""; renderAll(); });
    safeBind("#searchForm", "submit", (event) => { event.preventDefault(); state.query = $("#searchInput")?.value || ""; renderAll(); });
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
        sendChat(text);
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
      renderChat();
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
      renderChat();
    }
    safeBind("#messageChatBackBtn", "click", returnToMessages);
    safeBind("#messageChatForm", "submit", (event) => {
      event.preventDefault();
      const input = $("#messageChatInput");
      const text = input?.value?.trim() || "";
      if (!text) return;
      input.value = "";
      if (state.messageChatDomain && (state.messageChatScope === "probe" || state.messageChatScope === "avoidance_probe")) {
        const probeType = state.messageChatScope === "avoidance_probe" ? "avoidance.probe" : "interest.probe";
        state.handledProbeKeys.add(probeKey(probeType, state.messageChatDomain));
      }
      sendChat(text, {
        contextPrefix: state.messageChatPrompt,
        scope: state.messageChatScope,
        subjectId: state.messageChatDomain,
        subjectTitle: state.messageChatSubjectTitle
      });
    });
    safeBind("#llmProvider", "change", () => applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), default_provider: $("#llmProvider")?.value || "" } }));
    safeBind("#llmFallbackProvider", "change", () => applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), fallback_provider: $("#llmFallbackProvider")?.value || "" } }));
    safeBind("#embeddingFallbackProvider", "change", () => applyConfig({ ...(state.config || {}), llm: { ...(state.config?.llm || {}), embedding: { ...(state.config?.llm?.embedding || {}), fallback_provider: $("#embeddingFallbackProvider")?.value || "" } } }));
    safeBind("#probeLlm", "click", () => { void runLlmConfigProbe(); });
    safeBind("#probeEmbedding", "click", () => { void runEmbeddingConfigProbe(); });
    lanAuthControl = initLanAuthControl();
    bootAutostartControl = initBootAutostartControl();
    Object.values(SOURCE_ENABLE_SELECT_IDS).forEach((id) => {
      safeBind(`#${id}`, "change", () => renderSourcesStatusRows(state.sourceStatus));
    });
    safeBind("#suggestSharesBtn", "click", async () => {
      const result = await requestJson(ENDPOINTS.sourceShareSuggestion, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled_sources: { bilibili: $("#bilibiliEnabled").value === "on", xiaohongshu: $("#xhsEnabled").value === "on", douyin: $("#douyinEnabled").value === "on", youtube: $("#youtubeEnabled").value === "on", twitter: $("#twitterEnabled").value === "on", zhihu: $("#zhihuEnabled").value === "on" }, configured_shares: buildConfigUpdate().scheduler.pool_source_shares }) });
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
        const payload = buildConfigUpdate();
        const result = await requestJsonStrict(ENDPOINTS.config.replace("?reveal_keys=true", ""), {
          method: "PUT",
          timeoutMs: 60000,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (result?.config) applyConfig(result.config);
        const message = result?.message || "配置已保存。";
        const suffix = result?.restart_required ? "\n当前配置需要重启后端后完全生效。" : result?.reloaded === false ? "\n后端返回未热重载，请检查运行状态。" : "";
        if ($("#configStatus")) $("#configStatus").value = `${message}${suffix}`;
        showToast(result?.restart_required ? "配置已保存，需要重启后端" : "配置已保存");
        void hydrateFromBackend();
        void refreshUpdateStatus();
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
    routeFromPath();
    if (typeof window.__initSelfEvolution === "function") window.__initSelfEvolution();
    restoreBackendEndpoint();
    restoreFrontendSettings();
    setSideDrawerOpen(!isMobileViewport() && storageGet(SIDE_DRAWER_OPEN_KEY) !== "0", { persist: false });
    startChatPlaceholderRotation();
    try {
      renderAll();
    } catch (error) {
      console.error("首屏渲染失败", error);
      $("#statusLabel").textContent = "首屏渲染失败";
      $("#runtimeSummary").textContent = error?.message || "请检查后端返回的数据结构。";
    }
    ensureAuthenticated()
      .then(() => hydrateFromBackend())
      .then(connectRuntimeStream)
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

    })();
