/**
 * 自进化模块（Self-Evolution）
 * 独立前端脚本，包含：状态概览、洞察报告、兴趣漂移、专题挖掘、
 * 知识卡片、知识图谱、主动推送等功能。
 *
 * 依赖：window.showMainPage、window.navigateTo（由 app.js 暴露）
 */
(function () {
  "use strict";

  const SELF_EVO_API = {
    status: "/api/self-evolution/status",
    insightReports: "/api/self-evolution/insight-reports",
    insightReport: (id) => `/api/self-evolution/insight-reports/${id}`,
    generateInsight: "/api/self-evolution/insight-reports/generate",
    drift: "/api/self-evolution/drift",
    topics: "/api/self-evolution/topics",
    knowledgeCards: "/api/self-evolution/knowledge-cards",
    generateCards: "/api/self-evolution/knowledge-cards/generate",
    dueCards: "/api/self-evolution/knowledge-cards/due",
    reviewCard: (id) => `/api/self-evolution/knowledge-cards/${id}/review`,
    knowledgeGraph: "/api/self-evolution/knowledge-graph",
    entityGraph: (id) => `/api/self-evolution/knowledge-graph/entity/${id}`,
    notifications: "/api/self-evolution/notifications",
    checkPush: "/api/self-evolution/notifications/check",
    readNotification: (id) => `/api/self-evolution/notifications/${id}/read`,
  };

  // 局部工具函数
  function $(selector) { return document.querySelector(selector); }
  function safeBind(selector, eventName, handler) {
    const el = $(selector);
    if (!el) { console.warn("[self-evolution] 缺少元素", selector); return; }
    el.addEventListener(eventName, handler);
  }

  let selfEvoState = {
    status: null,
    insightReport: null,
    driftData: null,
    topicsData: null,
    cardsData: null,
    graphData: null,
    notifications: null,
    reviewSession: null,
    currentCardIndex: 0,
  };

function openSelfEvolutionPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  window.showMainPage("selfEvolutionPage");
  loadSelfEvoStatus();
  // 纯展示：打开页面即加载各模块已有数据，不做重新计算
  loadLatestInsight();
  loadLatestDrift();
  loadLatestTopics();
  loadLatestCards();
  loadLatestGraph();
  // 通知计数已由 loadSelfEvoStatus() 展示；列表由 viewNotifications() 点击触发，
  // 此处不调用全局 chat.js 的 loadNotifications（独立脚本无该符号，避免 ReferenceError）
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function loadSelfEvoStatus() {
  try {
    const res = await fetch(SELF_EVO_API.status);
    const data = await res.json();
    selfEvoState.status = data;
    renderSelfEvoStatus(data);
  } catch (error) {
    console.error("Failed to load self-evolution status", error);
  }
}

function renderSelfEvoStatus(data) {
  const stats = data.stats || {};
  setText("statInsightReports", stats.insight_reports || 0);
  setText("statDriftReports", stats.drift_reports || 0);
  setText("statTopicMining", stats.topic_mining_reports || 0);
  setText("statKnowledgeCards", stats.knowledge_cards || 0);
  setText("statKnowledgeGraph", stats.knowledge_graph || 0);
  setText("statNotifications", stats.push_notifications || 0);
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setHTML(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function showLoading(id) {
  setHTML(id, '<p class="self-evo-loading">加载中</p>');
}

// ===== Insight Report =====
async function loadLatestInsight() {
  try {
    const res = await fetch(SELF_EVO_API.insightReports + "?limit=1");
    const data = await res.json();
    const reports = data.reports || [];
    if (reports.length === 0) {
      setHTML("insightReportContent", '<p class="self-evo-empty">暂无洞察报告，点击下方按钮生成</p>');
      return;
    }
    const detailRes = await fetch(SELF_EVO_API.insightReport(reports[0].report_id));
    const detail = await detailRes.json();
    if (detail && !detail.error) renderInsightReport(detail);
  } catch (error) {
    console.error("Failed to load insight report", error);
  }
}

async function generateInsightReport() {
  showLoading("insightReportContent");
  try {
    const res = await fetch(SELF_EVO_API.generateInsight + "?window_days=7&include_llm=true", { method: "POST" });
    const data = await res.json();
    selfEvoState.insightReport = data;
    renderInsightReport(data);
    loadSelfEvoStatus();
  } catch (error) {
    console.error("Failed to generate insight report", error);
    setHTML("insightReportContent", '<p class="self-evo-empty">生成失败，请检查后端日志</p>');
  }
}

function renderInsightReport(data) {
  const summary = data.natural_language_summary || "暂无摘要";
  const takeaways = data.key_takeaways || [];
  const topics = data.topic_stats || [];
  const drifts = data.interest_drifts || [];

  let html = `<div class="insight-summary">${escapeHtml(summary)}</div>`;

  if (takeaways.length > 0) {
    html += '<h4 style="margin:12px 0 8px;font-size:14px;">关键洞察</h4><ul class="insight-takeaways">';
    takeaways.forEach((t) => { html += `<li>${escapeHtml(t)}</li>`; });
    html += "</ul>";
  }

  if (topics.length > 0) {
    html += '<h4 style="margin:12px 0 8px;font-size:14px;">热门主题</h4><div class="insight-topic-list">';
    topics.slice(0, 10).forEach((t) => {
      html += `<span class="insight-topic-tag">${escapeHtml(t.topic)} (${t.count})</span>`;
    });
    html += "</div>";
  }

  const rising = drifts.filter((d) => d.direction === "rising" || d.direction === "new");
  if (rising.length > 0) {
    html += '<h4 style="margin:12px 0 8px;font-size:14px;">🔥 兴趣上升</h4><div class="insight-topic-list">';
    rising.slice(0, 5).forEach((d) => {
      html += `<span class="insight-topic-tag rising">${escapeHtml(d.topic)} (${d.previous_count}→${d.current_count})</span>`;
    });
    html += "</div>";
  }

  setHTML("insightReportContent", html);
}

// ===== Interest Drift =====
async function loadLatestDrift() {
  try {
    const res = await fetch(SELF_EVO_API.drift);
    const data = await res.json();
    if (data && data.report_id) {
      renderDrift(data);
    } else {
      setHTML("driftContent", '<p class="self-evo-empty">暂无漂移分析，点击下方按钮分析</p>');
    }
  } catch (error) {
    console.error("Failed to load drift", error);
  }
}

async function analyzeDrift() {
  showLoading("driftContent");
  try {
    const res = await fetch(SELF_EVO_API.drift + "?recompute=true");
    const data = await res.json();
    selfEvoState.driftData = data;
    renderDrift(data);
    loadSelfEvoStatus();
  } catch (error) {
    console.error("Failed to analyze drift", error);
    setHTML("driftContent", '<p class="self-evo-empty">分析失败</p>');
  }
}

function renderDrift(data) {
  const drifts = data.topic_drifts || [];
  const newInterests = data.new_interests || [];
  const fading = data.fading_interests || [];
  const alerts = data.alerts || [];

  let html = "";

  if (alerts.length > 0) {
    html += '<div style="margin-bottom:12px;">';
    alerts.forEach((a) => { html += `<p style="margin:4px 0;font-size:13px;">${escapeHtml(a)}</p>`; });
    html += "</div>";
  }

  if (newInterests.length > 0) {
    html += '<h4 style="margin:8px 0;font-size:13px;color:#52c41a;">🆕 新兴趣</h4><div class="insight-topic-list">';
    newInterests.forEach((t) => { html += `<span class="insight-topic-tag rising">${escapeHtml(t)}</span>`; });
    html += "</div>";
  }

  const rising = drifts.filter((d) => d.direction === "rising").slice(0, 5);
  if (rising.length > 0) {
    html += '<h4 style="margin:12px 0 8px;font-size:13px;">📈 上升中</h4>';
    rising.forEach((d) => {
      html += `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px;">
        <span>${escapeHtml(d.topic)}</span>
        <span style="color:#52c41a;">${d.previous_count}→${d.current_count}</span>
      </div>`;
    });
  }

  const declining = drifts.filter((d) => d.direction === "declining").slice(0, 5);
  if (declining.length > 0) {
    html += '<h4 style="margin:12px 0 8px;font-size:13px;">📉 下降中</h4>';
    declining.forEach((d) => {
      html += `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:13px;">
        <span>${escapeHtml(d.topic)}</span>
        <span style="color:#ea6668;">${d.previous_count}→${d.current_count}</span>
      </div>`;
    });
  }

  if (fading.length > 0) {
    html += '<h4 style="margin:12px 0 8px;font-size:13px;color:#ea6668;">💨 消退中</h4><div class="insight-topic-list">';
    fading.forEach((t) => { html += `<span class="insight-topic-tag declining">${escapeHtml(t)}</span>`; });
    html += "</div>";
  }

  setHTML("driftContent", html || '<p class="self-evo-empty">暂无显著兴趣变化</p>');
}

// ===== Topic Mining =====
async function loadLatestTopics() {
  try {
    const res = await fetch(SELF_EVO_API.topics);
    const data = await res.json();
    if (data && data.report_id) {
      renderTopics(data);
    } else {
      setHTML("topicsContent", '<p class="self-evo-empty">暂无候选专题，点击下方按钮挖掘</p>');
    }
  } catch (error) {
    console.error("Failed to load topics", error);
  }
}

async function mineTopics() {
  showLoading("topicsContent");
  try {
    const res = await fetch(SELF_EVO_API.topics + "?recompute=true");
    const data = await res.json();
    selfEvoState.topicsData = data;
    renderTopics(data);
    loadSelfEvoStatus();
  } catch (error) {
    console.error("Failed to mine topics", error);
    setHTML("topicsContent", '<p class="self-evo-empty">挖掘失败</p>');
  }
}

function renderTopics(data) {
  const candidates = data.candidates || [];
  if (candidates.length === 0) {
    setHTML("topicsContent", '<p class="self-evo-empty">暂无候选专题</p>');
    return;
  }

  let html = "";
  candidates.slice(0, 8).forEach((c) => {
    const scorePct = Math.round(c.score * 100);
    const recColor = c.recommendation === "create" ? "#52c41a" : c.recommendation === "watch" ? "#faad14" : "#999";
    html += `<div style="padding:8px 0;border-bottom:1px solid rgba(0,0,0,0.05);">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span style="font-weight:600;font-size:13px;">${escapeHtml(c.topic_name)}</span>
        <span style="font-size:11px;color:${recColor};font-weight:600;">${c.recommendation === "create" ? "建议创建" : c.recommendation === "watch" ? "值得关注" : "暂不推荐"}</span>
      </div>
      <div style="font-size:12px;color:#666;margin-top:4px;">
        ${c.content_count}条内容 · 增长${c.growth_ratio.toFixed(1)}x · 评分${scorePct}%
      </div>
    </div>`;
  });

  setHTML("topicsContent", html);
}

// ===== Knowledge Cards =====
async function loadLatestCards() {
  try {
    const res = await fetch(SELF_EVO_API.knowledgeCards + "?limit=50");
    const data = await res.json();
    renderCards(data);
  } catch (error) {
    console.error("Failed to load knowledge cards", error);
  }
}

async function generateCards() {
  showLoading("cardsContent");
  try {
    const res = await fetch(SELF_EVO_API.generateCards + "?limit=10&max_per_article=2", { method: "POST" });
    const data = await res.json();
    selfEvoState.cardsData = data;
    renderCards(data);
    loadSelfEvoStatus();
  } catch (error) {
    console.error("Failed to generate cards", error);
    setHTML("cardsContent", '<p class="self-evo-empty">生成失败</p>');
  }
}

function renderCards(data) {
  const cards = data.cards || [];
  if (cards.length === 0) {
    setHTML("cardsContent", '<p class="self-evo-empty">暂无知识卡片</p>');
    return;
  }

  let html = `<p style="font-size:12px;color:#666;margin-bottom:8px;">已生成 ${cards.length} 张卡片</p>`;
  cards.slice(0, 5).forEach((c) => {
    html += `<div style="padding:8px 0;border-bottom:1px solid rgba(0,0,0,0.05);">
      <div style="font-size:11px;color:#999;margin-bottom:2px;">[${c.card_type}]</div>
      <div style="font-weight:600;font-size:13px;">${escapeHtml(c.front).substring(0, 60)}...</div>
    </div>`;
  });

  setHTML("cardsContent", html);
}

async function startReview() {
  showLoading("cardsContent");
  try {
    const res = await fetch(SELF_EVO_API.dueCards + "?limit=10");
    const data = await res.json();
    selfEvoState.reviewSession = data;
    selfEvoState.currentCardIndex = 0;
    renderReviewCard();
  } catch (error) {
    console.error("Failed to start review", error);
    setHTML("cardsContent", '<p class="self-evo-empty">加载复习卡片失败</p>');
  }
}

function renderReviewCard() {
  const session = selfEvoState.reviewSession;
  if (!session || !session.cards || session.cards.length === 0) {
    setHTML("cardsContent", '<p class="self-evo-empty">没有待复习的卡片</p>');
    return;
  }

  const idx = selfEvoState.currentCardIndex;
  if (idx >= session.cards.length) {
    setHTML("cardsContent", `<p class="self-evo-empty">复习完成！共复习 ${session.cards.length} 张卡片</p>`);
    return;
  }

  const card = session.cards[idx];
  const html = `
    <div class="card-review">
      <div style="font-size:11px;color:#999;margin-bottom:8px;">第 ${idx + 1}/${session.cards.length} 张 · [${card.card_type}]</div>
      <div class="card-review-question">${escapeHtml(card.front)}</div>
      <div class="card-review-answer" id="cardAnswer">${escapeHtml(card.back)}</div>
      <div class="card-review-actions">
        <button class="pill-btn" id="showAnswerBtn" type="button">显示答案</button>
        <button class="pill-btn" id="nextCardBtn" type="button" hidden>下一张</button>
      </div>
      <div class="card-review-quality" id="qualityBtns" hidden>
        <button class="quality-btn bad" data-quality="1" type="button">忘记了</button>
        <button class="quality-btn medium" data-quality="3" type="button">有点印象</button>
        <button class="quality-btn good" data-quality="5" type="button">完全记住</button>
      </div>
    </div>
  `;
  setHTML("cardsContent", html);

  const showBtn = document.getElementById("showAnswerBtn");
  const nextBtn = document.getElementById("nextCardBtn");
  const answer = document.getElementById("cardAnswer");
  const qualityBtns = document.getElementById("qualityBtns");

  if (showBtn) {
    showBtn.addEventListener("click", () => {
      answer.classList.add("show");
      showBtn.hidden = true;
      nextBtn.hidden = false;
      qualityBtns.hidden = false;
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      selfEvoState.currentCardIndex++;
      renderReviewCard();
    });
  }
  if (qualityBtns) {
    qualityBtns.querySelectorAll(".quality-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const quality = parseInt(btn.dataset.quality);
        try {
          await fetch(SELF_EVO_API.reviewCard(card.card_id) + "?quality=" + quality, { method: "POST" });
        } catch (e) { console.error(e); }
        selfEvoState.currentCardIndex++;
        renderReviewCard();
      });
    });
  }
}

// ===== Knowledge Graph =====
async function loadLatestGraph() {
  try {
    const res = await fetch(SELF_EVO_API.knowledgeGraph);
    const data = await res.json();
    if (data && data.generated_at) {
      renderGraph(data);
    } else {
      setHTML("graphContent", '<p class="self-evo-empty">暂无知识图谱，点击下方按钮构建</p>');
    }
  } catch (error) {
    console.error("Failed to load knowledge graph", error);
  }
}

async function buildGraph() {
  showLoading("graphContent");
  try {
    const res = await fetch(SELF_EVO_API.knowledgeGraph + "?recompute=true");
    const data = await res.json();
    selfEvoState.graphData = data;
    renderGraph(data);
    loadSelfEvoStatus();
  } catch (error) {
    console.error("Failed to build graph", error);
    setHTML("graphContent", '<p class="self-evo-empty">构建失败</p>');
  }
}

function renderGraph(data) {
  const entities = data.entities || [];
  const relationships = data.relationships || [];
  const stats = data.stats || {};

  if (entities.length === 0) {
    setHTML("graphContent", '<p class="self-evo-empty">暂无实体数据</p>');
    return;
  }

  let html = `<p style="font-size:12px;color:#666;margin-bottom:8px;">${stats.entity_count || entities.length} 个实体 · ${stats.relationship_count || relationships.length} 条关系</p>`;
  html += '<div class="graph-entities">';

  const sorted = [...entities].sort((a, b) => b.mention_count - a.mention_count);
  sorted.slice(0, 30).forEach((e) => {
    html += `<span class="graph-entity" data-id="${e.entity_id}">${escapeHtml(e.name)}<span class="entity-count">${e.mention_count}</span></span>`;
  });
  html += "</div>";

  setHTML("graphContent", html);

  // Add click handlers for entities
  document.querySelectorAll(".graph-entity").forEach((el) => {
    el.addEventListener("click", () => {
      const entityId = el.dataset.id;
      showEntitySubgraph(entityId, el.textContent);
    });
  });
}

async function showEntitySubgraph(entityId, entityName) {
  const detail = document.getElementById("selfEvoDetail");
  const detailTitle = document.getElementById("selfEvoDetailTitle");
  const detailBody = document.getElementById("selfEvoDetailBody");

  detailTitle.textContent = `🔗 ${entityName} 的关联网络`;
  detailBody.innerHTML = '<p class="self-evo-loading">加载中</p>';
  detail.hidden = false;
  detail.scrollIntoView({ behavior: "smooth" });

  try {
    const res = await fetch(SELF_EVO_API.entityGraph(entityId) + "?depth=2&max_nodes=30");
    const data = await res.json();
    const entities = data.entities || [];
    const relationships = data.relationships || [];

    let html = `<p style="font-size:13px;margin-bottom:12px;">共 ${entities.length} 个关联实体，${relationships.length} 条关系</p>`;
    html += '<div class="graph-entities">';
    entities.forEach((e) => {
      html += `<span class="graph-entity">${escapeHtml(e.name)}<span class="entity-count">${e.mention_count || 0}</span></span>`;
    });
    html += "</div>";

    if (relationships.length > 0) {
      html += '<h4 style="margin:16px 0 8px;font-size:14px;">主要关系</h4>';
      relationships.slice(0, 10).forEach((r) => {
        const source = entities.find((e) => e.entity_id === r.source_id);
        const target = entities.find((e) => e.entity_id === r.target_id);
        if (source && target) {
          html += `<div style="padding:4px 0;font-size:13px;">${escapeHtml(source.name)} ↔ ${escapeHtml(target.name)} <span style="color:#999;font-size:11px;">(${r.evidence_count}次共现)</span></div>`;
        }
      });
    }

    detailBody.innerHTML = html;
  } catch (error) {
    detailBody.innerHTML = '<p class="self-evo-empty">加载失败</p>';
  }
}

// ===== Proactive Push =====
async function checkPush() {
  showLoading("pushContent");
  try {
    const res = await fetch(SELF_EVO_API.checkPush + "?dry_run=false", { method: "POST" });
    const data = await res.json();
    renderPushResult(data);
    loadSelfEvoStatus();
  } catch (error) {
    console.error("Failed to check push", error);
    setHTML("pushContent", '<p class="self-evo-empty">检查失败</p>');
  }
}

function renderPushResult(data) {
  const notifications = data.notifications || [];
  if (notifications.length === 0) {
    setHTML("pushContent", '<p class="self-evo-empty">暂无新推送，当前内容都已掌握</p>');
    return;
  }

  let html = `<p style="font-size:12px;color:#666;margin-bottom:8px;">发现 ${notifications.length} 条新推送</p>`;
  notifications.forEach((n) => {
    html += `<div class="notification-item ${n.priority}" data-id="${n.notification_id}">
      <div class="notification-title">${escapeHtml(n.title)}</div>
      <div class="notification-body">${escapeHtml(n.body)}</div>
      <div class="notification-time">${n.notification_type} · ${n.priority}优先级</div>
    </div>`;
  });

  setHTML("pushContent", html);
}

async function viewNotifications() {
  showLoading("pushContent");
  try {
    const res = await fetch(SELF_EVO_API.notifications + "?limit=20");
    const data = await res.json();
    selfEvoState.notifications = data;
    renderNotificationList(data);
  } catch (error) {
    console.error("Failed to load notifications", error);
    setHTML("pushContent", '<p class="self-evo-empty">加载失败</p>');
  }
}

function renderNotificationList(data) {
  const notifications = data.notifications || [];
  if (notifications.length === 0) {
    setHTML("pushContent", '<p class="self-evo-empty">暂无通知</p>');
    return;
  }

  let html = "";
  notifications.forEach((n) => {
    const isRead = n.read_at && n.read_at.length > 0;
    html += `<div class="notification-item ${n.priority} ${isRead ? "read" : ""}" data-id="${n.notification_id}" style="opacity:${isRead ? 0.6 : 1};">
      <div class="notification-title">${escapeHtml(n.title)}</div>
      <div class="notification-body">${escapeHtml(n.body)}</div>
      <div class="notification-time">${n.created_at ? n.created_at.substring(0, 16).replace("T", " ") : ""}</div>
    </div>`;
  });

  setHTML("pushContent", html);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

  // ── 初始化（由 app.js 或页面加载时调用）──
  function init() {
    safeBind("#selfEvolutionBtn", "click", () => window.navigateTo("/web/self-evolution"));
    safeBind("#selfEvoRefreshBtn", "click", loadSelfEvoStatus);
    safeBind("#generateInsightBtn", "click", generateInsightReport);
    safeBind("#analyzeDriftBtn", "click", analyzeDrift);
    safeBind("#mineTopicsBtn", "click", mineTopics);
    safeBind("#generateCardsBtn", "click", generateCards);
    safeBind("#reviewCardsBtn", "click", startReview);
    safeBind("#buildGraphBtn", "click", buildGraph);
    safeBind("#checkPushBtn", "click", checkPush);
    safeBind("#viewNotificationsBtn", "click", viewNotifications);
    safeBind("#selfEvoDetailClose", "click", () => {
      document.getElementById("selfEvoDetail").hidden = true;
    });
  }

  window.__initSelfEvolution = init;
  window.openSelfEvolutionPage = openSelfEvolutionPage;
})();
