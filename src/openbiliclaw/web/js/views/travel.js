/**
 * Travel view — 新疆旅行预算与机票实时价格监控
 *
 * Shows: budget overview cards, live flight prices with price-drop alerts,
 * plan comparison, and the full budget markdown document.
 */

import { fetchTravelFlights, fetchTravelOverview, fetchTravelDoc, fetchTravelItinerary, fetchTravelExpenses, fetchTravelFlightsDetail, fetchTravelHotels } from "../api.js";

let $root = null;
let loaded = false;
let loading = false;
let flightsData = null;
let overviewData = null;
let docContent = null;
let itineraryData = null;
let expensesData = null;
let flightsDetailData = null;
let hotelsData = null;
let activeSection = "itinerary"; // itinerary | flights | flights-detail | hotels | expenses | overview | doc

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
    { id: "flights-detail", label: "✈️ 航班信息" },
    { id: "hotels", label: "🏨 住宿信息" },
    { id: "expenses", label: "💰 费用明细" },
    { id: "flights", label: "📈 实时机票" },
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

// ── Expenses section ───────────────────────────────────────────
function renderExpenses() {
  if (!expensesData || !expensesData.trip) {
    return `<div class="travel-empty">加载费用数据中…</div>`;
  }

  const { trip, expenses, summary } = expensesData;
  let html = "";

  // Summary cards
  html += `<div class="expenses-summary-cards">
    <div class="expense-card expense-total">
      <div class="expense-card-label">总费用</div>
      <div class="expense-card-value">${fmtPrice(summary.total)}</div>
      <div class="expense-card-sub">${summary.people_count}人 · 8天</div>
    </div>
    <div class="expense-card expense-perperson">
      <div class="expense-card-label">人均费用</div>
      <div class="expense-card-value">${fmtPrice(summary.per_person)}</div>
      <div class="expense-card-sub">不含餐费及个人消费</div>
    </div>
  </div>`;

  // Category breakdown with progress bars
  if (summary.by_category && summary.by_category.length > 0) {
    html += `<div class="expenses-section">
      <div class="expenses-section-title">📊 费用构成</div>
      <div class="expenses-categories">`;
    for (const cat of summary.by_category) {
      const pct = summary.total ? Math.round((cat.total / summary.total) * 100) : 0;
      const colorClass = cat.category === "团费" ? "cat-group" : cat.category === "机票" ? "cat-flight" : "cat-hotel";
      html += `<div class="expense-category">
        <div class="expense-cat-head">
          <span class="expense-cat-name">${esc(cat.category)}</span>
          <span class="expense-cat-amount">${fmtPrice(cat.total)} <em>(${pct}%)</em></span>
        </div>
        <div class="expense-cat-bar"><div class="expense-cat-fill ${colorClass}" style="width:${pct}%"></div></div>
      </div>`;
    }
    html += `</div></div>`;
  }

  // Detailed expense table grouped by category
  if (expenses && expenses.length > 0) {
    const categories = [...new Set(expenses.map((e) => e.category))];
    html += `<div class="expenses-section">
      <div class="expenses-section-title">📋 费用明细（${expenses.length}项）</div>`;

    for (const cat of categories) {
      const items = expenses.filter((e) => e.category === cat);
      const catTotal = items.reduce((s, e) => s + e.amount, 0);
      html += `<div class="expense-group">
        <div class="expense-group-header">
          <span>${esc(cat)}</span>
          <span class="expense-group-total">${fmtPrice(catTotal)}</span>
        </div>
        <table class="travel-table expense-detail-table">
          <thead><tr><th>项目</th><th>明细</th><th style="text-align:right">金额</th></tr></thead>
          <tbody>`;
      for (const item of items) {
        html += `<tr>
          <td class="expense-item-name">${esc(item.item)}</td>
          <td class="expense-item-detail">${esc(item.detail || "")}</td>
          <td class="travel-amount">${fmtPrice(item.amount)}</td>
        </tr>`;
      }
      html += `</tbody></table></div>`;
    }
    html += `</div>`;
  }

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

// ── Flights Detail section ─────────────────────────────────────
function renderFlightsDetail() {
  if (!flightsDetailData) {
    return `<div class="travel-empty">加载航班信息中…</div>`;
  }

  const { departures, returns, summary } = flightsDetailData;
  let html = "";

  // Summary cards
  html += `<div class="expenses-summary">
    <div class="expense-summary-card">
      <div class="expense-summary-label">去程航班</div>
      <div class="expense-summary-value">${summary.departure_count} 班</div>
    </div>
    <div class="expense-summary-card">
      <div class="expense-summary-label">返程航班</div>
      <div class="expense-summary-value">${summary.return_count} 班</div>
    </div>
    <div class="expense-summary-card">
      <div class="expense-summary-label">机票总费用</div>
      <div class="expense-summary-value">${fmtPrice(summary.total_price)}</div>
    </div>
  </div>`;

  // Departures
  html += `<div class="travel-section-title">🛫 去程航班（10月2日）</div>`;
  html += `<table class="travel-table">
    <thead><tr><th>航班号</th><th>航线</th><th>起飞</th><th>到达</th><th>时长</th><th>乘机人</th><th>费用</th></tr></thead><tbody>`;
  for (const f of departures) {
    html += `<tr>
      <td><strong>${esc(f.flight_no)}</strong><br><span style="color:#888;font-size:11px">${esc(f.airline)}</span></td>
      <td>${esc(f.departure_city)} → ${esc(f.arrival_city)}</td>
      <td>${esc(f.departure_time.split(" ")[1])}<br><span style="color:#888;font-size:11px">${esc(f.departure_airport)}</span></td>
      <td>${esc(f.arrival_time.split(" ")[1])}<br><span style="color:#888;font-size:11px">${esc(f.arrival_airport)}</span></td>
      <td>${esc(f.duration)}</td>
      <td>${esc(f.passengers)}<br><span style="color:#888;font-size:11px">${f.passenger_count}人</span></td>
      <td class="travel-amount">${f.price > 0 ? fmtPrice(f.price) : "含在往返"}</td>
    </tr>`;
  }
  html += `</tbody></table>`;

  // Returns
  html += `<div class="travel-section-title">🛬 返程航班</div>`;
  html += `<table class="travel-table">
    <thead><tr><th>日期</th><th>航班号</th><th>航线</th><th>起飞</th><th>到达</th><th>乘机人</th><th>费用</th></tr></thead><tbody>`;
  for (const f of returns) {
    const date = f.departure_time.split(" ")[0];
    const depTime = f.departure_time.split(" ")[1];
    const arrTime = f.arrival_time.split(" ")[1];
    const isNextDay = f.arrival_time.includes("2026-10-08") || f.arrival_time.includes("2026-10-10");
    html += `<tr>
      <td>${esc(date)}</td>
      <td><strong>${esc(f.flight_no)}</strong><br><span style="color:#888;font-size:11px">${esc(f.airline)}</span></td>
      <td>${esc(f.departure_city)} → ${esc(f.arrival_city)}</td>
      <td>${esc(depTime)}</td>
      <td>${esc(arrTime)}${isNextDay ? '<br><span style="color:#e74c3c;font-size:11px">次日抵达</span>' : ""}</td>
      <td>${esc(f.passengers)}<br><span style="color:#888;font-size:11px">${f.passenger_count}人</span></td>
      <td class="travel-amount">${f.price > 0 ? fmtPrice(f.price) : "含在往返"}</td>
    </tr>`;
  }
  html += `</tbody></table>`;

  // Notes
  html += `<div style="margin-top:12px;padding:10px;background:#fff8e1;border-radius:6px;font-size:12px;color:#795548">
    <strong>⚠️ 重要提醒：</strong><br>
    • 10月2日分两批抵达：早上7人（深圳/重庆出发），晚上4人（太原出发）<br>
    • 10月7日晚3人先返重庆（爸爸+三嬢+三姑爷）<br>
    • 10月9日分两批返程：下午4人飞太原，晚上4人飞深圳（次日凌晨抵达）<br>
    • 姐姐家4人为往返套票（CZ5196去+HU7446返），总价¥6,600
  </div>`;

  return html;
}

// ── Hotels section ─────────────────────────────────────────────
function renderHotels() {
  if (!hotelsData) {
    return `<div class="travel-empty">加载住宿信息中…</div>`;
  }

  const { hotels, summary } = hotelsData;
  let html = "";

  // Summary cards
  html += `<div class="expenses-summary">
    <div class="expense-summary-card">
      <div class="expense-summary-label">总晚数</div>
      <div class="expense-summary-value">${summary.total_nights} 晚</div>
    </div>
    <div class="expense-summary-card">
      <div class="expense-summary-label">团费包含</div>
      <div class="expense-summary-value">${summary.included_in_tour} 晚</div>
    </div>
    <div class="expense-summary-card">
      <div class="expense-summary-label">自费住宿</div>
      <div class="expense-summary-value">${summary.self_paid} 晚</div>
    </div>
    <div class="expense-summary-card">
      <div class="expense-summary-label">自费总额</div>
      <div class="expense-summary-value" style="color:#e74c3c">${fmtPrice(summary.self_paid_total)}</div>
    </div>
  </div>`;

  // Hotels list
  html += `<div class="travel-section-title">🏨 每日住宿详情</div>`;
  for (const h of hotels) {
    const isSelfPaid = h.included_in_tour === 0;
    const tagColor = isSelfPaid ? "#e74c3c" : "#27ae60";
    const tagText = isSelfPaid ? "自费" : "团费包含";
    html += `<div class="itinerary-day-card" style="margin-bottom:12px">
      <div class="itinerary-day-header">
        <span class="itinerary-day-num">D${h.day_number}</span>
        <span class="itinerary-day-title">${esc(h.date)} · ${esc(h.city)}</span>
        <span style="background:${tagColor};color:white;padding:2px 8px;border-radius:10px;font-size:11px;margin-left:auto">${tagText}</span>
      </div>
      <div style="padding:10px 14px">
        <div style="font-size:14px;font-weight:600;margin-bottom:6px">${esc(h.hotel_name)} <span style="color:#f39c12;font-size:12px">${esc(h.star_rating)}</span></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;color:#555">
          <div>📍 ${esc(h.address)}</div>
          <div>🛏️ ${esc(h.room_type)} × ${h.room_count}间</div>
          <div>🕐 入住 ${esc(h.check_in.split(" ")[1])} / 退房 ${esc(h.check_out.split(" ")[1])}</div>
          <div>👥 ${h.guest_count}人 ${h.breakfast ? "· 含早" : "· 不含早"}</div>
        </div>
        ${isSelfPaid ? `<div style="margin-top:6px;color:#e74c3c;font-size:13px;font-weight:600">费用：${fmtPrice(h.price)}</div>` : ""}
        ${h.notes ? `<div style="margin-top:6px;padding:6px 8px;background:#f8f9fa;border-radius:4px;font-size:11px;color:#666">💡 ${esc(h.notes)}</div>` : ""}
      </div>
    </div>`;
  }

  // Room allocation
  html += `<div class="travel-section-title">🛏️ 房间分配建议（11人/6间）</div>`;
  html += `<table class="travel-table">
    <thead><tr><th>房间</th><th>入住人员</th><th>备注</th></tr></thead><tbody>
    <tr><td>房间1</td><td>童力 + 刘艳艳</td><td>夫妻，全程</td></tr>
    <tr><td>房间2</td><td>周贤英（妈妈） + 童言（乐仔）</td><td>母子，全程</td></tr>
    <tr><td>房间3</td><td>刘霞（姐姐） + 姐夫</td><td>夫妻，全程</td></tr>
    <tr><td>房间4</td><td>岳母 + 田佳禾（外甥）</td><td>祖孙，全程</td></tr>
    <tr><td>房间5</td><td>童先海（爸爸）</td><td>10-02至10-07，10-07离团</td></tr>
    <tr><td>房间6</td><td>童淑琴（三嬢） + 卢昌友（三姑爷）</td><td>夫妻，10-02至10-07离团</td></tr>
    </tbody></table>`;

  // Notes
  html += `<div style="margin-top:12px;padding:10px;background:#fff8e1;border-radius:6px;font-size:12px;color:#795548">
    <strong>⚠️ 住宿注意：</strong><br>
    • 禾木木屋（D3）夜间0-5℃，无空调只有电热毯，注意保暖<br>
    • 禾木木屋不含早餐，需在村内小店解决<br>
    • 10-07晚3人离团后，赛湖和乌鲁木齐只住8人<br>
    • 建议自带洗漱用品，尤其是禾木木屋
  </div>`;

  return html;
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
  else if (activeSection === "flights-detail") content = renderFlightsDetail();
  else if (activeSection === "hotels") content = renderHotels();
  else if (activeSection === "flights") content = renderFlights();
  else if (activeSection === "expenses") content = renderExpenses();
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
    const [flights, overview, doc, itinerary, expenses, flightsDetail, hotels] = await Promise.all([
      fetchTravelFlights().catch((e) => ({ error: String(e) })),
      fetchTravelOverview().catch((e) => ({ error: String(e) })),
      fetchTravelDoc().catch((e) => ({ error: String(e) })),
      fetchTravelItinerary().catch((e) => ({ error: String(e) })),
      fetchTravelExpenses().catch((e) => ({ error: String(e) })),
      fetchTravelFlightsDetail().catch((e) => ({ error: String(e) })),
      fetchTravelHotels().catch((e) => ({ error: String(e) })),
    ]);

    if (!flights.error) flightsData = flights;
    if (!overview.error) overviewData = overview;
    if (!doc.error) docContent = doc;
    if (!itinerary.error) itineraryData = itinerary;
    if (!expenses.error) expensesData = expenses;
    if (!flightsDetail.error) flightsDetailData = flightsDetail;
    if (!hotels.error) hotelsData = hotels;
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
