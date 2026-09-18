/* 开源项目研究页（桌面 SPA 内嵌视图）。
 *
 * 数据来自 /api/oss-research/*（后端从独立 12_开源项目研究/oss_research.db 读取）。
 * 流程：把 GitHub 链接发给助手 → 助手研究并入库 → 本页卡片网格展示。
 * - 顶部统计条 + 搜索 + 标签筛选。
 * - 卡片展示：仓库名/owner、一句话定位、技术栈 chips、与本项目关联摘要。
 * - 点击「详情」弹窗看完整字段（核心能力 / 可迁移手法 / 坑 / 报告链接）。
 * - 支持手动「＋ 新增」与「删除」。
 * 顶层注册渲染逻辑；路由映射在 app.js 的 DESKTOP_PAGE_ROUTES["oss-research"]。
 */
(function () {
  "use strict";

  var state = { search: "", tag: "" };

  var styleEl = document.createElement("style");
  styleEl.textContent = `
    .oss-tagbar { display:flex; gap:8px; flex-wrap:wrap; margin:6px 0 12px; }
    .oss-tagbar .ed2k-action-btn.is-active { background: var(--accent,#4f7cff); color:#fff; border-color:var(--accent,#4f7cff); }
    .oss-card { display:flex; flex-direction:column; gap:8px; background:#fff; border:1px solid var(--border-color,#e6e8ec);
      border-radius:12px; padding:14px 16px; font-size:13px; box-shadow:0 1px 2px rgba(20,30,50,.04); }
    .oss-card-head { display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
    .oss-name { font-size:15px; font-weight:700; color:var(--text,#1d2129); }
    .oss-owner { font-size:12px; color:var(--muted,#8a9099); }
    .oss-oneliner { font-size:13px; color:var(--text,#1d2129); line-height:1.5; }
    .oss-tech { display:flex; gap:6px; flex-wrap:wrap; }
    .oss-chip { font-size:11.5px; padding:2px 8px; border-radius:999px; background:var(--chip-bg,#eef2ff); color:var(--accent,#4f7cff); border:1px solid var(--chip-border,#e0e7ff); }
    .oss-tag { font-size:11.5px; padding:2px 8px; border-radius:999px; background:#f2f3f5; color:var(--muted,#8a9099); }
    .oss-relevance { font-size:12.5px; color:var(--muted,#8a9099); line-height:1.5;
      border-left:3px solid var(--accent,#4f7cff); padding-left:8px; }
    .oss-card-foot { display:flex; align-items:center; gap:10px; margin-top:2px; }
    .oss-card-foot a { color:var(--accent,#4f7cff); text-decoration:none; font-size:12.5px; }
    .oss-card-foot a:hover { text-decoration:underline; }
    .oss-link-sep { color:var(--muted,#8a9099); }
    .oss-detail-btn, .oss-del-btn { font-size:12px; background:none; border:none; cursor:pointer; color:var(--muted,#8a9099); padding:0; }
    .oss-detail-btn:hover { color:var(--accent,#4f7cff); }
    .oss-del-btn:hover { color:#e5484d; }
    .oss-modal-mask { position:fixed; inset:0; background:rgba(15,23,42,.45); display:flex; align-items:center; justify-content:center; z-index:9999; padding:20px; }
    .oss-modal { background:#fff; border-radius:14px; max-width:760px; width:100%; max-height:86vh; overflow:auto; padding:22px 24px; }
    .oss-modal h2 { margin:0 0 2px; font-size:19px; }
    .oss-modal .oss-owner { margin-bottom:12px; }
    .oss-modal section { margin:14px 0; }
    .oss-modal h3 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted,#8a9099); margin:0 0 6px; }
    .oss-modal ul { margin:0; padding-left:18px; }
    .oss-modal li { margin:3px 0; line-height:1.5; }
    .oss-modal .oss-close { position:sticky; top:0; float:right; font-size:20px; background:none; border:none; cursor:pointer; color:var(--muted,#8a9099); }
    .oss-form-row { display:flex; flex-direction:column; gap:4px; margin:8px 0; }
    .oss-form-row label { font-size:12px; color:var(--muted,#8a9099); }
    .oss-form-row input, .oss-form-row textarea { border:1px solid var(--border-color,#e6e8ec); border-radius:8px; padding:7px 9px; font-size:13px; font-family:inherit; }
    .oss-form-row textarea { min-height:64px; resize:vertical; }
    .oss-form-actions { display:flex; gap:10px; justify-content:flex-end; margin-top:10px; }
    .oss-err { color:#e5484d; font-size:12.5px; margin-top:6px; }
  `;
  document.head.appendChild(styleEl);

  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function chips(arr, cls) {
    if (!Array.isArray(arr)) return "";
    return arr.map(function (x) {
      return '<span class="' + cls + '">' + esc(x) + "</span>";
    }).join("");
  }

  /* ── 标签筛选条 ── */
  function renderTags() {
    var bar = el("ossTagTabs");
    if (!bar) return;
    fetch("/api/oss-research/tags").then(function (r) { return r.json(); }).then(function (d) {
      var tags = (d.tags || []);
      var all = '<button type="button" class="ed2k-action-btn' + (state.tag === "" ? " is-active" : "") +
        '" data-tag="">全部</button>';
      var rest = tags.map(function (t) {
        return '<button type="button" class="ed2k-action-btn' + (state.tag === t ? " is-active" : "") +
          '" data-tag="' + esc(t) + '">' + esc(t) + "</button>";
      }).join("");
      bar.innerHTML = all + rest;
      bar.querySelectorAll("[data-tag]").forEach(function (b) {
        b.onclick = function () {
          state.tag = b.getAttribute("data-tag") || "";
          renderTags();
          loadItems();
        };
      });
    }).catch(function () { bar.innerHTML = ""; });
  }

  /* ── 拉取并渲染卡片网格 ── */
  function loadItems() {
    var grid = el("ossResearchGrid");
    var empty = el("ossResearchEmpty");
    var info = el("ossStatsInfo");
    if (!grid) return;
    grid.innerHTML = '<div class="observability-loading">加载中…</div>';
    var qs = "limit=500";
    if (state.tag) qs += "&tag=" + encodeURIComponent(state.tag);
    if (state.search) qs += "&search=" + encodeURIComponent(state.search);
    fetch("/api/oss-research/projects?" + qs)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        grid.innerHTML = "";
        var items = d.items || [];
        if (!items.length) {
          if (empty) empty.hidden = false;
          if (info) info.textContent = "暂无数据";
          return;
        }
        if (empty) empty.hidden = true;
        if (info) info.textContent = "共 " + d.count + " 个开源项目 · 点击卡片「详情」看完整研究";
        items.forEach(function (it) { grid.appendChild(buildCard(it)); });
      })
      .catch(function (e) {
        grid.innerHTML = '<div class="travel-error">加载失败：' + esc(e.message) + "</div>";
      });
  }

  function buildCard(it) {
    var card = document.createElement("div");
    card.className = "oss-card";

    var head = document.createElement("div");
    head.className = "oss-card-head";
    head.innerHTML =
      '<span class="oss-name">' + esc(it.name) + "</span>" +
      (it.owner ? '<span class="oss-owner">@' + esc(it.owner) + "</span>" : "");
    card.appendChild(head);

    if (it.one_liner) {
      var ol = document.createElement("div");
      ol.className = "oss-oneliner";
      ol.textContent = it.one_liner;
      card.appendChild(ol);
    }

    var tech = document.createElement("div");
    tech.className = "oss-tech";
    tech.innerHTML = chips(it.tech_stack, "oss-chip") + chips(it.tags, "oss-tag");
    card.appendChild(tech);

    if (it.relevance_summary) {
      var rel = document.createElement("div");
      rel.className = "oss-relevance";
      rel.textContent = it.relevance_summary;
      card.appendChild(rel);
    }

    var foot = document.createElement("div");
    foot.className = "oss-card-foot";
    var links = "";
    if (it.url) links += '<a href="' + esc(it.url) + '" target="_blank" rel="noopener">GitHub ↗</a>';
    if (it.report_path) {
      if (links) links += '<span class="oss-link-sep">·</span>';
      links += '<a href="/api/oss-research/' + esc(it.report_path) + '" target="_blank" rel="noopener">研究报告 ↗</a>';
    }
    foot.innerHTML = links +
      '<button type="button" class="oss-detail-btn">详情</button>' +
      '<button type="button" class="oss-del-btn">删除</button>';
    foot.querySelector(".oss-detail-btn").onclick = function () { openDetail(it.id); };
    foot.querySelector(".oss-del-btn").onclick = function () { removeProject(it.id, it.name); };
    card.appendChild(foot);
    return card;
  }

  /* ── 详情弹窗 ── */
  function openDetail(id) {
    fetch("/api/oss-research/projects/" + id).then(function (r) { return r.json(); }).then(function (it) {
      if (it.error) { alert("未找到：" + it.error); return; }
      var mask = document.createElement("div");
      mask.className = "oss-modal-mask";
      var html = '<div class="oss-modal"><button class="oss-close" type="button">×</button>';
      html += "<h2>" + esc(it.name) + "</h2>";
      html += '<div class="oss-owner">@' + esc(it.owner) + (it.url ? ' · <a href="' + esc(it.url) + '" target="_blank" rel="noopener">GitHub ↗</a>' : "") + "</div>";

      function block(title, body) {
        if (!body) return "";
        if (Array.isArray(body) && !body.length) return "";
        var inner = Array.isArray(body)
          ? "<ul>" + body.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>"
          : "<p>" + esc(body).replace(/\n/g, "<br>") + "</p>";
        return "<section><h3>" + title + "</h3>" + inner + "</section>";
      }

      html += block("它是什么", it.purpose);
      html += block("技术栈", it.tech_stack);
      html += block("核心能力", it.key_features);
      html += block("与 OpenBiliClaw 的关联 / 可迁移点", it.relevance_summary);
      html += block("可迁移手法", it.reusable_techniques);
      html += block("项目结构亮点", it.structure_notes);
      html += block("不建议照搬", it.caveats);
      html += block("标签", it.tags);
      if (it.report_path) {
        html += '<section><h3>研究报告</h3><p><a href="/api/oss-research/' + esc(it.report_path) +
          '" target="_blank" rel="noopener">' + esc(it.report_path) + " ↗</a></p></section>";
      }
      html += "</div>";
      mask.innerHTML = html;
      mask.querySelector(".oss-close").onclick = function () { document.body.removeChild(mask); };
      mask.onclick = function (e) { if (e.target === mask) document.body.removeChild(mask); };
      document.body.appendChild(mask);
    }).catch(function (e) { alert("加载详情失败：" + e.message); });
  }

  /* ── 新增表单 ── */
  function openAddForm() {
    var mask = document.createElement("div");
    mask.className = "oss-modal-mask";
    mask.innerHTML = `
      <div class="oss-modal">
        <button class="oss-close" type="button">×</button>
        <h2>新增开源项目</h2>
        <p style="color:var(--muted,#8a9099);font-size:12.5px;margin:2px 0 8px">把 GitHub 链接发给我研究并入库更省事；这里用于手动补录。</p>
        <div class="oss-form-row"><label>仓库名 *</label><input id="ossF_name" placeholder="如 TraeWorkAssistant-mac"></div>
        <div class="oss-form-row"><label>owner</label><input id="ossF_owner" placeholder="如 ailogk"></div>
        <div class="oss-form-row"><label>GitHub 地址</label><input id="ossF_url" placeholder="https://github.com/..."></div>
        <div class="oss-form-row"><label>一句话定位</label><input id="ossF_oneliner"></div>
        <div class="oss-form-row"><label>它是什么</label><textarea id="ossF_purpose"></textarea></div>
        <div class="oss-form-row"><label>技术栈（逗号分隔）</label><input id="ossF_tech"></div>
        <div class="oss-form-row"><label>核心能力（逗号分隔）</label><input id="ossF_features"></div>
        <div class="oss-form-row"><label>与本项目关联 / 可迁移点</label><textarea id="ossF_relevance"></textarea></div>
        <div class="oss-form-row"><label>可迁移手法（逗号分隔）</label><input id="ossF_techs"></div>
        <div class="oss-form-row"><label>不建议照搬</label><textarea id="ossF_caveats"></textarea></div>
        <div class="oss-form-row"><label>标签（逗号分隔）</label><input id="ossF_tags" placeholder="chrome-extension, card-feed"></div>
        <div class="oss-form-row"><label>研究报告路径</label><input id="ossF_report" placeholder="WikiTok-借鉴分析.md"></div>
        <div class="oss-err" id="ossF_err" hidden></div>
        <div class="oss-form-actions">
          <button type="button" class="ed2k-action-btn" id="ossF_cancel">取消</button>
          <button type="button" class="ed2k-action-btn is-active" id="ossF_save">保存</button>
        </div>
      </div>`;
    mask.querySelector(".oss-close").onclick = function () { document.body.removeChild(mask); };
    mask.querySelector("#ossF_cancel").onclick = function () { document.body.removeChild(mask); };
    mask.querySelector("#ossF_save").onclick = function () {
      var split = function (v) {
        return v.split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
      };
      var payload = {
        name: mask.querySelector("#ossF_name").value.trim(),
        owner: mask.querySelector("#ossF_owner").value.trim(),
        url: mask.querySelector("#ossF_url").value.trim(),
        one_liner: mask.querySelector("#ossF_oneliner").value.trim(),
        purpose: mask.querySelector("#ossF_purpose").value.trim(),
        tech_stack: split(mask.querySelector("#ossF_tech").value),
        key_features: split(mask.querySelector("#ossF_features").value),
        relevance_summary: mask.querySelector("#ossF_relevance").value.trim(),
        reusable_techniques: split(mask.querySelector("#ossF_techs").value),
        caveats: mask.querySelector("#ossF_caveats").value.trim(),
        tags: split(mask.querySelector("#ossF_tags").value),
        report_path: mask.querySelector("#ossF_report").value.trim(),
      };
      var errEl = mask.querySelector("#ossF_err");
      if (!payload.name) { errEl.textContent = "仓库名必填"; errEl.hidden = false; return; }
      fetch("/api/oss-research/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok) { errEl.textContent = res.j.error || "保存失败"; errEl.hidden = false; return; }
          document.body.removeChild(mask);
          renderTags();
          loadItems();
        }).catch(function (e) { errEl.textContent = "保存失败：" + e.message; errEl.hidden = false; });
    };
    document.body.appendChild(mask);
  }

  function removeProject(id, name) {
    if (!confirm("确认删除「" + name + "」？此操作不可恢复。")) return;
    fetch("/api/oss-research/projects/" + id, { method: "DELETE" })
      .then(function (r) { return r.json(); })
      .then(function () { renderTags(); loadItems(); })
      .catch(function (e) { alert("删除失败：" + e.message); });
  }

  /* ── 对外暴露（app.js 调用）── */
  window.initOssResearchPage = function () {
    var btn = el("ossAddBtn");
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.onclick = openAddForm;
    }
    var ref = el("ossRefreshBtn");
    if (ref && !ref._bound) {
      ref._bound = true;
      ref.onclick = loadItems;
    }
    var search = el("ossSearchInput");
    if (search && !search._bound) {
      search._bound = true;
      search.oninput = function () {
        clearTimeout(window.__ossSearchTimer);
        window.__ossSearchTimer = setTimeout(function () {
          state.search = search.value.trim();
          loadItems();
        }, 250);
      };
    }
  };

  window.reloadOssResearchPage = function () {
    renderTags();
    loadItems();
  };
})();
