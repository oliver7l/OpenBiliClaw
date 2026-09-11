/* ed2k / Kad 下载管理页（桌面 SPA 内嵌视图）。
 *
 * 数据来自 /api/ed2k/*（由后端经 mule CLI 驱动 MLDonkey）。
 * - 搜索 / 下载 / 进行中列表（轮询）/ 取消 / commit / 落地路径。
 * - 顶层注册渲染逻辑；路由映射在 app.js 的 DESKTOP_PAGE_ROUTES["ed2k"]。
 */
(function () {
  "use strict";

  var POLL_MS = 3000;
  var timer = null;
  var lastSearchResults = [];

  /* ── 本页自用样式（与桌面统一风格对齐） ── */
  var styleEl = document.createElement("style");
  styleEl.textContent = `
    .ed2k-toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
    .ed2k-linkrow { display:flex; gap:6px; flex-basis:100%; max-width:100%; }
    .ed2k-linkrow .ed2k-search-input { flex:1; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12.5px; }
    .ed2k-search { display:flex; gap:6px; flex:1; min-width:240px; max-width:560px; }
    .ed2k-search-input { flex:1; padding:7px 12px; border:1px solid var(--border-color,#e6e8ec); border-radius:8px; font-size:13px; background:#fff; }
    .ed2k-search-btn { padding:7px 16px; border:none; border-radius:8px; background:var(--accent,#4f7cff); color:#fff; font-size:13px; cursor:pointer; }
    .ed2k-actions { display:flex; gap:8px; }
    .ed2k-action-btn { padding:7px 14px; border:1px solid var(--border-color,#e6e8ec); border-radius:8px; background:#fff; color:var(--text,#1f2329); font-size:13px; cursor:pointer; }
    .ed2k-action-btn:hover { background:var(--accent-soft,#eef2ff); }
    .ed2k-netstrip { display:flex; align-items:center; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--muted,#8a9099); background:#fff; border:1px solid var(--border-color,#e6e8ec); border-radius:8px; padding:8px 12px; margin-bottom:14px; }
    .ed2k-netstrip code { background:#f4f5f7; border-radius:4px; padding:1px 6px; font-size:12px; word-break:break-all; }
    .ed2k-section-head { display:flex; align-items:baseline; gap:10px; margin:16px 0 8px; }
    .ed2k-section-head h3 { margin:0; font-size:14px; font-weight:600; }
    .ed2k-hint { font-size:12px; color:var(--muted,#8a9099); }
    .ed2k-empty { color:var(--muted,#8a9099); font-size:13px; padding:16px 2px; margin:0; }
    .ed2k-row { display:flex; align-items:center; gap:10px; background:#fff; border:1px solid var(--border-color,#e6e8ec); border-radius:8px; padding:8px 12px; margin-bottom:6px; font-size:13px; }
    .ed2k-row .meta { flex:1; min-width:0; }
    .ed2k-row .name { font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ed2k-row .sub { color:var(--muted,#8a9099); font-size:12px; margin-top:2px; }
    .ed2k-state { flex:0 0 auto; min-width:70px; text-align:right; color:var(--muted,#8a9099); }
    .ed2k-progress { height:6px; border-radius:3px; background:#eef0f3; overflow:hidden; margin-top:5px; }
    .ed2k-progress > i { display:block; height:100%; background:var(--accent,#4f7cff); }
    .ed2k-op-btn { border:1px solid var(--border-color,#e6e8ec); background:#fff; border-radius:6px; padding:4px 10px; font-size:12px; cursor:pointer; color:var(--text,#1f2329); }
    .ed2k-op-btn.danger:hover { color:#ff4d4f; border-color:#ffa39e; }
    .ed2k-op-btn.primary { background:var(--accent,#4f7cff); border-color:var(--accent,#4f7cff); color:#fff; }
    .ed2k-error { color:#cf1322; background:#fff1f0; border:1px solid #ffa39e; border-radius:8px; padding:8px 12px; font-size:13px; margin-bottom:10px; }
  `;
  document.head.appendChild(styleEl);

  function el(id) {
    return document.getElementById(id);
  }

  function showMsg(id, msg) {
    var box = el(id);
    if (box) box.textContent = msg;
  }

  function stateLabel(state) {
    return { downloading: "下载中", paused: "已暂停", queued: "排队中", finished: "已完成", connecting: "连接中" }[state] || state;
  }

  function normalizeSize(bytes) {
    if (bytes == null || isNaN(bytes)) return "";
    var n = Number(bytes);
    var u = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)) + u[i];
  }

  /* ── 初始化：网络状态 + 落地路径 ── */
  function loadNetAndPath() {
    fetch("/api/ed2k/net").then(function (r) { return r.json().catch(function () { return {}; }); }).then(function (d) {
      var servers = (d.servers || "").match(/(\d+)\s+servers/);
      var msg = d.kad_connected ? "Kad 已连接" : "Kad 未连接";
      if (servers) msg += "，" + servers[1] + " 个服务器";
      showMsg("ed2kNetInfo", msg);
    }).catch(function () { showMsg("ed2kNetInfo", "网络状态获取失败"); });

    fetch("/api/ed2k/path").then(function (r) { return r.json().catch(function () { return {}; }); }).then(function (d) {
      showMsg("ed2kPathInfo", d.path || "—");
    }).catch(function () { showMsg("ed2kPathInfo", "—"); });
  }

  /* ── 直接粘贴链接下载 ── */
  function doDownloadLink() {
    var link = (el("ed2kLinkInput") || {}).value;
    if (!link || !link.trim()) return;
    el("ed2kLinkBtn").disabled = true;
    showMsg("ed2kSearchHint", "正在添加下载：链接…");
    fetch("/api/ed2k/download-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ link: link.trim() })
    }).then(function (r) { return r.json(); }).then(function (d) {
      showMsg("ed2kSearchHint", d && d.error ? "添加失败：" + d.error : "已加入下载队列");
      el("ed2kLinkInput").value = "";
      setTimeout(loadDownloads, 1200);
    }).catch(function () { showMsg("ed2kSearchHint", "添加失败，请检查链接"); })
      .finally(function () { el("ed2kLinkBtn").disabled = false; });
  }

  /* ── 搜索 ── */
  function doSearch() {
    var q = (el("ed2kSearchInput") || {}).value;
    if (!q || !q.trim()) return;
    showMsg("ed2kSearchHint", "搜索中，可能要 30-60s…");
    el("ed2kSearchBtn").disabled = true;
    fetch("/api/ed2k/search?q=" + encodeURIComponent(q.trim()) + "&wait=35")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.error) { showMsg("ed2kSearchHint", d.error); renderResults([]); return; }
        var res = (d && (d.results || [])) || [];
        lastSearchResults = res;
        renderResults(res);
        showMsg("ed2kSearchHint", "共 " + (d.count != null ? d.count : res.length) + " 条结果");
      })
      .catch(function () { showMsg("ed2kSearchHint", "搜索失败"); renderResults([]); })
      .finally(function () { el("ed2kSearchBtn").disabled = false; });
  }

  function renderResults(results) {
    var box = el("ed2kResultsList");
    if (!box) return;
    if (!results.length) {
      box.innerHTML = '<p class="ed2k-empty">无结果。ed2k/Kad 主要索引媒体文件，可换关键词重试。</p>';
      return;
    }
    box.innerHTML = "";
    results.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "ed2k-row";
      var meta = document.createElement("div");
      meta.className = "meta";
      var name = document.createElement("div");
      name.className = "name";
      name.title = r.name;
      name.textContent = r.name;
      var sub = document.createElement("div");
      sub.className = "sub";
      sub.textContent = (r.size || "") + " · " + (r.sources != null ? r.sources + " 来源" : "");
      meta.appendChild(name);
      meta.appendChild(sub);
      var btn = document.createElement("button");
      btn.className = "ed2k-op-btn primary";
      btn.textContent = "下载";
      btn.addEventListener("click", function () {
        fetch("/api/ed2k/download", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ids: [r.id] })
        }).then(function (resp) { return resp.json(); }).then(function (d) {
          showMsg("ed2kSearchHint", d && d.error ? "下载失败：" + d.error : "已加入下载：" + r.name);
          setTimeout(loadDownloads, 1200);
        });
      });
      row.appendChild(meta);
      row.appendChild(btn);
      box.appendChild(row);
    });
  }

  /* ── 进行中下载列表 ── */
  function loadDownloads() {
    fetch("/api/ed2k/downloads").then(function (r) { return r.json().catch(function () { return {}; }); }).then(function (d) {
      var list = (d && (d.downloads || [])) || [];
      renderDownloads(list);
    }).catch(function () {
      var box = el("ed2kDownloadsList");
      if (box) box.innerHTML = '<div class="ed2k-error">获取下载列表失败</div>';
    });
  }

  function renderDownloads(list) {
    var box = el("ed2kDownloadsList");
    if (!box) return;
    if (!list.length) {
      box.innerHTML = '<p class="ed2k-empty">暂无进行中下载。</p>';
      return;
    }
    box.innerHTML = "";
    list.forEach(function (dl) {
      var row = document.createElement("div");
      row.className = "ed2k-row";
      var meta = document.createElement("div");
      meta.className = "meta";
      var name = document.createElement("div");
      name.className = "name";
      name.title = dl.name;
      name.textContent = dl.name;
      var sub = document.createElement("div");
      sub.className = "sub";
      sub.textContent = (dl.size || "") + (dl.percent ? " · " + dl.percent : "");
      meta.appendChild(name);
      meta.appendChild(sub);
      var state = document.createElement("div");
      state.className = "ed2k-state";
      state.textContent = stateLabel(dl.state);
      var progress = document.createElement("div");
      progress.className = "ed2k-progress";
      progress.style.display = (dl.state === "finished") ? "none" : "";
      var pct = 0;
      if (dl.percent) {
        var ms = dl.percent.match(/([\d.]+)%/);
        if (ms) pct = parseFloat(ms[1]);
      }
      progress.innerHTML = "<i style='width:" + (isNaN(pct) ? 0 : pct) + "%'></i>";
      meta.appendChild(progress);
      row.appendChild(meta);
      row.appendChild(state);
      if (dl.state !== "finished") {
        var cancelBtn = document.createElement("button");
        cancelBtn.className = "ed2k-op-btn danger";
        cancelBtn.textContent = "取消";
        cancelBtn.addEventListener("click", function () {
          fetch("/api/ed2k/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ids: [dl.id] })
          }).then(function () { setTimeout(loadDownloads, 800); });
        });
        row.appendChild(cancelBtn);
      }
      box.appendChild(row);
    });
  }

  /* ── commit ── */
  function doCommit() {
    fetch("/api/ed2k/commit", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        showMsg("ed2kSearchHint", d && d.error ? "commit 失败：" + d.error : "已把完成文件移入落地目录");
        setTimeout(loadDownloads, 1200);
      })
      .catch(function () { showMsg("ed2kSearchHint", "commit 失败"); });
  }

  /* ── 页面生命周期 ── */
  function startPoll() {
    stopPoll();
    timer = setInterval(loadDownloads, POLL_MS);
  }
  function stopPoll() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  window.reloadEd2kPage = function () {
    // 每次进入页面：刷新网络/路径 + 下载列表 + 重新轮询
    if (!el("ed2kPage") || el("ed2kPage").hidden) return;
    loadNetAndPath();
    loadDownloads();
    startPoll();
  };

  window.pauseEd2kPoll = stopPoll;

  /* ── 事件绑定（页面首次渲染后执行；挂载在 window，供 app.js 生命周期复用） ── */
  window.initEd2kPage = function () {
    var linkBtn = el("ed2kLinkBtn");
    if (linkBtn && !linkBtn.dataset.bound) {
      linkBtn.dataset.bound = "1";
      linkBtn.addEventListener("click", doDownloadLink);
      var linkInput = el("ed2kLinkInput");
      if (linkInput) linkInput.addEventListener("keydown", function (e) { if (e.key === "Enter") doDownloadLink(); });
    }
    var searchBtn = el("ed2kSearchBtn");
    if (searchBtn && !searchBtn.dataset.bound) {
      searchBtn.dataset.bound = "1";
      searchBtn.addEventListener("click", doSearch);
      var input = el("ed2kSearchInput");
      if (input) input.addEventListener("keydown", function (e) { if (e.key === "Enter") doSearch(); });
    }
    var commitBtn = el("ed2kCommitBtn");
    if (commitBtn && !commitBtn.dataset.bound) {
      commitBtn.dataset.bound = "1";
      commitBtn.addEventListener("click", doCommit);
    }
    var refreshBtn = el("ed2kRefreshBtn");
    if (refreshBtn && !refreshBtn.dataset.bound) {
      refreshBtn.dataset.bound = "1";
      refreshBtn.addEventListener("click", loadDownloads);
    }
    loadNetAndPath();
    loadDownloads();
  };

  // app.js 在 showMainPage 切换时会调用本文件注册的路由函数；若直接进入 /web/ed2k，
  // openEd2kPage(route) 已触发 reloadEd2kPage。此处在 defer 末尾调用 init 以确保按钮已绑定。
  document.addEventListener("DOMContentLoaded", function () {
    if (window.initEd2kPage) window.initEd2kPage();
  });
})();