/**
 * 日记语义搜索模块
 *
 * 功能：
 * 1. 自然语言语义搜索（基于 embedding 相似度）
 * 2. 搜索结果按相似度排序，显示匹配度
 * 3. 支持来源、日期范围、相似度阈值筛选
 * 4. 点击结果可查看日记详情
 */

(function () {
  'use strict';

  // DOM 元素
  const searchInput = document.getElementById('semanticSearchInput');
  const searchBtn = document.getElementById('semanticSearchBtn');
  const resultsList = document.getElementById('semanticResultsList');
  const resultsCount = document.getElementById('semanticResultsCount');
  const sourceFilter = document.getElementById('semanticSourceFilter');
  const startDateInput = document.getElementById('semanticStartDate');
  const endDateInput = document.getElementById('semanticEndDate');
  const thresholdSlider = document.getElementById('semanticThreshold');
  const thresholdVal = document.getElementById('semanticThresholdVal');

  if (!searchInput) return; // 页面未加载语义搜索模块

  // 状态
  let isSearching = false;
  let currentResults = [];

  // 初始化
  function init() {
    // 阈值滑块
    thresholdSlider.addEventListener('input', () => {
      thresholdVal.textContent = parseFloat(thresholdSlider.value).toFixed(2);
    });

    // 搜索按钮
    searchBtn.addEventListener('click', doSearch);

    // 回车搜索
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        doSearch();
      }
    });

    // 筛选变化时重新搜索（如果有结果）
    sourceFilter.addEventListener('change', () => {
      if (currentResults.length > 0 || searchInput.value.trim()) {
        doSearch();
      }
    });
    startDateInput.addEventListener('change', () => {
      if (currentResults.length > 0 || searchInput.value.trim()) {
        doSearch();
      }
    });
    endDateInput.addEventListener('change', () => {
      if (currentResults.length > 0 || searchInput.value.trim()) {
        doSearch();
      }
    });
  }

  // 执行搜索
  async function doSearch() {
    const query = searchInput.value.trim();
    if (!query) {
      showEmpty('请输入搜索关键词');
      return;
    }
    if (isSearching) return;

    isSearching = true;
    searchBtn.disabled = true;
    searchBtn.textContent = '搜索中...';
    resultsCount.textContent = '正在搜索...';
    showLoading();

    try {
      const params = new URLSearchParams({
        q: query,
        top_k: 20,
        min_score: thresholdSlider.value,
      });
      if (sourceFilter.value) params.set('source', sourceFilter.value);
      if (startDateInput.value) params.set('start_date', startDateInput.value);
      if (endDateInput.value) params.set('end_date', endDateInput.value);

      const resp = await fetch(`/api/diary/rag/search?${params.toString()}`);
      const data = await resp.json();

      if (!data.ok) {
        showError(data.error || '搜索失败');
        return;
      }

      currentResults = data.results || [];
      renderResults(currentResults, data.query);
    } catch (err) {
      console.error('语义搜索失败:', err);
      showError('网络错误，请稍后重试');
    } finally {
      isSearching = false;
      searchBtn.disabled = false;
      searchBtn.textContent = '🔍 搜索';
    }
  }

  // 渲染搜索结果
  function renderResults(results, query) {
    if (results.length === 0) {
      resultsCount.textContent = '没有找到相关日记';
      resultsList.innerHTML = `
        <div class="diary-semantic-empty">
          <div class="diary-semantic-empty-icon">🔍</div>
          <p>没有找到与"${escapeHtml(query)}"相关的日记</p>
          <p class="diary-semantic-empty-hint">试试降低相似度阈值，或换个问法</p>
        </div>
      `;
      return;
    }

    resultsCount.textContent = `找到 ${results.length} 篇相关日记`;

    resultsList.innerHTML = results.map((r, idx) => {
      const scorePercent = (r.score * 100).toFixed(1);
      const scoreColor = r.score >= 0.6 ? '#22c55e' : r.score >= 0.45 ? '#eab308' : '#94a3b8';
      const moodEmoji = getMoodEmoji(r.mood);
      const sourceLabel = getSourceLabel(r.source);

      return `
        <div class="diary-semantic-result" data-entry-id="${r.id}" style="animation: fadeInUp 0.3s ease ${idx * 0.05}s both;">
          <div class="diary-semantic-result-header">
            <div class="diary-semantic-result-meta">
              <span class="diary-semantic-result-date">📅 ${r.date}</span>
              <span class="diary-semantic-result-mood">${moodEmoji}</span>
              <span class="diary-semantic-result-source">${sourceLabel}</span>
            </div>
            <div class="diary-semantic-result-score">
              <div class="diary-semantic-score-bar">
                <div class="diary-semantic-score-fill" style="width: ${scorePercent}%; background: ${scoreColor};"></div>
              </div>
              <span class="diary-semantic-score-text" style="color: ${scoreColor};">${scorePercent}%</span>
            </div>
          </div>
          ${r.title ? `<h4 class="diary-semantic-result-title">${escapeHtml(r.title)}</h4>` : ''}
          <p class="diary-semantic-result-content">${escapeHtml(r.content)}</p>
          <div class="diary-semantic-result-actions">
            <button class="small-btn primary diary-semantic-view-btn" data-entry-id="${r.id}" type="button">📖 查看详情</button>
          </div>
        </div>
      `;
    }).join('');

    // 绑定查看详情按钮
    resultsList.querySelectorAll('.diary-semantic-view-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const entryId = parseInt(btn.dataset.entryId);
        viewEntryDetail(entryId);
      });
    });
  }

  // 查看日记详情（切换到日记列表视图并选中）
  function viewEntryDetail(entryId) {
    // 触发自定义事件，由 diary.js 处理
    const event = new CustomEvent('diary:view-entry', { detail: { entryId } });
    document.dispatchEvent(event);

    // 切换到日记列表视图
    const listTab = document.querySelector('.diary-subtab[data-view="list"]');
    if (listTab) listTab.click();
  }

  // 显示加载状态
  function showLoading() {
    resultsList.innerHTML = `
      <div class="diary-semantic-loading">
        <div class="diary-semantic-spinner"></div>
        <p>正在理解你的问题，搜索 925 篇日记...</p>
      </div>
    `;
  }

  // 显示错误
  function showError(msg) {
    resultsCount.textContent = '搜索失败';
    resultsList.innerHTML = `
      <div class="diary-semantic-empty">
        <div class="diary-semantic-empty-icon">⚠️</div>
        <p>${escapeHtml(msg)}</p>
      </div>
    `;
  }

  // 显示空状态
  function showEmpty(msg) {
    resultsCount.textContent = msg;
    resultsList.innerHTML = `
      <div class="diary-semantic-empty">
        <div class="diary-semantic-empty-icon">💭</div>
        <p>${escapeHtml(msg)}</p>
        <p class="diary-semantic-empty-hint">试试搜索："我最开心的日子"、"和妈妈的回忆"、"工作迷茫的时候"</p>
      </div>
    `;
  }

  // 工具函数
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
  }

  function getMoodEmoji(mood) {
    const map = {
      very_happy: '😄', happy: '🙂', neutral: '😐',
      sad: '😔', very_sad: '😢', angry: '😠', anxious: '😰', unknown: '❓'
    };
    return map[mood] || '❓';
  }

  function getSourceLabel(source) {
    const map = {
      manual: '✍️ 手动', import_lele: '👶 乐乐日记', import_text: '📄 文本导入',
      import_mindback: '🧠 MindBack', youdao_note: '📝 有道云',
      apple_notes: '🍎 苹果备忘录', wps_note: '📘 WPS笔记', api: '🔌 API'
    };
    return map[source] || source;
  }

  // 由日记页面动态加载后手动初始化
  window.__initDiarySemantic = init;
})();
