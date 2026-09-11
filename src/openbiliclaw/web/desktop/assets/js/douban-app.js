/* 豆瓣书影音页（桌面 SPA 内嵌视图）。
 *
 * 数据来自 /api/douban/*（由后端从独立 douban.db 读取书影音清单）。
 * - 分类：影视 / 书 / 音乐；状态：看过/读过/听过、想看/想读/想听、在看/在读/在听。
 * - 顶部统计条 + 搜索 + 分类/状态子 tab 筛选，条目卡片可跳转豆瓣原文。
 * - 顶层注册渲染逻辑；路由映射在 app.js 的 DESKTOP_PAGE_ROUTES["douban"]。
 */
(function () {
  "use strict";

  var state = {
    category: "",
    status: "",
    search: "",
  };

  var CATEGORIES = [
    { key: "", label: "全部" },
    { key: "movie", label: "影视" },
    { key: "book", label: "书" },
    { key: "music", label: "音乐" },
  ];
  var STATUSES = [
    { key: "", label: "全部状态" },
    { key: "collect", label: "看过/读过/听过" },
    { key: "wish", label: "想看/想读/想听" },
    { key: "do", label: "在看/在读/在听" },
  ];

  /* ── 本页自用样式（与桌面统一风格对齐） ── */
  var styleEl = document.createElement("style");
  styleEl.textContent = `
    .douban-filters .ed2k-action-btn.is-active { background: var(--accent,#4f7cff); color:#fff; border-color:var(--accent,#4f7cff); }
    .douban-card { display:flex; flex-direction:column; gap:4px; background:#fff; border:1px solid var(--border-color,#e6e8ec); border-radius:10px; padding:10px 12px; font-size:13px; }
    .douban-card .name { font-size:13.5px; font-weight:600; }
    .douban-card .meta { color:var(--muted,#8a9099); font-size:12px; }
    .douban-card .comment { color:var(--muted,#8a9099); font-size:12px; }
    .douban-card a { color:var(--accent,#4f7cff); text-decoration:none; font-size:12px; }
    .douban-card a:hover { text-decoration:underline; }
  `;
  document.head.appendChild(styleEl);

  function el(id) { return document.getElementById(id); }

  /* ── 渲染统计条 + 筛选 tab ── */
  function renderFilters() {
    var catEl = el("doubanCategoryTabs");
    if (catEl) catEl.innerHTML = CATEGORIES.map(function (c) {
      return '<button type="button" class="ed2k-action-btn' + (c.key === state.category ? " is-active" : "") +
        '" data-cat="' + c.key + '">' + c.label + "</button>";
    }).join("");
    var stEl = el("doubanStatusTabs");
    if (stEl) stEl.innerHTML = STATUSES.map(function (s) {
      return '<button type="button" class="ed2k-action-btn' + (s.key === state.status ? " is-active" : "") +
        '" data-status="' + s.key + '">' + s.label + "</button>";
    }).join("");
  }

  /* ── 拉取清单并渲染 ── */
  function loadItems() {
    var grid = el("doubanGrid");
    var empty = el("doubanEmpty");
    if (!grid) return;
    grid.innerHTML = '<div class="observability-loading">加载中…</div>';
    var qs = "limit=500&offset=0";
    if (state.category) qs += "&category=" + encodeURIComponent(state.category);
    if (state.status) qs += "&status=" + encodeURIComponent(state.status);
    if (state.search) qs += "&search=" + encodeURIComponent(state.search);
    fetch("/api/douban/items?" + qs)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        grid.innerHTML = "";
        if (!d.items || !d.items.length) {
          el("doubanHint").textContent = "无结果";
          if (empty) empty.hidden = false;
          return;
        }
        if (empty) empty.hidden = true;
        el("doubanHint").textContent = "共 " + d.count + " 条 · 点击条目可跳转豆瓣原文";
        d.items.forEach(function (it) {
          var card = document.createElement("div");
          card.className = "douban-card";
          var name = document.createElement("div");
          name.className = "name";
          name.textContent = it.name || "";
          var meta = document.createElement("div");
          meta.className = "meta";
          var bits = [];
          if (it.date) bits.push(it.date);
          if (it.pub) bits.push(it.pub);
          if (it.rating) bits.push(it.rating);
          if (it.intro) bits.push(it.intro);
          meta.textContent = bits.join(" · ");
          card.appendChild(name);
          if (meta.textContent) card.appendChild(meta);
          if (it.comment) {
            var com = document.createElement("div");
            com.className = "comment";
            com.textContent = "短评：" + it.comment;
            card.appendChild(com);
          }
          if (it.url) {
            var lnk = document.createElement("a");
            lnk.href = it.url;
            lnk.target = "_blank";
            lnk.rel = "noopener";
            lnk.textContent = "去豆瓣看原文 ↗";
            card.appendChild(lnk);
          }
          grid.appendChild(card);
        });
      })
      .catch(function () {
        grid.innerHTML = '<p class="ed2k-empty">加载失败，请检查后端 /api/douban 是否可用。</p>';
      });
  }

  function loadStats() {
    fetch("/api/douban/stats")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var elt = el("doubanStatsInfo");
        if (!elt) return;
        if (!d || !d.total) { elt.textContent = "豆瓣库暂无数据（可运行 import_douban_db 导入）"; return; }
        var parts = ["共 " + d.total + " 条"];
        if (d.categories) {
          Object.keys(d.categories).forEach(function (c) {
            var cat = d.categories[c];
            var sum = 0;
            if (cat.statuses) Object.keys(cat.statuses).forEach(function (s) { sum += cat.statuses[s].count; });
            parts.push(cat.label + " " + sum);
          });
        }
        elt.textContent = parts.join(" · ");
      })
      .catch(function () {
        var elt = el("doubanStatsInfo");
        if (elt) elt.textContent = "统计加载失败";
      });
  }

  function refreshAll() {
    if (!el("doubanPage") || el("doubanPage").hidden) return;
    renderFilters();
    loadStats();
    loadItems();
  }

  /* ── 子视图切换（书影音 | 画像）── */
  function switchView(view) {
    document.querySelectorAll("#doubanPage .page-subtab-btn").forEach(function (b) {
      b.classList.toggle("is-active", b.getAttribute("data-douban-view") === view);
    });
    var list = el("doubanListView");
    var profile = el("doubanProfileView");
    if (list) list.hidden = view !== "list";
    if (profile) profile.hidden = view !== "profile";
    if (view === "profile") { loadAnalytics(); loadInsight(); }
  }

  /* ── 统计画像加载与渲染 ── */
  function loadAnalytics() {
    var body = el("doubanProfileStats");
    if (!body) return;
    fetch("/api/douban/analytics")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var html = "";
        if (d.status_ratio) {
          html += '<div class="ed2k-netstrip">' +
            "总条目 " + d.status_ratio.total + " · 实际消费(看过/读过/听过) " + d.status_ratio.collected +
            " · 占比 " + d.status_ratio.collected_pct + "%</div>";
        }
        // 品味跨度
        if (d.era_span && d.era_span.earliest_year) {
          html += '<div class="ed2k-netstrip">品味跨度：' +
            d.era_span.earliest_year + " → " + d.era_span.latest_year +
            "（" + d.era_span.span_years + " 年）</div>";
        }
        // 分类分布
        var dist = (d.type_distribution || {}).categories || {};
        html += '<div class="section-head" style="margin-top:10px"><h3>分类分布</h3></div>';
        Object.keys(dist).forEach(function (c) {
          var cat = dist[c];
          var parts = [];
          Object.keys(cat.statuses || {}).forEach(function (s) {
            parts.push(cat.statuses[s].label + " " + cat.statuses[s].count);
          });
          html += '<div class="ed2k-netstrip">' + (cat.label || c) + "：" + parts.join(" · ") + "</div>";
        });
        body.innerHTML = html;
        renderTrend(d.yearly_trend);
      })
      .catch(function () { body.innerHTML = '<p class="ed2k-empty">画像统计加载失败</p>'; });
  }

  /* ── 年度趋势条 ── */
  function renderTrend(trend) {
    var wrap = el("doubanProfileTrend");
    if (!wrap) return;
    if (!trend || !trend.years || !trend.years.length) {
      wrap.innerHTML = '<div class="section-head" style="margin-top:10px"><h3>年度趋势</h3><span class="ed2k-hint">暂无有日期的消费记录</span></div>';
      return;
    }
    var max = 1;
    Object.keys(trend.series || {}).forEach(function (c) {
      (trend.series[c] || []).forEach(function (v) { if (v > max) max = v; });
    });
    var html = '<div class="section-head" style="margin-top:10px"><h3>年度消费趋势（看过/读过/听过）</h3></div><div style="display:flex;flex-direction:column;gap:4px">';
    trend.years.forEach(function (y, i) {
      var parts = [];
      Object.keys(trend.series || {}).forEach(function (c) {
        var v = (trend.series[c] || [])[i] || 0;
        if (v > 0) parts.push(c + " " + v);
      });
      if (!parts.length) return;
      html += '<div style="display:flex;align-items:center;gap:8px">' +
        '<span style="min-width:34px;font-size:12px;color:var(--muted,#8a9099)">' + y + "</span>" +
        '<div style="flex:1;height:14px;background:#eef0f3;border-radius:3px;overflow:hidden;display:flex">' +
        Object.keys(trend.series || {}).map(function (c) {
          var v = (trend.series[c] || [])[i] || 0;
          if (!v) return "";
          var pct = Math.round(v / max * 100);
          var color = c === "movie" ? "#4f7cff" : (c === "book" ? "#34c759" : "#ff9f0a");
          return '<i style="display:block;width:' + pct + "%;background:" + color + '"></i>';
        }).join("") +
        "</div><span style='font-size:12px'>" + parts.join(" · ") + "</span></div>";
    });
    html += "</div>";
    wrap.innerHTML = html;
  }

  /* ── 深度画像报告加载（GET）与生成（POST）── */
  function loadInsight() {
    fetch("/api/douban/insight")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok && d.report) {
          renderInsight(d);
        }
        // 未生成本地有报告时保持空提示
      })
      .catch(function () { /* 忽略 */ });
  }

  function renderInsight(d) {
    var body = el("doubanInsightBody");
    if (!body) return;
    var html = "<div style='white-space:pre-wrap;line-height:1.7;font-size:13px;background:#fff;border:1px solid var(--border-color,#e6e8ec);border-radius:10px;padding:12px 14px'>" +
      escapeHtml2(d.report) + "</div>";
    if (d.generated_at) {
      html += '<p class="ed2k-hint" style="margin-top:6px">生成于 ' + d.generated_at + "</p>";
    }
    body.innerHTML = html;
  }

  function escapeHtml2(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function generateInsight() {
    var btn = el("doubanInsightBtn");
    var body = el("doubanInsightBody");
    if (btn) btn.disabled = true;
    if (body) body.innerHTML = '<p class="ed2k-empty">正在让模型读你的书影音清单，生成画像报告（需数十秒）…</p>';
    fetch("/api/douban/insight", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force: true }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok && d.report) {
          renderInsight(d);
        } else {
          if (body) body.innerHTML = '<p class="ed2k-empty">' + escapeHtml2(d.reason || "生成失败，请检查 LLM 配置") + "</p>";
        }
      })
      .catch(function () {
        if (body) body.innerHTML = '<p class="ed2k-empty">生成失败，请检查后端是否运行</p>';
      })
      .finally(function () { if (btn) btn.disabled = false; });
  }

  /* ── 事件绑定（页面首次渲染后执行；挂载在 window，供 app.js 生命周期复用） ── */
  window.initDoubanPage = function () {
    document.querySelectorAll("#doubanPage .page-subtab-btn").forEach(function (btn) {
      if (btn.dataset.doubanBound) return;
      btn.dataset.doubanBound = "1";
      btn.addEventListener("click", function () {
        switchView(btn.getAttribute("data-douban-view"));
      });
    });
    var catEl = el("doubanCategoryTabs");
    if (catEl && !catEl.dataset.bound) {
      catEl.dataset.bound = "1";
      catEl.addEventListener("click", function (e) {
        var btn = e.target.closest("button[data-cat]");
        if (!btn) return;
        state.category = btn.getAttribute("data-cat");
        renderFilters(); loadItems();
      });
    }
    var stEl = el("doubanStatusTabs");
    if (stEl && !stEl.dataset.bound) {
      stEl.dataset.bound = "1";
      stEl.addEventListener("click", function (e) {
        var btn = e.target.closest("button[data-status]");
        if (!btn) return;
        state.status = btn.getAttribute("data-status");
        renderFilters(); loadItems();
      });
    }
    var srcEl = el("doubanSearchInput");
    if (srcEl && !srcEl.dataset.bound) {
      srcEl.dataset.bound = "1";
      srcEl.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { state.search = srcEl.value.trim(); loadItems(); }
      });
    }
    var rbtn = el("doubanRefreshBtn");
    if (rbtn && !rbtn.dataset.bound) {
      rbtn.dataset.bound = "1";
      rbtn.addEventListener("click", refreshAll);
    }
    var ibtn = el("doubanInsightBtn");
    if (ibtn && !ibtn.dataset.bound) {
      ibtn.dataset.bound = "1";
      ibtn.addEventListener("click", generateInsight);
    }
  };

  window.reloadDoubanPage = function () { refreshAll(); };
})();