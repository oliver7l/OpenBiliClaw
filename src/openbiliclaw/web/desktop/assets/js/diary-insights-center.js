/**
 * 日记洞察中心 + 记忆中心前端逻辑
 */

(function () {
  "use strict";

  // ─── 洞察中心 ──────────────────────────────────────────────────────

  let currentInsightsView = "memory";
  let _insightsInitialized = false;
  let _memoryInitialized = false;

  // 初始化（幂等：只执行一次，避免重复绑定事件和重复加载）
  function initInsights() {
    if (_insightsInitialized) return;
    _insightsInitialized = true;

    initInsightsSubtabs();
    loadInsightsCurrentView();
  }

  function initInsightsSubtabs() {
    document.querySelectorAll(".diary-insights-subtab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.insightsView;
        switchInsightsView(view);
      });
    });
  }

  function switchInsightsView(view) {
    currentInsightsView = view;
    document.querySelectorAll(".diary-insights-subtab").forEach((t) => {
      t.classList.toggle("active", t.dataset.insightsView === view);
    });

    document.getElementById("insightsMemoryContent").hidden = view !== "memory";
    document.getElementById("insightsPatternsContent").hidden = view !== "patterns";
    document.getElementById("insightsBriefingContent").hidden = view !== "briefing";
    document.getElementById("insightsLoopsContent").hidden = view !== "loops";

    loadInsightsCurrentView();
  }

  function loadInsightsCurrentView() {
    switch (currentInsightsView) {
      case "memory":
        loadMemoryOnThisDay();
        break;
      case "patterns":
        loadPatterns();
        break;
      case "briefing":
        loadMorningBriefing();
        break;
      case "loops":
        loadOpenLoops();
        break;
    }
  }

  async function loadMemoryOnThisDay() {
    const container = document.getElementById("insightsMemoryContent");
    container.innerHTML = '<div class="diary-insights-loading">加载中...</div>';

    try {
      const res = await fetch("/api/diary/insights/memory-on-this-day");
      const data = await res.json();

      if (!data.ok || !data.data) {
        container.innerHTML = '<div class="diary-insights-empty">加载失败</div>';
        return;
      }

      const memory = data.data;
      let html = `
        <div class="insights-memory-header">
          <h4>📅 ${memory.summary}</h4>
          <p>共 ${memory.entries.length} 篇日记，跨越 ${memory.years_span.length} 年</p>
        </div>
        <div class="insights-memory-list">
      `;

      if (memory.entries.length === 0) {
        html += '<div class="diary-insights-empty">历史上的今天没有日记记录</div>';
      } else {
        for (const entry of memory.entries) {
          html += `
            <div class="insights-memory-card">
              <div class="insights-memory-date">
                <span class="insights-memory-years">${entry.years_ago} 年前</span>
                <span class="insights-memory-full-date">${entry.entry_date}</span>
              </div>
              <div class="insights-memory-title">${entry.title || "无标题"}</div>
              <div class="insights-memory-content">${entry.content || ""}</div>
            </div>
          `;
        }
      }

      html += `</div>`;
      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div class="diary-insights-error">加载失败: ${e.message}</div>`;
    }
  }

  async function loadPatterns() {
    const container = document.getElementById("insightsPatternsContent");
    container.innerHTML = '<div class="diary-insights-loading">加载中...</div>';

    try {
      const res = await fetch("/api/diary/insights/patterns?lookback_days=365");
      const data = await res.json();

      if (!data.ok || !data.data) {
        container.innerHTML = '<div class="diary-insights-empty">加载失败</div>';
        return;
      }

      const patterns = data.data;
      const typeNames = {
        emotion: "情绪", time: "时间", topic: "主题",
        relationship: "关系", behavior: "行为",
      };
      const severityColors = {
        info: "#3b82f6", interesting: "#8b5cf6", warning: "#f59e0b",
      };

      let html = `<div class="insights-patterns-list">`;

      if (patterns.length === 0) {
        html += '<div class="diary-insights-empty">暂无发现的模式</div>';
      } else {
        for (const pattern of patterns) {
          const color = severityColors[pattern.severity] || "#6b7280";
          const typeName = typeNames[pattern.pattern_type] || pattern.pattern_type;
          html += `
            <div class="insights-pattern-card" style="border-left-color: ${color}">
              <div class="insights-pattern-header">
                <span class="insights-pattern-type" style="background: ${color}">${typeName}</span>
                <span class="insights-pattern-title">${pattern.title}</span>
                <span class="insights-pattern-confidence">置信度 ${(pattern.confidence * 100).toFixed(0)}%</span>
              </div>
              <div class="insights-pattern-description">${pattern.description}</div>
            </div>
          `;
        }
      }

      html += `</div>`;
      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div class="diary-insights-error">加载失败: ${e.message}</div>`;
    }
  }

  async function loadMorningBriefing() {
    const container = document.getElementById("insightsBriefingContent");
    container.innerHTML = '<div class="diary-insights-loading">加载中...</div>';

    try {
      const res = await fetch("/api/diary/insights/morning-briefing");
      const data = await res.json();

      if (!data.ok || !data.data) {
        container.innerHTML = '<div class="diary-insights-empty">加载失败</div>';
        return;
      }

      const briefing = data.data;
      const yesterday = briefing.yesterday_summary || {};

      let html = `
        <div class="insights-briefing-header">
          <h4>🌅 ${briefing.briefing_date} 晨间简报</h4>
          ${briefing.mood_forecast ? `<p class="insights-briefing-forecast">${briefing.mood_forecast}</p>` : ""}
        </div>

        <div class="insights-briefing-grid">
          <div class="insights-briefing-card">
            <h5>📝 昨天总结</h5>
            <div class="insights-briefing-stats">
              <div class="insights-briefing-stat">
                <div class="insights-briefing-value">${yesterday.entry_count || 0}</div>
                <div class="insights-briefing-label">日记篇数</div>
              </div>
              <div class="insights-briefing-stat">
                <div class="insights-briefing-value">${yesterday.total_words || 0}</div>
                <div class="insights-briefing-label">总字数</div>
              </div>
            </div>
            ${yesterday.highlights && yesterday.highlights.length > 0 ? `
              <div class="insights-briefing-highlights">
                <h6>亮点</h6>
                ${yesterday.highlights.map((h) => `<div class="insights-briefing-highlight">${h.title || "无标题"}: ${h.content || ""}</div>`).join("")}
              </div>
            ` : ""}
          </div>

          <div class="insights-briefing-card">
            <h5>📌 今天提醒</h5>
            ${briefing.today_reminders && briefing.today_reminders.length > 0 ? `
              <ul class="insights-briefing-reminders">
                ${briefing.today_reminders.map((r) => `<li>${r}</li>`).join("")}
              </ul>
            ` : '<div class="diary-insights-empty">暂无提醒</div>'}
          </div>

          <div class="insights-briefing-card">
            <h5>📅 历史上下文</h5>
            ${briefing.historical_context && briefing.historical_context.length > 0 ? `
              <ul class="insights-briefing-history">
                ${briefing.historical_context.map((h) => `<li>${h}</li>`).join("")}
              </ul>
            ` : '<div class="diary-insights-empty">暂无历史上下文</div>'}
          </div>
        </div>
      `;

      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div class="diary-insights-error">加载失败: ${e.message}</div>`;
    }
  }

  async function loadOpenLoops() {
    const container = document.getElementById("insightsLoopsContent");
    container.innerHTML = '<div class="diary-insights-loading">加载中...</div>';

    try {
      const res = await fetch("/api/diary/insights/open-loops?status=open&limit=50");
      const data = await res.json();

      if (!data.ok || !data.data) {
        container.innerHTML = '<div class="diary-insights-empty">加载失败</div>';
        return;
      }

      const loops = data.data;
      const typeNames = {
        promise: "承诺", goal: "目标", todo: "待办",
        question: "问题", idea: "想法",
      };
      const priorityColors = {
        high: "#ef4444", medium: "#f59e0b", low: "#6b7280",
      };

      let html = `
        <div class="insights-loops-header">
          <h4>🔄 未完成事项（${loops.length} 个）</h4>
          <button id="scanLoopsBtn" type="button">🔍 重新扫描</button>
        </div>
        <div class="insights-loops-list">
      `;

      if (loops.length === 0) {
        html += '<div class="diary-insights-empty">暂无未完成事项</div>';
      } else {
        for (const loop of loops) {
          const typeName = typeNames[loop.loop_type] || loop.loop_type;
          const color = priorityColors[loop.priority] || "#6b7280";
          html += `
            <div class="insights-loop-card" style="border-left-color: ${color}">
              <div class="insights-loop-header">
                <span class="insights-loop-type" style="background: ${color}">${typeName}</span>
                <span class="insights-loop-priority">${loop.priority}</span>
                <span class="insights-loop-date">${loop.created_date}</span>
              </div>
              <div class="insights-loop-content">${loop.content}</div>
              <div class="insights-loop-meta">
                提及 ${loop.mention_count} 次
                ${loop.last_mentioned_date ? `· 最后提及 ${loop.last_mentioned_date}` : ""}
              </div>
              <div class="insights-loop-actions">
                <button class="insights-loop-complete" data-id="${loop.loop_id}" type="button">✅ 完成</button>
                <button class="insights-loop-abandon" data-id="${loop.loop_id}" type="button">❌ 放弃</button>
              </div>
            </div>
          `;
        }
      }

      html += `</div>`;
      container.innerHTML = html;

      // 绑定按钮事件
      document.querySelectorAll(".insights-loop-complete").forEach((btn) => {
        btn.addEventListener("click", () => updateLoopStatus(btn.dataset.id, "completed"));
      });
      document.querySelectorAll(".insights-loop-abandon").forEach((btn) => {
        btn.addEventListener("click", () => updateLoopStatus(btn.dataset.id, "abandoned"));
      });
      const scanBtn = document.getElementById("scanLoopsBtn");
      if (scanBtn) {
        scanBtn.addEventListener("click", async () => {
          scanBtn.disabled = true;
          scanBtn.textContent = "扫描中...";
          await fetch("/api/diary/insights/open-loops/scan?lookback_days=365", { method: "POST" });
          loadOpenLoops();
        });
      }
    } catch (e) {
      container.innerHTML = `<div class="diary-insights-error">加载失败: ${e.message}</div>`;
    }
  }

  async function updateLoopStatus(loopId, status) {
    try {
      await fetch(`/api/diary/insights/open-loops/${loopId}/status?status=${status}`, { method: "PUT" });
      loadOpenLoops();
    } catch (e) {
      alert("更新失败: " + e.message);
    }
  }

  // ─── 记忆中心 ──────────────────────────────────────────────────────

  // 初始化（幂等：只执行一次，避免重复绑定事件和重复加载）
  function initMemory() {
    if (_memoryInitialized) return;
    _memoryInitialized = true;

    initMemoryButtons();
    initMemorySearch();
    loadMemoryStats();
  }

  function initMemoryButtons() {
    const updateBtn = document.getElementById("memoryUpdateTiersBtn");
    if (updateBtn) {
      updateBtn.addEventListener("click", async () => {
        updateBtn.disabled = true;
        updateBtn.textContent = "更新中...";
        await fetch("/api/diary/memory/update-tiers", { method: "POST" });
        loadMemoryStats();
        updateBtn.disabled = false;
        updateBtn.textContent = "🔄 更新分层";
      });
    }

    const compressBtn = document.getElementById("memoryCompressBtn");
    if (compressBtn) {
      compressBtn.addEventListener("click", async () => {
        compressBtn.disabled = true;
        compressBtn.textContent = "压缩中...";
        await fetch("/api/diary/memory/compress-cold", { method: "POST" });
        loadMemoryStats();
        compressBtn.disabled = false;
        compressBtn.textContent = "🗜️ 压缩Cold记忆";
      });
    }

    const maintenanceBtn = document.getElementById("memoryMaintenanceBtn");
    if (maintenanceBtn) {
      maintenanceBtn.addEventListener("click", async () => {
        maintenanceBtn.disabled = true;
        maintenanceBtn.textContent = "维护中...";
        await fetch("/api/diary/memory/maintenance", { method: "POST" });
        loadMemoryStats();
        maintenanceBtn.disabled = false;
        maintenanceBtn.textContent = "🛠️ 一键维护";
      });
    }
  }

  function initMemorySearch() {
    const searchBtn = document.getElementById("memorySearchBtn");
    if (searchBtn) {
      searchBtn.addEventListener("click", performMemorySearch);
    }
    const input = document.getElementById("memorySearchInput");
    if (input) {
      input.addEventListener("keypress", (e) => {
        if (e.key === "Enter") performMemorySearch();
      });
    }
  }

  async function performMemorySearch() {
    const query = document.getElementById("memorySearchInput").value;
    const tier = document.getElementById("memoryTierSelect").value;
    const resultsContainer = document.getElementById("memorySearchResults");

    resultsContainer.innerHTML = '<div class="diary-memory-loading">搜索中...</div>';

    try {
      let url = `/api/diary/memory/search?query=${encodeURIComponent(query)}&limit=20`;
      if (tier) url += `&tier=${tier}`;

      const res = await fetch(url);
      const data = await res.json();

      if (!data.ok || !data.data) {
        resultsContainer.innerHTML = '<div class="diary-memory-empty">搜索失败</div>';
        return;
      }

      const results = data.data;
      const tierColors = { hot: "#ef4444", warm: "#f59e0b", cold: "#3b82f6" };
      const tierNames = { hot: "🔥 Hot", warm: "☀️ Warm", cold: "❄️ Cold" };

      let html = `<p class="memory-search-count">找到 ${results.length} 个记忆</p>`;

      if (results.length === 0) {
        html += '<div class="diary-memory-empty">没有找到匹配的记忆</div>';
      } else {
        html += '<div class="memory-search-list">';
        for (const result of results) {
          const color = tierColors[result.memory_tier] || "#6b7280";
          const tierName = tierNames[result.memory_tier] || result.memory_tier;
          html += `
            <div class="memory-search-card" style="border-left-color: ${color}">
              <div class="memory-search-header">
                <span class="memory-search-tier" style="background: ${color}">${tierName}</span>
                <span class="memory-search-title">${result.title || "无标题"}</span>
                <span class="memory-search-date">${result.entry_date}</span>
              </div>
              <div class="memory-search-importance">重要性: ${(result.importance_score * 100).toFixed(0)}%</div>
              ${result.compressed_summary ? `<div class="memory-search-summary">${result.compressed_summary}</div>` : ""}
              ${result.entities && result.entities.length > 0 ? `
                <div class="memory-search-entities">
                  ${result.entities.map((e) => `<span class="memory-entity-tag">${e}</span>`).join("")}
                </div>
              ` : ""}
            </div>
          `;
        }
        html += "</div>";
      }

      resultsContainer.innerHTML = html;
    } catch (e) {
      resultsContainer.innerHTML = `<div class="diary-memory-error">搜索失败: ${e.message}</div>`;
    }
  }

  async function loadMemoryStats() {
    const container = document.getElementById("memoryStatsContent");
    container.innerHTML = '<div class="diary-memory-loading">加载中...</div>';

    try {
      const res = await fetch("/api/diary/memory/stats");
      const data = await res.json();

      if (!data.ok || !data.data) {
        container.innerHTML = '<div class="diary-memory-empty">加载失败</div>';
        return;
      }

      const stats = data.data;

      let html = `
        <div class="memory-stats-grid">
          <div class="memory-stat-card">
            <div class="memory-stat-value">${stats.total_entries}</div>
            <div class="memory-stat-label">总记忆数</div>
          </div>
          <div class="memory-stat-card hot">
            <div class="memory-stat-value">${stats.hot_count}</div>
            <div class="memory-stat-label">🔥 Hot (0-24h)</div>
          </div>
          <div class="memory-stat-card warm">
            <div class="memory-stat-value">${stats.warm_count}</div>
            <div class="memory-stat-label">☀️ Warm (1-7天)</div>
          </div>
          <div class="memory-stat-card cold">
            <div class="memory-stat-value">${stats.cold_count}</div>
            <div class="memory-stat-label">❄️ Cold (7+天)</div>
          </div>
          <div class="memory-stat-card">
            <div class="memory-stat-value">${(stats.avg_importance * 100).toFixed(1)}%</div>
            <div class="memory-stat-label">平均重要性</div>
          </div>
          <div class="memory-stat-card">
            <div class="memory-stat-value">${(stats.compression_ratio * 100).toFixed(1)}%</div>
            <div class="memory-stat-label">压缩比</div>
          </div>
        </div>
      `;

      if (stats.top_entities && stats.top_entities.length > 0) {
        html += `
          <div class="memory-top-entities">
            <h4>🏷️ 高频实体（Top 10）</h4>
            <div class="memory-entities-cloud">
              ${stats.top_entities.slice(0, 10).map((e, i) => {
                const size = 14 + (10 - i) * 2;
                return `<span class="memory-entity-cloud-item" style="font-size: ${size}px">${e.entity} (${e.count})</span>`;
              }).join("")}
            </div>
          </div>
        `;
      }

      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div class="diary-memory-error">加载失败: ${e.message}</div>`;
    }
  }

  // 暴露到全局
  window.initDiaryInsights = initInsights;
  window.initDiaryMemory = initMemory;
})();
