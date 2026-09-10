/* 面试题阅读追踪页面逻辑 */
(function () {
  "use strict";

  const API_BASE = "/api/interview";
  let currentTab = "today";

  // ── 工具函数 ──────────────────────────────────────────────

  function requestJson(url, options) {
    return fetch(url, Object.assign({ headers: { "Content-Type": "application/json" } }, options || {}))
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      });
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function difficultyStars(n) {
    return "⭐".repeat(Math.max(0, Math.min(5, n || 0)));
  }

  const MASTERY_LABELS = {
    not_started: "未开始",
    reading: "阅读中",
    understood: "已理解",
    mastered: "已掌握",
    need_review: "需复习",
  };

  const CATEGORY_LABELS = {
    recommendation: "推荐算法",
    llm_engineering: "大模型工程",
    agent: "Agent",
    machine_learning: "机器学习",
    system_design: "系统设计",
    coding: "手撕代码",
    other: "其他",
  };

  // ── 加载统计 ──────────────────────────────────────────────

  function loadStats() {
    return requestJson(API_BASE + "/stats")
      .then((data) => {
        const set = (id, val) => {
          const el = document.getElementById(id);
          if (el) el.textContent = val;
        };
        set("statTotal", data.total);
        set("statMastered", data.mastered);
        set("statUnderstood", data.understood);
        set("statReview", data.need_review);
        set("statRate", (data.mastery_rate || 0) + "%");
      })
      .catch((err) => console.error("加载面试统计失败:", err));
  }

  // ── 加载面试安排 ──────────────────────────────────────────

  function loadSchedule() {
    const container = document.getElementById("interviewSchedule");
    if (!container) return Promise.resolve();
    container.innerHTML = '<div class="interview-loading">正在加载面试安排…</div>';
    return requestJson(API_BASE + "/schedule")
      .then((data) => {
        const badge = document.getElementById("upcomingCount");
        if (badge) badge.textContent = data.upcoming_count + " 场待面";
        const all = (data.upcoming || []).concat(data.history || []);
        if (all.length === 0) {
          container.innerHTML = '<div class="interview-empty">暂无面试安排</div>';
          return;
        }
        container.innerHTML = all.map(renderScheduleCard).join("");
      })
      .catch((err) => {
        console.error("加载面试安排失败:", err);
        container.innerHTML = '<div class="interview-empty">加载失败</div>';
      });
  }

  function renderScheduleCard(job) {
    const interviewAt = job.interview_at || "";
    const datePart = interviewAt.split(" ")[0] || "";
    const timePart = interviewAt.split(" ")[1] || "";
    const day = datePart ? datePart.split("-")[2] : "?";
    const month = datePart ? datePart.split("-")[1] + "月" : "";
    const statusClass = job.is_upcoming ? (job.status === "进行中" ? "status-progress" : "status-upcoming") : "status-done";
    const statusText = job.status || "未知";
    return `
      <div class="interview-schedule-card ${job.is_upcoming ? "" : "status-done"}">
        <div class="interview-schedule-date">
          <div class="day">${escapeHtml(day)}</div>
          <div class="month">${escapeHtml(month)} ${escapeHtml(timePart)}</div>
        </div>
        <div class="interview-schedule-info">
          <div class="interview-schedule-company">${escapeHtml(job.company)}</div>
          <div class="interview-schedule-role">${escapeHtml(job.role)}${job.direction ? " · " + escapeHtml(job.direction) : ""}</div>
          ${job.note ? `<div class="interview-schedule-note">${escapeHtml(job.note)}</div>` : ""}
        </div>
        <div class="interview-schedule-status ${statusClass}">${escapeHtml(statusText)}</div>
      </div>
    `;
  }

  // ── 加载岗位弹药库 ────────────────────────────────────────

  function loadAmmoLibrary() {
    const container = document.getElementById("interviewAmmo");
    if (!container) return Promise.resolve();
    container.innerHTML = '<div class="interview-loading">正在加载岗位资料…</div>';
    return requestJson(API_BASE + "/ammo")
      .then((data) => {
        const badge = document.getElementById("ammoCount");
        if (badge) badge.textContent = data.total_companies + " 家公司 · " + data.total_files + " 份资料";
        if (!data.companies || data.companies.length === 0) {
          container.innerHTML = '<div class="interview-empty">暂无岗位资料</div>';
          return;
        }
        container.innerHTML = data.companies.map(renderAmmoCard).join("");
      })
      .catch((err) => {
        console.error("加载岗位弹药库失败:", err);
        container.innerHTML = '<div class="interview-empty">加载失败</div>';
      });
  }

  function renderAmmoCard(company) {
    const catCounts = Object.keys(company.categories || {}).map((cat) => {
      const files = company.categories[cat] || [];
      return `${cat}:${files.length}`;
    }).join(" · ");
    const keyFiles = (company.key_files || []).slice(0, 3).map((f) => `
      <div class="interview-ammo-file">
        <span class="interview-ammo-file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
        <span class="interview-ammo-file-date">${escapeHtml(f.mtime)}</span>
      </div>
    `).join("");
    return `
      <div class="interview-ammo-card" data-company="${escapeHtml(company.company)}">
        <div class="interview-ammo-company">${escapeHtml(company.company)}</div>
        <div class="interview-ammo-stats">
          <div class="interview-ammo-stat">📄 <span class="interview-ammo-stat-value">${company.total_files}</span> 份</div>
          <div class="interview-ammo-stat">💾 <span class="interview-ammo-stat-value">${company.total_size_mb}</span> MB</div>
          ${company.has_index ? '<div class="interview-ammo-stat">📋 有索引</div>' : ""}
        </div>
        <div class="interview-ammo-files">
          <div style="color:var(--text-primary);margin-bottom:4px;">${escapeHtml(catCounts)}</div>
          ${keyFiles}
        </div>
      </div>
    `;
  }

  // ── 渲染题目卡片 ──────────────────────────────────────────

  function renderQuestionCard(q) {
    const mastery = q.mastery || "not_started";
    const categoryLabel = CATEGORY_LABELS[q.category] || q.category;
    return `
      <div class="interview-question-card" data-id="${q.id}">
        <div class="interview-question-header">
          <div class="interview-question-title">${escapeHtml(q.title)}</div>
          <div class="interview-question-meta">
            <span class="interview-difficulty">${difficultyStars(q.difficulty)}</span>
            <span class="interview-category">${escapeHtml(categoryLabel)}</span>
            <span class="interview-mastery-badge interview-mastery-${mastery}">${MASTERY_LABELS[mastery] || mastery}</span>
          </div>
        </div>
        ${q.tags ? `<div class="interview-question-tags">🏷️ ${escapeHtml(q.tags)}</div>` : ""}
        ${q.source ? `<div class="interview-question-tags">📌 ${escapeHtml(q.source)}</div>` : ""}
        <div class="interview-question-actions">
          <button class="interview-action-btn primary" data-action="read" data-id="${q.id}">标记已读</button>
          <button class="interview-action-btn success" data-action="understood" data-id="${q.id}">已理解</button>
          <button class="interview-action-btn" data-action="mastered" data-id="${q.id}">已掌握</button>
          <button class="interview-action-btn warning" data-action="review" data-id="${q.id}">需复习</button>
        </div>
      </div>
    `;
  }

  function renderQuestions(questions, emptyMsg) {
    const container = document.getElementById("interviewContent");
    if (!container) return;
    if (!questions || questions.length === 0) {
      container.innerHTML = `<div class="interview-empty">${escapeHtml(emptyMsg || "暂无题目")}</div>`;
      return;
    }
    container.innerHTML = questions.map(renderQuestionCard).join("");
  }

  // ── 加载各 tab 数据 ───────────────────────────────────────

  function loadToday() {
    const container = document.getElementById("interviewContent");
    if (container) container.innerHTML = '<div class="interview-loading">正在加载今日待读…</div>';
    return requestJson(API_BASE + "/today")
      .then((data) => {
        renderQuestions(data.questions || [], "今日待读已完成！🎉");
      })
      .catch((err) => {
        console.error("加载今日待读失败:", err);
        const container = document.getElementById("interviewContent");
        if (container) container.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function loadQueue() {
    const container = document.getElementById("interviewContent");
    if (container) container.innerHTML = '<div class="interview-loading">正在加载待看队列…</div>';
    return requestJson(API_BASE + "/queue")
      .then((data) => {
        renderQuestions(data.queue || [], "待看队列为空");
      })
      .catch((err) => {
        console.error("加载待看队列失败:", err);
        const container = document.getElementById("interviewContent");
        if (container) container.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function loadAll() {
    const container = document.getElementById("interviewContent");
    if (container) container.innerHTML = '<div class="interview-loading">正在加载全部题目…</div>';
    return requestJson(API_BASE + "/questions?limit=200")
      .then((data) => {
        renderQuestions(data.questions || [], "题库为空");
      })
      .catch((err) => {
        console.error("加载全部题目失败:", err);
        const container = document.getElementById("interviewContent");
        if (container) container.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function loadCurrentTab() {
    if (currentTab === "today") return loadToday();
    if (currentTab === "queue") return loadQueue();
    if (currentTab === "all") return loadAll();
    return Promise.resolve();
  }

  // ── 标记操作 ──────────────────────────────────────────────

  function markQuestion(id, action) {
    const masteryMap = {
      read: "reading",
      understood: "understood",
      mastered: "mastered",
      review: "need_review",
    };
    const mastery = masteryMap[action] || "reading";
    const endpoint = action === "mastered" ? "/master" : action === "review" ? "/review" : "/read";
    const body = action === "read" || action === "understood" ? JSON.stringify({ mastery }) : undefined;

    return requestJson(API_BASE + "/questions/" + id + endpoint, { method: "POST", body })
      .then(() => {
        showToast("已更新：" + (MASTERY_LABELS[mastery] || mastery));
        return Promise.all([loadStats(), loadCurrentTab()]);
      })
      .catch((err) => {
        console.error("标记失败:", err);
        showToast("操作失败，请重试");
      });
  }

  function showToast(msg) {
    if (window.showToast) {
      window.showToast(msg);
    } else {
      console.log("[Toast]", msg);
    }
  }

  // ── Tab 切换 ──────────────────────────────────────────────

  function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll(".interview-tab-btn").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.tab === tab);
    });
    loadCurrentTab();
  }

  // ── 事件绑定 ──────────────────────────────────────────────

  function bindEvents() {
    // Tab 切换
    document.querySelectorAll(".interview-tab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // 题目操作按钮（事件委托）
    const content = document.getElementById("interviewContent");
    if (content) {
      content.addEventListener("click", (e) => {
        const btn = e.target.closest(".interview-action-btn");
        if (!btn) return;
        const id = btn.dataset.id;
        const action = btn.dataset.action;
        if (id && action) markQuestion(id, action);
      });
    }
  }

  // ── 主加载函数 ────────────────────────────────────────────

  function loadInterviewData() {
    bindEvents();
    return Promise.all([loadStats(), loadSchedule(), loadAmmoLibrary(), loadToday()]);
  }

  // 暴露到全局
  window.loadInterviewData = loadInterviewData;
  window.switchInterviewTab = switchTab;

  // DOM 就绪后自动绑定（如果页面已加载）
  if (document.readyState !== "loading") {
    // 延迟绑定，确保元素已存在
    setTimeout(bindEvents, 100);
  } else {
    document.addEventListener("DOMContentLoaded", () => setTimeout(bindEvents, 100));
  }
})();
