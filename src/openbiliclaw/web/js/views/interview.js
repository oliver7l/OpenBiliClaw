/**
 * Interview view — 求职面试备战
 *
 * Shows: job overview with speed-card drill-down, full-text search across the
 * knowledge base, authoritative number table, and interview logs (list + append).
 */

import {
  fetchInterviewStatus,
  fetchInterviewJobs,
  fetchInterviewSearch,
  fetchInterviewNumbers,
  fetchInterviewCard,
  fetchInterviewLogs,
  postInterviewLog,
  fetchInterviewTopics,
  fetchInterviewTopicDetail,
  fetchInterviewScripts,
  fetchInterviewScriptTypes,
  fetchInterviewScriptDetail,
  fetchInterviewPositions,
  fetchInterviewPositionDetail,
  updateInterviewPositionStatus,
} from "../api.js";

let $root = null;
let loaded = false;
let loading = false;
let statusData = null;
let jobsData = null;
let searchResults = null;
let numbersData = null;
let logsData = null;
let topicsData = null;
let topicDetail = null;
let topicLoading = false;
let scriptsData = null;
let scriptTypes = null;
let scriptDetail = null;
let scriptFilterType = "";
let scriptFilterCompany = "";
let positionsData = null;
let positionDetail = null;
let positionFilterCompany = "";
let positionFilterCity = "";
let positionFilterStatus = "";
let cardData = null;
let cardCompany = "";
let cardLoading = false;
let searchQuery = "";
let numbersQuery = "";
let searchLoading = false;
let activeSection = "jobs"; // jobs | search | numbers | logs

function esc(s) {
  const el = document.createElement("span");
  el.textContent = s == null ? "" : String(s);
  return el.innerHTML;
}

function errMsg(e) {
  const detail = e?.details?.detail;
  if (detail) return String(detail);
  return e ? String(e.message || e) : "未知错误";
}

// ── Section tabs ───────────────────────────────────────────────
function renderSectionTabs() {
  const tabs = [
    { id: "jobs", label: "🏢 岗位" },
    { id: "positions", label: "📋 投递" },
    { id: "resumes", label: "📄 简历" },
    { id: "search", label: "🔍 检索" },
    { id: "numbers", label: "🔢 数字" },
    { id: "topics", label: "📚 专题" },
    { id: "scripts", label: "💬 话术" },
    { id: "logs", label: "📝 日志" },
  ];
  return (
    `<div class="interview-section-tabs">` +
    tabs
      .map(
        (t) =>
          `<button class="interview-section-tab${activeSection === t.id ? " active" : ""}" data-section="${t.id}" type="button">${t.label}</button>`,
      )
      .join("") +
    `</div>`
  );
}

// ── Jobs section ───────────────────────────────────────────────
function renderJobs() {
  if (!jobsData) return `<div class="interview-empty">加载岗位中…</div>`;
  const items = jobsData.items || [];
  if (items.length === 0) return `<div class="interview-empty">未找到匹配岗位</div>`;

  let html = `<div class="interview-jobs">`;
  for (const j of items) {
    html += `<div class="interview-job-card" data-company="${esc(j.公司 || "")}">
      <div class="interview-job-head">
        <span class="interview-job-company">${esc(j.公司 || "")}</span>
        <span class="interview-job-role">${esc(j.岗位 || "")}</span>
        <span class="interview-job-status">${esc(j.状态 || "")}</span>
      </div>
      <div class="interview-job-meta">
        ${j.面试时间 ? `<span>🗓 ${esc(j.面试时间)}</span>` : ""}
        ${j.主打方向 ? `<span>🎯 ${esc(j.主打方向)}</span>` : ""}
        ${j.备战目录 ? `<span>📁 ${esc(j.备战目录)}</span>` : ""}
      </div>
      ${j.备注 ? `<div class="interview-job-note">${esc(j.备注)}</div>` : ""}
      <button class="interview-card-btn" data-company="${esc(j.公司 || "")}" type="button">速记卡 →</button>
    </div>`;
  }
  html += `</div>`;

  if (cardData && cardData.job) {
    html += renderCard(cardData);
  } else if (cardLoading) {
    html += `<div class="interview-empty">加载速记卡中…</div>`;
  }
  return html;
}

