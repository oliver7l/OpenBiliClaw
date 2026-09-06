/**
 * 日记人物与标签模块
 *
 * 功能：
 * - 加载和展示 AI 提取的人物列表
 * - 加载和展示标签云
 * - 人物详情弹窗（包含相关日记）
 * - 批量提取按钮
 * - 按关系/类型筛选
 */

(function () {
  "use strict";

  // ═══════════════════════════════════════════
  // 初始化
  // ═══════════════════════════════════════════
  function init() {
    // 批量提取按钮
    const extractBtn = document.getElementById("extractBatchBtn");
    if (extractBtn) {
      extractBtn.addEventListener("click", handleBatchExtract);
    }

    // 筛选器
    const relationFilter = document.getElementById("personRelationFilter");
    if (relationFilter) {
      relationFilter.addEventListener("change", loadPeople);
    }
    const tagTypeFilter = document.getElementById("tagTypeFilter");
    if (tagTypeFilter) {
      tagTypeFilter.addEventListener("change", loadTags);
    }

    // 监听子 Tab 切换，切换到人物与标签时加载数据
    document.addEventListener("diary-view-change", (e) => {
      if (e.detail && e.detail.view === "people") {
        loadPeople();
        loadTags();
        loadStats();
      }
    });
  }

  // ═══════════════════════════════════════════
  // 统计信息
  // ═══════════════════════════════════════════
  async function loadStats() {
    try {
      const res = await fetch("/api/diary/extraction-stats");
      const data = await res.json();
      if (data.ok) {
        const s = data.data;
        document.getElementById("statTotalPersons").textContent = s.total_persons || 0;
        document.getElementById("statTotalTags").textContent = s.total_tags || 0;
        document.getElementById("statExtractedEntries").textContent =
          s.total_entries ? Math.min(s.total_entries, 720) : 0;
      }
    } catch (e) {
      console.error("加载统计失败", e);
    }
  }

  // ═══════════════════════════════════════════
  // 人物列表
  // ═══════════════════════════════════════════
  async function loadPeople() {
    const listEl = document.getElementById("diaryPeopleList");
    const relationFilter = document.getElementById("personRelationFilter");
    const relation = relationFilter ? relationFilter.value : "";

    try {
      let url = "/api/diary/persons?limit=200&min_appearances=1";
      if (relation) url += `&relation=${encodeURIComponent(relation)}`;
      const res = await fetch(url);
      const data = await res.json();

      if (!data.ok || !data.data || data.data.length === 0) {
        listEl.innerHTML =
          '<div class="diary-people-empty">暂无人物数据，点击「批量提取」开始 AI 分析</div>';
        return;
      }

      const relationColors = {
        家人: "#e74c3c",
        朋友: "#3498db",
        同事: "#f39c12",
        儿子: "#9b59b6",
        母亲: "#e91e63",
        伴侣: "#ff6b6b",
      };

      let html = "";
      data.data.forEach((p) => {
        const color = relationColors[p.relation] || "#95a5a6";
        const dateRange =
          p.first_appeared && p.last_appeared
            ? `${p.first_appeared} ~ ${p.last_appeared}`
            : "";
        html += `
          <div class="diary-person-card" onclick="window.__showPersonDetail(${p.id})">
            <div class="diary-person-avatar" style="background:${color}">
              ${p.name.charAt(0)}
            </div>
            <div class="diary-person-info">
              <div class="diary-person-name">${escapeHtml(p.name)}</div>
              <div class="diary-person-meta">
                ${p.relation ? `<span class="diary-person-relation" style="color:${color}">${escapeHtml(p.relation)}</span>` : ""}
                <span class="diary-person-count">出现 ${p.appearance_count} 次</span>
              </div>
              ${dateRange ? `<div class="diary-person-dates">${dateRange}</div>` : ""}
            </div>
          </div>`;
      });
      listEl.innerHTML = html;
    } catch (e) {
      console.error("加载人物列表失败", e);
      listEl.innerHTML = '<div class="diary-people-empty">加载失败</div>';
    }
  }

  // ═══════════════════════════════════════════
  // 人物详情
  // ═══════════════════════════════════════════
  async function showPersonDetail(personId) {
    const modal = document.getElementById("personDetailModal");
    const body = document.getElementById("personDetailBody");
    const nameEl = document.getElementById("personDetailName");

    body.innerHTML = '<div class="diary-loading">加载中...</div>';
    modal.hidden = false;

    try {
      const res = await fetch(`/api/diary/persons/${personId}`);
      const data = await res.json();
      if (!data.ok) {
        body.innerHTML = "<p>加载失败</p>";
        return;
      }
      const p = data.data;
      nameEl.textContent = p.name;

      let html = `
        <div class="diary-person-detail-header">
          <div class="diary-person-detail-stats">
            <div><strong>${p.appearance_count}</strong><span>出现次数</span></div>
            <div><strong>${p.first_appeared || "-"}</strong><span>首次出现</span></div>
            <div><strong>${p.last_appeared || "-"}</strong><span>最近出现</span></div>
            ${p.relation ? `<div><strong>${escapeHtml(p.relation)}</strong><span>关系</span></div>` : ""}
          </div>
        </div>
        <h4 style="margin:20px 0 10px">📖 相关日记（${p.related_entries.length} 篇）</h4>
        <div class="diary-person-entries">`;

      p.related_entries.forEach((e) => {
        html += `
          <div class="diary-person-entry" onclick="window.__jumpToDiary(${e.id})">
            <div class="diary-person-entry-date">${e.entry_date}</div>
            <div class="diary-person-entry-title">${escapeHtml(e.title || "(无标题)")}</div>
            ${e.context ? `<div class="diary-person-entry-context">“${escapeHtml(e.context)}”</div>` : ""}
          </div>`;
      });

      html += "</div>";
      body.innerHTML = html;
    } catch (e) {
      body.innerHTML = "<p>加载失败</p>";
    }
  }

  // ═══════════════════════════════════════════
  // 标签云
  // ═══════════════════════════════════════════
  async function loadTags() {
    const cloudEl = document.getElementById("diaryTagCloud");
    const typeFilter = document.getElementById("tagTypeFilter");
    const type = typeFilter ? typeFilter.value : "";

    try {
      let url = "/api/diary/tags?limit=200&min_count=1";
      if (type) url += `&type=${type}`;
      const res = await fetch(url);
      const data = await res.json();

      if (!data.ok || !data.data || data.data.length === 0) {
        cloudEl.innerHTML = '<div class="diary-people-empty">暂无标签数据</div>';
        return;
      }

      const typeColors = {
        emotion: "#e74c3c",
        topic: "#3498db",
        event: "#f39c12",
        location: "#2ecc71",
        work: "#9b59b6",
        family: "#e91e63",
        health: "#1abc9c",
        finance: "#f1c40f",
        other: "#95a5a6",
      };

      const maxCount = Math.max(...data.data.map((t) => t.count));
      let html = "";
      data.data.forEach((t) => {
        const size = 12 + (t.count / maxCount) * 20;
        const color = typeColors[t.type] || "#95a5a6";
        const opacity = 0.6 + (t.count / maxCount) * 0.4;
        html += `
          <span class="diary-tagcloud-item"
                style="font-size:${size}px;color:${color};opacity:${opacity}"
                title="${t.name} (${t.count}次, ${t.type})"
                onclick="window.__filterByTag('${escapeHtml(t.name)}')">
            ${escapeHtml(t.name)}
          </span>`;
      });
      cloudEl.innerHTML = html;
    } catch (e) {
      console.error("加载标签云失败", e);
      cloudEl.innerHTML = '<div class="diary-people-empty">加载失败</div>';
    }
  }

  // ═══════════════════════════════════════════
  // 批量提取
  // ═══════════════════════════════════════════
  async function handleBatchExtract() {
    const btn = document.getElementById("extractBatchBtn");
    if (!confirm("确定要批量提取所有日记的标签和人物吗？\n\n这会调用 AI 分析每篇日记，可能需要较长时间。\n建议先处理 50 篇测试效果。")) {
      return;
    }

    const limit = prompt("处理多少篇？（输入数字，默认 50，最大 720）", "50");
    if (!limit) return;
    const num = parseInt(limit, 10);
    if (isNaN(num) || num <= 0) return;

    btn.disabled = true;
    btn.textContent = "⏳ 提取中...";

    try {
      const res = await fetch("/api/diary/extract-batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: num, only_unextracted: true }),
      });
      const data = await res.json();

      if (data.ok) {
        const s = data.data;
        alert(
          `✅ 批量提取完成！\n\n共处理 ${s.total} 篇\n成功: ${s.success}\n失败: ${s.failed}\n\n点击确定刷新页面查看结果。`
        );
        loadPeople();
        loadTags();
        loadStats();
      } else {
        alert("提取失败：" + (data.error || "未知错误"));
      }
    } catch (e) {
      alert("提取失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "🚀 批量提取";
    }
  }

  // ═══════════════════════════════════════════
  // 工具函数
  // ═══════════════════════════════════════════
  function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // 暴露到全局
  window.__showPersonDetail = showPersonDetail;
  window.__jumpToDiary = function (entryId) {
    // 切换到日记列表并定位
    document.dispatchEvent(new CustomEvent("diary-view-change", { detail: { view: "list" } }));
    // 触发日记列表加载（如果有全局函数）
    if (typeof window.loadDiaryList === "function") {
      window.loadDiaryList();
    }
  };
  window.__filterByTag = function (tagName) {
    alert(`按标签「${tagName}」筛选功能开发中...`);
  };

  // DOM 加载完成后初始化
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
