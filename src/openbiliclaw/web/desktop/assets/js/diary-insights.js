/**
 * 日记数据洞察与随手记功能
 * 包含：子Tab切换、情绪趋势图、字数趋势图、年度报告、词云、随手记
 */
(function () {
  "use strict";

  const API_BASE = "/api/diary/insights";
  const FRAGMENTS_KEY = "diary_fragments";

  // ─── 初始化（由日记页面动态加载后手动调用）───
  window.__initDiaryInsights = init;

  function init() {
    initSubtabs();
    initInsightsControls();
    initFragments();
  }

  // ═══════════════════════════════════════════
  // 子 Tab 切换
  // ═══════════════════════════════════════════
  function initSubtabs() {
    const tabs = document.querySelectorAll(".diary-subtab");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const view = tab.dataset.view;
        switchDiaryView(view);
      });
    });
  }

  function switchDiaryView(view) {
    // 更新 tab 状态
    document.querySelectorAll(".diary-subtab").forEach((t) => {
      t.classList.toggle("active", t.dataset.view === view);
    });
    // 切换视图
    document.getElementById("diaryListView").hidden = view !== "list";
    document.getElementById("diaryInsightsView").hidden = view !== "insights";
    document.getElementById("diaryPeopleView").hidden = view !== "people";
    document.getElementById("diaryFragmentsView").hidden = view !== "fragments";
    // 反思回顾视图
    const reflectionView = document.getElementById("diaryReflectionView");
    if (reflectionView) reflectionView.hidden = view !== "reflection";
    // 知识网络视图
    const knowledgeView = document.getElementById("diaryKnowledgeView");
    if (knowledgeView) knowledgeView.hidden = view !== "knowledge";
    // 自进化中心视图
    const seView = document.getElementById("diarySelfEvolutionView");
    if (seView) seView.hidden = view !== "self-evolution";
    // 洞察中心视图
    const insightsView = document.getElementById("diaryInsightsView");
    if (insightsView) insightsView.hidden = view !== "insights";
    // 记忆中心视图
    const memoryView = document.getElementById("diaryMemoryView");
    if (memoryView) memoryView.hidden = view !== "memory";
    // 情绪中心视图
    const emotionView = document.getElementById("diaryEmotionView");
    if (emotionView) emotionView.hidden = view !== "emotion";
    // 高级记忆视图
    const advancedMemoryView = document.getElementById("diaryAdvancedMemoryView");
    if (advancedMemoryView) advancedMemoryView.hidden = view !== "advanced-memory";
    // 时间线视图
    const timelineView = document.getElementById("diaryTimelineView");
    if (timelineView) timelineView.hidden = view !== "timeline";
    // 语义搜索和日记对话视图
    const semanticView = document.getElementById("diarySemanticView");
    const chatView = document.getElementById("diaryChatView");
    if (semanticView) semanticView.hidden = view !== "semantic";
    if (chatView) chatView.hidden = view !== "chat";

    // 触发自定义事件，让其他模块（如 diary-people.js）能监听到
    document.dispatchEvent(new CustomEvent("diary-view-change", { detail: { view } }));

    // 进入洞察视图时加载数据
    if (view === "insights") {
      loadAllInsights();
    }
    // 进入随手记视图时加载碎片
    if (view === "fragments") {
      renderFragments();
    }
    // 进入反思回顾视图时初始化
    if (view === "reflection") {
      if (typeof window.initDiaryReflection === "function") {
        window.initDiaryReflection();
      }
    }
    // 进入知识网络视图时初始化
    if (view === "knowledge") {
      if (typeof window.initDiaryKnowledge === "function") {
        window.initDiaryKnowledge();
      }
    }
    // 进入自进化中心视图时初始化
    if (view === "self-evolution") {
      if (typeof window.initDiarySelfEvolution === "function") {
        window.initDiarySelfEvolution();
      }
    }
    // 进入洞察中心视图时初始化
    if (view === "insights") {
      if (typeof window.initDiaryInsights === "function") {
        window.initDiaryInsights();
      }
    }
    // 进入记忆中心视图时初始化
    if (view === "memory") {
      if (typeof window.initDiaryMemory === "function") {
        window.initDiaryMemory();
      }
    }
    // 进入情绪中心视图时初始化
    if (view === "emotion") {
      if (window.DiaryEnhancedCenter?.EmotionCenter) {
        window.DiaryEnhancedCenter.EmotionCenter.loadAll();
      }
    }
    // 进入高级记忆视图时初始化
    if (view === "advanced-memory") {
      if (window.DiaryEnhancedCenter?.AdvancedMemory) {
        window.DiaryEnhancedCenter.AdvancedMemory.loadAll();
      }
    }
    // 进入时间线视图时初始化
    if (view === "timeline") {
      if (window.DiaryEnhancedCenter?.Timeline) {
        window.DiaryEnhancedCenter.Timeline.loadAll();
      }
    }
  }

  // ═══════════════════════════════════════════
  // 数据洞察
  // ═══════════════════════════════════════════
  let moodGranularity = "month";
  let wordGranularity = "year";

  function initInsightsControls() {
    // 情绪趋势粒度切换
    document.querySelectorAll("[data-mood-gran]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-mood-gran]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        moodGranularity = btn.dataset.moodGran;
        loadMoodTrend();
      });
    });
    // 字数趋势粒度切换
    document.querySelectorAll("[data-word-gran]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-word-gran]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        wordGranularity = btn.dataset.wordGran;
        loadWordTrend();
      });
    });
    // 生成年度报告
    const genBtn = document.getElementById("generateYearReportBtn");
    if (genBtn) {
      genBtn.addEventListener("click", generateYearReport);
    }
  }

  async function loadAllInsights() {
    loadStreak();
    loadMoodTrend();
    loadWordTrend();
    loadKeywords();
    loadYearStats();
  }

  // ─── 连续打卡 ───
  async function loadStreak() {
    try {
      const res = await fetch(`${API_BASE}/streak`);
      const data = await res.json();
      if (data.ok) {
        document.getElementById("streakCurrent").textContent = data.current_streak + "天";
        document.getElementById("streakLongest").textContent = data.longest_streak + "天";
        document.getElementById("streakTotal").textContent = data.total_days + "天";
        document.getElementById("streakWeek").textContent = data.this_week_count + "天";
        document.getElementById("streakMonth").textContent = data.this_month_count + "天";
      }
    } catch (e) {
      console.error("加载连续打卡失败", e);
    }
  }

  // ─── 情绪趋势（SVG 折线图） ───
  async function loadMoodTrend() {
    try {
      const res = await fetch(`${API_BASE}/mood-trend?granularity=${moodGranularity}`);
      const data = await res.json();
      if (data.ok && data.data.length > 0) {
        renderLineChart(
          "moodTrendChart",
          data.data.map((d) => ({ label: d.period, value: d.avg_score, count: d.entry_count })),
          {
            yMin: -1,
            yMax: 1,
            color: "#8b5cf6",
            fillColor: "rgba(139,92,246,0.1)",
            yLabel: "情绪分",
            zeroLine: true,
          }
        );
      } else {
        document.getElementById("moodTrendChart").innerHTML =
          '<div style="text-align:center;padding:60px;color:#9ca3af;">暂无情绪数据</div>';
      }
    } catch (e) {
      console.error("加载情绪趋势失败", e);
    }
  }

  // ─── 字数趋势（CSS 柱状图） ───
  async function loadWordTrend() {
    try {
      const res = await fetch(`${API_BASE}/word-trend?granularity=${wordGranularity}`);
      const data = await res.json();
      if (data.ok && data.data.length > 0) {
        renderBarChart("wordTrendChart", data.data);
      } else {
        document.getElementById("wordTrendChart").innerHTML =
          '<div style="text-align:center;padding:60px;color:#9ca3af;">暂无数据</div>';
      }
    } catch (e) {
      console.error("加载字数趋势失败", e);
    }
  }

  // ─── 高频关键词（词云） ───
  async function loadKeywords() {
    try {
      const res = await fetch(`${API_BASE}/keywords?limit=60`);
      const data = await res.json();
      if (data.ok && data.data.length > 0) {
        renderWordCloud("diaryWordCloud", data.data);
      }
    } catch (e) {
      console.error("加载关键词失败", e);
    }
  }

  // ─── 年度统计 ───
  async function loadYearStats() {
    const year = document.getElementById("yearReportSelect").value;
    try {
      const res = await fetch(`${API_BASE}/yearly/${year}`);
      const data = await res.json();
      if (data.ok && data.data.entry_count > 0) {
        const s = data.data;
        document.getElementById("diaryYearStats").innerHTML = `
          <div class="diary-year-stat"><div class="diary-year-stat-val">${s.entry_count}</div><div class="diary-year-stat-label">日记篇数</div></div>
          <div class="diary-year-stat"><div class="diary-year-stat-val">${(s.total_words / 10000).toFixed(1)}w</div><div class="diary-year-stat-label">总字数</div></div>
          <div class="diary-year-stat"><div class="diary-year-stat-val">${s.avg_words}</div><div class="diary-year-stat-label">平均字数</div></div>
          <div class="diary-year-stat"><div class="diary-year-stat-val">${s.avg_mood_score > 0 ? "😊 " + s.avg_mood_score : "😔 " + s.avg_mood_score}</div><div class="diary-year-stat-label">平均情绪</div></div>
        `;
        // 隐藏之前的报告
        document.getElementById("diaryYearReport").hidden = true;
      } else {
        document.getElementById("diaryYearStats").innerHTML =
          '<div style="grid-column:1/-1;text-align:center;padding:20px;color:#9ca3af;">该年度暂无日记</div>';
      }
    } catch (e) {
      console.error("加载年度统计失败", e);
    }
  }

  // ─── 生成年度报告（LLM） ───
  async function generateYearReport() {
    const year = document.getElementById("yearReportSelect").value;
    const reportEl = document.getElementById("diaryYearReport");
    const loadingEl = document.getElementById("diaryReportLoading");
    const contentEl = document.getElementById("diaryReportContent");

    reportEl.hidden = false;
    loadingEl.style.display = "block";
    contentEl.innerHTML = "";

    try {
      const res = await fetch(`${API_BASE}/yearly/${year}/generate`, { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        loadingEl.style.display = "none";
        // 简单的 markdown 渲染（处理标题和段落）
        let html = data.report
          .replace(/^### (.*$)/gm, "<h4>$1</h4>")
          .replace(/^## (.*$)/gm, "<h4>$1</h4>")
          .replace(/^# (.*$)/gm, "<h3>$1</h3>")
          .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
          .replace(/\n\n/g, "</p><p>")
          .replace(/\n/g, "<br>");
        contentEl.innerHTML = "<p>" + html + "</p>";
      } else {
        loadingEl.style.display = "none";
        contentEl.innerHTML = `<div style="color:#ef4444;padding:20px;">生成失败：${data.error || "未知错误"}</div>`;
      }
    } catch (e) {
      loadingEl.style.display = "none";
      contentEl.innerHTML = `<div style="color:#ef4444;padding:20px;">生成失败：${e.message}</div>`;
    }
  }

  // ═══════════════════════════════════════════
  // 图表渲染
  // ═══════════════════════════════════════════

  /**
   * SVG 折线图
   */
  function renderLineChart(containerId, data, opts) {
    const container = document.getElementById(containerId);
    if (!container || data.length === 0) return;

    const width = container.clientWidth || 600;
    const height = container.clientHeight || 280;
    const padL = 50, padR = 20, padT = 20, padB = 40;
    const chartW = width - padL - padR;
    const chartH = height - padT - padB;

    const yMin = opts.yMin ?? Math.min(...data.map((d) => d.value));
    const yMax = opts.yMax ?? Math.max(...data.map((d) => d.value));
    const yRange = yMax - yMin || 1;

    const xStep = data.length > 1 ? chartW / (data.length - 1) : 0;
    const points = data.map((d, i) => ({
      x: padL + i * xStep,
      y: padT + chartH - ((d.value - yMin) / yRange) * chartH,
      ...d,
    }));

    // 生成路径
    let pathD = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
    let areaD = pathD + ` L${points[points.length - 1].x},${padT + chartH} L${points[0].x},${padT + chartH} Z`;

    // Y 轴刻度
    const yTicks = [];
    const tickCount = 5;
    for (let i = 0; i <= tickCount; i++) {
      const val = yMin + (yRange * i) / tickCount;
      const y = padT + chartH - (i / tickCount) * chartH;
      yTicks.push({ val: val.toFixed(1), y });
    }

    // X 轴标签（最多显示 12 个）
    const labelStep = Math.ceil(data.length / 12);
    const xLabels = points
      .filter((_, i) => i % labelStep === 0 || i === points.length - 1)
      .map((p) => ({ label: p.label, x: p.x }));

    let svg = `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;

    // 零轴
    if (opts.zeroLine && yMin < 0 && yMax > 0) {
      const zeroY = padT + chartH - ((0 - yMin) / yRange) * chartH;
      svg += `<line x1="${padL}" y1="${zeroY}" x2="${width - padR}" y2="${zeroY}" stroke="#d1d5db" stroke-dasharray="4,4"/>`;
    }

    // Y 轴网格和标签
    yTicks.forEach((t) => {
      svg += `<line x1="${padL}" y1="${t.y}" x2="${width - padR}" y2="${t.y}" stroke="#f3f4f6"/>`;
      svg += `<text x="${padL - 8}" y="${t.y + 4}" text-anchor="end" font-size="10" fill="#9ca3af">${t.val}</text>`;
    });

    // 区域填充
    svg += `<path d="${areaD}" fill="${opts.fillColor || "rgba(99,102,241,0.1)"}"/>`;
    // 折线
    svg += `<path d="${pathD}" fill="none" stroke="${opts.color || "#6366f1"}" stroke-width="2" stroke-linejoin="round"/>`;

    // 数据点
    points.forEach((p) => {
      svg += `<circle cx="${p.x}" cy="${p.y}" r="3" fill="${opts.color || "#6366f1"}" class="chart-dot" data-label="${p.label}" data-value="${p.value}" data-count="${p.count || ""}"/>`;
    });

    // X 轴标签
    xLabels.forEach((l) => {
      svg += `<text x="${l.x}" y="${height - 10}" text-anchor="middle" font-size="10" fill="#9ca3af">${l.label}</text>`;
    });

    svg += `</svg>`;
    svg += `<div class="diary-line-tooltip" id="${containerId}Tooltip"></div>`;

    container.innerHTML = svg;

    // 悬浮提示
    const tooltip = document.getElementById(`${containerId}Tooltip`);
    container.querySelectorAll(".chart-dot").forEach((dot) => {
      dot.addEventListener("mouseenter", (e) => {
        const rect = container.getBoundingClientRect();
        tooltip.style.left = e.clientX - rect.left + 10 + "px";
        tooltip.style.top = e.clientY - rect.top - 30 + "px";
        tooltip.style.opacity = "1";
        const count = dot.dataset.count ? ` (${dot.dataset.count}篇)` : "";
        tooltip.textContent = `${dot.dataset.label}: 情绪分 ${dot.dataset.value}${count}`;
      });
      dot.addEventListener("mouseleave", () => {
        tooltip.style.opacity = "0";
      });
    });
  }

  /**
   * CSS 柱状图
   */
  function renderBarChart(containerId, data) {
    const container = document.getElementById(containerId);
    if (!container || data.length === 0) return;

    const maxVal = Math.max(...data.map((d) => d.total_words));
    const labelStep = Math.ceil(data.length / 15);

    let html = '<div class="diary-bar-chart">';
    data.forEach((d, i) => {
      const heightPct = maxVal > 0 ? (d.total_words / maxVal) * 100 : 0;
      const showLabel = i % labelStep === 0 || i === data.length - 1;
      html += `
        <div class="diary-bar-item" title="${d.period}: ${d.total_words}字, ${d.entry_count}篇, 平均${d.avg_words}字">
          <div class="diary-bar-fill" style="height:${heightPct}%"></div>
          ${showLabel ? `<div class="diary-bar-label">${d.period}</div>` : ""}
        </div>`;
    });
    html += "</div>";
    container.innerHTML = html;
  }

  /**
   * 词云
   */
  function renderWordCloud(containerId, data) {
    const container = document.getElementById(containerId);
    if (!container || data.length === 0) return;

    const maxCount = Math.max(...data.map((d) => d.count));
    const minCount = Math.min(...data.map((d) => d.count));
    const colors = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#22c55e", "#06b6d4", "#ef4444"];

    let html = "";
    data.forEach((d, i) => {
      const ratio = maxCount > minCount ? (d.count - minCount) / (maxCount - minCount) : 0.5;
      const fontSize = 12 + ratio * 24;
      const color = colors[i % colors.length];
      const opacity = 0.6 + ratio * 0.4;
      html += `<span class="diary-wordcloud-item" style="font-size:${fontSize}px;color:${color};opacity:${opacity};" title="${d.word}: ${d.count}次">${d.word}</span>`;
    });
    container.innerHTML = html;
  }

  // ═══════════════════════════════════════════
  // 随手记（碎片）— 使用后端 API
  // ═══════════════════════════════════════════
  function initFragments() {
    const addBtn = document.getElementById("fragmentAddBtn");
    if (addBtn) {
      addBtn.addEventListener("click", addFragment);
    }
    const genBtn = document.getElementById("generateDiaryFromFragmentsBtn");
    if (genBtn) {
      genBtn.addEventListener("click", generateDiaryFromFragments);
    }
    const autoTagBtn = document.getElementById("fragmentAutoTagBtn");
    if (autoTagBtn) {
      autoTagBtn.addEventListener("click", autoTagCurrentFragment);
    }
    const batchAutoTagBtn = document.getElementById("batchAutoTagBtn");
    if (batchAutoTagBtn) {
      batchAutoTagBtn.addEventListener("click", batchAutoTagFragments);
    }
    // 碎片类型切换
    const typeSelect = document.getElementById("fragmentType");
    if (typeSelect) {
      typeSelect.addEventListener("change", () => {
        const mediaDescInput = document.getElementById("fragmentMediaDesc");
        if (mediaDescInput) {
          if (typeSelect.value === "image" || typeSelect.value === "voice") {
            mediaDescInput.style.display = "block";
          } else {
            mediaDescInput.style.display = "none";
          }
        }
      });
    }
    // 回车快捷添加
    const input = document.getElementById("fragmentInput");
    if (input) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
          addFragment();
        }
      });
    }
  }

  async function loadFragments() {
    try {
      const today = new Date().toISOString().split("T")[0];
      const res = await fetch(`/api/diary/fragments?fragment_date=${today}&limit=200`);
      const data = await res.json();
      return data.ok ? data.data : [];
    } catch (e) {
      console.error("加载碎片失败", e);
      return [];
    }
  }

  async function addFragment() {
    const input = document.getElementById("fragmentInput");
    const moodSelect = document.getElementById("fragmentMood");
    const typeSelect = document.getElementById("fragmentType");
    const mediaDescInput = document.getElementById("fragmentMediaDesc");
    const text = input.value.trim();
    if (!text) {
      input.focus();
      return;
    }
    const addBtn = document.getElementById("fragmentAddBtn");
    addBtn.disabled = true;
    addBtn.textContent = "添加中...";

    try {
      const res = await fetch("/api/diary/fragments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          mood: moodSelect.value || "unknown",
          source: "web",
          fragment_type: typeSelect ? typeSelect.value : "text",
          media_description: mediaDescInput ? mediaDescInput.value.trim() : "",
        }),
      });
      const data = await res.json();
      if (data.ok) {
        input.value = "";
        moodSelect.value = "";
        if (mediaDescInput) mediaDescInput.value = "";
        await renderFragments();
      } else {
        alert("添加失败：" + (data.error || "未知错误"));
      }
    } catch (e) {
      alert("添加失败：" + e.message);
    } finally {
      addBtn.disabled = false;
      addBtn.textContent = "➕ 添加碎片";
    }
  }

  async function autoTagCurrentFragment() {
    const input = document.getElementById("fragmentInput");
    const text = input.value.trim();
    if (!text) {
      alert("请先输入碎片内容");
      return;
    }
    const btn = document.getElementById("fragmentAutoTagBtn");
    btn.disabled = true;
    btn.textContent = "🏷️ 标注中...";
    try {
      // 先创建碎片，然后自动标注
      const moodSelect = document.getElementById("fragmentMood");
      const typeSelect = document.getElementById("fragmentType");
      const mediaDescInput = document.getElementById("fragmentMediaDesc");
      const res = await fetch("/api/diary/fragments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          mood: moodSelect.value || "unknown",
          source: "web",
          fragment_type: typeSelect ? typeSelect.value : "text",
          media_description: mediaDescInput ? mediaDescInput.value.trim() : "",
        }),
      });
      const data = await res.json();
      if (data.ok && data.data) {
        // 自动标注
        const tagRes = await fetch(`/api/diary/fragments/${data.data.id}/auto-tag`, { method: "POST" });
        const tagData = await tagRes.json();
        if (tagData.ok) {
          input.value = "";
          if (mediaDescInput) mediaDescInput.value = "";
          await renderFragments();
          alert("✅ 已自动标注标签和情绪！");
        } else {
          alert("标注失败：" + (tagData.error || "未知错误"));
        }
      } else {
        alert("创建失败：" + (data.error || "未知错误"));
      }
    } catch (e) {
      alert("操作失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "🏷️ 自动标注";
    }
  }

  async function batchAutoTagFragments() {
    const btn = document.getElementById("batchAutoTagBtn");
    btn.disabled = true;
    btn.textContent = "🏷️ 批量标注中...";
    try {
      const res = await fetch("/api/diary/fragments/auto-tag-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 50 }),
      });
      const data = await res.json();
      if (data.ok) {
        await renderFragments();
        alert(`✅ 批量标注完成！成功 ${data.data.success} 条，失败 ${data.data.failed} 条`);
      } else {
        alert("批量标注失败：" + (data.error || "未知错误"));
      }
    } catch (e) {
      alert("批量标注失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "🏷️ 批量标注";
    }
  }

  async function deleteFragment(id) {
    if (!confirm("确定删除这条碎片吗？")) return;
    try {
      await fetch(`/api/diary/fragments/${id}`, { method: "DELETE" });
      await renderFragments();
    } catch (e) {
      console.error("删除碎片失败", e);
    }
  }

  async function renderFragments() {
    const fragments = await loadFragments();
    const listEl = document.getElementById("diaryFragmentsList");
    const countEl = document.getElementById("fragmentsCount");

    countEl.textContent = fragments.length + " 条";

    if (fragments.length === 0) {
      listEl.innerHTML = '<div class="diary-fragments-empty">还没有碎片，随手记点什么吧 ✨</div>';
      return;
    }

    const moodMap = {
      very_happy: "😄 非常开心",
      happy: "🙂 开心",
      neutral: "😐 平静",
      sad: "😔 低落",
      anxious: "😰 焦虑",
      angry: "😠 生气",
    };

    const typeMap = {
      text: "📝",
      image: "🖼️",
      voice: "🎙️",
      link: "🔗",
    };

    let html = "";
    fragments.forEach((f) => {
      const time = f.created_at ? new Date(f.created_at).toLocaleString("zh-CN") : "";
      const typeIcon = typeMap[f.fragment_type] || "📝";
      html += `
        <div class="diary-fragment-item">
          <div class="diary-fragment-content">
            <div class="diary-fragment-header">
              <span class="diary-fragment-type">${typeIcon}</span>
              ${f.mood && f.mood !== "unknown" ? `<span class="diary-fragment-mood">${moodMap[f.mood] || f.mood}</span>` : ""}
              <span class="diary-fragment-time">${time}</span>
            </div>
            <div class="diary-fragment-text">${escapeHtml(f.content)}</div>
            ${f.media_description ? `<div class="diary-fragment-media-desc">📎 ${escapeHtml(f.media_description)}</div>` : ""}
            ${f.tags && f.tags.length > 0 ? `<div class="diary-fragment-tags">${f.tags.map(t => `<span class="diary-tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
          </div>
          <button class="diary-fragment-delete" onclick="window.__deleteDiaryFragment(${f.id})" title="删除">×</button>
        </div>`;
    });
    listEl.innerHTML = html;

    // 暴露删除函数到全局
    window.__deleteDiaryFragment = deleteFragment;
  }

  async function generateDiaryFromFragments() {
    const fragments = await loadFragments();
    if (fragments.length === 0) {
      alert("还没有碎片，先随手记点什么吧 ✨");
      return;
    }

    const btn = document.getElementById("generateDiaryFromFragmentsBtn");
    btn.disabled = true;
    btn.textContent = "🌙 正在生成...";

    try {
      const res = await fetch("/api/diary/fragments/generate-diary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_delete: true }),
      });
      const data = await res.json();

      if (data.ok) {
        await renderFragments();
        alert("✅ 日记已生成！切换到「日记」标签查看。");
        switchDiaryView("list");
        if (typeof window.loadDiaryList === "function") {
          window.loadDiaryList();
        }
      } else {
        alert("生成失败：" + (data.error || "未知错误"));
      }
    } catch (e) {
      alert("生成失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "🌙 生成今日日记";
    }
  }

  // ─── 工具函数 ───
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }
})();
