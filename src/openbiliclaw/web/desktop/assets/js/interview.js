/* 面试备战中心页面逻辑 — 三级导航布局 */
(function () {
  "use strict";

  const API_BASE = "/api/interview";
  let currentSubtab = "schedule";
  let cachedData = {
    stats: null,
    schedule: null,
    companies: null,
    today: null,
    queue: null,
    all: null,
  };

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

  function showToast(msg) {
    if (window.showToast) window.showToast(msg);
    else console.log("[Toast]", msg);
  }

  // ── 二级 Tab 切换 ─────────────────────────────────────────

  function switchSubtab(subtab) {
    currentSubtab = subtab;
    document.querySelectorAll(".page-subtab-btn").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.subtab === subtab);
    });
    renderSubtab(subtab);
  }

  function renderSubtab(subtab) {
    const area = document.getElementById("interviewContentArea");
    if (!area) return;
    area.innerHTML = '<div class="interview-loading">正在加载…</div>';

    switch (subtab) {
      case "schedule":
        renderSchedule(area);
        break;
      case "companies":
        renderCompanies(area);
        break;
      case "today":
        renderToday(area);
        break;
      case "queue":
        renderQueue(area);
        break;
      case "all":
        renderAll(area);
        break;
      case "stats":
        renderStatsPage(area);
        break;
    }
  }

  // ── 面试安排 ──────────────────────────────────────────────

  function renderSchedule(area) {
    if (cachedData.schedule) {
      area.innerHTML = renderScheduleHtml(cachedData.schedule);
      return;
    }
    requestJson(API_BASE + "/schedule")
      .then((data) => {
        cachedData.schedule = data;
        area.innerHTML = renderScheduleHtml(data);
      })
      .catch((err) => {
        console.error("加载面试安排失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function renderScheduleHtml(data) {
    const all = (data.upcoming || []).concat(data.history || []);
    if (all.length === 0) return '<div class="interview-empty">暂无面试安排</div>';
    return `
      <div style="margin-bottom:12px;font-size:13px;color:var(--muted);">
        📌 共 ${data.total} 场面试，${data.upcoming_count} 场待面
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;">
        ${all.map(renderScheduleCard).join("")}
      </div>
    `;
  }

  function renderScheduleCard(job) {
    const interviewAt = job.interview_at || "";
    const datePart = interviewAt.split(" ")[0] || "";
    const timePart = interviewAt.split(" ")[1] || "";
    const day = datePart ? datePart.split("-")[2] : "?";
    const month = datePart ? datePart.split("-")[1] + "月" : "";
    const statusClass = job.is_upcoming ? (job.status === "进行中" ? "status-progress" : "status-upcoming") : "status-done";
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
        <div class="interview-schedule-status ${statusClass}">${escapeHtml(job.status || "未知")}</div>
      </div>
    `;
  }

  // ── 公司岗位信息 ──────────────────────────────────────────

  function renderCompanies(area) {
    if (cachedData.companies) {
      area.innerHTML = renderCompaniesHtml(cachedData.companies);
      return;
    }
    requestJson(API_BASE + "/company-profiles")
      .then((data) => {
        cachedData.companies = data;
        area.innerHTML = renderCompaniesHtml(data);
      })
      .catch((err) => {
        console.error("加载公司岗位失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function renderCompaniesHtml(data) {
    if (!data.companies || data.companies.length === 0) {
      return '<div class="interview-empty">暂无公司岗位信息</div>';
    }
    return `
      <div style="margin-bottom:12px;font-size:13px;color:var(--muted);">
        🏢 共 ${data.total} 家公司岗位信息
      </div>
      <div class="interview-company-grid">
        ${data.companies.map(renderCompanyCard).join("")}
      </div>
    `;
  }

  function renderCompanyCard(company) {
    const statusClass = company.status === "进行中" ? "status-progress" :
                       company.status === "待面" ? "status-upcoming" : "status-done";
    const responsibilities = (company.key_responsibilities || []).slice(0, 3).map(
      (r) => `<li>${escapeHtml(r)}</li>`
    ).join("");
    const highlights = (company.match_highlights || []).slice(0, 3).map(
      (h) => `<li>${escapeHtml(h)}</li>`
    ).join("");
    return `
      <div class="interview-company-card">
        <div class="interview-company-header">
          <div class="interview-company-name">${escapeHtml(company.company)}</div>
          <div class="interview-company-status ${statusClass}">${escapeHtml(company.status || "未知")}</div>
        </div>
        <div class="interview-company-position">${escapeHtml(company.position || "未知岗位")}</div>
        <div class="interview-company-meta">
          ${company.location ? `<div class="interview-company-meta-item">📍 ${escapeHtml(company.location)}</div>` : ""}
          ${company.interview_time ? `<div class="interview-company-meta-item">📅 ${escapeHtml(company.interview_time)}</div>` : ""}
          ${company.direction ? `<div class="interview-company-meta-item">🎯 ${escapeHtml(company.direction)}</div>` : ""}
        </div>
        ${company.job_summary ? `<div class="interview-company-summary">💡 ${escapeHtml(company.job_summary)}</div>` : ""}
        ${responsibilities ? `
          <div class="interview-company-section">
            <div class="interview-company-section-title">📋 核心职责</div>
            <ul class="interview-company-list">${responsibilities}</ul>
          </div>
        ` : ""}
        ${highlights ? `
          <div class="interview-company-section">
            <div class="interview-company-section-title">⭐ 匹配亮点</div>
            <ul class="interview-company-list">${highlights}</ul>
          </div>
        ` : ""}
        ${company.resume_version || company.resume_file ? `
          <div class="interview-company-resume">
            📄 投递简历：<strong>${escapeHtml(company.resume_version || "未记录")}</strong>
            ${company.resume_file ? `<br><span style="font-size:10px;">${escapeHtml(company.resume_file)}</span>` : ""}
          </div>
        ` : ""}
        ${company.notes ? `<div class="interview-company-summary" style="margin-top:8px;">📝 ${escapeHtml(company.notes)}</div>` : ""}
      </div>
    `;
  }

  // ── 今日待读 ──────────────────────────────────────────────

  function renderToday(area) {
    if (cachedData.today) {
      area.innerHTML = renderQuestionsHtml(cachedData.today.questions, "今日待读");
      return;
    }
    requestJson(API_BASE + "/today")
      .then((data) => {
        cachedData.today = data;
        area.innerHTML = renderQuestionsHtml(data.questions, "今日待读", data.plan);
      })
      .catch((err) => {
        console.error("加载今日待读失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  // ── 待看队列 ──────────────────────────────────────────────

  function renderQueue(area) {
    if (cachedData.queue) {
      area.innerHTML = renderQuestionsHtml(cachedData.queue.queue, "待看队列");
      return;
    }
    requestJson(API_BASE + "/queue")
      .then((data) => {
        cachedData.queue = data;
        area.innerHTML = renderQuestionsHtml(data.queue, "待看队列");
      })
      .catch((err) => {
        console.error("加载待看队列失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  // ── 全部题目 ──────────────────────────────────────────────

  function renderAll(area) {
    if (cachedData.all) {
      area.innerHTML = renderQuestionsHtml(cachedData.all.questions, "全部题目");
      return;
    }
    requestJson(API_BASE + "/questions?limit=200")
      .then((data) => {
        cachedData.all = data;
        area.innerHTML = renderQuestionsHtml(data.questions, "全部题目");
      })
      .catch((err) => {
        console.error("加载全部题目失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function renderQuestionsHtml(questions, title, plan) {
    if (!questions || questions.length === 0) {
      return `<div class="interview-empty">${title}为空</div>`;
    }
    return `
      <div style="margin-bottom:12px;font-size:13px;color:var(--muted);">
        📖 ${title}：共 ${questions.length} 题
        ${plan ? ` · 计划：${escapeHtml(plan.name)} · 每日 ${plan.daily_target} 题` : ""}
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;">
        ${questions.map(renderQuestionCard).join("")}
      </div>
    `;
  }

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

  // ── 学习统计 ──────────────────────────────────────────────

  function renderStatsPage(area) {
    if (cachedData.stats) {
      area.innerHTML = renderStatsHtml(cachedData.stats);
      return;
    }
    requestJson(API_BASE + "/stats")
      .then((data) => {
        cachedData.stats = data;
        area.innerHTML = renderStatsHtml(data);
      })
      .catch((err) => {
        console.error("加载统计失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function renderStatsHtml(data) {
    const maxCat = Math.max(...Object.values(data.by_category || {}), 1);
    const maxDiff = Math.max(...Object.values(data.by_difficulty || {}), 1);
    return `
      <div class="interview-stats-page">
        <div class="interview-stats-row">
          <div class="interview-stat-card">
            <div class="interview-stat-value">${data.total}</div>
            <div class="interview-stat-label">总题数</div>
          </div>
          <div class="interview-stat-card">
            <div class="interview-stat-value" style="color:#16a34a;">${data.mastered}</div>
            <div class="interview-stat-label">已掌握</div>
          </div>
          <div class="interview-stat-card">
            <div class="interview-stat-value" style="color:#059669;">${data.understood}</div>
            <div class="interview-stat-label">已理解</div>
          </div>
          <div class="interview-stat-card">
            <div class="interview-stat-value" style="color:#dc2626;">${data.need_review}</div>
            <div class="interview-stat-label">需复习</div>
          </div>
          <div class="interview-stat-card">
            <div class="interview-stat-value" style="color:var(--accent);">${data.mastery_rate}%</div>
            <div class="interview-stat-label">掌握率</div>
          </div>
        </div>

        <div class="interview-chart-section">
          <div class="interview-chart-title">📊 按分类分布</div>
          ${Object.entries(data.by_category || {}).sort((a, b) => b[1] - a[1]).map(([cat, count]) => `
            <div class="interview-bar-row">
              <div class="interview-bar-label">${escapeHtml(CATEGORY_LABELS[cat] || cat)}</div>
              <div class="interview-bar-track">
                <div class="interview-bar-fill" style="width:${(count / maxCat * 100).toFixed(0)}%">${count}</div>
              </div>
            </div>
          `).join("")}
        </div>

        <div class="interview-chart-section">
          <div class="interview-chart-title">⭐ 按难度分布</div>
          ${Object.entries(data.by_difficulty || {}).sort((a, b) => parseInt(a[0]) - parseInt(b[0])).map(([diff, count]) => `
            <div class="interview-bar-row">
              <div class="interview-bar-label">${"⭐".repeat(parseInt(diff))}</div>
              <div class="interview-bar-track">
                <div class="interview-bar-fill" style="width:${(count / maxDiff * 100).toFixed(0)}%;background:#f59e0b;">${count}</div>
              </div>
            </div>
          `).join("")}
        </div>

        <div class="interview-chart-section">
          <div class="interview-chart-title">📈 掌握状态分布</div>
          <div class="interview-bar-row">
            <div class="interview-bar-label">未开始</div>
            <div class="interview-bar-track">
              <div class="interview-bar-fill" style="width:${(data.not_started / data.total * 100).toFixed(0)}%;background:#9ca3af;">${data.not_started}</div>
            </div>
          </div>
          <div class="interview-bar-row">
            <div class="interview-bar-label">阅读中</div>
            <div class="interview-bar-track">
              <div class="interview-bar-fill" style="width:${(data.reading / data.total * 100).toFixed(0)}%;background:#3b82f6;">${data.reading}</div>
            </div>
          </div>
          <div class="interview-bar-row">
            <div class="interview-bar-label">已理解</div>
            <div class="interview-bar-track">
              <div class="interview-bar-fill" style="width:${(data.understood / data.total * 100).toFixed(0)}%;background:#10b981;">${data.understood}</div>
            </div>
          </div>
          <div class="interview-bar-row">
            <div class="interview-bar-label">已掌握</div>
            <div class="interview-bar-track">
              <div class="interview-bar-fill" style="width:${(data.mastered / data.total * 100).toFixed(0)}%;background:#f59e0b;">${data.mastered}</div>
            </div>
          </div>
          <div class="interview-bar-row">
            <div class="interview-bar-label">需复习</div>
            <div class="interview-bar-track">
              <div class="interview-bar-fill" style="width:${(data.need_review / data.total * 100).toFixed(0)}%;background:#ef4444;">${data.need_review}</div>
            </div>
          </div>
        </div>
      </div>
    `;
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
        // 清除缓存，重新加载
        cachedData.today = null;
        cachedData.queue = null;
        cachedData.all = null;
        cachedData.stats = null;
        if (currentSubtab === "today" || currentSubtab === "queue" || currentSubtab === "all" || currentSubtab === "stats") {
          renderSubtab(currentSubtab);
        }
      })
      .catch((err) => {
        console.error("标记失败:", err);
        showToast("操作失败，请重试");
      });
  }

  // ── 事件绑定 ──────────────────────────────────────────────

  function bindEvents() {
    // 二级 tab 切换
    document.querySelectorAll(".page-subtab-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchSubtab(btn.dataset.subtab));
    });

    // 题目操作按钮（事件委托）
    const area = document.getElementById("interviewContentArea");
    if (area) {
      area.addEventListener("click", (e) => {
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
    // 预加载统计数据（其他tab按需加载）
    requestJson(API_BASE + "/stats").then((data) => { cachedData.stats = data; }).catch(() => {});
    // 默认渲染第一个tab
    renderSubtab(currentSubtab);
  }

  // 暴露到全局
  window.loadInterviewData = loadInterviewData;
  window.switchInterviewSubtab = switchSubtab;

  // DOM 就绪后自动绑定
  if (document.readyState !== "loading") {
    setTimeout(bindEvents, 100);
  } else {
    document.addEventListener("DOMContentLoaded", () => setTimeout(bindEvents, 100));
  }
})();
