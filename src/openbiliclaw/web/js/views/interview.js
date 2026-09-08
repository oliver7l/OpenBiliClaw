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
} from "../api.js";

let $root = null;
let loaded = false;
let loading = false;
let statusData = null;
let jobsData = null;
let searchResults = null;
let numbersData = null;
let logsData = null;
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
    { id: "search", label: "🔍 检索" },
    { id: "numbers", label: "🔢 数字" },
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
  else if (activeSection === "search") content = renderSearch();
  else if (activeSection === "numbers") content = renderNumbers();
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
      render();
    });
  });

  const refreshBtn = $root.querySelector("#interviewRefreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadAll(true));
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

async function loadAll(force = false) {
  if (loading && !force) return;
  loading = true;
  if (force) render();

  try {
    const [status, jobs, numbers, logs] = await Promise.all([
      fetchInterviewStatus().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewJobs().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewNumbers().catch((e) => ({ error: errMsg(e) })),
      fetchInterviewLogs().catch((e) => ({ error: errMsg(e) })),
    ]);
    if (!status.error) statusData = status;
    if (!jobs.error) jobsData = jobs;
    if (!numbers.error) numbersData = numbers;
    if (!logs.error) logsData = logs;
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
