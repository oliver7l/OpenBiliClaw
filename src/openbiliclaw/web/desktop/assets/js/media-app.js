/* 媒体浏览页（桌面 SPA 内嵌视图）。
 *
 * 复用 app.js 的 showMainPage / safeBind / DESKTOP_PAGE_ROUTES：
 * - 本文件 defer 执行，app.js 先加载完成，因此顶层直接注册路由
 *   window.DESKTOP_PAGE_ROUTES["media"] = openMediaPage。
 * - 数据来自 /api/media/*；卡片沿用桌面小白卡 .video-card.is-minimal。
 */
(function () {
  "use strict";

  var PAGE_SIZE = 60;
  var UNSUPPORTED = [".mkv", ".avi", ".flv", ".wmv", ".ts"];

  /* ── 注入本页自用样式（与桌面统一风格对齐） ── */
  var styleEl = document.createElement("style");
  styleEl.textContent = `
    .media-toolbar { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
    .media-toolbar .media-seg { display:flex; gap:2px; }
    .media-search { padding:6px 10px; border:1px solid var(--border-color,#e6e8ec); border-radius:6px; font-size:13px; background:#fff; min-width:180px; }
    #mediaGrid .video-card-cover { aspect-ratio:16/10; display:flex; align-items:center; justify-content:center; color:#fff; }
    #mediaGrid .video-card-cover img { width:100%; height:100%; object-fit:cover; background:#e8eaee; }
    #mediaGrid .media-play { width:44px; height:44px; border-radius:50%; background:rgba(15,18,22,.55); display:flex; align-items:center; justify-content:center; }
    #mediaGrid .media-play svg { width:18px; height:18px; fill:#fff; }
    #mediaGrid .media-play-overlay { position:absolute; inset:0; background:rgba(0,0,0,.12); }
    #mediaGrid .media-play, #mediaGrid .media-folder { color:#fff; }
    #mediaGrid .media-fav-btn { position:absolute; top:8px; left:8px; z-index:3; width:30px; height:30px; border-radius:50%; background:rgba(0,0,0,.5); border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:16px; line-height:1; }
    #mediaGrid .media-fav-btn.on { color:#ffd34d; }
    #mediaGrid .media-fav-btn.off { color:#fff; }
    #mediaGrid .media-rating { display:flex; gap:2px; margin-top:2px; align-items:center; }
    #mediaGrid .media-star { cursor:pointer; font-size:14px; line-height:1; color:#d9d9d9; }
    #mediaGrid .media-star.on { color:#ffb400; }
    #mediaGrid .media-folder { display:flex; align-items:center; justify-content:center; color:var(--accent,#4f7cff); }
    #mediaGrid.dir-card .video-card-title { color:var(--accent,#4f7cff); font-weight:600; }
    .media-breadcrumb { display:flex; align-items:center; gap:6px; font-size:13px; color:var(--text-secondary,#8a9099); margin:4px 0 10px; flex-wrap:wrap; }
    .media-breadcrumb span.crumb { cursor:pointer; color:var(--text-secondary,#8a9099); }
    .media-breadcrumb span.crumb:hover { color:var(--accent,#4f7cff); }
    .media-breadcrumb .sep { color:#c9cdd4; }
    .media-breadcrumb .cur { color:var(--text-primary,#1f2329); }
    .media-state { text-align:center; color:var(--text-secondary,#8a9099); padding:36px 0; font-size:14px; }
    .media-load-row { display:flex; justify-content:center; padding:14px 0 26px; }
    .media-addrow { display:flex; align-items:center; gap:6px; flex:1; max-width:420px; }
    .media-addrow input { flex:1; padding:6px 10px; border:1px solid var(--border-color,#e6e8ec); border-radius:6px; font-size:13px; background:#fff; }
    #mediaGrid .media-del-btn { position:absolute; top:8px; right:8px; z-index:3; width:30px; height:30px; border-radius:50%; background:rgba(0,0,0,.5); border:none; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:13px; line-height:1; color:#fff; opacity:.85; }
    #mediaGrid .media-del-btn:hover { opacity:1; color:#ff7875; }
    .media-modal { position:fixed; inset:0; background:rgba(12,15,20,.82); display:none; align-items:center; justify-content:center; z-index:100; }
    .media-modal.open { display:flex; }
    .media-modal-close { position:absolute; top:16px; right:18px; color:#fff; background:rgba(255,255,255,.16); border:none; width:36px; height:36px; border-radius:50%; font-size:20px; cursor:pointer; line-height:1; }
    .media-modal img { max-width:92vw; max-height:90vh; border-radius:6px; }
    .media-modal video { max-width:94vw; max-height:90vh; background:#000; border-radius:6px; }
    .media-nav { position:absolute; top:50%; transform:translateY(-50%); color:#fff; background:rgba(255,255,255,.16); border:none; width:44px; height:44px; border-radius:50%; font-size:26px; cursor:pointer; line-height:1; }
    .media-nav.prev { left:16px; } .media-nav.next { right:16px; }
    .media-video-note { position:absolute; bottom:22px; left:0; right:0; text-align:center; color:#ffd6a6; font-size:13px; }
  `;
  (document.head || document.documentElement).appendChild(styleEl);

  var state = {
    roots: [],
    root: null,
    sub: "",
    kind: "all",
    q: "",
    favoriteView: false,
    items: [],
    offset: 0,
    hasMore: true,
    loading: false,
    dataVersion: 0,
    loadedOnce: false,
    randomMode: false,
    current: null
  };

  function $(id) { return document.getElementById(id); }
  function fileUrl(root, rel) {
    return "/api/media/file?root=" + encodeURIComponent(root) + "&path=" + encodeURIComponent(rel);
  }
  function posterUrl(root, rel) {
    return "/api/media/poster?root=" + encodeURIComponent(root) + "&path=" + encodeURIComponent(rel);
  }
  function fmtSize(bytes) {
    if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(2) + " GB";
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    if (bytes >= 1024) return (bytes / 1024).toFixed(0) + " KB";
    return bytes + " B";
  }
  function fmtTime(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function showState(msg) {
    var el = $("mediaState");
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  /* ── 收藏 / 评级 ───────────────────────────── */
  function itemRoot(item) { return item.root || state.root; }

  function setItemState(item, payload) {
    return fetch("/api/media/item", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root: itemRoot(item), path: item.rel, favorite: payload.favorite, rating: payload.rating })
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || "保存失败"); });
      return r.json();
    }).then(function (s) {
      item.favorite = s.favorite;
      item.rating = s.rating;
      refreshCard(item);
      if (state.favoriteView && !item.favorite) removeCard(item);
    }).catch(function (err) { alert("操作失败：" + err.message); });
  }
  function toggleFavorite(item, e) {
    if (e) e.stopPropagation();
    setItemState(item, { favorite: item.favorite ? 0 : 1 });
  }
  function setRating(item, n, e) {
    if (e) e.stopPropagation();
    var next = item.rating === n ? 0 : n;
    setItemState(item, { rating: next });
  }
  function refreshCard(item) {
    var card = document.querySelector('#mediaGrid .video-card[data-rel="' + CSS.escape(String(item.rel)) + '"]');
    if (card) card.replaceWith(renderCard(item));
  }
  function removeCard(item) {
    var card = document.querySelector('#mediaGrid .video-card[data-rel="' + CSS.escape(String(item.rel)) + '"]');
    if (card) card.remove();
  }
  function deleteItem(item, e) {
    if (e) e.stopPropagation();
    if (!window.confirm("确定删除「" + item.name + "」？\n文件将移动到回收站 data/media_trash/，可手动找回。")) return;
    fetch("/api/media/item?root=" + encodeURIComponent(itemRoot(item)) + "&path=" + encodeURIComponent(item.rel), { method: "DELETE" })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || "删除失败"); });
        return r.json();
      })
      .then(function () {
        state.items = state.items.filter(function (i) { return i !== item; });
        removeCard(item);
      })
      .catch(function (err) { alert("删除失败：" + err.message); });
  }

  /* ── 播放弹窗内的收藏 / 评级（⏭ 旁的 ☆ 与底部星级） ── */
  function bindMediaFav(item) {
    var favBtn = $("mediaVideoFav");
    if (!favBtn) return;
    favBtn.textContent = item.favorite ? "★" : "☆";
    favBtn.setAttribute("aria-label", item.favorite ? "取消收藏" : "收藏");
    favBtn.onclick = function (e) {
      e.stopPropagation();
      toggleFavorite(item);
      favBtn.textContent = item.favorite ? "★" : "☆";
    };
  }
  function bindMediaRating(item) {
    var box = $("mediaVideoRating");
    if (!box) return;
    box.innerHTML = "";
    for (var i = 1; i <= 5; i++) {
      (function (n) {
        var s = document.createElement("span");
        s.className = "media-star" + (item.rating >= n ? " on" : "");
        s.textContent = "★";
        s.onclick = function (e) { setRating(item, n, e); bindMediaRating(item); };
        box.appendChild(s);
      })(i);
    }
  }

  /* ── 「下一个」：随机模式跳另一个，否则按列表顺序播下一个 ── */
  function mediaVideoList() { return state.items.filter(function (i) { return !i.is_dir && i.kind === "video"; }); }
  function nextVideo() {
    var list = mediaVideoList();
    if (!list.length) return;
    var v = $("mediaVideoView");
    var n;
    if (state.randomMode) {
      n = list[Math.floor(Math.random() * list.length)];
      if (list.length > 1 && n === state.current) n = list[(list.indexOf(n) + 1) % list.length];
    } else {
      var idx = list.indexOf(state.current);
      n = list[(idx >= 0 ? idx + 1 : 0) % list.length];
    }
    state.current = n;
    v.src = fileUrl(itemRoot(n), n.rel);
    v.play();
    bindMediaFav(n);
    bindMediaRating(n);
    v.onended = nextVideo;
  }

  /* ── 随机播放（从当前列表选随机视频并连播） ── */
  function randomVideos() {
    return state.items.filter(function (i) { return !i.is_dir && i.kind === "video"; });
  }
  function startRandom() {
    var pool = randomVideos();
    if (!pool.length) { alert("当前列表没有可播放的视频"); return; }
    state.randomMode = true;
    var note = $("mediaVideoNote");
    note.hidden = false;
    note.textContent = "随机播放中（将从当前列表自动连播），点右上角 ✕ 停止。";
    playRandomOnce();
  }
  function playRandomOnce() {
    var pool = randomVideos();
    if (!pool.length || !state.randomMode) return;
    var item = pool[Math.floor(Math.random() * pool.length)];
    var v = $("mediaVideoView");
    state.current = item;
    v.src = fileUrl(itemRoot(item), item.rel);
    $("mediaVideoModal").classList.add("open");
    bindMediaFav(item);
    bindMediaRating(item);
    // 播完自动切下一个随机
    v.onended = function () {
      if (state.randomMode) {
        var p = randomVideos();
        if (!p.length) { closeModal($("mediaVideoModal")); state.randomMode = false; return; }
        var next = p[Math.floor(Math.random() * p.length)];
        state.current = next;
        v.src = fileUrl(itemRoot(next), next.rel);
        v.play();
        bindMediaFav(next);
        bindMediaRating(next);
      }
    };
  }

  /* ── 根目录 ── */
  function loadRoots() {
    return fetch("/api/media/roots").then(function (r) { return r.json(); }).then(function (body) {
      state.roots = body.roots || [];
      if (!state.root && state.roots.length) {
        var first = state.roots.find(function (r) { return r.exists; }) || state.roots[0];
        state.root = first.path;
      }
      renderRootTabs();
      if (state.root) resetAndLoad();
      else showState("尚未配置媒体目录 —— 点击「+ 添加目录」开始。");
    }).catch(function () {
      showState("无法连接后端 API。");
    });
  }

  function renderRootTabs() {
    var tabs = $("mediaRootTabs");
    tabs.innerHTML = "";
    state.roots.forEach(function (r) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "page-subtab-btn" + (r.path === state.root ? " is-active" : "");
      b.title = r.path;
      b.textContent = r.name + (r.exists ? "  " + r.video_count + "▸/" + r.image_count + "🖼" : " (不存在)");
      b.addEventListener("click", function () {
        if (state.root !== r.path) { state.root = r.path; state.sub = ""; renderRootTabs(); resetAndLoad(); }
      });
      tabs.appendChild(b);
    });
  }

  /* ── 列表 ── */
  function paramsFor() {
    var p = new URLSearchParams();
    p.set("root", state.root);
    p.set("kind", state.kind);
    if (state.q) p.set("q", state.q);
    if (state.sub) p.set("sub", state.sub);
    p.set("offset", String(state.offset));
    p.set("limit", String(PAGE_SIZE));
    return p;
  }

  function resetAndLoad() {
    state.dataVersion++;
    state.items = [];
    state.offset = 0;
    state.hasMore = true;
    $("mediaGrid").innerHTML = "";
    $("mediaLoadMoreBtn").hidden = true;
    showState("加载中…");
    renderBreadcrumb();
    loadNext(true);
  }

  function loadNext(reset) {
    if (state.loading) return;
    if (state.favoriteView) { loadFavorites(); return; }
    if (!state.root) return;
    state.loading = true;
    var version = state.dataVersion;
    fetch("/api/media/list?" + paramsFor().toString()).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (body) {
      if (version !== state.dataVersion) { state.loading = false; return; }
      state.items = state.items.concat(body.items);
      state.offset = body.offset + body.items.length;
      state.hasMore = !!body.hasMore;
      renderCards(reset || $("mediaGrid").childNodes.length === 0);
      $("mediaLoadMoreBtn").hidden = !state.hasMore;
      state.loading = false;
    }).catch(function (err) {
      if (version !== state.dataVersion) { state.loading = false; return; }
      showState("加载失败：" + err.message);
      state.loading = false;
    });
  }

  function loadFavorites() {
    state.loading = true;
    var version = state.dataVersion;
    var p = new URLSearchParams();
    // 收藏里没有目录，dir 分类下收藏退化为看全部
    p.set("kind", state.kind === "dir" ? "all" : state.kind);
    if (state.q) p.set("q", state.q);
    fetch("/api/media/favorites?" + p.toString()).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (body) {
      if (version !== state.dataVersion) { state.loading = false; return; }
      state.items = body.items || [];
      state.hasMore = false;
      $("mediaGrid").innerHTML = "";
      $("mediaLoadMoreBtn").hidden = true;
      if (!state.items.length) { showState("还没有收藏的媒体 —— 点卡片左上角 ☆ 收藏，★ 表示已收藏。"); }
      else { showState(""); }
      state.items.forEach(function (item) { $("mediaGrid").appendChild(renderCard(item)); });
      state.loading = false;
    }).catch(function (err) {
      if (version !== state.dataVersion) { state.loading = false; return; }
      showState("加载收藏失败：" + err.message);
      state.loading = false;
    });
  }

  function renderCards(reset) {
    var grid = $("mediaGrid");
    if (reset) grid.innerHTML = "";
    if (!state.items.length) { showState("该目录下没有符合条件的媒体文件。"); return; }
    showState("");
    state.items.forEach(function (item) { grid.appendChild(renderCard(item)); });
  }

  function renderCard(item) {
    var card = document.createElement("article");
    card.className = "video-card is-minimal" + (item.is_dir ? " dir-card" : "");
    card.dataset.rel = String(item.rel || "");
    var cover = document.createElement("div");
    cover.className = "video-card-cover";
    if (item.is_dir) {
      cover.innerHTML = '<div class="media-folder"><svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor"><path d="M3 5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v5H3V5zm0 6h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8z"/></svg></div>';
    } else if (item.kind === "image") {
      var img = document.createElement("img");
      img.loading = "lazy";
      img.src = fileUrl(itemRoot(item), item.rel);
      img.alt = item.name;
      cover.appendChild(img);
    } else {
      var img = document.createElement("img");
      img.loading = "lazy";
      img.src = posterUrl(itemRoot(item), item.rel);
      img.alt = item.name;
      // 抽帧封面失败（无法解码/无 ffmpeg）时退化为纯播放图标
      img.onerror = function () { if (img.parentNode) img.remove(); };
      cover.appendChild(img);
      var overlay = document.createElement("div");
      overlay.className = "media-play media-play-overlay";
      overlay.innerHTML = '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z"/></svg>';
      cover.appendChild(overlay);
    }
    if (!item.is_dir) {
      var fav = document.createElement("button");
      fav.type = "button";
      fav.className = "media-fav-btn " + (item.favorite ? "on" : "off");
      fav.title = item.favorite ? "取消收藏" : "收藏";
      fav.textContent = item.favorite ? "★" : "☆";
      fav.addEventListener("click", function (e) { toggleFavorite(item, e); });
      cover.appendChild(fav);
      var del = document.createElement("button");
      del.type = "button";
      del.className = "media-del-btn";
      del.title = "删除（移入回收站）";
      del.textContent = "🗑";
      del.addEventListener("click", function (e) { deleteItem(item, e); });
      cover.appendChild(del);
    }
    card.appendChild(cover);

    var title = document.createElement("p");
    title.className = "video-card-title";
    title.textContent = item.name;
    card.appendChild(title);

    var stars = document.createElement("div");
    stars.className = "media-rating";
    for (var i = 1; i <= 5; i++) {
      (function (n) {
        var s = document.createElement("span");
        s.className = "media-star" + (item.rating >= n ? " on" : "");
        s.textContent = "★";
        s.addEventListener("click", function (e) { setRating(item, n, e); });
        stars.appendChild(s);
      })(i);
    }
    card.appendChild(stars);

    var meta = document.createElement("div");
    meta.className = "video-card-meta";
    var author = document.createElement("span");
    author.className = "video-card-author";
    author.textContent = item.is_dir ? "" : (fmtSize(item.size) + (item.mtime ? " · " + fmtTime(item.mtime) : ""));
    meta.appendChild(author);
    var tag = document.createElement("span");
    tag.className = "video-card-tag";
    tag.textContent = item.is_dir ? "目录" : (item.kind === "video" ? "视频" : "图片");
    meta.appendChild(tag);
    card.appendChild(meta);

    card.addEventListener("click", function () { openItem(item); });
    return card;
  }

  function renderBreadcrumb() {
    var bc = $("mediaBreadcrumb");
    if (state.favoriteView || !state.sub) { bc.hidden = true; bc.innerHTML = ""; return; }
    bc.hidden = false;
    bc.innerHTML = "";
    var rootLink = document.createElement("span");
    rootLink.className = "crumb";
    rootLink.textContent = "根目录";
    rootLink.addEventListener("click", function () { state.sub = ""; resetAndLoad(); });
    bc.appendChild(rootLink);
    var parts = state.sub.split("/");
    var acc = "";
    parts.forEach(function (part, i) {
      bc.appendChild(document.createTextNode(" / "));
      var span = document.createElement("span");
      span.textContent = part;
      if (i < parts.length - 1) {
        span.className = "crumb";
        acc = acc ? acc + "/" + part : part;
        (function (target) { span.addEventListener("click", function () { state.sub = target; resetAndLoad(); }); })(acc);
      } else {
        span.className = "cur";
      }
      bc.appendChild(span);
    });
  }

  /* ── 点击 ── */
  function openItem(item) {
    if (item.is_dir) { state.sub = item.rel; resetAndLoad(); return; }
    if (item.kind === "video") openVideo(item);
    else openImage(item.name);
  }
  function imageList() { return state.items.filter(function (i) { return !i.is_dir && i.kind === "image"; }); }
  function openImage(name) {
    var imgs = imageList();
    var idx = imgs.findIndex(function (i) { return i.name === name; });
    if (idx < 0) return;
    showImageAt(imgs, idx);
  }
  function showImageAt(imgs, idx) {
    $("mediaImageModal").classList.add("open");
    $("mediaImageView").src = fileUrl(itemRoot(imgs[idx]), imgs[idx].rel);
    $("mediaImagePrev").onclick = function () { showImageAt(imgs, (idx - 1 + imgs.length) % imgs.length); };
    $("mediaImageNext").onclick = function () { showImageAt(imgs, (idx + 1) % imgs.length); };
  }
  function openVideo(item) {
    var v = $("mediaVideoView");
    var note = $("mediaVideoNote");
    state.current = item;
    v.src = fileUrl(itemRoot(item), item.rel);
    var ext = item.name.toLowerCase().match(/\.[a-z0-9]+$/);
    var ok = ext ? UNSUPPORTED.indexOf(ext[0]) < 0 : true;
    note.hidden = ok;
    note.textContent = ok ? "" : ("浏览器可能无法直接播放 " + (ext[0] || "").toUpperCase() + "，建议转码为 MP4。");
    $("mediaVideoModal").classList.add("open");
    bindMediaFav(item);
    bindMediaRating(item);
    // 单集播完自动按列表顺序播下一个，直到列表末尾停止
    v.onended = function () {
      if (state.randomMode) return;
      var list = mediaVideoList();
      var idx = list.indexOf(item);
      var next = idx >= 0 ? list[idx + 1] : undefined;
      if (next) openVideo(next);
    };
  }
  function closeModal(modal) {
    modal.classList.remove("open");
    state.randomMode = false;
    var v = modal.querySelector("video");
    if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
  }

  /* ── 添加目录 ── */
  function addRootRow() {
    var row = document.createElement("div");
    row.className = "media-addrow";
    var input = document.createElement("input");
    input.type = "text";
    input.placeholder = "输入本地目录绝对路径，如 /Volumes/.../";
    var ok = document.createElement("button");
    ok.className = "small-btn";
    ok.type = "button";
    ok.textContent = "确定";
    var cancel = document.createElement("button");
    cancel.className = "small-btn";
    cancel.type = "button";
    cancel.textContent = "取消";
    row.appendChild(input);
    row.appendChild(ok);
    row.appendChild(cancel);
    var addBtn = $("mediaAddRootBtn");
    addBtn.hidden = true;
    addBtn.parentNode.insertBefore(row, addBtn.nextSibling);
    input.focus();
    function submit() {
      var path = input.value.trim();
      if (!path) return;
      fetch("/api/media/roots", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path })
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (b) { throw new Error(b.detail || "添加失败"); });
        return r.json();
      }).then(function () {
        row.remove();
        addBtn.hidden = false;
        return fetch("/api/media/roots").then(function (r) { return r.json(); });
      }).then(function (body) {
        var before = state.roots.map(function (r) { return r.path; });
        state.roots = body.roots || [];
        var fresh = state.roots.find(function (r) { return before.indexOf(r.path) < 0 && r.exists; });
        if (fresh) state.root = fresh.path;
        renderRootTabs();
        resetAndLoad();
      }).catch(function (err) { alert("添加失败：" + err.message); });
    }
    ok.addEventListener("click", submit);
    cancel.addEventListener("click", function () { row.remove(); addBtn.hidden = false; });
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") submit(); });
  }

  /* ── 打开页面（供 app.js 路由调用） ── */
  window.openMediaPage = function openMediaPage() {
    if (window.showMainPage) window.showMainPage("mediaPage");
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (!state.loadedOnce) {
      state.loadedOnce = true;
      loadRoots();
    }
  };
  window.reloadMediaPage = function reloadMediaPage() {
    if (state.root) resetAndLoad();
    else loadRoots();
  };

  /* ── 事件绑定 ── */
  function bindEl(id, evt, fn) {
    var el = $(id);
    if (el) el.addEventListener(evt, fn);
  }
  bindEl("mediaKindSeg", "click", function (e) {
    var btn = e.target.closest("button[data-kind]");
    if (!btn) return;
    Array.prototype.forEach.call($("mediaKindSeg").children, function (b) { b.classList.remove("is-active"); });
    btn.classList.add("is-active");
    state.kind = btn.dataset.kind;
    resetAndLoad();
  });
  var searchTimer = null;
  bindEl("mediaSearch", "input", function () {
    clearTimeout(searchTimer);
    var val = this.value.trim();
    searchTimer = setTimeout(function () {
      if (state.q !== val) { state.q = val; resetAndLoad(); }
    }, 350);
  });
  bindEl("mediaAddRootBtn", "click", addRootRow);
  bindEl("mediaLoadMoreBtn", "click", function () { loadNext(false); });
  bindEl("mediaRandomBtn", "click", function () {
    if (state.randomMode) { state.randomMode = false; closeModal($("mediaVideoModal")); }
    else startRandom();
  });
  bindEl("mediaFavToggleBtn", "click", function () {
    state.favoriteView = !state.favoriteView;
    this.classList.toggle("is-active", state.favoriteView);
    this.textContent = state.favoriteView ? "★ 只看收藏（再次点击返回浏览）" : "☆ 只看收藏";
    renderBreadcrumb();
    resetAndLoad();
  });
  bindEl("mediaImageClose", "click", function () { closeModal($("mediaImageModal")); });
  bindEl("mediaVideoClose", "click", function () { closeModal($("mediaVideoModal")); });
  bindEl("mediaVideoNext", "click", nextVideo);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeModal($("mediaImageModal"));
      closeModal($("mediaVideoModal"));
    }
  });

  // 注册到桌面 SPA 路由（app.js 已先执行，window.DESKTOP_PAGE_ROUTES 就绪）
  if (window.DESKTOP_PAGE_ROUTES) {
    window.DESKTOP_PAGE_ROUTES["media"] = window.openMediaPage;
  }
})();