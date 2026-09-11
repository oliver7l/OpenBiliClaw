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
    ammo: null,
    reviews: null,
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
      case "ammo":
        renderAmmo(area);
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
      case "reviews":
        renderReviews(area);
        break;
      case "rebuttals":
        renderRebuttals(area);
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

  // ── 弹药库（阅读 + 状态跟踪）────────────────────────────

  let ammoReading = {};
  let ammoCompany = null; // 当前选中的公司
  let ammoCurrent = null; // 正在阅读的文件 {company, category, name}

  function ammoKey(company, category, name) {
    return company + "|" + category + "|" + name;
  }

  function ammoStatusOf(company, category, name) {
    return ammoReading[ammoKey(company, category, name)] || "unread";
  }

  function renderAmmo(area) {
    Promise.all([
      requestJson("/api/interview/ammo"),
      requestJson("/api/interview/ammo/reading"),
    ])
      .then(([data, reading]) => {
        cachedData.ammo = data;
        ammoReading = {};
        (reading.items || []).forEach((r) => {
          ammoReading[ammoKey(r.company, r.category, r.name)] = r.status;
        });
        area.innerHTML = renderAmmoHtml(data);
        bindAmmo(area);
      })
      .catch((err) => {
        console.error("加载弹药库失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function renderAmmoHtml(data) {
    if (!data.companies || data.companies.length === 0) {
      return '<div class="interview-empty">暂无弹药库（03_岗位弹药库 下没有「XX-面试准备」目录）</div>';
    }
    if (!ammoCompany || !data.companies.some((c) => c.company === ammoCompany)) {
      ammoCompany = data.companies[0].company;
    }
    return `
      <div class="interview-ammo-layout">
        <div class="interview-ammo-companies" id="ammoCompanies">
          ${renderAmmoCompanies(data)}
        </div>
        <div class="interview-ammo-files-panel" id="ammoFilesPanel">
          ${renderAmmoFiles(data, ammoCompany)}
        </div>
        <div class="interview-ammo-reader" id="ammoReader">
          <div class="interview-ammo-reader-empty">← 选择公司，点击弹药文件开始阅读</div>
        </div>
      </div>
    `;
  }

  function renderAmmoCompanies(data) {
    return (data.companies || []).map((c) => {
      const active = c.company === ammoCompany;
      return `
        <button type="button" class="interview-ammo-company-btn${active ? " is-active" : ""}" data-company="${escapeHtml(c.company)}">
          <span class="interview-ammo-company-btn-name">${escapeHtml(c.company)}</span>
          <span class="interview-ammo-company-btn-count">${c.total_files}</span>
        </button>
      `;
    }).join("");
  }

  function renderAmmoFiles(data, companyName) {
    const company = (data.companies || []).find((c) => c.company === companyName);
    if (!company) {
      return '<div class="interview-empty">未找到该公司弹药库</div>';
    }
    const categories = company.categories || {};
    const fileRows = Object.keys(categories).map((label) => {
      const files = categories[label] || [];
      if (files.length === 0) return "";
      return `
        <div class="interview-ammo-cat-label">${escapeHtml(label)}</div>
        ${files.map((f) => {
          const status = ammoStatusOf(company.company, label, f.name);
          const badge = { unread: "未读", reading: "在读", finished: "已读" }[status];
          const cls = "interview-ammo-status is-" + status;
          const isCurrent = ammoCurrent && ammoCurrent.name === f.name &&
            ammoCurrent.company === company.company && ammoCurrent.category === label;
          return `
            <div class="interview-ammo-file-row${isCurrent ? " is-current" : ""}">
              <button type="button" class="interview-ammo-file-btn"
                data-company="${escapeHtml(company.company)}"
                data-category="${escapeHtml(label)}"
                data-name="${escapeHtml(f.name)}">
                <span class="${cls}">${badge}</span>
                <span class="interview-ammo-file-name">${escapeHtml(f.name)}</span>
              </button>
            </div>
          `;
        }).join("")}
      `;
    }).join("");
    return `
      <div class="interview-ammo-company-block">
        <div class="interview-ammo-company-title">${escapeHtml(company.company)}</div>
        ${fileRows || '<div class="interview-ammo-empty-line">该筛选下无文件</div>'}
      </div>
    `;
  }

  function bindAmmo(area) {
    area.querySelectorAll(".interview-ammo-company-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        ammoCompany = btn.dataset.company;
        const panel = area.querySelector("#ammoFilesPanel");
        const nav = area.querySelector("#ammoCompanies");
        if (panel) panel.innerHTML = renderAmmoFiles(cachedData.ammo, ammoCompany);
        if (nav) nav.innerHTML = renderAmmoCompanies(cachedData.ammo);
        bindAmmo(area);
        bindAmmoFileButtons(area);
      });
    });
    bindAmmoFileButtons(area);
  }

  function bindAmmoFileButtons(area) {
    area.querySelectorAll(".interview-ammo-file-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        loadAmmoFile(area, btn.dataset.company, btn.dataset.category, btn.dataset.name);
      });
    });
    const readBtn = area.querySelector("#ammoMarkRead");
    if (readBtn) {
      readBtn.addEventListener("click", () =>
        setAmmoStatus(area, ammoCurrent, "finished"));
    }
    const unreadBtn = area.querySelector("#ammoMarkUnread");
    if (unreadBtn) {
      unreadBtn.addEventListener("click", () =>
        setAmmoStatus(area, ammoCurrent, "unread"));
    }
  }

  function loadAmmoFile(area, company, category, name) {
    ammoCurrent = { company, category, name };
    const reader = area.querySelector("#ammoReader");
    if (!reader) return;
    reader.innerHTML = '<div class="interview-ammo-reader-loading">加载中…</div>';
    const q = new URLSearchParams({ company, category, name }).toString();
    requestJson("/api/interview/ammo/file?" + q)
      .then((d) => {
        const status = ammoStatusOf(company, category, name);
        reader.innerHTML = `
          <div class="interview-ammo-reader-header">
            <div class="interview-ammo-reader-title">${escapeHtml(d.name)}</div>
            <div class="interview-ammo-reader-meta">${escapeHtml(d.company)} · ${d.lines} 行${d.truncated ? " · 已截断" : ""} ·
              <span class="interview-ammo-reader-status is-${status}" id="ammoReaderStatus">${status === "reading" ? "在读" : status === "finished" ? "已读" : "未读"}</span>
            </div>
            <div class="interview-ammo-reader-actions">
              <button type="button" id="ammoMarkRead" class="pill-btn">✅ 标为已读</button>
              <button type="button" id="ammoMarkUnread" class="pill-btn">↩ 标为未读</button>
            </div>
          </div>
          <div class="interview-ammo-reader-body">${renderMarkdown(d.content)}</div>
        `;
        bindAmmoFileButtons(area);
        if (status !== "finished") setAmmoStatus(area, ammoCurrent, "reading");
      })
      .catch((err) => {
        reader.innerHTML = '<div class="interview-ammo-reader-err">加载失败：' + escapeHtml(err.message) + "</div>";
      });
  }

  function setAmmoStatus(area, file, status) {
    if (!file) return;
    const prev = ammoStatusOf(file.company, file.category, file.name);
    ammoReading[ammoKey(file.company, file.category, file.name)] = status;
    // 局部更新徽标与筛选计数
    const panel = area.querySelector("#ammoFilesPanel");
    if (panel) panel.innerHTML = renderAmmoFiles(cachedData.ammo, ammoCompany);
    const statusEl = area.querySelector("#ammoReaderStatus");
    if (statusEl) {
      statusEl.textContent = { unread: "未读", reading: "在读", finished: "已读" }[status] || "未读";
      statusEl.className = "interview-ammo-reader-status is-" + status;
    }
    bindAmmoFileButtons(area);
    requestJson("/api/interview/ammo/reading", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company: file.company, category: file.category, name: file.name, status }),
    }).catch((err) => {
      ammoReading[ammoKey(file.company, file.category, file.name)] = prev;
      console.error("更新阅读状态失败:", err);
    });
  }

  // 轻量 markdown 渲染（转义防 XSS；支持标题/列表/引用/代码块/链接/粗斜体/表格/分隔线）
  function renderMarkdown(src) {
    const esc = escapeHtml(src);
    const lines = esc.split(/\r?\n/);
    const out = [];
    let inCode = false, inTable = false, listType = null;
    const closeList = () => { if (listType) { out.push(listType === "ul" ? "</ul>" : "</ol>"); listType = null; } };
    const closeTable = () => { if (inTable) { out.push("</tbody></table>"); inTable = false; } };
    const inline = (s) => s
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (/^```/.test(line)) {
        closeList(); closeTable();
        if (inCode) { out.push("</code></pre>"); inCode = false; }
        else { out.push("<pre><code>"); inCode = true; }
        continue;
      }
      if (inCode) { out.push(line); continue; }
      if (/^\|/.test(line) && /\|/.test(line.slice(1))) {
        if (!inTable) {
          closeList();
          out.push("<table><thead><tr>");
          out.push(line.split("|").slice(1, -1).map((c) => "<th>" + inline(c.trim()) + "</th>").join(""));
          out.push("</tr></thead><tbody>");
          inTable = true;
        } else {
          if (/^[\s:|-]*-[\s:|-]*$/.test(line.replace(/\|/g, ""))) continue; // 分隔行
          out.push("<tr>" + line.split("|").slice(1, -1).map((c) => "<td>" + inline(c.trim()) + "</td>").join("") + "</tr>");
        }
        continue;
      }
      closeTable();
      if (/^#{1,6} /.test(line)) {
        closeList();
        const level = line.indexOf(" ");
        const text = inline(line.slice(level + 1));
        out.push("<h" + level + ">" + text + "</h" + level + ">");
      } else if (/^>\s?/.test(line)) {
        closeList();
        out.push("<blockquote>" + inline(line.replace(/^>\s?/, "")) + "</blockquote>");
      } else if (/^-{3,}$/.test(line) || /^\*{3,}$/.test(line)) {
        closeList();
        out.push("<hr/>");
      } else if (/^[-*] /.test(line)) {
        if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; }
        out.push("<li>" + inline(line.replace(/^[-*] /, "")) + "</li>");
      } else if (/^\d+\. /.test(line)) {
        if (listType !== "ol") { closeList(); out.push("<ol>"); listType = "ol"; }
        out.push("<li>" + inline(line.replace(/^\d+\. /, "")) + "</li>");
      } else if (line.trim() === "") {
        closeList();
      } else {
        closeList();
        out.push("<p>" + inline(line) + "</p>");
      }
    }
    closeList(); closeTable();
    if (inCode) out.push("</code></pre>");
    return out.join("\n");
  }

  // ── 复盘（interview_reviews 记录）─────────────────────────

  function renderReviews(area) {
    if (cachedData.reviews) {
      area.innerHTML = renderReviewsHtml(cachedData.reviews);
      bindReviews(area);
      return;
    }
    requestJson("/api/interview/reviews?limit=100")
      .then((data) => {
        cachedData.reviews = data || [];
        area.innerHTML = renderReviewsHtml(cachedData.reviews);
        bindReviews(area);
      })
      .catch((err) => {
        console.error("加载复盘失败:", err);
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  const REVIEW_RESULT_LABELS = {
    first_round_done: "一面完成", first: "一面完成", second: "二面完成",
    pending: "待定", cancelled: "已取消", passed: "通过", rejected: "未通过",
  };
  const REVIEW_ROUND_LABELS = { first: "一面", second: "二面", hr: "HR面" };

  function renderReviewsHtml(list) {
    if (!list || list.length === 0) {
      return '<div class="interview-empty">暂无复盘记录（面试后把题目和回答整理进来）</div>';
    }
    return `
      <div style="margin-bottom:12px;font-size:13px;color:var(--muted);">📝 共 ${list.length} 场面试复盘</div>
      <div class="interview-company-grid">
        ${list.map(renderReviewCard).join("")}
      </div>
    `;
  }

  function renderReviewCard(r) {
    const resultLabel = REVIEW_RESULT_LABELS[r.result] || r.result || "待定";
    const roundLabel = REVIEW_ROUND_LABELS[r.round] || r.round || "";
    return `
      <div class="interview-company-card interview-review-card" data-id="${r.id}" style="cursor:pointer;">
        <div class="interview-company-header">
          <div class="interview-company-name">${escapeHtml(r.company)}</div>
          <div class="interview-company-status status-done">${escapeHtml(resultLabel)}</div>
        </div>
        <div class="interview-company-position">${escapeHtml(r.position || "未知岗位")}${roundLabel ? " · " + escapeHtml(roundLabel) : ""}</div>
        <div class="interview-company-meta">
          <span>📅 ${escapeHtml(r.interview_date || "")}</span>
          ${r.duration_min ? `<span>⏱ ${r.duration_min} 分钟</span>` : ""}
          ${r.emotion_level ? `<span>💬 ${escapeHtml(r.emotion_level)}</span>` : ""}
        </div>
        ${r.tags ? `<div class="interview-company-summary" style="margin-top:6px;">🏷 ${escapeHtml(r.tags)}</div>` : ""}
      </div>
    `;
  }

  function bindReviews(area) {
    area.querySelectorAll(".interview-review-card").forEach((card) => {
      card.addEventListener("click", () => {
        loadReviewDetail(area, card.dataset.id);
      });
    });
  }

  function loadReviewDetail(area, id) {
    requestJson("/api/interview/reviews/" + id)
      .then((r) => {
        area.innerHTML = renderReviewDetailHtml(r);
        const back = area.querySelector("#reviewBack");
        if (back) {
          back.addEventListener("click", () => {
            cachedData.reviews = null;
            renderReviews(area);
          });
        }
      })
      .catch((err) => {
        area.innerHTML = '<div class="interview-empty">加载失败：' + escapeHtml(err.message) + "</div>";
      });
  }

  function renderReviewDetailHtml(r) {
    const section = (title, lines, tag) => {
      const items = (lines || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      if (items.length === 0) return "";
      return `
        <div class="interview-company-section">
          <div class="interview-company-section-title">${title}</div>
          ${tag === "ul"
            ? `<ul class="interview-company-list">${items.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>`
            : items.map((s) => `<div class="interview-company-summary" style="margin-bottom:4px;">${escapeHtml(s)}</div>`).join("")}
        </div>
      `;
    };
    return `
      <div style="margin-bottom:8px;"><button class="pill-btn" id="reviewBack" type="button">← 返回复盘列表</button></div>
      <div class="interview-company-card">
        <div class="interview-company-header">
          <div class="interview-company-name">${escapeHtml(r.company)} · ${escapeHtml(r.position || "未知岗位")}</div>
          <div class="interview-company-status status-done">${escapeHtml(REVIEW_RESULT_LABELS[r.result] || r.result || "")}</div>
        </div>
        <div class="interview-company-meta">
          <span>📅 ${escapeHtml(r.interview_date || "")}</span>
          <span>⏱ ${r.duration_min || 0} 分钟</span>
        </div>
        ${section("❓ 关键问题", r.key_questions, "ul")}
        ${section("🤖 AI 评价", r.ai_evaluation)}
        ${section("✍️ 自评", r.self_assessment)}
        ${section("🔧 技术复盘", r.technical_review)}
        ${section("📌 下一步行动", r.action_items, "ul")}
        ${section("📝 备注", r.notes)}
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

  // ── 反问话术 ──────────────────────────────────────────────

  function renderRebuttals(area) {
    if (cachedData.rebuttals) {
      area.innerHTML = renderRebuttalsHtml(cachedData.rebuttals);
      bindRebuttalEvents();
      return;
    }
    requestJson(API_BASE + "/rebuttals")
      .then((data) => {
        cachedData.rebuttals = data;
        area.innerHTML = renderRebuttalsHtml(data);
        bindRebuttalEvents();
      })
      .catch(() => {
        area.innerHTML = '<div class="interview-empty">加载失败，请刷新重试</div>';
      });
  }

  function renderRebuttalsHtml(data) {
    const items = data.items || [];
    if (items.length === 0) {
      return '<div class="interview-empty">暂无反问话术，点击右上角添加</div>';
    }
    // 按分类分组
    const groups = {};
    items.forEach((item) => {
      if (!groups[item.category]) groups[item.category] = [];
      groups[item.category].push(item);
    });
    let html = '<div class="rebuttals-container">';
    // 分类筛选
    html += '<div class="rebuttals-filter">';
    html += '<button class="rebuttal-filter-btn is-active" data-category="all" type="button">全部</button>';
    ["HR面", "技术面", "业务面", "通用"].forEach((cat) => {
      if (groups[cat]) {
        html += `<button class="rebuttal-filter-btn" data-category="${cat}" type="button">${cat}(${groups[cat].length})</button>`;
      }
    });
    html += '</div>';
    // 按分类渲染
    Object.keys(groups).forEach((cat) => {
      html += `<div class="rebuttal-group" data-category="${cat}">`;
      html += `<h3 class="rebuttal-group-title">${cat}</h3>`;
      groups[cat].forEach((item) => {
        const priorityClass = item.priority === "高" ? "priority-high" : item.priority === "中" ? "priority-mid" : "priority-low";
        const companyTag = item.company ? `<span class="rebuttal-company">${item.company}</span>` : "";
        html += `<div class="rebuttal-card ${priorityClass}" data-id="${item.id}">`;
        html += `<div class="rebuttal-question">${item.question}</div>`;
        if (item.purpose) html += `<div class="rebuttal-purpose">💡 ${item.purpose}</div>`;
        html += '<div class="rebuttal-meta">';
        html += `<span class="rebuttal-priority">${item.priority}优先级</span>`;
        if (companyTag) html += companyTag;
        if (item.tags) html += `<span class="rebuttal-tags">${item.tags}</span>`;
        if (item.used_count > 0) html += `<span class="rebuttal-used">已用${item.used_count}次</span>`;
        html += '</div>';
        html += '<div class="rebuttal-actions">';
        html += `<button class="rebuttal-action-btn" data-action="use" data-id="${item.id}" type="button">✓ 已用</button>`;
        html += '</div>';
        html += '</div>';
      });
      html += '</div>';
    });
    html += '</div>';
    return html;
  }

  function bindRebuttalEvents() {
    // 分类筛选
    document.querySelectorAll(".rebuttal-filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".rebuttal-filter-btn").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        const cat = btn.dataset.category;
        document.querySelectorAll(".rebuttal-group").forEach((group) => {
          group.style.display = cat === "all" || group.dataset.category === cat ? "" : "none";
        });
      });
    });
    // 标记已用
    document.querySelectorAll(".rebuttal-action-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.id;
        if (btn.dataset.action === "use") {
          requestJson(API_BASE + `/rebuttals/${id}/use`, { method: "POST" })
            .then(() => {
              // 刷新缓存
              delete cachedData.rebuttals;
              const area = document.getElementById("interviewContentArea");
              if (area) renderRebuttals(area);
            })
            .catch(() => {});
        }
      });
    });
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
