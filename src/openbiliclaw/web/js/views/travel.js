/**
 * Travel view — 新疆旅行预算与机票实时价格监控
 *
 * Shows: budget overview cards, live flight prices with price-drop alerts,
 * plan comparison, and the full budget markdown document.
 */

import { fetchTravelFlights, fetchTravelOverview, fetchTravelDoc, fetchTravelItinerary } from "../api.js";

let $root = null;
let loaded = false;
let loading = false;
let flightsData = null;
let overviewData = null;
let docContent = null;
let itineraryData = null;
let activeSection = "itinerary"; // itinerary | flights | overview | doc

function esc(s) {
  const el = document.createElement("span");
  el.textContent = s == null ? "" : String(s);
  return el.innerHTML;
}

function fmtTime(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

function fmtPrice(n) {
  if (n == null || isNaN(n)) return "—";
  return `¥${Number(n).toLocaleString()}`;
}

// ── Section tabs ───────────────────────────────────────────────
function renderSectionTabs() {
  const tabs = [
    { id: "itinerary", label: "🗺️ 行程安排" },
    { id: "flights", label: "✈️ 实时机票" },
    { id: "overview", label: "📊 预算概览" },
    { id: "doc", label: "📄 完整文档" },
  ];
  return (
    `<div class="travel-section-tabs">` +
    tabs
      .map(
        (t) =>
          `<button class="travel-section-tab${activeSection === t.id ? " active" : ""}" data-section="${t.id}" type="button">${t.label}</button>`,
      )
      .join("") +
    `</div>`
  );
}

// ── Itinerary section ──────────────────────────────────────────
function renderItinerary() {
  if (!itineraryData || !itineraryData.trip) {
    return `<div class="travel-empty">加载行程数据中…</div>`;
  }

  const { trip, days, members, checklist } = itineraryData;
  let html = "";

  // Trip overview card
  html += `<div class="itinerary-overview">
    <div class="itinerary-title">${esc(trip.title)}</div>
    <div class="itinerary-meta">
      <span>📅 ${esc(trip.start_date)} ~ ${esc(trip.end_date)}</span>
      <span>👥 ${trip.people_count}人</span>
      <span>📍 ${esc(trip.destination || "")}</span>
    </div>
    <div class="itinerary-notes">${esc(trip.notes || "")}</div>
  </div>`;

  // Members
  html += `<div class="itinerary-section">
    <div class="itinerary-section-title">👥 同行人员（${members.length}人）</div>
    <div class="itinerary-members">`;
  for (const m of members) {
    const age = m.age ? `${m.age}岁` : "";
    html += `<div class="itinerary-member">
      <span class="member-name">${esc(m.name)}</span>
      <span class="member-relation">${esc(m.relation)}</span>
      ${age ? `<span class="member-age">${age}</span>` : ""}
      ${m.notes ? `<span class="member-notes">${esc(m.notes)}</span>` : ""}
    </div>`;
  }
  html += `</div></div>`;

  // Days timeline
  html += `<div class="itinerary-section">
    <div class="itinerary-section-title">🗓️ 每日行程（${days.length}天）</div>
    <div class="itinerary-timeline">`;
  for (const d of days) {
    html += `<div class="itinerary-day">
      <div class="day-header">
        <span class="day-num">Day ${d.day_number}</span>
        <span class="day-date">${esc(d.date || "")}</span>
        <span class="day-title">${esc(d.title)}</span>
      </div>
      <div class="day-body">
        <div class="day-desc">${esc(d.description || "")}</div>
        <div class="day-meta">
          ${d.transport ? `<span class="meta-item">🚗 ${esc(d.transport)}</span>` : ""}
          ${d.accommodation ? `<span class="meta-item">🏨 ${esc(d.accommodation)}</span>` : ""}
          ${d.meals ? `<span class="meta-item">🍽️ ${esc(d.meals)}</span>` : ""}
        </div>
        ${d.highlights ? `<div class="day-highlights">✨ ${esc(d.highlights)}</div>` : ""}
      </div>
    </div>`;
  }
  html += `</div></div>`;

  // Checklist
  if (checklist && checklist.length > 0) {
    const categories = [...new Set(checklist.map((c) => c.category))];
    html += `<div class="itinerary-section">
      <div class="itinerary-section-title">✅ 准备清单（${checklist.length}项）</div>`;
    for (const cat of categories) {
      const items = checklist.filter((c) => c.category === cat);
      const doneCount = items.filter((c) => c.done).length;
      html += `<div class="checklist-category">
        <div class="checklist-cat-title">${esc(cat)}（${doneCount}/${items.length}）</div>
        <div class="checklist-items">`;
      for (const item of items) {
        html += `<div class="checklist-item${item.done ? " done" : ""}">
          <span class="check-icon">${item.done ? "✅" : "⬜"}</span>
          <span class="check-text">${esc(item.item)}</span>
          <span class="check-owner">${esc(item.owner || "")}</span>
          ${item.notes ? `<span class="check-notes">${esc(item.notes)}</span>` : ""}
        </div>`;
      }
      html += `</div></div>`;
    }
    html += `</div>`;
  }

  return html;
}

// ── Flights section ────────────────────────────────────────────
function renderFlights() {
  if (!flightsData) {
    return `<div class="travel-empty">加载机票数据中…</div>`;
  }

  const { routes, alerts, fee_note, updated_at } = flightsData;
  let html = "";

  // Alert banner
  if (alerts && alerts.length > 0) {
    html += `<div class="travel-alert-banner">
      🔔 <strong>${alerts.length} 条航线降价提醒</strong>（较基线降≥¥200或≥10%）
      <div class="travel-alert-list">
        ${alerts
          .map(
            (a) =>
              `<span class="travel-alert-item">${esc(a.dep_city)}→${esc(a.arr_city)} ${esc(a.lowest_flight)} ${fmtPrice(a.lowest_price)} <em>↓${fmtPrice(a.vs_baseline.diff)}(${a.vs_baseline.pct}%)</em></span>`,
          )
          .join("")}
      </div>
    </div>`;
  }

  // Route cards
  html += `<div class="travel-routes">`;
  for (const r of routes) {
    const isAlert = r.vs_baseline?.alert;
    const priceClass = isAlert ? "travel-price-good" : r.lowest_price < r.baseline ? "travel-price-down" : "";

    html += `<div class="travel-route-card${isAlert ? " alert" : ""}">
      <div class="travel-route-head">
        <span class="travel-route-city">${esc(r.dep_city)} → ${esc(r.arr_city)}</span>
        <span class="travel-route-date">${esc(r.date?.slice(5) || "")}</span>
      </div>`;

    if (r.success && r.lowest_price) {
      html += `
        <div class="travel-route-price ${priceClass}">
          <span class="travel-price-num">${fmtPrice(r.lowest_price)}</span>
          <span class="travel-price-label">含税最低</span>
        </div>
        <div class="travel-route-flight">
          <strong>${esc(r.lowest_flight)}</strong>
          ${esc(r.lowest_airline)}<br>
          <span class="travel-time">${esc(r.lowest_departure?.slice(11, 16) || "")} → ${esc(r.lowest_arrival?.slice(11, 16) || "")}</span>
          ${r.baggage_kg ? `<span class="travel-baggage">🧳 ${r.baggage_kg}kg</span>` : ""}
          ${r.seat_count ? `<span class="travel-seats">余${r.seat_count}座</span>` : ""}
        </div>`;

      if (r.vs_baseline) {
        const diff = r.vs_baseline.diff;
        const sign = diff >= 0 ? "↓" : "↑";
        const cls = diff >= 0 ? "travel-baseline-good" : "travel-baseline-bad";
        html += `<div class="travel-baseline ${cls}">基线 ${fmtPrice(r.baseline)} ${sign}${fmtPrice(Math.abs(diff))} (${Math.abs(r.vs_baseline.pct)}%)</div>`;
      }

      // Top 3
      if (r.top3 && r.top3.length > 1) {
        html += `<div class="travel-top3">`;
        for (const f of r.top3.slice(1)) {
          html += `<div class="travel-top3-item">
            <span>${esc(f.flight)}</span>
            <span class="travel-time">${esc(f.departure?.slice(11, 16) || "")}</span>
            <span class="travel-top3-price">${fmtPrice(f.price_tax_inclusive)}</span>
          </div>`;
        }
        html += `</div>`;
      }
    } else {
      html += `<div class="travel-route-error">⚠️ 查询失败：${esc(r.error || "未知错误")}</div>`;
    }

    html += `</div>`;
  }
  html += `</div>`;

  html += `<div class="travel-footer-note">${esc(fee_note || "")} · 更新于 ${fmtTime(updated_at)}</div>`;
  return html;
}

// ── Overview section ───────────────────────────────────────────
function renderOverview() {
  if (!overviewData) {
    return `<div class="travel-empty">加载预算概览中…</div>`;
  }

  let html = "";

  // Totals table
  if (overviewData.totals && overviewData.totals.length > 0) {
    html += `<div class="travel-section-title">💰 方案C全款预算</div>`;
    html += `<table class="travel-table">
      <thead><tr><th>项目</th><th>金额</th><th>说明</th></tr></thead><tbody>`;
    for (const row of overviewData.totals) {
      const isTotal = row.item.includes("合计") || row.item.includes("总计");
      html += `<tr${isTotal ? ' class="travel-total-row"' : ""}>
        <td>${esc(row.item)}</td>
        <td class="travel-amount">${esc(row.amount)}</td>
        <td class="travel-note-cell">${esc(row.note)}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  // Plans comparison
  if (overviewData.plans && overviewData.plans.length > 0) {
    html += `<div class="travel-section-title">📋 三方案机票对比</div>`;
    html += `<table class="travel-table">
      <thead><tr><th>项目</th><th>方案A 9/30→10/7</th><th>方案B 10/1→10/8</th><th>方案C 10/2→10/9</th></tr></thead><tbody>`;
    for (const row of overviewData.plans) {
      html += `<tr>
        <td>${esc(row.item)}</td>
        <td>${esc(row.plan_a)}</td>
        <td>${esc(row.plan_b)}</td>
        <td class="travel-plan-c">${esc(row.plan_c)}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  html += `<div class="travel-footer-note">文档更新于 ${fmtTime(overviewData.doc_updated_at)}</div>`;
  return html;
}

// ── Doc section ────────────────────────────────────────────────
function renderDoc() {
  if (!docContent) {
    return `<div class="travel-empty">加载预算文档中…</div>`;
  }
  return `<div class="travel-doc-content">
    <div class="travel-doc-meta">${esc(docContent.title)} · 更新于 ${fmtTime(docContent.updated_at)}</div>
    <pre class="travel-doc-pre">${esc(docContent.content)}</pre>
  </div>`;
}

// ── Main render ────────────────────────────────────────────────
function render() {
  if (!$root) return;

  if (loading && !flightsData) {
    $root.innerHTML = `<div class="travel-view"><div style="padding:40px;text-align:center"><div class="spinner"></div><div style="margin-top:12px;color:#888">加载旅行数据…</div></div></div>`;
    return;
  }

  let content = "";
  if (activeSection === "itinerary") content = renderItinerary();
  else if (activeSection === "flights") content = renderFlights();
  else if (activeSection === "overview") content = renderOverview();
  else if (activeSection === "doc") content = renderDoc();

  $root.innerHTML = `<div class="travel-view">
    <div class="travel-head">
      <span class="travel-head-icon">✈️</span>
      <span class="travel-head-title">新疆旅行预算</span>
      <button class="travel-refresh-btn" id="travelRefreshBtn" type="button" title="刷新">🔄</button>
    </div>
    ${renderSectionTabs()}
    <div class="travel-content">${content}</div>
  </div>`;

  // Bind events
  $root.querySelectorAll(".travel-section-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeSection = btn.dataset.section;
      render();
    });
  });

  const refreshBtn = $root.querySelector("#travelRefreshBtn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadData(true));
  }
}

// ── Data loading ───────────────────────────────────────────────
async function loadData(force = false) {
  if (loading && !force) return;
  loading = true;
  if (force || !flightsData) render();

  try {
    const [flights, overview, doc, itinerary] = await Promise.all([
      fetchTravelFlights().catch((e) => ({ error: String(e) })),
      fetchTravelOverview().catch((e) => ({ error: String(e) })),
      fetchTravelDoc().catch((e) => ({ error: String(e) })),
      fetchTravelItinerary().catch((e) => ({ error: String(e) })),
    ]);

    if (!flights.error) flightsData = flights;
    if (!overview.error) overviewData = overview;
    if (!doc.error) docContent = doc;
    if (!itinerary.error) itineraryData = itinerary;
  } catch (err) {
    console.error("Travel data load failed:", err);
  } finally {
    loading = false;
    loaded = true;
    render();
  }
}

// ── Public init ────────────────────────────────────────────────
export function initTravelView(rootEl) {
  $root = rootEl;
  if (!loaded) {
    loadData();
  } else {
    render();
  }
}
