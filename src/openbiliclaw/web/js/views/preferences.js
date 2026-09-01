/**
 * Preferences view — display user preference/behavior events
 * (likes, favorites, follows, views) aggregated by platform.
 */

import { fetchUserEvents } from "../api.js";
import { getSourceLabel } from "../view-models.js";

let $root = null;
let loaded = false;
let loading = false;
let data = null;
let selectedPlatform = null;
let selectedEventType = null; // null = all

const PLATFORM_ICONS = {
  bilibili: "📺",
  xiaohongshu: "📕",
  douyin: "🎵",
  youtube: "▶️",
  twitter: "🐦",
  zhihu: "💡",
  web: "🌐",
};

const EVENT_LABELS = {
  like: "点赞",
  favorite: "收藏",
  follow: "关注",
  view: "浏览",
};

const EVENT_TYPES = ["like", "favorite", "follow", "view"];

function esc(s) {
  const el = document.createElement("span");
  el.textContent = s == null ? "" : String(s);
  return el.innerHTML;
}

function platformIcon(source) {
  return PLATFORM_ICONS[source] || "📦";
}

function render() {
  if (loading) {
    $root.innerHTML = `<div class="preferences-view"><div style="padding:40px"><div class="spinner"></div></div></div>`;
    return;
  }
  if (!data) {
    $root.innerHTML = `<div class="preferences-view"><div class="preferences-empty">暂无偏好数据</div></div>`;
    return;
  }

  const { platforms, totals, recent_events } = data;
  const sortedPlatforms = Object.entries(platforms).sort((a, b) => b[1].total - a[1].total);

  let html = `<div class="preferences-view">`;

  // Header
  const headTitle = selectedPlatform
    ? `${platformIcon(selectedPlatform)} ${esc(getSourceLabel(selectedPlatform))}`
    : "🧠 偏好数据";
  const headCount = selectedPlatform
    ? platforms[selectedPlatform]?.total || 0
    : totals.total || 0;

  html += `
    <div class="preferences-head">
      <span class="preferences-head-icon">${selectedPlatform ? platformIcon(selectedPlatform) : "🧠"}</span>
      <span class="preferences-head-title">${selectedPlatform ? esc(getSourceLabel(selectedPlatform)) : "偏好数据"}</span>
      <span class="preferences-head-count">${headCount}</span>
    </div>`;

  // Back button
  if (selectedPlatform) {
    html += `<button class="pref-back-btn" id="prefBackBtn" type="button">← 全部平台</button>`;
  }

  // Platform summary cards (horizontal scrollable row)
  if (!selectedPlatform) {
    html += `<div class="pref-platform-row">`;
    for (const [source, counts] of sortedPlatforms) {
      const label = getSourceLabel(source);
      const icon = platformIcon(source);
      html += `
        <div class="pref-platform-card" data-source="${esc(source)}">
          <div class="pref-platform-head">
            <span class="pref-platform-icon">${icon}</span>
            <span class="pref-platform-name">${esc(label)}</span>
            <span class="pref-platform-total">${counts.total}</span>
          </div>
        </div>`;
    }
    html += `</div>`;

    // Show platform breakdown
    html += `<div class="pref-section-title">各平台详情</div>`;
    for (const [source, counts] of sortedPlatforms) {
      const label = getSourceLabel(source);
      const icon = platformIcon(source);
      const statsHtml = EVENT_TYPES
        .filter((t) => counts[t] > 0)
        .map((t) => `<span class="pref-stat"><span class="pref-stat-type">${EVENT_LABELS[t]}</span><span class="pref-stat-count">${counts[t]}</span></span>`)
        .join("");
      html += `
        <div class="pref-platform-detail" data-source="${esc(source)}">
          <div class="pref-platform-detail-head">
            <span>${icon} ${esc(label)}</span>
            <span class="pref-platform-total">${counts.total}</span>
          </div>
          ${statsHtml ? `<div class="pref-platform-stats">${statsHtml}</div>` : ""}
        </div>`;
    }
  } else {
    // Platform selected — show event type filter chips
    const counts = platforms[selectedPlatform];
    html += `<div class="pref-type-filter">`;
    html += `<button class="pref-type-chip${selectedEventType === null ? " is-active" : ""}" data-type="">全部 ${counts.total || 0}</button>`;
    for (const t of EVENT_TYPES) {
      if (counts[t] > 0) {
        html += `<button class="pref-type-chip${selectedEventType === t ? " is-active" : ""}" data-type="${t}">${EVENT_LABELS[t]} ${counts[t]}</button>`;
      }
    }
    html += `</div>`;

    // Filtered events list
    const filteredEvents = recent_events.filter((e) => {
      if (e.source_platform !== selectedPlatform) return false;
      if (selectedEventType && e.event_type !== selectedEventType) return false;
      return true;
    });

    if (filteredEvents.length === 0) {
      html += `<div class="preferences-empty" style="padding:30px 0">暂无匹配事件</div>`;
    } else {
      html += `<div class="pref-event-list">`;
      for (const evt of filteredEvents) {
        const eventLabel = EVENT_LABELS[evt.event_type] || evt.event_type;
        const authorHtml = evt.author ? `<span class="pref-event-author">${esc(evt.author)}</span>` : "";
        html += `
          <div class="pref-event-item" data-url="${esc(evt.url)}">
            <div class="pref-event-body">
              <div class="pref-event-title">${esc(evt.title || "(无标题)")}</div>
              <div class="pref-event-meta">
                <span class="pref-event-type-badge" data-type="${evt.event_type}">${esc(eventLabel)}</span>
                ${authorHtml}
                <span class="pref-event-time">${esc(evt.originated_at || evt.created_at || "")}</span>
              </div>
            </div>
          </div>`;
      }
      html += `</div>`;
    }
  }

  html += `</div>`;
  $root.innerHTML = html;

  // Bind platform card clicks (overview)
  for (const card of $root.querySelectorAll(".pref-platform-card, .pref-platform-detail")) {
    const source = card.dataset.source;
    if (source) {
      card.style.cursor = "pointer";
      card.addEventListener("click", () => {
        selectedPlatform = source;
        selectedEventType = null;
        render();
      });
    }
  }

  // Bind back button
  const backBtn = document.getElementById("prefBackBtn");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      selectedPlatform = null;
      selectedEventType = null;
      render();
    });
  }

  // Bind event type filter chips
  for (const chip of $root.querySelectorAll(".pref-type-chip")) {
    const type = chip.dataset.type || null;
    chip.addEventListener("click", () => {
      selectedEventType = type;
      render();
    });
  }

  // Bind event item clicks
  for (const item of $root.querySelectorAll(".pref-event-item")) {
    const url = item.dataset.url;
    if (url) {
      item.style.cursor = "pointer";
      item.addEventListener("click", () => window.open(url, "_blank"));
    }
  }
}

async function load() {
  loading = true;
  render();
  try {
    const result = await fetchUserEvents(2000);
    data = result || null;
    loaded = true;
  } catch {
    data = null;
  }
  loading = false;
  render();
}

export function initPreferencesView(rootEl) {
  $root = rootEl;
  selectedPlatform = null;
  selectedEventType = null;
  if (!loaded) {
    load();
  } else {
    render();
  }
}