function renderCard(card) {
  const job = card.job || {};
  let html = `<div class="interview-card" id="interviewCard">
    <div class="interview-card-title">
      ⚡ 速记卡 · ${esc(job.公司 || "")} · ${esc(job.岗位 || "")}
      <span class="interview-card-meta">面试 ${esc(job.面试时间 || "")} · ${esc(job.状态 || "")}</span>
    </div>
    <div class="interview-card-sub">主打方向：${esc(job.主打方向 || "")}</div>
    <div class="interview-card-block">
      <div class="interview-card-block-title">核心数字</div>
      ${(card.numbers || []).length === 0
        ? `<div class="interview-empty">无</div>`
        : (card.numbers || [])
            .map((n) => `<div class="interview-num-row">${esc(n.数字 || "")} <span class="interview-num-ke">${esc(n.口径 || "")}</span> <span class="interview-num-src">(${esc(n["公司/项目"] || "")})</span></div>`)
            .join("")}
    </div>
    <div class="interview-card-block">
      <div class="interview-card-block-title">可讲项目</div>
      ${(card.projects || []).length === 0
        ? `<div class="interview-empty">无</div>`
        : (card.projects || [])
            .map((p) => `<div class="interview-proj-row">· ${esc(p.项目名 || "")} [${esc(p.公司 || "")}] <span class="interview-num-ke">${esc(p.核心数字 || "")}</span></div>`)
            .join("")}
    </div>
    <div class="interview-card-block">
      <div class="interview-card-block-title">题库入口</div>
      ${(card.questions || []).length === 0
        ? `<div class="interview-empty">无</div>`
        : (card.questions || [])
            .map((q) => `<div class="interview-q-row">· ${esc(q.题目 || "")} [${esc(q.方向 || "")}] → <span class="interview-num-ke">${esc(q.答案位置 || "")}</span></div>`)
            .join("")}
    </div>
    ${card.prep && card.prep.dir
      ? `<div class="interview-card-block">
          <div class="interview-card-block-title">岗位定制弹药 · 速成包/备战资料</div>
          ${(card.prep.quick_pack || []).map((p) => `<div class="interview-proj-row">📦 ${esc(p.name)} <span class="interview-num-ke">${esc(p.lines)} 行</span></div>`).join("")}
          ${(card.prep.prep_docs || []).map((d) => `<div class="interview-proj-row">📄 ${esc(d.name)} <span class="interview-num-ke">${esc(d.lines)} 行</span></div>`).join("")}
        </div>`
      : ""}
    ${card.prep && card.prep.quick_card
      ? `<div class="interview-card-block">
          <div class="interview-card-block-title">面试前速记卡</div>
          <pre class="interview-card-notes">${esc(card.prep.quick_card)}</pre>
        </div>`
      : ""}
  </div>`;
  return html;
}

// ── Search section ─────────────────────────────────────────────
function renderSearch() {
  return `<div class="interview-search-box">
    <input id="interviewSearchInput" type="text" placeholder="全文检索：oCPX / 召回 / ARPU / 因果…" value="${esc(searchQuery)}" />
    <button id="interviewSearchBtn" type="button">检索</button>
  </div>
  <div class="interview-search-results" id="interviewSearchResults">
    ${searchLoading
      ? `<div class="interview-empty">检索中…</div>`
      : searchResults === null
        ? `<div class="interview-empty">输入关键词后检索（覆盖 02 方向库 / 03 岗位库 / 工作资料 / 解码文本 / 系统规范）</div>`
        : searchResults.total === 0
          ? `<div class="interview-empty">无命中</div>`
          : searchResults.items
              .map(
                (h) =>
                  `<div class="interview-hit">
                    <div class="interview-hit-path">${esc(h.path)}:${esc(h.line)}</div>
                    <div class="interview-hit-snippet">${esc(h.snippet)}</div>
                  </div>`,
              )
              .join("")}
  </div>`;
}

// ── Numbers section ────────────────────────────────────────────
function renderNumbers() {
  if (!numbersData) return `<div class="interview-empty">加载数字表中…</div>`;
  const rows = numbersData.items || [];
  if (rows.length === 0) return `<div class="interview-empty">未找到匹配数字</div>`;
  let html = `<div class="interview-numbers-filter">
    <input id="interviewNumbersInput" type="text" placeholder="过滤：ARPU / ecpm / 留存…" value="${esc(numbersQuery)}" />
    <button id="interviewNumbersBtn" type="button">过滤</button>
  </div>
  <table class="interview-table">
    <thead><tr><th>数字</th><th>口径</th><th>公司/项目</th><th>来源</th></tr></thead><tbody>`;
  for (const r of rows) {
    html += `<tr>
      <td class="interview-num-value">${esc(r.数字 || "")}</td>
      <td>${esc(r.口径 || "")}</td>
      <td>${esc(r["公司/项目"] || "")}</td>
      <td>${esc(r.来源 || "")}</td>
    </tr>`;
  }
  html += `</tbody></table>
    <div class="interview-footer-note">共 ${rows.length} 条 · 口径以真实数字表为准，严禁编造</div>`;
  return html;
}

