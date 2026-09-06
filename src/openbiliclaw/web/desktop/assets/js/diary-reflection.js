/**
 * 日记反思回顾功能
 * 包含：周报、月度反思、年度回顾、人生里程碑
 */

(function () {
  "use strict";

  let currentReflectionView = "weekly";
  let currentWeekStart = null;
  let currentYearMonth = null;

  // 初始化
  function init() {
    initReflectionSubtabs();
    initWeeklyControls();
    initMonthlyControls();
    initYearlyControls();
    initMilestoneControls();

    // 默认加载本周周报
    loadWeeklyReport();
  }

  // 反思子 Tab 切换
  function initReflectionSubtabs() {
    document.querySelectorAll(".diary-reflection-subtab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.reflectionView;
        switchReflectionView(view);
      });
    });
  }

  function switchReflectionView(view) {
    currentReflectionView = view;
    document.querySelectorAll(".diary-reflection-subtab").forEach((t) => {
      t.classList.toggle("active", t.dataset.reflectionView === view);
    });
    document.getElementById("reflectionWeeklyContent").hidden = view !== "weekly";
    document.getElementById("reflectionMonthlyContent").hidden = view !== "monthly";
    document.getElementById("reflectionYearlyContent").hidden = view !== "yearly";
    document.getElementById("reflectionMilestonesContent").hidden = view !== "milestones";

    // 切换时加载对应数据
    if (view === "weekly") loadWeeklyReport();
    if (view === "monthly") loadMonthlyReflection();
    if (view === "yearly") loadYearlyReview();
    if (view === "milestones") loadMilestones();
  }

  // ═══════════════════════════════════════════
  // 周报
  // ═══════════════════════════════════════════

  function initWeeklyControls() {
    // 默认设置为本周
    const today = new Date();
    const weekStart = new Date(today);
    weekStart.setDate(today.getDate() - today.getDay() + 1); // 周一
    currentWeekStart = formatDate(weekStart);
    document.getElementById("weeklyDateInput").value = currentWeekStart;

    document.getElementById("weeklyDateInput").addEventListener("change", (e) => {
      const date = new Date(e.target.value);
      const weekStart = new Date(date);
      weekStart.setDate(date.getDate() - date.getDay() + 1);
      currentWeekStart = formatDate(weekStart);
      loadWeeklyReport();
    });

    document.getElementById("weeklyPrevBtn").addEventListener("click", () => {
      const date = new Date(currentWeekStart);
      date.setDate(date.getDate() - 7);
      currentWeekStart = formatDate(date);
      document.getElementById("weeklyDateInput").value = currentWeekStart;
      loadWeeklyReport();
    });

    document.getElementById("weeklyNextBtn").addEventListener("click", () => {
      const date = new Date(currentWeekStart);
      date.setDate(date.getDate() + 7);
      currentWeekStart = formatDate(date);
      document.getElementById("weeklyDateInput").value = currentWeekStart;
      loadWeeklyReport();
    });

    document.getElementById("weeklyGenerateBtn").addEventListener("click", generateWeeklyAI);
  }

  async function loadWeeklyReport() {
    const resultEl = document.getElementById("weeklyResult");
    resultEl.innerHTML = '<div class="diary-reflection-loading">加载中...</div>';

    try {
      const res = await fetch(`/api/diary/reflection/weekly?week_start=${currentWeekStart}`);
      const data = await res.json();
      if (data.ok) {
        renderWeeklyReport(data.data);
      } else {
        resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${data.error}</div>`;
      }
    } catch (e) {
      resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${e.message}</div>`;
    }
  }

  function renderWeeklyReport(report) {
    const resultEl = document.getElementById("weeklyResult");
    const weekEnd = addDays(report.week_start, 6);

    if (report.entry_count === 0) {
      resultEl.innerHTML = `
        <div class="diary-reflection-empty">
          <div class="diary-reflection-empty-icon">📝</div>
          <h4>${report.week_start} 至 ${weekEnd}</h4>
          <p>本周还没有日记，先去写点什么吧 ✨</p>
        </div>`;
      return;
    }

    const moodHtml = Object.entries(report.mood_distribution || {})
      .map(([mood, count]) => `<span class="diary-reflection-mood-tag">${getMoodEmoji(mood)} ${mood}: ${count}</span>`)
      .join("");

    const themesHtml = (report.themes || [])
      .map((t) => `<span class="diary-reflection-theme-tag">${escapeHtml(t)}</span>`)
      .join("");

    const eventsHtml = (report.key_events || [])
      .map((e) => `<li>${escapeHtml(e)}</li>`)
      .join("");

    const highlightsHtml = (report.highlights || [])
      .map((e) => `<li>${escapeHtml(e)}</li>`)
      .join("");

    const lowlightsHtml = (report.lowlights || [])
      .map((e) => `<li>${escapeHtml(e)}</li>`)
      .join("");

    resultEl.innerHTML = `
      <div class="diary-reflection-header">
        <h3>📅 ${report.week_start} 至 ${weekEnd}</h3>
        <div class="diary-reflection-stats">
          <div class="diary-reflection-stat">
            <span class="stat-number">${report.entry_count}</span>
            <span class="stat-label">篇日记</span>
          </div>
          <div class="diary-reflection-stat">
            <span class="stat-number">${report.total_words}</span>
            <span class="stat-label">字</span>
          </div>
          <div class="diary-reflection-stat">
            <span class="stat-number">${getMoodEmoji(report.dominant_mood)}</span>
            <span class="stat-label">主导情绪</span>
          </div>
        </div>
      </div>

      <div class="diary-reflection-section">
        <h4>🎭 情绪分布</h4>
        <div class="diary-reflection-mood-tags">${moodHtml || "暂无数据"}</div>
      </div>

      <div class="diary-reflection-section">
        <h4>🏷️ 本周主题</h4>
        <div class="diary-reflection-theme-tags">${themesHtml || "暂无数据"}</div>
      </div>

      <div class="diary-reflection-grid">
        <div class="diary-reflection-section">
          <h4>📌 关键事件</h4>
          <ul class="diary-reflection-list">${eventsHtml || "<li>暂无</li>"}</ul>
        </div>
        <div class="diary-reflection-section">
          <h4>✨ 高光时刻</h4>
          <ul class="diary-reflection-list highlights">${highlightsHtml || "<li>暂无</li>"}</ul>
        </div>
      </div>

      ${report.lowlights && report.lowlights.length > 0 ? `
      <div class="diary-reflection-section">
        <h4>🌧️ 低谷时刻</h4>
        <ul class="diary-reflection-list lowlights">${lowlightsHtml}</ul>
      </div>` : ""}

      <div class="diary-reflection-ai-result" id="weeklyAIResult"></div>
    `;
  }

  async function generateWeeklyAI() {
    const loadingEl = document.getElementById("weeklyLoading");
    const aiResultEl = document.getElementById("weeklyAIResult");
    loadingEl.hidden = false;
    aiResultEl.innerHTML = "";

    try {
      const res = await fetch("/api/diary/reflection/weekly/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ week_start: currentWeekStart }),
      });
      const data = await res.json();
      if (data.ok) {
        renderAIResult(aiResultEl, data.ai_result);
      } else {
        aiResultEl.innerHTML = `<div class="diary-reflection-error">生成失败：${data.error}</div>`;
      }
    } catch (e) {
      aiResultEl.innerHTML = `<div class="diary-reflection-error">生成失败：${e.message}</div>`;
    } finally {
      loadingEl.hidden = true;
    }
  }

  // ═══════════════════════════════════════════
  // 月度反思
  // ═══════════════════════════════════════════

  function initMonthlyControls() {
    const now = new Date();
    currentYearMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    document.getElementById("monthlyDateInput").value = currentYearMonth;

    document.getElementById("monthlyDateInput").addEventListener("change", (e) => {
      currentYearMonth = e.target.value;
      loadMonthlyReflection();
    });

    document.getElementById("monthlyPrevBtn").addEventListener("click", () => {
      const [year, month] = currentYearMonth.split("-").map(Number);
      const date = new Date(year, month - 2, 1);
      currentYearMonth = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
      document.getElementById("monthlyDateInput").value = currentYearMonth;
      loadMonthlyReflection();
    });

    document.getElementById("monthlyNextBtn").addEventListener("click", () => {
      const [year, month] = currentYearMonth.split("-").map(Number);
      const date = new Date(year, month, 1);
      currentYearMonth = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
      document.getElementById("monthlyDateInput").value = currentYearMonth;
      loadMonthlyReflection();
    });

    document.getElementById("monthlyGenerateBtn").addEventListener("click", generateMonthlyAI);
  }

  async function loadMonthlyReflection() {
    const resultEl = document.getElementById("monthlyResult");
    resultEl.innerHTML = '<div class="diary-reflection-loading">加载中...</div>';

    const [year, month] = currentYearMonth.split("-").map(Number);
    try {
      const res = await fetch(`/api/diary/reflection/monthly/${year}/${month}`);
      const data = await res.json();
      if (data.ok) {
        renderMonthlyReflection(data.data);
      } else {
        resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${data.error}</div>`;
      }
    } catch (e) {
      resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${e.message}</div>`;
    }
  }

  function renderMonthlyReflection(reflection) {
    const resultEl = document.getElementById("monthlyResult");

    if (reflection.entry_count === 0) {
      resultEl.innerHTML = `
        <div class="diary-reflection-empty">
          <div class="diary-reflection-empty-icon">📝</div>
          <h4>${reflection.year}年${reflection.month}月</h4>
          <p>这个月还没有日记，先去写点什么吧 ✨</p>
        </div>`;
      return;
    }

    const moodHtml = Object.entries(reflection.mood_distribution || {})
      .map(([mood, count]) => `<span class="diary-reflection-mood-tag">${getMoodEmoji(mood)} ${mood}: ${count}</span>`)
      .join("");

    const themesHtml = (reflection.themes || [])
      .map((t) => `<span class="diary-reflection-theme-tag">${escapeHtml(t)}</span>`)
      .join("");

    const tagsHtml = (reflection.top_tags || [])
      .map(([tag, count]) => `<span class="diary-reflection-theme-tag">${escapeHtml(tag)} (${count})</span>`)
      .join("");

    const eventsHtml = (reflection.key_events || [])
      .map((e) => `<li>${escapeHtml(e)}</li>`)
      .join("");

    const milestonesHtml = (reflection.milestones || [])
      .map((m) => `<li>${escapeHtml(m)}</li>`)
      .join("");

    resultEl.innerHTML = `
      <div class="diary-reflection-header">
        <h3>📆 ${reflection.year}年${reflection.month}月</h3>
        <div class="diary-reflection-stats">
          <div class="diary-reflection-stat">
            <span class="stat-number">${reflection.entry_count}</span>
            <span class="stat-label">篇日记</span>
          </div>
          <div class="diary-reflection-stat">
            <span class="stat-number">${reflection.total_words}</span>
            <span class="stat-label">字</span>
          </div>
          <div class="diary-reflection-stat">
            <span class="stat-number">${getMoodEmoji(reflection.dominant_mood)}</span>
            <span class="stat-label">主导情绪</span>
          </div>
        </div>
      </div>

      <div class="diary-reflection-section">
        <h4>🎭 情绪分布</h4>
        <div class="diary-reflection-mood-tags">${moodHtml || "暂无数据"}</div>
      </div>

      <div class="diary-reflection-grid">
        <div class="diary-reflection-section">
          <h4>🏷️ 本月主题</h4>
          <div class="diary-reflection-theme-tags">${themesHtml || "暂无数据"}</div>
        </div>
        <div class="diary-reflection-section">
          <h4>🔥 热门标签</h4>
          <div class="diary-reflection-theme-tags">${tagsHtml || "暂无数据"}</div>
        </div>
      </div>

      <div class="diary-reflection-grid">
        <div class="diary-reflection-section">
          <h4>📌 关键事件</h4>
          <ul class="diary-reflection-list">${eventsHtml || "<li>暂无</li>"}</ul>
        </div>
        <div class="diary-reflection-section">
          <h4>🏆 本月里程碑</h4>
          <ul class="diary-reflection-list milestones">${milestonesHtml || "<li>暂无</li>"}</ul>
        </div>
      </div>

      <div class="diary-reflection-ai-result" id="monthlyAIResult"></div>
    `;
  }

  async function generateMonthlyAI() {
    const loadingEl = document.getElementById("monthlyLoading");
    const aiResultEl = document.getElementById("monthlyAIResult");
    loadingEl.hidden = false;
    aiResultEl.innerHTML = "";

    const [year, month] = currentYearMonth.split("-").map(Number);
    try {
      const res = await fetch(`/api/diary/reflection/monthly/${year}/${month}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json();
      if (data.ok) {
        renderAIResult(aiResultEl, data.ai_result);
      } else {
        aiResultEl.innerHTML = `<div class="diary-reflection-error">生成失败：${data.error}</div>`;
      }
    } catch (e) {
      aiResultEl.innerHTML = `<div class="diary-reflection-error">生成失败：${e.message}</div>`;
    } finally {
      loadingEl.hidden = true;
    }
  }

  // ═══════════════════════════════════════════
  // 年度回顾
  // ═══════════════════════════════════════════

  function initYearlyControls() {
    const now = new Date();
    document.getElementById("yearlyYearSelect").value = now.getFullYear();

    document.getElementById("yearlyYearSelect").addEventListener("change", () => {
      loadYearlyReview();
    });

    document.getElementById("yearlyGenerateBtn").addEventListener("click", generateYearlyAI);
  }

  async function loadYearlyReview() {
    const resultEl = document.getElementById("yearlyResult");
    resultEl.innerHTML = '<div class="diary-reflection-loading">加载中...</div>';

    const year = document.getElementById("yearlyYearSelect").value;
    try {
      const res = await fetch(`/api/diary/reflection/yearly/${year}`);
      const data = await res.json();
      if (data.ok) {
        renderYearlyReview(data.data);
      } else {
        resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${data.error}</div>`;
      }
    } catch (e) {
      resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${e.message}</div>`;
    }
  }

  function renderYearlyReview(review) {
    const resultEl = document.getElementById("yearlyResult");

    if (review.entry_count === 0) {
      resultEl.innerHTML = `
        <div class="diary-reflection-empty">
          <div class="diary-reflection-empty-icon">📝</div>
          <h4>${review.year}年</h4>
          <p>这一年还没有日记，先去写点什么吧 ✨</p>
        </div>`;
      return;
    }

    const monthlyHtml = (review.monthly_stats || [])
      .map((m) => `
        <div class="diary-reflection-month-card">
          <div class="month-number">${m.month}月</div>
          <div class="month-stats">
            <span>${m.entry_count}篇</span>
            <span>${m.total_words}字</span>
          </div>
          <div class="month-mood">${getMoodEmoji(m.dominant_mood)}</div>
        </div>
      `)
      .join("");

    const top10Html = (review.top_10_events || [])
      .map((e, i) => `<li><span class="rank">${i + 1}</span> ${escapeHtml(e)}</li>`)
      .join("");

    const milestonesHtml = (review.milestones || [])
      .map((m) => `<li>${escapeHtml(m)}</li>`)
      .join("");

    const themesHtml = (review.themes || [])
      .map((t) => `<span class="diary-reflection-theme-tag">${escapeHtml(t)}</span>`)
      .join("");

    const tagsHtml = (review.top_tags || [])
      .map(([tag, count]) => `<span class="diary-reflection-theme-tag">${escapeHtml(tag)} (${count})</span>`)
      .join("");

    resultEl.innerHTML = `
      <div class="diary-reflection-header">
        <h3>🎊 ${review.year}年年度回顾</h3>
        <div class="diary-reflection-stats">
          <div class="diary-reflection-stat">
            <span class="stat-number">${review.entry_count}</span>
            <span class="stat-label">篇日记</span>
          </div>
          <div class="diary-reflection-stat">
            <span class="stat-number">${review.total_words}</span>
            <span class="stat-label">字</span>
          </div>
          <div class="diary-reflection-stat">
            <span class="stat-number">${review.monthly_stats.length}</span>
            <span class="stat-label">个月有记录</span>
          </div>
        </div>
      </div>

      <div class="diary-reflection-section">
        <h4>📅 月度概览</h4>
        <div class="diary-reflection-month-grid">${monthlyHtml}</div>
      </div>

      <div class="diary-reflection-grid">
        <div class="diary-reflection-section">
          <h4>🏷️ 年度主题</h4>
          <div class="diary-reflection-theme-tags">${themesHtml || "暂无数据"}</div>
        </div>
        <div class="diary-reflection-section">
          <h4>🔥 热门标签</h4>
          <div class="diary-reflection-theme-tags">${tagsHtml || "暂无数据"}</div>
        </div>
      </div>

      <div class="diary-reflection-grid">
        <div class="diary-reflection-section">
          <h4>🏆 年度十大事件</h4>
          <ul class="diary-reflection-list top10">${top10Html || "<li>暂无</li>"}</ul>
        </div>
        <div class="diary-reflection-section">
          <h4>🌟 年度里程碑</h4>
          <ul class="diary-reflection-list milestones">${milestonesHtml || "<li>暂无</li>"}</ul>
        </div>
      </div>

      <div class="diary-reflection-ai-result" id="yearlyAIResult"></div>
    `;
  }

  async function generateYearlyAI() {
    const loadingEl = document.getElementById("yearlyLoading");
    const aiResultEl = document.getElementById("yearlyAIResult");
    loadingEl.hidden = false;
    aiResultEl.innerHTML = "";

    const year = document.getElementById("yearlyYearSelect").value;
    try {
      const res = await fetch(`/api/diary/reflection/yearly/${year}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json();
      if (data.ok) {
        renderAIResult(aiResultEl, data.ai_result);
      } else {
        aiResultEl.innerHTML = `<div class="diary-reflection-error">生成失败：${data.error}</div>`;
      }
    } catch (e) {
      aiResultEl.innerHTML = `<div class="diary-reflection-error">生成失败：${e.message}</div>`;
    } finally {
      loadingEl.hidden = true;
    }
  }

  // ═══════════════════════════════════════════
  // 人生里程碑
  // ═══════════════════════════════════════════

  function initMilestoneControls() {
    document.getElementById("milestoneStartDate").value = "2016-01-01";
    document.getElementById("milestoneEndDate").value = formatDate(new Date());

    document.getElementById("milestoneLoadBtn").addEventListener("click", loadMilestones);
  }

  async function loadMilestones() {
    const resultEl = document.getElementById("milestonesResult");
    const loadingEl = document.getElementById("milestonesLoading");
    loadingEl.hidden = false;
    resultEl.innerHTML = "";

    const startDate = document.getElementById("milestoneStartDate").value;
    const endDate = document.getElementById("milestoneEndDate").value;

    try {
      const res = await fetch(`/api/diary/reflection/milestones?start_date=${startDate}&end_date=${endDate}&limit=50`);
      const data = await res.json();
      if (data.ok) {
        renderMilestones(data.data);
      } else {
        resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${data.error}</div>`;
      }
    } catch (e) {
      resultEl.innerHTML = `<div class="diary-reflection-error">加载失败：${e.message}</div>`;
    } finally {
      loadingEl.hidden = true;
    }
  }

  function renderMilestones(milestones) {
    const resultEl = document.getElementById("milestonesResult");

    if (!milestones || milestones.length === 0) {
      resultEl.innerHTML = `
        <div class="diary-reflection-empty">
          <div class="diary-reflection-empty-icon">🏆</div>
          <h4>暂无里程碑</h4>
          <p>在这个时间段内还没有识别到重要里程碑</p>
        </div>`;
      return;
    }

    const categoryColors = {
      career: "#f59e0b",
      relationship: "#ec4899",
      health: "#10b981",
      finance: "#3b82f6",
      family: "#8b5cf6",
      travel: "#06b6d4",
      education: "#f97316",
      other: "#6b7280",
    };

    const categoryNames = {
      career: "💼 职业",
      relationship: "💕 感情",
      health: "🏥 健康",
      finance: "💰 财务",
      family: "👨‍👩‍👧 家庭",
      travel: "✈️ 旅行",
      education: "📚 学习",
      other: "📌 其他",
    };

    // 按年份分组
    const byYear = {};
    milestones.forEach((m) => {
      const year = m.date.substring(0, 4);
      if (!byYear[year]) byYear[year] = [];
      byYear[year].push(m);
    });

    let html = `<div class="diary-milestone-timeline">`;
    Object.keys(byYear).sort().reverse().forEach((year) => {
      html += `<div class="diary-milestone-year"><h4>📅 ${year}年</h4></div>`;
      byYear[year].forEach((m) => {
        const color = categoryColors[m.category] || "#6b7280";
        const categoryName = categoryNames[m.category] || m.category;
        html += `
          <div class="diary-milestone-item" style="border-left-color: ${color}">
            <div class="diary-milestone-date">${m.date}</div>
            <div class="diary-milestone-category" style="background: ${color}">${categoryName}</div>
            <div class="diary-milestone-title">${escapeHtml(m.title)}</div>
            <div class="diary-milestone-desc">${escapeHtml(m.description)}</div>
            <div class="diary-milestone-significance">
              重要性: ${"★".repeat(Math.round(m.significance * 5))}${"☆".repeat(5 - Math.round(m.significance * 5))}
            </div>
          </div>
        `;
      });
    });
    html += `</div>`;

    resultEl.innerHTML = html;
  }

  // ═══════════════════════════════════════════
  // 通用工具函数
  // ═══════════════════════════════════════════

  function renderAIResult(el, aiResult) {
    if (!aiResult) {
      el.innerHTML = '<div class="diary-reflection-error">AI 返回为空</div>';
      return;
    }

    // 尝试解析 JSON
    let parsed = null;
    try {
      // 提取 JSON 部分
      const jsonMatch = aiResult.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        parsed = JSON.parse(jsonMatch[0]);
      }
    } catch (e) {
      // 解析失败，直接显示原文
    }

    if (parsed) {
      let html = '<div class="diary-reflection-ai-card">';
      html += '<div class="diary-reflection-ai-header">✨ AI 深度分析</div>';

      if (parsed.summary) {
        html += `<div class="diary-reflection-ai-section"><h5>📝 总结</h5><p>${escapeHtml(parsed.summary).replace(/\n/g, "<br>")}</p></div>`;
      }
      if (parsed.reflection) {
        html += `<div class="diary-reflection-ai-section"><h5>💭 深度反思</h5><p>${escapeHtml(parsed.reflection).replace(/\n/g, "<br>")}</p></div>`;
      }
      if (parsed.growth_insights && parsed.growth_insights.length > 0) {
        html += `<div class="diary-reflection-ai-section"><h5>🌱 成长洞察</h5><ul>${parsed.growth_insights.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul></div>`;
      }
      if (parsed.growth_trajectory) {
        html += `<div class="diary-reflection-ai-section"><h5>📈 成长轨迹</h5><p>${escapeHtml(parsed.growth_trajectory).replace(/\n/g, "<br>")}</p></div>`;
      }
      if (parsed.lessons_learned && parsed.lessons_learned.length > 0) {
        html += `<div class="diary-reflection-ai-section"><h5>💡 经验教训</h5><ul>${parsed.lessons_learned.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul></div>`;
      }
      if (parsed.suggestions && parsed.suggestions.length > 0) {
        html += `<div class="diary-reflection-ai-section"><h5>🎯 建议</h5><ul>${parsed.suggestions.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul></div>`;
      }
      if (parsed.hopes_for_next_year) {
        html += `<div class="diary-reflection-ai-section"><h5>🌟 对未来的期许</h5><p>${escapeHtml(parsed.hopes_for_next_year).replace(/\n/g, "<br>")}</p></div>`;
      }

      html += "</div>";
      el.innerHTML = html;
    } else {
      // 直接显示原文
      el.innerHTML = `
        <div class="diary-reflection-ai-card">
          <div class="diary-reflection-ai-header">✨ AI 深度分析</div>
          <div class="diary-reflection-ai-content">${escapeHtml(aiResult).replace(/\n/g, "<br>")}</div>
        </div>
      `;
    }
  }

  function getMoodEmoji(mood) {
    const emojiMap = {
      very_happy: "😄",
      happy: "🙂",
      neutral: "😐",
      sad: "😔",
      very_sad: "😢",
      anxious: "😰",
      angry: "😠",
      unknown: "❓",
    };
    return emojiMap[mood] || "❓";
  }

  function formatDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function addDays(dateStr, days) {
    const date = new Date(dateStr);
    date.setDate(date.getDate() + days);
    return formatDate(date);
  }

  function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // 暴露到全局
  window.initDiaryReflection = init;
})();
