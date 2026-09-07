/**
 * 日记自进化中心前端逻辑
 * 包含：用户画像、漂移检测、夜间日志、标签优化
 */

(function () {
  "use strict";

  let currentSeView = "profile";
  let _initialized = false;

  // 初始化（幂等：只执行一次，避免重复绑定事件和重复加载）
  function init() {
    if (_initialized) return;
    _initialized = true;

    initSeSubtabs();
    initRunButton();
    loadCurrentView();
  }

  function initSeSubtabs() {
    document.querySelectorAll(".diary-se-subtab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.seView;
        switchSeView(view);
      });
    });
  }

  function initRunButton() {
    const btn = document.getElementById("seRunNightlyBtn");
    if (btn) {
      btn.addEventListener("click", runNightlyCycle);
    }
  }

  function switchSeView(view) {
    currentSeView = view;
    document.querySelectorAll(".diary-se-subtab").forEach((t) => {
      t.classList.toggle("active", t.dataset.seView === view);
    });

    // 显示/隐藏内容
    document.getElementById("seProfileContent").hidden = view !== "profile";
    document.getElementById("seDriftsContent").hidden = view !== "drifts";
    document.getElementById("seNightlyContent").hidden = view !== "nightly";
    document.getElementById("seTagsContent").hidden = view !== "tags";

    loadCurrentView();
  }

  function loadCurrentView() {
    switch (currentSeView) {
      case "profile":
        loadProfile();
        break;
      case "drifts":
        loadDrifts();
        break;
      case "nightly":
        loadNightlyLogs();
        break;
      case "tags":
        loadTagOptimizations();
        break;
    }
  }

  // ─── 用户画像 ─────────────────────────────────────────────────────

  async function loadProfile() {
    const container = document.getElementById("seProfileContent");
    container.innerHTML = '<div class="diary-se-loading">加载用户画像中...</div>';

    try {
      const res = await fetch("/api/diary/self-evolution/profile");
      const data = await res.json();

      if (!data.ok || !data.data) {
        container.innerHTML = `
          <div class="diary-se-empty">
            <p>用户画像尚未生成</p>
            <p>点击上方"立即运行夜间循环"按钮生成画像</p>
          </div>
        `;
        return;
      }

      const profile = data.data;
      container.innerHTML = renderProfile(profile);
    } catch (e) {
      container.innerHTML = `<div class="diary-se-error">加载失败: ${e.message}</div>`;
    }
  }

  function renderProfile(profile) {
    const focusNames = {
      work: "工作", family: "家庭", health: "健康",
      finance: "财务", learning: "学习", travel: "旅行",
      entertainment: "娱乐", emotion: "情感",
    };

    const traitNames = {
      introversion: "内向", extraversion: "外向",
      emotional: "感性", analytical: "理性",
      optimism: "乐观", pessimism: "悲观",
    };

    let html = `
      <div class="se-profile-header">
        <div class="se-profile-date">画像日期: ${profile.profile_date}</div>
        <div class="se-profile-stats">
          <span>📝 基于 ${profile.total_entries_analyzed} 篇日记</span>
          <span>👥 ${Object.keys(profile.relationships || {}).length} 个核心人物</span>
          <span>💡 ${(profile.values || []).length} 个核心价值观</span>
        </div>
      </div>

      <div class="se-profile-grid">
    `;

    // 性格特质
    if (profile.personality && Object.keys(profile.personality).length > 0) {
      html += `
        <div class="se-profile-card">
          <h4>🧠 性格特质</h4>
          <div class="se-trait-list">
      `;
      const topTraits = Object.entries(profile.personality)
        .filter(([k]) => !k.endsWith("_score"))
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6);
      for (const [trait, score] of topTraits) {
        const name = traitNames[trait] || trait;
        const maxScore = topTraits[0][1] || 1;
        const percent = (score / maxScore) * 100;
        html += `
          <div class="se-trait-item">
            <span class="se-trait-name">${name}</span>
            <div class="se-trait-bar">
              <div class="se-trait-fill" style="width: ${percent}%"></div>
            </div>
          </div>
        `;
      }
      html += `</div></div>`;
    }

    // 关注分布
    if (profile.focus_distribution && Object.keys(profile.focus_distribution).length > 0) {
      html += `
        <div class="se-profile-card">
          <h4>🎯 关注领域分布</h4>
          <div class="se-focus-list">
      `;
      for (const [domain, percent] of Object.entries(profile.focus_distribution)) {
        const name = focusNames[domain] || domain;
        html += `
          <div class="se-focus-item">
            <span class="se-focus-name">${name}</span>
            <span class="se-focus-percent">${(percent * 100).toFixed(0)}%</span>
          </div>
        `;
      }
      html += `</div></div>`;
    }

    // 情绪基调
    if (profile.emotional_baseline && Object.keys(profile.emotional_baseline).length > 0) {
      const eb = profile.emotional_baseline;
      html += `
        <div class="se-profile-card">
          <h4>💭 情绪基调</h4>
          <div class="se-emotion-stats">
            <div class="se-emotion-stat">
              <div class="se-emotion-value">${(eb.average_mood || 0).toFixed(2)}</div>
              <div class="se-emotion-label">平均情绪分</div>
            </div>
            <div class="se-emotion-stat">
              <div class="se-emotion-value">${(eb.volatility || 0).toFixed(2)}</div>
              <div class="se-emotion-label">情绪波动性</div>
            </div>
            <div class="se-emotion-stat">
              <div class="se-emotion-value">${((eb.positive_ratio || 0) * 100).toFixed(0)}%</div>
              <div class="se-emotion-label">积极情绪占比</div>
            </div>
          </div>
        </div>
      `;
    }

    // 人际关系
    if (profile.relationships && Object.keys(profile.relationships).length > 0) {
      html += `
        <div class="se-profile-card">
          <h4>👥 核心人际关系</h4>
          <div class="se-relation-list">
      `;
      const sortedRels = Object.entries(profile.relationships)
        .sort((a, b) => (b[1].intimacy || 0) - (a[1].intimacy || 0))
        .slice(0, 8);
      for (const [name, rel] of sortedRels) {
        const intimacy = (rel.intimacy || 0) * 100;
        const trendNames = { rising: "📈 上升", declining: "📉 下降", stable: "➡️ 稳定" };
        const trend = trendNames[rel.trend] || rel.trend;
        html += `
          <div class="se-relation-item">
            <div class="se-relation-header">
              <span class="se-relation-name">${name}</span>
              <span class="se-relation-trend">${trend}</span>
            </div>
            <div class="se-relation-bar">
              <div class="se-relation-fill" style="width: ${intimacy}%"></div>
            </div>
            <div class="se-relation-meta">
              亲密度 ${intimacy.toFixed(0)}% · 共 ${rel.total_mentions || 0} 次
            </div>
          </div>
        `;
      }
      html += `</div></div>`;
    }

    // 价值观
    if (profile.values && profile.values.length > 0) {
      html += `
        <div class="se-profile-card">
          <h4>💡 核心价值观</h4>
          <div class="se-values-list">
      `;
      for (const value of profile.values) {
        html += `<div class="se-value-item">✨ ${value}</div>`;
      }
      html += `</div></div>`;
    }

    // 写作习惯
    if (profile.writing_pattern && Object.keys(profile.writing_pattern).length > 0) {
      const wp = profile.writing_pattern;
      html += `
        <div class="se-profile-card">
          <h4>✍️ 写作习惯</h4>
          <div class="se-writing-stats">
            <div class="se-writing-stat">
              <div class="se-writing-value">${(wp.writing_frequency_per_week || 0).toFixed(1)}</div>
              <div class="se-writing-label">篇/周</div>
            </div>
            <div class="se-writing-stat">
              <div class="se-writing-value">${wp.average_length || 0}</div>
              <div class="se-writing-label">平均字数</div>
            </div>
            <div class="se-writing-stat">
              <div class="se-writing-value">${wp.unique_days || 0}</div>
              <div class="se-writing-label">写作天数</div>
            </div>
          </div>
        </div>
      `;
    }

    html += `</div>`;
    return html;
  }

  // ─── 漂移检测 ─────────────────────────────────────────────────────

  async function loadDrifts() {
    const container = document.getElementById("seDriftsContent");
    container.innerHTML = '<div class="diary-se-loading">加载漂移事件中...</div>';

    try {
      const res = await fetch("/api/diary/self-evolution/drifts?limit=50");
      const data = await res.json();

      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = `
          <div class="diary-se-empty">
            <p>暂无漂移事件</p>
            <p>系统需要至少两次画像快照才能检测变化</p>
            <p>运行夜间循环后，系统会自动对比历史画像</p>
          </div>
        `;
        return;
      }

      const drifts = data.data;
      const severityColors = {
        info: "#3b82f6",
        warning: "#f59e0b",
        alert: "#ef4444",
      };
      const typeNames = {
        behavior: "行为", emotion: "情绪", focus: "关注",
        relationship: "关系", writing: "写作",
      };

      let html = `<div class="se-drift-list">`;
      for (const drift of drifts) {
        const color = severityColors[drift.severity] || "#6b7280";
        const typeName = typeNames[drift.drift_type] || drift.drift_type;
        html += `
          <div class="se-drift-card" style="border-left-color: ${color}">
            <div class="se-drift-header">
              <span class="se-drift-type" style="background: ${color}">${typeName}</span>
              <span class="se-drift-title">${drift.title}</span>
            </div>
            <div class="se-drift-description">${drift.description}</div>
            <div class="se-drift-meta">
              <span>变化: ${drift.change_percent.toFixed(0)}%</span>
              <span>检测时间: ${new Date(drift.detected_at).toLocaleString()}</span>
            </div>
          </div>
        `;
      }
      html += `</div>`;
      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div class="diary-se-error">加载失败: ${e.message}</div>`;
    }
  }

  // ─── 夜间日志 ─────────────────────────────────────────────────────

  async function loadNightlyLogs() {
    const container = document.getElementById("seNightlyContent");
    container.innerHTML = '<div class="diary-se-loading">加载夜间日志中...</div>';

    try {
      const res = await fetch("/api/diary/self-evolution/nightly-logs?limit=30");
      const data = await res.json();

      if (!data.ok || !data.data || data.data.length === 0) {
        container.innerHTML = `
          <div class="diary-se-empty">
            <p>暂无夜间日志</p>
            <p>点击上方"立即运行夜间循环"按钮生成日志</p>
          </div>
        `;
        return;
      }

      const logs = data.data;
      let html = `<div class="se-nightly-list">`;

      for (const log of logs) {
        const summary = log.summary || {};
        html += `
          <div class="se-nightly-card" data-date="${log.log_date}">
            <div class="se-nightly-header">
              <span class="se-nightly-date">📅 ${log.log_date}</span>
              <span class="se-nightly-stats">
                📝 ${summary.entry_count || 0} 篇 · 
                ⚠️ ${summary.drift_count || 0} 漂移 · 
                💡 ${summary.learning_count || 0} 洞察
              </span>
            </div>
            <div class="se-nightly-detail" id="nightly-detail-${log.log_date}" hidden></div>
            <button class="se-nightly-toggle" data-date="${log.log_date}" type="button">
              查看详情 ▼
            </button>
          </div>
        `;
      }

      html += `</div>`;
      container.innerHTML = html;

      // 绑定详情按钮
      container.querySelectorAll(".se-nightly-toggle").forEach((btn) => {
        btn.addEventListener("click", () => toggleNightlyDetail(btn.dataset.date));
      });
    } catch (e) {
      container.innerHTML = `<div class="diary-se-error">加载失败: ${e.message}</div>`;
    }
  }

  async function toggleNightlyDetail(date) {
    const detailEl = document.getElementById(`nightly-detail-${date}`);
    const btn = document.querySelector(`.se-nightly-toggle[data-date="${date}"]`);

    if (!detailEl.hidden) {
      detailEl.hidden = true;
      btn.textContent = "查看详情 ▼";
      return;
    }

    if (detailEl.innerHTML === "") {
      try {
        const res = await fetch(`/api/diary/self-evolution/nightly-logs/${date}`);
        const data = await res.json();
        if (data.ok && data.data) {
          detailEl.innerHTML = renderNightlyDetail(data.data);
        }
      } catch (e) {
        detailEl.innerHTML = `<div class="diary-se-error">加载失败: ${e.message}</div>`;
      }
    }

    detailEl.hidden = false;
    btn.textContent = "收起详情 ▲";
  }

  function renderNightlyDetail(log) {
    let html = `<div class="se-nightly-full">`;

    // 学到了什么
    if (log.learnings && log.learnings.length > 0) {
      html += `<div class="se-nightly-section"><h5>🧠 学到了什么</h5><ul>`;
      for (const learning of log.learnings) {
        html += `<li>${learning}</li>`;
      }
      html += `</ul></div>`;
    }

    // 发现的模式
    if (log.patterns && log.patterns.length > 0) {
      html += `<div class="se-nightly-section"><h5>🔍 发现的模式</h5><ul>`;
      for (const pattern of log.patterns) {
        html += `<li><strong>[${pattern.type}]</strong> ${pattern.description}</li>`;
      }
      html += `</ul></div>`;
    }

    // 系统建议
    if (log.suggestions && log.suggestions.length > 0) {
      html += `<div class="se-nightly-section"><h5>💡 系统建议</h5><ul>`;
      for (const suggestion of log.suggestions) {
        html += `<li>${suggestion}</li>`;
      }
      html += `</ul></div>`;
    }

    // 自我优化
    if (log.self_optimizations && log.self_optimizations.length > 0) {
      html += `<div class="se-nightly-section"><h5>🔧 自我优化</h5><ul>`;
      for (const opt of log.self_optimizations) {
        html += `<li>${opt}</li>`;
      }
      html += `</ul></div>`;
    }

    html += `</div>`;
    return html;
  }

  // ─── 标签优化 ─────────────────────────────────────────────────────

  async function loadTagOptimizations() {
    const container = document.getElementById("seTagsContent");
    container.innerHTML = '<div class="diary-se-loading">加载标签优化建议中...</div>';

    try {
      const res = await fetch("/api/diary/self-evolution/tag-optimizations");
      const data = await res.json();

      if (!data.ok || !data.data) {
        container.innerHTML = `<div class="diary-se-error">加载失败</div>`;
        return;
      }

      const opt = data.data;
      let html = `<div class="se-tags-grid">`;

      // 新标签候选
      html += `
        <div class="se-tags-card">
          <h4>🆕 新标签候选 (${(opt.new_tags || []).length}个)</h4>
      `;
      if (opt.new_tags && opt.new_tags.length > 0) {
        html += `<div class="se-new-tags">`;
        for (const tag of opt.new_tags) {
          html += `
            <div class="se-new-tag-item">
              <span class="se-new-tag-name">${tag.tag}</span>
              <span class="se-new-tag-count">${tag.count}次</span>
            </div>
          `;
        }
        html += `</div>`;
      } else {
        html += `<p class="se-tags-empty">暂无新标签候选</p>`;
      }
      html += `</div>`;

      // 合并建议
      html += `
        <div class="se-tags-card">
          <h4>🔄 合并建议 (${(opt.merge_suggestions || []).length}个)</h4>
      `;
      if (opt.merge_suggestions && opt.merge_suggestions.length > 0) {
        html += `<div class="se-merge-list">`;
        for (const merge of opt.merge_suggestions) {
          html += `
            <div class="se-merge-item">
              <div class="se-merge-from">${merge.from.join(" + ")}</div>
              <div class="se-merge-arrow">→</div>
              <div class="se-merge-to">${merge.to}</div>
              <div class="se-merge-reason">${merge.reason}</div>
            </div>
          `;
        }
        html += `</div>`;
      } else {
        html += `<p class="se-tags-empty">暂无合并建议</p>`;
      }
      html += `</div>`;

      // 过时标签
      html += `
        <div class="se-tags-card">
          <h4>📦 过时标签 (${(opt.outdated_tags || []).length}个)</h4>
      `;
      if (opt.outdated_tags && opt.outdated_tags.length > 0) {
        html += `<div class="se-outdated-tags">`;
        for (const tag of opt.outdated_tags) {
          html += `<span class="se-outdated-tag">${tag}</span>`;
        }
        html += `</div><p class="se-tags-hint">超过90天未使用的标签，可以考虑归档或删除</p>`;
      } else {
        html += `<p class="se-tags-empty">暂无过时标签</p>`;
      }
      html += `</div>`;

      // 标签层级
      html += `
        <div class="se-tags-card">
          <h4>🌲 标签层级 (${Object.keys(opt.hierarchy || {}).length}个分类)</h4>
      `;
      if (opt.hierarchy && Object.keys(opt.hierarchy).length > 0) {
        html += `<div class="se-hierarchy">`;
        for (const [parent, children] of Object.entries(opt.hierarchy)) {
          html += `
            <div class="se-hierarchy-item">
              <div class="se-hierarchy-parent">${parent}</div>
              <div class="se-hierarchy-children">${(children || []).join("、")}</div>
            </div>
          `;
        }
        html += `</div>`;
      } else {
        html += `<p class="se-tags-empty">暂无标签层级</p>`;
      }
      html += `</div>`;

      html += `</div>`;
      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div class="diary-se-error">加载失败: ${e.message}</div>`;
    }
  }

  // ─── 运行夜间循环 ─────────────────────────────────────────────────

  async function runNightlyCycle() {
    const btn = document.getElementById("seRunNightlyBtn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "⏳ 正在运行夜间循环...";
    }

    try {
      const res = await fetch("/api/diary/self-evolution/run-nightly", {
        method: "POST",
      });
      const data = await res.json();

      if (data.ok) {
        alert("夜间循环完成！系统已更新用户画像并生成夜间日志。");
        loadCurrentView();
      } else {
        alert("夜间循环失败: " + (data.error || "未知错误"));
      }
    } catch (e) {
      alert("夜间循环失败: " + e.message);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "🌙 立即运行夜间循环";
      }
    }
  }

  // 暴露到全局
  window.initDiarySelfEvolution = init;
})();
