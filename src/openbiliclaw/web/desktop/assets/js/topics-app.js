// ── 专题页面（从独立 /topics 迁移进桌面应用，沿用推荐流卡片风格）────────

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var initialized = false;
  var state = { slug: null, topics: [], collecting: false };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function fmtTime(t) {
    if (!t) return '从未收集';
    var d = new Date(String(t).replace(' ', 'T') + (String(t).includes('Z') ? '' : 'Z'));
    if (isNaN(d.getTime())) return String(t).slice(0, 16);
    var now = new Date();
    var diff = (now - d) / 1000;
    if (diff < 3600) return Math.max(1, Math.round(diff / 60)) + ' 分钟前';
    if (diff < 86400) return Math.round(diff / 3600) + ' 小时前';
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' +
      String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  function chipList(arr) {
    return (arr || []).map(function (k) {
      return '<span class="topic-chip">' + esc(k) + '</span>';
    }).join('');
  }

  // 列表视图：沿用推荐流的小白卡风格（video-card.is-minimal）
  function cardHtml(t) {
    var badge = t.status === 'active'
      ? '<span class="topic-badge active">采集中</span>'
      : '<span class="topic-badge paused">已暂停</span>';
    return '<div class="video-card is-minimal topic-card" data-slug="' + esc(t.slug) + '">' +
      '<div class="video-card-body">' +
        '<p class="video-card-title">' + esc(t.name) + ' ' + badge + '</p>' +
        '<p class="topic-desc">' + esc(t.description || '（无描述）') + '</p>' +
        '<div class="topic-chips">' + chipList(t.keywords) + '</div>' +
        '<div class="video-card-footer topic-meta-row">' +
          '<span class="topic-meta">收录 ' + t.item_count + ' 条</span>' +
          '<span class="topic-meta">' + (t.platforms || []).join(' · ') + '</span>' +
          '<span class="topic-meta">' + fmtTime(t.last_collected_at) + '</span>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function renderList() {
    var list = $('topicsList');
    var empty = $('topicsEmpty');
    $('topicsDetail').setAttribute('hidden', '');
    list.removeAttribute('hidden');
    if (!state.topics.length) {
      list.setAttribute('hidden', '');
      empty.removeAttribute('hidden');
      empty.textContent = '还没有专题。点「＋ 新建专题」建第一个，或告诉我你想追踪什么方向。';
      return;
    }
    empty.setAttribute('hidden', '');
    list.innerHTML = state.topics.map(cardHtml).join('');
    list.querySelectorAll('.topic-card').forEach(function (card) {
      card.addEventListener('click', function () {
        openDetail(card.getAttribute('data-slug'));
      });
    });
  }

  function itemHtml(it) {
    var url = it.url || '#';
    var href = url === '#' ? 'javascript:void(0)' : url;
    var target = url === '#' ? '' : ' target="_blank" rel="noopener"';
    return '<div class="topic-item">' +
      '<div class="topic-item-main">' +
        '<div class="topic-item-title"><a href="' + esc(href) + '"' + target + '>' + esc(it.title) + '</a></div>' +
        (it.summary ? '<div class="topic-item-summary">' + esc(it.summary) + '</div>' : '') +
        '<div class="topic-item-meta">' +
          '<span>' + esc(it.source_platform || '未知来源') + '</span>' +
          (it.source_name ? '<span>' + esc(it.source_name) + '</span>' : '') +
          '<span>' + fmtTime(it.collected_at) + '</span>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function renderDetail(t) {
    var detail = $('topicsDetail');
    var list = $('topicsList');
    list.setAttribute('hidden', '');
    $('topicsEmpty').setAttribute('hidden', '');
    detail.removeAttribute('hidden');
    detail.innerHTML =
      '<div class="topics-backbar">' +
        '<button class="pill-btn" id="tpBackBtn" type="button">← 返回专题</button>' +
        '<div class="topics-back-info">' +
          '<p class="video-card-title topic-detail-name">' + esc(t.name) + '</p>' +
          (t.description ? '<p class="topic-desc">' + esc(t.description) + '</p>' : '') +
          '<div class="topic-chips">' + chipList(t.keywords) + '</div>' +
          '<div class="video-card-footer topic-meta-row">' +
            '<span class="topic-meta">收录 ' + t.item_count + ' 条</span>' +
            '<span class="topic-meta">' + (t.platforms || []).join(' · ') + '</span>' +
            '<span class="topic-meta">最近收集：' + fmtTime(t.last_collected_at) + '</span>' +
          '</div>' +
        '</div>' +
        '<button class="pill-btn dark" id="tpCollectBtn" type="button">立即收集一次</button>' +
      '</div>' +
      '<div class="topic-msg" id="tpCollectMsg"></div>' +
      '<div class="topics-items">' +
        (t.items && t.items.length
          ? t.items.map(itemHtml).join('')
          : '<div class="topic-empty">暂无条目——点「立即收集一次」从各平台搜索关键词。</div>') +
      '</div>';

    $('tpBackBtn').addEventListener('click', backToList);
    $('tpCollectBtn').addEventListener('click', function () { collectNow(t.slug); });
  }

  function openDetail(slug) {
    state.slug = slug;
    var list = $('topicsList');
    list.removeAttribute('hidden');
    list.innerHTML = '<div class="topic-empty">加载专题…</div>';
    fetch('/api/topics/' + encodeURIComponent(slug))
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (t) { renderDetail(t); })
      .catch(function (e) {
        list.removeAttribute('hidden');
        list.innerHTML = '<div class="topic-empty">加载失败：' + esc(e.message) + '</div>';
        state.slug = null;
      });
  }

  function backToList() {
    state.slug = null;
    loadTopics();
  }

  function collectNow(slug) {
    var btn = $('tpCollectBtn');
    var msg = $('tpCollectMsg');
    if (btn) { btn.disabled = true; btn.textContent = '收集中…'; }
    if (msg) { msg.textContent = ''; msg.className = 'topic-msg'; }
    fetch('/api/topics/' + encodeURIComponent(slug) + '/collect', { method: 'POST' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.detail || 'HTTP ' + res.status);
        if (msg) {
          msg.className = 'topic-msg ok';
          msg.textContent = '完成：新增 ' + res.d.new + ' 条，重复 ' + res.d.dup +
            '，失败 ' + res.d.failed + '，当前共 ' + res.d.item_count + ' 条。';
        }
        return loadTopics().then(function () { openDetail(slug); });
      })
      .catch(function (e) {
        if (msg) { msg.className = 'topic-msg err'; msg.textContent = '收集失败：' + esc(e.message); }
        if (btn) { btn.disabled = false; btn.textContent = '立即收集一次'; }
      });
  }

  function loadTopics() {
    $('topicsDetail').setAttribute('hidden', '');
    $('topicsList').removeAttribute('hidden');
    return fetch('/api/topics')
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (list) {
        state.topics = list || [];
        renderList();
      })
      .catch(function (e) {
        $('topicsList').setAttribute('hidden', '');
        $('topicsEmpty').removeAttribute('hidden');
        $('topicsEmpty').textContent = '加载专题失败：' + esc(e.message);
      });
  }

  function showNewPanel() {
    var panel = $('topicsNewPanel');
    panel.removeAttribute('hidden');
    $('tpNewMsg').textContent = '';
    $('tpNewMsg').className = 'topic-msg';
    $('tpName').focus();
  }
  function hideNewPanel() {
    $('topicsNewPanel').setAttribute('hidden', '');
  }
  function createTopic() {
    var msg = $('tpNewMsg');
    var name = $('tpName').value.trim();
    var slug = $('tpSlug').value.trim().toLowerCase();
    if (!name || !slug) {
      msg.className = 'topic-msg err'; msg.textContent = '名称和 slug 必填。';
      return;
    }
    var keywords = $('tpKeywords').value.split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
    var platforms = Array.prototype.filter.call($('tpPlatforms').options, function (o) { return o.selected; })
      .map(function (o) { return o.value; });
    var body = {
      name: name, slug: slug,
      description: $('tpDesc').value.trim(),
      keywords: keywords,
      platforms: platforms.length ? platforms : ['bilibili']
    };
    var btn = $('tpCreateBtn'); btn.disabled = true;
    fetch('/api/topics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.detail || 'HTTP ' + res.status);
        msg.className = 'topic-msg ok'; msg.textContent = '专题「' + res.d.name + '」已创建。';
        $('tpName').value = ''; $('tpSlug').value = ''; $('tpKeywords').value = ''; $('tpDesc').value = '';
        hideNewPanel();
        loadTopics();
      })
      .catch(function (e) {
        msg.className = 'topic-msg err'; msg.textContent = '创建失败：' + esc(e.message);
      })
      .finally(function () { btn.disabled = false; });
  }

  function collectAll() {
    var btn = $('topicsCollectAllBtn');
    btn.disabled = true; btn.textContent = '收集中…';
    var slugs = state.topics.map(function (t) { return t.slug; });
    var chain = Promise.resolve();
    var done = 0;
    slugs.forEach(function (slug) {
      chain = chain.then(function () {
        return fetch('/api/topics/' + encodeURIComponent(slug) + '/collect', { method: 'POST' })
          .catch(function () { /* per-topic tolerance */ });
      }).then(function () {
        done += 1;
        btn.textContent = '收集中…(' + done + '/' + slugs.length + ')';
      });
    });
    chain.then(function () { btn.textContent = '全部收集一次'; btn.disabled = false; return loadTopics(); })
      .catch(function () { btn.textContent = '全部收集一次'; btn.disabled = false; });
  }

  function bindEvents() {
    window.safeBind("#topicsNewBtn", "click", showNewPanel);
    window.safeBind("#tpCancelBtn", "click", hideNewPanel);
    window.safeBind("#tpCreateBtn", "click", createTopic);
    window.safeBind("#topicsCollectAllBtn", "click", collectAll);
  }

  function openTopicsPage() {
    window.showMainPage("topicsPage");
    if (!initialized) { bindEvents(); initialized = true; }
    loadTopics();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // 注册路由（参照 feed-pages.js：app.js 初始化后挂到 DESKTOP_PAGE_ROUTES）
  if (window.DESKTOP_PAGE_ROUTES) {
    window.DESKTOP_PAGE_ROUTES["topics"] = openTopicsPage;
  }
  var match = (location.pathname || "/web").match(/^\/web\/([a-zA-Z0-9-]+)\/?$/);
  var page = match ? match[1] : null;
  if (page === "topics") openTopicsPage();
})();