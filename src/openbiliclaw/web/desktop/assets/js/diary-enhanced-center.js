/**
 * 情绪中心 + 高级记忆 + 智能时间线 交互逻辑
 */

(function () {
  "use strict";

  // ─── 工具函数 ──────────────────────────────────────────────────────

  async function fetchJSON(url, options = {}) {
    try {
      const resp = await fetch(url, options);
      const data = await resp.json();
      return data;
    } catch (err) {
      console.error("Fetch error:", err);
      return { ok: false, error: String(err) };
    }
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  function formatDate(dateStr) {
    if (!dateStr) return "";
    return dateStr.substring(0, 10);
  }

  // ─── 情绪中心 ──────────────────────────────────────────────────────

  const EmotionCenter = {
    async loadStats() {
      const container = document.getElementById("emotionStatsContent");
      if (!container) return;

      container.innerHTML = '<div class="diary-emotion-loading">加载中...</div>';

      const data = await fetchJSON("/api/diary/emotion/stats");
      if (!data.ok) {
        container.innerHTML = `<div class="error">加载失败: ${escapeHtml(data.error || "未知错误")}</div>`;
        return;
      }

      const stats = data.data;
      const labels = stats.label_distribution || {};
      const labelHtml = Object.entries(labels)
        .map(([label, count]) => {
          const pct = stats.total_analyzed > 0 ? ((count / stats.total_analyzed) * 100).toFixed(1) : 0;
          return `<div class="emotion-label-item">
            <span class="emotion-label-name">${escapeHtml(label)}</span>
            <div class="emotion-label-bar"><div style="width:${pct}%"></div></div>
            <span class="emotion-label-count">${count} (${pct}%)</span>
          </div>`;
        })
        .join("");

      container.innerHTML = `
        <div class="emotion-stats-grid">
          <div class="emotion-stat-card">
            <div class="emotion-stat-value">${stats.total_analyzed || 0}</div>
            <div class="emotion-stat-label">已分析日记</div>
          </div>
          <div class="emotion-stat-card">
            <div class="emotion-stat-value">${(stats.avg_valence || 0).toFixed(3)}</div>
            <div class="emotion-stat-label">平均效价（愉悦度）</div>
          </div>
          <div class="emotion-stat-card">
            <div class="emotion-stat-value">${(stats.avg_arousal || 0).toFixed(3)}</div>
            <div class="emotion-stat-label">平均唤醒（活跃度）</div>
          </div>
          <div class="emotion-stat-card">
            <div class="emotion-stat-value">${escapeHtml(stats.dominant_emotion || "中性")}</div>
            <div class="emotion-stat-label">主导情绪</div>
          </div>
        </div>
        <div class="emotion-labels-section">
          <h4>情绪分布</h4>
          <div class="emotion-labels-list">${labelHtml}</div>
        </div>
      `;
    },

    async loadTrend() {
      const container = document.getElementById("emotionTrendChart");
      if (!container) return;

      const data = await fetchJSON("/api/diary/emotion/trend?days=30");
      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = '<div class="empty">暂无情绪趋势数据</div>';
        return;
      }

      const trend = data.data;
      const maxVal = Math.max(...trend.map((t) => Math.abs(t.valence)), 0.5);

      const chartHtml = trend
        .map((t) => {
          const valenceHeight = Math.abs(t.valence) / maxVal * 80;
          const arousalHeight = Math.abs(t.arousal) / maxVal * 80;
          const valenceColor = t.valence >= 0 ? "#4ade80" : "#f87171";
          const arousalColor = t.arousal >= 0 ? "#60a5fa" : "#fbbf24";
          return `<div class="emotion-trend-bar" title="${formatDate(t.date)}: 效价=${t.valence}, 唤醒=${t.arousal}, ${t.emotion_label}">
            <div class="emotion-trend-valence" style="height:${valenceHeight}px;background:${valenceColor}"></div>
            <div class="emotion-trend-arousal" style="height:${arousalHeight}px;background:${arousalColor}"></div>
            <div class="emotion-trend-date">${formatDate(t.date).substring(5)}</div>
          </div>`;
        })
        .join("");

      container.innerHTML = `
        <div class="emotion-trend-legend">
          <span><span class="legend-dot" style="background:#4ade80"></span>效价（愉悦度）</span>
          <span><span class="legend-dot" style="background:#60a5fa"></span>唤醒（活跃度）</span>
        </div>
        <div class="emotion-trend-chart-inner">${chartHtml}</div>
      `;
    },

    async loadForecast() {
      const container = document.getElementById("emotionForecastList");
      if (!container) return;

      const data = await fetchJSON("/api/diary/emotion/forecast?days=7");
      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = '<div class="empty">暂无情绪预测数据</div>';
        return;
      }

      const forecast = data.data;
      const trendText = { improving: "📈 上升", declining: "📉 下降", stable: "➡️ 稳定" }[forecast[0].trend] || "➡️ 稳定";

      container.innerHTML = `
        <div class="emotion-forecast-trend">整体趋势: ${trendText}</div>
        <div class="emotion-forecast-grid">
          ${forecast
            .map(
              (f) => `
            <div class="emotion-forecast-card">
              <div class="emotion-forecast-date">${formatDate(f.forecast_date)}</div>
              <div class="emotion-forecast-emotion">${escapeHtml(f.predicted_emotion)}</div>
              <div class="emotion-forecast-values">
                <span>效价: ${f.predicted_valence.toFixed(2)}</span>
                <span>唤醒: ${f.predicted_arousal.toFixed(2)}</span>
              </div>
              <div class="emotion-forecast-confidence">置信度: ${(f.confidence * 100).toFixed(0)}%</div>
            </div>
          `
            )
            .join("")}
        </div>
      `;
    },

    async loadBurnout() {
      const container = document.getElementById("emotionBurnoutContent");
      if (!container) return;

      const data = await fetchJSON("/api/diary/emotion/burnout?days=30");
      if (!data.ok) {
        container.innerHTML = `<div class="error">加载失败: ${escapeHtml(data.error || "未知错误")}</div>`;
        return;
      }

      const b = data.data;
      const levelColors = { healthy: "#4ade80", mild: "#fbbf24", moderate: "#f97316", severe: "#ef4444" };
      const levelText = { healthy: "健康", mild: "轻度", moderate: "中度", severe: "重度" };
      const color = levelColors[b.level] || "#94a3b8";

      const warningsHtml = (b.warnings || []).map((w) => `<li>⚠️ ${escapeHtml(w)}</li>`).join("");
      const recsHtml = (b.recommendations || []).map((r) => `<li>💡 ${escapeHtml(r)}</li>`).join("");

      container.innerHTML = `
        <div class="burnout-overview">
          <div class="burnout-score" style="color:${color}">${b.overall_score.toFixed(1)}</div>
          <div class="burnout-level" style="color:${color}">${levelText[b.level] || b.level}</div>
          <div class="burnout-label">倦怠指数（0-100）</div>
        </div>
        <div class="burnout-dimensions">
          <div class="burnout-dim">
            <span class="burnout-dim-name">情绪耗竭</span>
            <div class="burnout-dim-bar"><div style="width:${b.emotional_exhaustion}%;background:#ef4444"></div></div>
            <span class="burnout-dim-value">${b.emotional_exhaustion.toFixed(0)}</span>
          </div>
          <div class="burnout-dim">
            <span class="burnout-dim-name">去人格化</span>
            <div class="burnout-dim-bar"><div style="width:${b.depersonalization}%;background:#f97316"></div></div>
            <span class="burnout-dim-value">${b.depersonalization.toFixed(0)}</span>
          </div>
          <div class="burnout-dim">
            <span class="burnout-dim-name">成就感降低</span>
            <div class="burnout-dim-bar"><div style="width:${b.reduced_accomplishment}%;background:#fbbf24"></div></div>
            <span class="burnout-dim-value">${b.reduced_accomplishment.toFixed(0)}</span>
          </div>
          <div class="burnout-dim">
            <span class="burnout-dim-name">写作一致性</span>
            <div class="burnout-dim-bar"><div style="width:${b.writing_consistency}%;background:#60a5fa"></div></div>
            <span class="burnout-dim-value">${b.writing_consistency.toFixed(0)}</span>
          </div>
        </div>
        ${warningsHtml ? `<div class="burnout-warnings"><h5>⚠️ 警告</h5><ul>${warningsHtml}</ul></div>` : ""}
        ${recsHtml ? `<div class="burnout-recommendations"><h5>💡 建议</h5><ul>${recsHtml}</ul></div>` : ""}
      `;
    },

    async analyzeAll() {
      const btn = document.getElementById("emotionAnalyzeAllBtn");
      if (btn) btn.disabled = true;

      const data = await fetchJSON("/api/diary/emotion/analyze-all", { method: "POST" });
      if (data.ok) {
        alert(`✅ 已分析 ${data.data.analyzed} 篇日记的情绪`);
        this.loadAll();
      } else {
        alert(`❌ 分析失败: ${data.error || "未知错误"}`);
      }

      if (btn) btn.disabled = false;
    },

    loadAll() {
      this.loadStats();
      this.loadTrend();
      this.loadForecast();
      this.loadBurnout();
    },
  };

  // ─── 高级记忆 ──────────────────────────────────────────────────────

  const AdvancedMemory = {
    layerNames: {
      episodic: "情景记忆",
      semantic_self: "语义自我",
      beliefs: "信念",
      relationships: "关系",
      narrative: "叙事状态",
      diary: "日记摘要",
    },

    async loadOverview() {
      const container = document.getElementById("advancedMemoryStatsContent");
      if (!container) return;

      container.innerHTML = '<div class="diary-advanced-memory-loading">加载中...</div>';

      const data = await fetchJSON("/api/diary/advanced-memory/overview");
      if (!data.ok) {
        container.innerHTML = `<div class="error">加载失败: ${escapeHtml(data.error || "未知错误")}</div>`;
        return;
      }

      const overview = data.data;
      const layers = overview.layers || [];
      const maxCount = Math.max(...layers.map((l) => l.count), 1);

      const layersHtml = layers
        .map((l) => {
          const name = this.layerNames[l.layer] || l.layer;
          const pct = (l.count / maxCount) * 100;
          return `<div class="am-layer-item">
            <span class="am-layer-name">${escapeHtml(name)}</span>
            <div class="am-layer-bar"><div style="width:${pct}%"></div></div>
            <span class="am-layer-count">${l.count} 条</span>
            <span class="am-layer-importance">重要性: ${(l.avg_importance || 0).toFixed(2)}</span>
          </div>`;
        })
        .join("");

      container.innerHTML = `
        <div class="am-stats-grid">
          <div class="am-stat-card">
            <div class="am-stat-value">${overview.total_memories || 0}</div>
            <div class="am-stat-label">总记忆数</div>
          </div>
          <div class="am-stat-card">
            <div class="am-stat-value">${overview.total_beliefs || 0}</div>
            <div class="am-stat-label">核心信念</div>
          </div>
          <div class="am-stat-card">
            <div class="am-stat-value">${overview.total_conflicts || 0}</div>
            <div class="am-stat-label">信念冲突</div>
          </div>
          <div class="am-stat-card">
            <div class="am-stat-value">6</div>
            <div class="am-stat-label">记忆层数</div>
          </div>
        </div>
        <div class="am-layers-section">
          <h4>记忆层分布</h4>
          <div class="am-layers-list">${layersHtml}</div>
        </div>
      `;
    },

    async loadBeliefs() {
      const container = document.getElementById("advancedMemoryBeliefsList");
      if (!container) return;

      const data = await fetchJSON("/api/diary/advanced-memory/beliefs");
      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = '<div class="empty">暂无信念数据，点击"构建6层记忆"按钮开始</div>';
        return;
      }

      const beliefs = data.data.slice(0, 20);
      container.innerHTML = beliefs
        .map(
          (b) => `
        <div class="am-belief-item">
          <div class="am-belief-content">${escapeHtml(b.content)}</div>
          <div class="am-belief-meta">
            <span class="am-belief-category">[${escapeHtml(b.category)}]</span>
            <span class="am-belief-confidence">置信度: ${(b.confidence || 0).toFixed(2)}</span>
            <span class="am-belief-evidence">证据: ${b.evidence_count || 0} 次</span>
          </div>
        </div>
      `
        )
        .join("");
    },

    async search() {
      const query = document.getElementById("advancedMemorySearchInput")?.value || "";
      const layer = document.getElementById("advancedMemoryLayerSelect")?.value || "";
      const container = document.getElementById("advancedMemorySearchResults");
      if (!container) return;

      let url = `/api/diary/advanced-memory/search?limit=20`;
      if (query) url += `&query=${encodeURIComponent(query)}`;
      if (layer) url += `&layer=${encodeURIComponent(layer)}`;

      const data = await fetchJSON(url);
      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = '<div class="empty">未找到匹配的记忆</div>';
        return;
      }

      container.innerHTML = data.data
        .map(
          (m) => `
        <div class="am-search-result">
          <div class="am-search-header">
            <span class="am-search-layer">[${escapeHtml(m.layer_name || m.layer)}]</span>
            <span class="am-search-importance">重要性: ${(m.importance || 0).toFixed(2)}</span>
            <span class="am-search-date">${formatDate(m.created_at)}</span>
          </div>
          <div class="am-search-content">${escapeHtml(m.content)}</div>
        </div>
      `
        )
        .join("");
    },

    async build() {
      const btn = document.getElementById("advancedMemoryBuildBtn");
      if (btn) btn.disabled = true;

      const data = await fetchJSON("/api/diary/advanced-memory/build", { method: "POST" });
      if (data.ok) {
        const result = data.data;
        const summary = Object.entries(result).map(([k, v]) => `${this.layerNames[k] || k}: ${v}`).join(", ");
        alert(`✅ 6层记忆构建完成\n${summary}`);
        this.loadAll();
      } else {
        alert(`❌ 构建失败: ${data.error || "未知错误"}`);
      }

      if (btn) btn.disabled = false;
    },

    async consolidate() {
      const data = await fetchJSON("/api/diary/advanced-memory/consolidate", { method: "POST" });
      if (data.ok) {
        const r = data.data;
        alert(`✅ 记忆巩固完成\n处理: ${r.entries_processed}\n提升: ${r.entries_promoted}\n降级: ${r.entries_demoted}\n遗忘: ${r.entries_forgotten}\n冲突检测: ${r.conflicts_detected}`);
        this.loadOverview();
      } else {
        alert(`❌ 巩固失败: ${data.error || "未知错误"}`);
      }
    },

    async dreamReview() {
      const data = await fetchJSON("/api/diary/advanced-memory/dream-review", { method: "POST" });
      if (data.ok) {
        const r = data.data;
        alert(`🌙 梦境回顾完成\n回顾教训: ${r.lessons_reviewed}\n验证通过: ${r.lessons_validated}\n标记争议: ${r.lessons_disputed}\n记忆健康评分: ${r.memory_health_score}/100`);
        this.loadOverview();
      } else {
        alert(`❌ 梦境回顾失败: ${data.error || "未知错误"}`);
      }
    },

    loadAll() {
      this.loadOverview();
      this.loadBeliefs();
    },
  };

  // ─── 智能时间线 ────────────────────────────────────────────────────

  const Timeline = {
    typeNames: {
      event: "事件",
      person: "人物",
      place: "地点",
      task: "任务",
      metric: "指标",
      article: "文章",
      emotion: "情绪",
      milestone: "里程碑",
    },

    typeColors: {
      event: "#60a5fa",
      person: "#f472b6",
      place: "#34d399",
      task: "#fbbf24",
      metric: "#a78bfa",
      article: "#fb923c",
      emotion: "#f87171",
      milestone: "#ef4444",
    },

    async loadStats() {
      const container = document.getElementById("timelineStatsContent");
      if (!container) return;

      container.innerHTML = '<div class="diary-timeline-loading">加载中...</div>';

      const data = await fetchJSON("/api/diary/timeline/stats");
      if (!data.ok) {
        container.innerHTML = `<div class="error">加载失败: ${escapeHtml(data.error || "未知错误")}</div>`;
        return;
      }

      const stats = data.data;
      const types = stats.type_distribution || {};
      const maxCount = Math.max(...Object.values(types), 1);

      const typesHtml = Object.entries(types)
        .map(([type, count]) => {
          const name = this.typeNames[type] || type;
          const color = this.typeColors[type] || "#94a3b8";
          const pct = (count / maxCount) * 100;
          return `<div class="tl-type-item">
            <span class="tl-type-name" style="color:${color}">${escapeHtml(name)}</span>
            <div class="tl-type-bar"><div style="width:${pct}%;background:${color}"></div></div>
            <span class="tl-type-count">${count}</span>
          </div>`;
        })
        .join("");

      const entitiesHtml = (stats.top_entities || [])
        .slice(0, 10)
        .map(([entity, count]) => `<span class="tl-entity-tag">${escapeHtml(entity)} (${count})</span>`)
        .join("");

      container.innerHTML = `
        <div class="tl-stats-grid">
          <div class="tl-stat-card">
            <div class="tl-stat-value">${stats.total_cards || 0}</div>
            <div class="tl-stat-label">总卡片数</div>
          </div>
          <div class="tl-stat-card">
            <div class="tl-stat-value">${Object.keys(types).length}</div>
            <div class="tl-stat-label">卡片类型</div>
          </div>
          <div class="tl-stat-card">
            <div class="tl-stat-value">${formatDate(stats.date_range?.[0] || "")}</div>
            <div class="tl-stat-label">最早日期</div>
          </div>
          <div class="tl-stat-card">
            <div class="tl-stat-value">${formatDate(stats.date_range?.[1] || "")}</div>
            <div class="tl-stat-label">最新日期</div>
          </div>
        </div>
        <div class="tl-types-section">
          <h4>卡片类型分布</h4>
          <div class="tl-types-list">${typesHtml}</div>
        </div>
        <div class="tl-entities-section">
          <h4>高频实体</h4>
          <div class="tl-entities-list">${entitiesHtml}</div>
        </div>
      `;
    },

    async loadMilestones() {
      const container = document.getElementById("timelineMilestonesList");
      if (!container) return;

      const data = await fetchJSON("/api/diary/timeline/milestones?limit=15");
      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = '<div class="empty">暂无里程碑卡片</div>';
        return;
      }

      container.innerHTML = data.data
        .map(
          (m) => `
        <div class="tl-milestone-item">
          <div class="tl-milestone-date">${formatDate(m.entry_date)}</div>
          <div class="tl-milestone-importance">⭐ ${(m.importance || 0).toFixed(2)}</div>
          <div class="tl-milestone-title">${escapeHtml(m.title)}</div>
          <div class="tl-milestone-content">${escapeHtml(m.content || "").substring(0, 100)}...</div>
        </div>
      `
        )
        .join("");
    },

    async loadCards() {
      const cardType = document.getElementById("timelineTypeSelect")?.value || "";
      const entity = document.getElementById("timelineEntityInput")?.value || "";
      const minImportance = parseFloat(document.getElementById("timelineMinImportance")?.value || "0");
      const container = document.getElementById("timelineCardsList");
      if (!container) return;

      let url = `/api/diary/timeline/cards?limit=50&min_importance=${minImportance}`;
      if (cardType) url += `&card_type=${encodeURIComponent(cardType)}`;
      if (entity) url += `&entity=${encodeURIComponent(entity)}`;

      const data = await fetchJSON(url);
      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = '<div class="empty">未找到匹配的卡片</div>';
        return;
      }

      container.innerHTML = data.data
        .map((c) => {
          const typeName = this.typeNames[c.card_type] || c.card_type;
          const typeColor = this.typeColors[c.card_type] || "#94a3b8";
          const entitiesHtml = (c.entities || [])
            .map((e) => `<span class="tl-card-entity">${escapeHtml(e)}</span>`)
            .join("");
          return `
        <div class="tl-card-item">
          <div class="tl-card-header">
            <span class="tl-card-type" style="background:${typeColor}">${escapeHtml(typeName)}</span>
            <span class="tl-card-date">${formatDate(c.entry_date)}</span>
            <span class="tl-card-importance">⭐ ${(c.importance || 0).toFixed(2)}</span>
          </div>
          <div class="tl-card-title">${escapeHtml(c.title)}</div>
          <div class="tl-card-content">${escapeHtml(c.content || "")}</div>
          ${entitiesHtml ? `<div class="tl-card-entities">${entitiesHtml}</div>` : ""}
        </div>
      `;
        })
        .join("");
    },

    async generateAll() {
      const btn = document.getElementById("timelineGenerateBtn");
      if (btn) btn.disabled = true;

      const data = await fetchJSON("/api/diary/timeline/generate-all", { method: "POST" });
      if (data.ok) {
        alert(`✅ 已生成 ${data.data.total_cards} 张时间线卡片`);
        this.loadAll();
      } else {
        alert(`❌ 生成失败: ${data.error || "未知错误"}`);
      }

      if (btn) btn.disabled = false;
    },

    loadAll() {
      this.loadStats();
      this.loadMilestones();
      this.loadCards();
    },
  };

  // ─── 初始化 ────────────────────────────────────────────────────────

  function init() {
    // 情绪中心按钮
    document.getElementById("emotionAnalyzeAllBtn")?.addEventListener("click", () => EmotionCenter.analyzeAll());
    document.getElementById("emotionRefreshBtn")?.addEventListener("click", () => EmotionCenter.loadAll());

    // 高级记忆按钮
    document.getElementById("advancedMemoryBuildBtn")?.addEventListener("click", () => AdvancedMemory.build());
    document.getElementById("advancedMemoryConsolidateBtn")?.addEventListener("click", () => AdvancedMemory.consolidate());
    document.getElementById("advancedMemoryDreamBtn")?.addEventListener("click", () => AdvancedMemory.dreamReview());
    document.getElementById("advancedMemorySearchBtn")?.addEventListener("click", () => AdvancedMemory.search());

    // 时间线按钮
    document.getElementById("timelineGenerateBtn")?.addEventListener("click", () => Timeline.generateAll());
    document.getElementById("timelineRefreshBtn")?.addEventListener("click", () => Timeline.loadAll());
    document.getElementById("timelineFilterBtn")?.addEventListener("click", () => Timeline.loadCards());
  }

  // 暴露给全局，供 diary-insights.js 调用
  window.DiaryEnhancedCenter = {
    EmotionCenter,
    AdvancedMemory,
    Timeline,
    init,
  };

  // 由日记页面动态加载后手动初始化
  window.__initDiaryEnhancedCenter = init;
})();