// ── Logs section ───────────────────────────────────────────────
function renderLogs() {
  if (!logsData) return `<div class="interview-empty">加载面试日志中…</div>`;
  const rows = logsData.items || [];
  let html = `<div class="interview-log-form">
    <input id="interviewLogCompany" type="text" placeholder="公司" />
    <input id="interviewLogRound" type="text" placeholder="轮次（一面/二面/HR面）" />
    <input id="interviewLogPoints" type="text" placeholder="被问要点" />
    <button id="interviewLogBtn" type="button">追加日志</button>
  </div>
  <div class="interview-log-msg" id="interviewLogMsg"></div>`;
  if (rows.length === 0) {
    html += `<div class="interview-empty">暂无面试日志</div>`;
  } else {
    html += `<table class="interview-table">
      <thead><tr><th>日期</th><th>公司</th><th>轮次</th><th>被问要点</th><th>复盘</th></tr></thead><tbody>`;
    for (const r of rows) {
      html += `<tr>
        <td>${esc(r.日期 || "")}</td>
        <td>${esc(r.公司 || "")}</td>
        <td>${esc(r.轮次 || "")}</td>
        <td>${esc(r["被问要点"] || "")}</td>
        <td>${esc(r.复盘 || "")}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }
  return html;
}

// ── Topics section ─────────────────────────────────────────────
function renderTopics() {
  if (topicDetail) {
    const t = topicDetail;
    return `
      <div class="interview-topic-detail">
        <button class="interview-topic-back" id="interviewTopicBack" type="button">← 返回专题列表</button>
        <div class="interview-topic-title">${esc(t.topic || "")}</div>
        <div class="interview-topic-meta">
          ${t.category ? `<span>📂 ${esc(t.category)}</span>` : ""}
          ${t.chapter ? `<span>📖 ${esc(t.chapter)}</span>` : ""}
          ${t.tags ? `<span>🏷 ${esc(t.tags)}</span>` : ""}
          ${t.updated ? `<span>🕐 ${esc(String(t.updated).slice(0, 10))}</span>` : ""}
        </div>
        <div class="interview-topic-content">${esc(t.content || "").replace(/\n/g, "<br>")}</div>
      </div>`;
  }

  if (!topicsData) return `<div class="interview-empty">加载专题库中…</div>`;
  const items = topicsData.items || [];
  if (items.length === 0) return `<div class="interview-empty">暂无面试专题</div>`;

  let html = `<div class="interview-topics">`;
  for (const t of items) {
    html += `<div class="interview-topic-card" data-id="${t.id}" type="button">
      <div class="interview-topic-card-title">${esc(t.topic || "")}</div>
      <div class="interview-topic-card-meta">
        ${t.category ? `<span>📂 ${esc(t.category)}</span>` : ""}
        ${t.chapter ? `<span>📖 ${esc(t.chapter)}</span>` : ""}
        ${t.updated ? `<span>🕐 ${esc(String(t.updated).slice(0, 10))}</span>` : ""}
      </div>
      ${t.tags ? `<div class="interview-topic-card-tags">${esc(t.tags)}</div>` : ""}
    </div>`;
  }
  html += `</div>`;
  return html;
}

// ── Scripts section ────────────────────────────────────────────
function renderScripts() {
  if (scriptDetail) {
    const s = scriptDetail;
    return `
      <div class="interview-script-detail">
        <button class="interview-script-back" id="interviewScriptBack" type="button">← 返回话术列表</button>
        <div class="interview-script-title">${esc(s.title || "")}</div>
        <div class="interview-script-meta">
          ${s.type ? `<span class="interview-script-tag">${esc(s.type)}</span>` : ""}
          ${s.company && s.company !== "通用" ? `<span>🏢 ${esc(s.company)}</span>` : ""}
          ${s.priority ? `<span>⭐ ${esc(s.priority)}优先级</span>` : ""}
          ${s.tags ? `<span>🏷 ${esc(s.tags)}</span>` : ""}
        </div>
        ${s.key_points ? `<div class="interview-script-keypoints"><strong>关键要点：</strong>${esc(s.key_points)}</div>` : ""}
        <div class="interview-script-content">${esc(s.content || "").replace(/\n/g, "<br>")}</div>
      </div>`;
  }

  if (!scriptsData) return `<div class="interview-empty">加载话术库中…</div>`;
  const items = scriptsData.items || [];

  // 筛选栏
  let filterHtml = `<div class="interview-script-filters">`;
  filterHtml += `<select id="scriptTypeFilter" class="interview-script-select">
    <option value="">全部类型</option>`;
  if (scriptTypes) {
    for (const t of scriptTypes.items || []) {
      filterHtml += `<option value="${esc(t.type)}"${scriptFilterType === t.type ? " selected" : ""}>${esc(t.type)} (${t.count})</option>`;
    }
  }
  filterHtml += `</select>`;
  filterHtml += `<select id="scriptCompanyFilter" class="interview-script-select">
    <option value="">全部公司</option>
    <option value="通用"${scriptFilterCompany === "通用" ? " selected" : ""}>通用</option>`;
  // 从job表取公司列表
  if (jobsData) {
    for (const j of jobsData.items || []) {
      const company = j.公司 || "";
      if (company) filterHtml += `<option value="${esc(company)}"${scriptFilterCompany === company ? " selected" : ""}>${esc(company)}</option>`;
    }
  }
  filterHtml += `</select>`;
  filterHtml += `</div>`;

  if (items.length === 0) {
    return filterHtml + `<div class="interview-empty">暂无匹配话术</div>`;
  }

  let html = filterHtml + `<div class="interview-scripts">`;
  for (const s of items) {
    const priColor = s.priority === "高" ? "#ea6668" : s.priority === "中" ? "#faad14" : "#999";
    html += `<div class="interview-script-card" data-id="${s.id}" type="button">
      <div class="interview-script-card-head">
        <span class="interview-script-card-type">${esc(s.type || "")}</span>
        <span class="interview-script-card-pri" style="color:${priColor}">${esc(s.priority || "")}</span>
        ${s.company && s.company !== "通用" ? `<span class="interview-script-card-company">${esc(s.company)}</span>` : ""}
      </div>
      <div class="interview-script-card-title">${esc(s.title || "")}</div>
      ${s.key_points ? `<div class="interview-script-card-keypoints">${esc(String(s.key_points).slice(0, 60))}${String(s.key_points).length > 60 ? "…" : ""}</div>` : ""}
    </div>`;
  }
  html += `</div>`;
  return html;
}

// ── Positions section (岗位投递管理) ─────────────────────────
function renderPositions() {
  if (positionDetail) {
    const p = positionDetail;
    const statusColors = {
      "待投递": "#999", "已投递": "#1890ff", "面试中": "#faad14",
      "已offer": "#52c41a", "已拒绝": "#ff4d4f", "已归档": "#bbb",
    };
    const stColor = statusColors[p.status] || "#999";
    return `
      <div class="interview-script-detail">
        <button class="interview-script-back" id="positionBackBtn" type="button">← 返回岗位列表</button>
        <div class="interview-script-title">${esc(p.title || "")}</div>
        <div class="interview-script-meta">
          <span>🏢 ${esc(p.company || "")}${p.bg ? `·${esc(p.bg)}` : ""}</span>
          <span>📍 ${esc(p.city || "未标注")}</span>
          <span>📅 ${esc(p.years_required || "经验不限")}</span>
          <span style="color:${stColor}">● ${esc(p.status || "")}</span>
          ${p.match_score ? `<span>🎯 匹配度 ${p.match_score}</span>` : ""}
        </div>
        ${p.match_points ? `<div class="interview-script-keypoints"><strong>匹配点：</strong>${esc(p.match_points)}</div>` : ""}
        ${p.description ? `<div class="interview-position-block"><strong>岗位职责：</strong><div>${esc(p.description).replace(/\n/g, "<br>")}</div></div>` : ""}
        ${p.requirements ? `<div class="interview-position-block"><strong>岗位要求：</strong><div>${esc(p.requirements).replace(/\n/g, "<br>")}</div></div>` : ""}
        ${p.job_url ? `<div class="interview-position-block"><a href="${esc(p.job_url)}" target="_blank" rel="noopener">🔗 打开招聘页面</a></div>` : ""}
        <div class="interview-position-actions">
          <select id="positionStatusSelect" class="interview-script-select">
            <option value="待投递"${p.status === "待投递" ? " selected" : ""}>待投递</option>
            <option value="已投递"${p.status === "已投递" ? " selected" : ""}>已投递</option>
            <option value="面试中"${p.status === "面试中" ? " selected" : ""}>面试中</option>
            <option value="已offer"${p.status === "已offer" ? " selected" : ""}>已offer</option>
            <option value="已拒绝"${p.status === "已拒绝" ? " selected" : ""}>已拒绝</option>
            <option value="已归档"${p.status === "已归档" ? " selected" : ""}>已归档</option>
          </select>
          <button id="positionStatusSave" class="interview-refresh-btn" type="button">更新状态</button>
        </div>
      </div>`;
  }

  if (!positionsData) return `<div class="interview-empty">加载岗位列表中…</div>`;
  const items = positionsData.items || [];

  // 筛选栏
  let filterHtml = `<div class="interview-script-filters">`;
  filterHtml += `<select id="positionCompanyFilter" class="interview-script-select">
    <option value="">全部公司</option>`;
  const companies = [...new Set(items.map((p) => p.company).filter(Boolean))];
  for (const co of companies) {
    filterHtml += `<option value="${esc(co)}"${positionFilterCompany === co ? " selected" : ""}>${esc(co)}</option>`;
  }
  filterHtml += `</select>`;
  filterHtml += `<select id="positionCityFilter" class="interview-script-select">
    <option value="">全部城市</option>
    <option value="深圳"${positionFilterCity === "深圳" ? " selected" : ""}>深圳</option>
    <option value="广州"${positionFilterCity === "广州" ? " selected" : ""}>广州</option>
    <option value="北京"${positionFilterCity === "北京" ? " selected" : ""}>北京</option>
  </select>`;
  filterHtml += `<select id="positionStatusFilter" class="interview-script-select">
    <option value="">全部状态</option>
    <option value="待投递"${positionFilterStatus === "待投递" ? " selected" : ""}>待投递</option>
    <option value="已投递"${positionFilterStatus === "已投递" ? " selected" : ""}>已投递</option>
    <option value="面试中"${positionFilterStatus === "面试中" ? " selected" : ""}>面试中</option>
  </select>`;
  // 统计
  const byStatus = {};
  for (const p of items) byStatus[p.status] = (byStatus[p.status] || 0) + 1;
  filterHtml += `<span style="margin-left:auto;color:#888;font-size:12px">共 ${items.length} 个 · 待投递 ${byStatus["待投递"] || 0} · 已投递 ${byStatus["已投递"] || 0} · 面试中 ${byStatus["面试中"] || 0}</span>`;
  filterHtml += `</div>`;

  if (items.length === 0) {
    return filterHtml + `<div class="interview-empty">暂无匹配岗位</div>`;
  }

  let html = filterHtml + `<div class="interview-scripts">`;
  for (const p of items) {
    const stColor = { "待投递": "#999", "已投递": "#1890ff", "面试中": "#faad14", "已offer": "#52c41a", "已拒绝": "#ff4d4f", "已归档": "#bbb" }[p.status] || "#999";
    const matchColor = p.match_score >= 90 ? "#52c41a" : p.match_score >= 80 ? "#faad14" : p.match_score > 0 ? "#999" : "#ddd";
    html += `<div class="interview-script-card" data-position-id="${p.id}" type="button">
      <div class="interview-script-card-head">
        <span class="interview-script-card-type">${esc(p.company || "")}${p.bg ? `·${esc(p.bg)}` : ""}</span>
        <span style="color:${stColor};font-size:12px">● ${esc(p.status || "")}</span>
        ${p.match_score > 0 ? `<span style="color:${matchColor};font-size:12px;font-weight:600">匹配${p.match_score}</span>` : ""}
      </div>
      <div class="interview-script-card-title">${esc(p.title || "")}</div>
      <div style="font-size:12px;color:#888;margin-top:4px">📍 ${esc(p.city || "未标注")} · 📅 ${esc(p.years_required || "经验不限")}</div>
      ${p.match_points ? `<div class="interview-script-card-keypoints">${esc(String(p.match_points).slice(0, 50))}${String(p.match_points).length > 50 ? "…" : ""}</div>` : ""}
    </div>`;
  }
  html += `</div>`;
  return html;
}

// ── Resumes ────────────────────────────────────────────────────
let resumesData = null;
let resumesLoading = false;
let activeResumeId = null;

async function loadResumes() {
  if (resumesLoading) return;
  resumesLoading = true;
  try {
    const data = await api.fetchInterviewResumes();
    resumesData = data;
  } catch (e) {
    console.error("loadResumes error:", e);
    resumesData = { items: [], total: 0 };
  }
  resumesLoading = false;
  render();
}

async function loadResumeDetail(id) {
  try {
    const detail = await api.fetchInterviewResumeDetail(id);
    activeResumeId = id;
    // 把详情存到items里
    if (resumesData && resumesData.items) {
      const idx = resumesData.items.findIndex((r) => r.id === id);
      if (idx >= 0) resumesData.items[idx] = { ...resumesData.items[idx], ...detail };
    }
    render();
  } catch (e) {
    console.error("loadResumeDetail error:", e);
  }
}

function renderResumes() {
  if (!resumesData) {
    loadResumes();
    return `<div style="padding:40px;text-align:center"><div class="spinner"></div><div style="margin-top:12px;color:#888">加载简历…</div></div>`;
  }
  const items = resumesData.items || [];
  if (items.length === 0) {
    return `<div class="interview-empty">暂无定制简历，点击岗位详情页"生成简历"按钮创建</div>`;
  }

  // 详情视图
  if (activeResumeId) {
    const r = items.find((x) => x.id === activeResumeId);
    if (r && r.full_text) {
      return `
        <div class="interview-script-detail">
          <button class="interview-back-btn" id="resumeBackBtn" type="button">← 返回列表</button>
          <div class="interview-script-title">${esc(r.company)} - ${esc(r.target_position)}</div>
          <div class="interview-script-meta">版本：${esc(r.version_name || "")}</div>
          ${r.highlights ? `<div class="interview-script-keypoints"><strong>核心亮点：</strong>${esc(r.highlights)}</div>` : ""}
          ${r.matched_keywords ? `<div class="interview-script-meta" style="margin-bottom:16px">匹配关键词：${esc(r.matched_keywords)}</div>` : ""}
          <div class="interview-script-body" style="white-space:pre-wrap;font-size:13px;line-height:1.8;color:#374151">${esc(r.full_text)}</div>
          ${r.file_path ? `<div class="interview-script-meta" style="margin-top:16px">文件路径：<code>${esc(r.file_path)}</code></div>` : ""}
        </div>`;
    }
  }

  // 列表视图
  let html = `<div class="interview-script-grid">`;
  for (const r of items) {
    html += `
      <div class="interview-script-card" data-resume-id="${r.id}" style="cursor:pointer">
        <div class="interview-script-card-title">${esc(r.company)} · ${esc(r.target_position)}</div>
        <div class="interview-script-card-meta">${esc(r.version_name || "")}</div>
        ${r.highlights ? `<div class="interview-script-card-keypoints">${esc(String(r.highlights).slice(0, 60))}${String(r.highlights).length > 60 ? "…" : ""}</div>` : ""}
        ${r.matched_keywords ? `<div class="interview-script-card-meta" style="margin-top:8px;font-size:11px;color:#8b5cf6">${esc(r.matched_keywords)}</div>` : ""}
      </div>`;
  }
  html += `</div>`;
  html += `<div class="interview-script-meta" style="margin-top:12px">共 ${resumesData.total} 份定制简历</div>`;
  return html;
}

// ── Main render ────────────────────────────────────────────────
function render() {
  if (!$root) return;

  if (loading && !statusData) {
    $root.innerHTML = `<div class="interview-view"><div style="padding:40px;text-align:center"><div class="spinner"></div><div style="margin-top:12px;color:#888">加载求职知识库…</div></div></div>`;
    return;
  }

  if (statusData && statusData.configured === false) {
    $root.innerHTML = `<div class="interview-view">
      <div class="interview-head"><span class="interview-head-icon">🎯</span><span class="interview-head-title">求职面试备战</span></div>
      <div class="interview-unconfigured">
        求职知识库未配置或目录不存在（${esc(statusData.root)}）。<br>
        请设置 config.toml 的 <code>[interview] root</code> 或环境变量 <code>OPENBILICLAW_INTERVIEW_ROOT</code>。
      </div>
    </div>`;
    return;
  }

  let content = "";
  if (activeSection === "jobs") content = renderJobs();
  else if (activeSection === "positions") content = renderPositions();
  else if (activeSection === "resumes") content = renderResumes();
  else if (activeSection === "search") content = renderSearch();
  else if (activeSection === "numbers") content = renderNumbers();
  else if (activeSection === "topics") content = renderTopics();
  else if (activeSection === "scripts") content = renderScripts();
  else if (activeSection === "logs") content = renderLogs();

  $root.innerHTML = `<div class="interview-view">
    <div class="interview-head">
      <span class="interview-head-icon">🎯</span>
      <span class="interview-head-title">求职面试备战</span>
      ${statusData ? `<span class="interview-head-stat">${statusData.jobs.length} 岗 · ${statusData.number_count} 数字 · ${statusData.log_count} 日志</span>` : ""}
      <button class="interview-refresh-btn" id="interviewRefreshBtn" type="button" title="刷新">🔄</button>
    </div>
    ${renderSectionTabs()}
    <div class="interview-content">${content}</div>
  </div>`;

  // Bind events
  $root.querySelectorAll(".interview-section-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeSection = btn.dataset.section;
      if (activeSection === "positions" && !positionsData) loadPositions();
      render();
    });
  });

  const refreshBtn = $root.querySelector("#interviewRefreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadAll(true));
  }

  // Topic cards click → load detail
  const topicCards = $root.querySelectorAll(".interview-topic-card");
  topicCards.forEach((card) => {
    card.addEventListener("click", () => loadTopicDetail(parseInt(card.dataset.id, 10)));
  });
  const topicBack = $root.querySelector("#interviewTopicBack");
  if (topicBack) {
    topicBack.addEventListener("click", () => {
      topicDetail = null;
      render();
    });
  }

  // Script cards click → load detail
  const scriptCards = $root.querySelectorAll(".interview-script-card");
  scriptCards.forEach((card) => {
    card.addEventListener("click", () => loadScriptDetail(parseInt(card.dataset.id, 10)));
  });
  const scriptBack = $root.querySelector("#interviewScriptBack");
  if (scriptBack) {
    scriptBack.addEventListener("click", () => {
      scriptDetail = null;
      render();
    });
  }
  const typeFilter = $root.querySelector("#scriptTypeFilter");
  if (typeFilter) {
    typeFilter.addEventListener("change", () => {
      scriptFilterType = typeFilter.value;
      loadScripts();
    });
  }
  const companyFilter = $root.querySelector("#scriptCompanyFilter");
  if (companyFilter) {
    companyFilter.addEventListener("change", () => {
      scriptFilterCompany = companyFilter.value;
      loadScripts();
    });
  }

  // Position cards click → load detail
  const positionCards = $root.querySelectorAll("[data-position-id]");
  positionCards.forEach((card) => {
    card.addEventListener("click", () => loadPositionDetail(parseInt(card.dataset.positionId, 10)));
  });
  const positionBack = $root.querySelector("#positionBackBtn");
  if (positionBack) {
    positionBack.addEventListener("click", () => {
      positionDetail = null;
      render();
    });
  }
  // 简历事件
  const resumeCards = $root.querySelectorAll("[data-resume-id]");
  resumeCards.forEach((card) => {
    card.addEventListener("click", () => loadResumeDetail(parseInt(card.dataset.resumeId, 10)));
  });
  const resumeBack = $root.querySelector("#resumeBackBtn");
  if (resumeBack) {
    resumeBack.addEventListener("click", () => {
      activeResumeId = null;
      render();
    });
  }
  const posCompanyFilter = $root.querySelector("#positionCompanyFilter");
  if (posCompanyFilter) {
    posCompanyFilter.addEventListener("change", () => {
      positionFilterCompany = posCompanyFilter.value;
      loadPositions();
    });
  }
  const posCityFilter = $root.querySelector("#positionCityFilter");
  if (posCityFilter) {
    posCityFilter.addEventListener("change", () => {
      positionFilterCity = posCityFilter.value;
      loadPositions();
    });
  }
  const posStatusFilter = $root.querySelector("#positionStatusFilter");
  if (posStatusFilter) {
    posStatusFilter.addEventListener("change", () => {
      positionFilterStatus = posStatusFilter.value;
      loadPositions();
    });
  }
  const posStatusSave = $root.querySelector("#positionStatusSave");
  if (posStatusSave && positionDetail) {
    posStatusSave.addEventListener("click", async () => {
      const sel = $root.querySelector("#positionStatusSelect");
      if (!sel) return;
      try {
        await updateInterviewPositionStatus(positionDetail.id, sel.value);
        positionDetail.status = sel.value;
        loadPositions();
        render();
      } catch (e) {
        alert("更新状态失败：" + (e.message || e));
      }
    });
  }

  const cardBtns = $root.querySelectorAll(".interview-card-btn");
  cardBtns.forEach((btn) => {
    btn.addEventListener("click", () => loadCard(btn.dataset.company));
  });

  const searchBtn = $root.querySelector("#interviewSearchBtn");
  const searchInput = $root.querySelector("#interviewSearchInput");
  if (searchBtn && searchInput) {
    const run = () => {
      searchQuery = searchInput.value.trim();
      loadSearch();
    };
    searchBtn.addEventListener("click", run);
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") run();
    });
  }

  const numbersBtn = $root.querySelector("#interviewNumbersBtn");
  const numbersInput = $root.querySelector("#interviewNumbersInput");
  if (numbersBtn && numbersInput) {
    const run = () => {
      numbersQuery = numbersInput.value.trim();
      loadNumbers();
    };
    numbersBtn.addEventListener("click", run);
    numbersInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") run();
    });
  }

  const logBtn = $root.querySelector("#interviewLogBtn");
  if (logBtn) {
    logBtn.addEventListener("click", async () => {
      const company = $root.querySelector("#interviewLogCompany")?.value.trim() || "";
      const round = $root.querySelector("#interviewLogRound")?.value.trim() || "";
      const points = $root.querySelector("#interviewLogPoints")?.value.trim() || "";
      const msg = $root.querySelector("#interviewLogMsg");
      if (!company || !round || !points) {
        if (msg) msg.textContent = "公司 / 轮次 / 被问要点 均必填";
        return;
      }
      try {
        await postInterviewLog({ company, round, points });
        if (msg) msg.textContent = `✅ 已追加 ${company} / ${round}`;
        await loadLogs();
        render();
      } catch (e) {
        if (msg) msg.textContent = `❌ ${errMsg(e)}`;
      }
    });
  }
}

// ── Data loading ───────────────────────────────────────────────
async function loadCard(company) {
  if (!company) return;
  cardLoading = true;
  cardCompany = company;
  render();
  try {
    cardData = await fetchInterviewCard(company);
  } catch (e) {
    cardData = null;
    console.error("Interview card load failed:", e);
  } finally {
    cardLoading = false;
    render();
  }
}

async function loadSearch() {
  searchLoading = true;
  render();
  try {
    searchResults = await fetchInterviewSearch(searchQuery || "x", 40);
    if (!searchQuery) searchResults = null;
  } catch (e) {
    searchResults = { total: 0, items: [] };
    console.error("Interview search failed:", e);
  } finally {
    searchLoading = false;
    render();
  }
}

async function loadNumbers() {
  try {
    numbersData = await fetchInterviewNumbers(numbersQuery);
  } catch (e) {
    console.error("Interview numbers failed:", e);
  } finally {
    render();
  }
}

async function loadLogs() {
  try {
    logsData = await fetchInterviewLogs();
  } catch (e) {
    console.error("Interview logs failed:", e);
  }
}

async function loadTopicDetail(id) {
  topicLoading = true;
  render();
  try {
    topicDetail = await fetchInterviewTopicDetail(id);
  } catch (e) {
    topicDetail = null;
    console.error("Interview topic detail failed:", e);
  } finally {
    topicLoading = false;
    render();
  }
}

async function loadScripts() {
  try {
    const [typesRes, listRes] = await Promise.all([
      fetchInterviewScriptTypes(),
      fetchInterviewScripts(scriptFilterType, scriptFilterCompany),
    ]);
    scriptTypes = typesRes;
    scriptsData = listRes;
  } catch (e) {
    scriptsData = { total: 0, items: [], error: String(e) };
    console.error("Interview scripts failed:", e);
  }
  if (activeSection === "scripts") render();
}

async function loadScriptDetail(id) {
  try {
    scriptDetail = await fetchInterviewScriptDetail(id);
  } catch (e) {
    scriptDetail = { title: "加载失败", content: String(e) };
    console.error("Interview script detail failed:", e);
  }
  render();
}

async function loadPositions() {
  try {
    positionsData = await fetchInterviewPositions(positionFilterCompany, positionFilterCity, positionFilterStatus);
  } catch (e) {
    positionsData = { total: 0, items: [], error: String(e) };
    console.error("Interview positions failed:", e);
  }
  if (activeSection === "positions") render();
}

async function loadPositionDetail(id) {
  try {
    positionDetail = await fetchInterviewPositionDetail(id);
  } catch (e) {
    positionDetail = { title: "加载失败", description: String(e) };
    console.error("Interview position detail failed:", e);
  }
  render();
}

async function loadAll(force = false) {
  if (loading && !force) return;
  loading = true;
  if (force) render();

  try {
    const [status, jobs, numbers, logs, topics, scriptTypesRes, scriptsRes, positionsRes] = await Promise.all([
      fetchInterviewStatus().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewJobs().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewNumbers().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewLogs().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewTopics().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewScriptTypes().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewScripts().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewPositions().catch((e) => ({ error: errMsg(e) })),
    ]);
    if (!status.error) statusData = status;
    if (!jobs.error) jobsData = jobs;
    if (!numbers.error) numbersData = numbers;
    if (!logs.error) logsData = logs;
    if (!topics.error) topicsData = topics;
    if (!scriptTypesRes.error) scriptTypes = scriptTypesRes;
    if (!scriptsRes.error) scriptsData = scriptsRes;
    if (!positionsRes.error) positionsData = positionsRes;
  } catch (err) {
    console.error("Interview data load failed:", err);
  } finally {
    loading = false;
    loaded = true;
    render();
  }
}

// ── Public init ────────────────────────────────────────────────
export function initInterviewView(rootEl) {
  $root = rootEl;
  if (!loaded) {
    loadAll();
  } else {
    render();
  }
}
