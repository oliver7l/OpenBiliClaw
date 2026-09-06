/**
 * 日记对话模块
 *
 * 功能：
 * 1. 基于 RAG 的日记问答（AI 基于日记内容回答问题）
 * 2. 回答引用具体日记作为证据
 * 3. 推荐相关问题
 * 4. 聊天历史（当前会话）
 */

(function () {
  'use strict';

  // DOM 元素
  const chatMessages = document.getElementById('diaryChatMessages');
  const chatInput = document.getElementById('diaryChatInput');
  const chatSendBtn = document.getElementById('diaryChatSendBtn');
  const suggestionsContainer = document.getElementById('diaryChatSuggestions');

  if (!chatMessages) return; // 页面未加载日记对话模块

  // 状态
  let isAsking = false;
  let chatHistory = []; // 当前会话的聊天历史

  // 初始化
  function init() {
    // 发送按钮
    chatSendBtn.addEventListener('click', sendMessage);

    // 回车发送（Shift+Enter 换行）
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    // 推荐问题点击
    if (suggestionsContainer) {
      suggestionsContainer.addEventListener('click', (e) => {
        const btn = e.target.closest('.diary-chat-suggestion');
        if (btn) {
          chatInput.value = btn.dataset.q;
          sendMessage();
        }
      });
    }

    // 自动调整输入框高度
    chatInput.addEventListener('input', () => {
      chatInput.style.height = 'auto';
      chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
    });
  }

  // 发送消息
  async function sendMessage() {
    const question = chatInput.value.trim();
    if (!question || isAsking) return;

    // 添加用户消息
    addMessage('user', question);
    chatHistory.push({ role: 'user', content: question });
    chatInput.value = '';
    chatInput.style.height = 'auto';

    // 显示 AI 思考中
    const thinkingId = addThinkingMessage();
    isAsking = true;
    chatSendBtn.disabled = true;
    chatSendBtn.textContent = '思考中...';

    try {
      const resp = await fetch('/api/diary/rag/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, top_k: 8 }),
      });
      const data = await resp.json();

      // 移除思考中消息
      removeMessage(thinkingId);

      if (!data.ok) {
        addMessage('ai', `抱歉，回答生成失败：${data.error || '未知错误'}`);
        return;
      }

      // 添加 AI 回答
      addAIMessage(data.answer, data.sources, data.related_questions);
      chatHistory.push({ role: 'assistant', content: data.answer });
    } catch (err) {
      console.error('日记对话失败:', err);
      removeMessage(thinkingId);
      addMessage('ai', '抱歉，网络错误，请稍后重试。');
    } finally {
      isAsking = false;
      chatSendBtn.disabled = false;
      chatSendBtn.textContent = '发送';
      chatInput.focus();
    }
  }

  // 添加用户消息
  function addMessage(role, content) {
    const msgEl = document.createElement('div');
    msgEl.className = `diary-chat-message ${role}`;
    msgEl.innerHTML = `
      <div class="diary-chat-avatar">${role === 'user' ? '🧑' : '🤖'}</div>
      <div class="diary-chat-bubble">
        <div class="diary-chat-content">${formatContent(content)}</div>
      </div>
    `;
    chatMessages.appendChild(msgEl);
    scrollToBottom();
    return msgEl;
  }

  // 添加 AI 消息（含引用来源和相关问题）
  function addAIMessage(answer, sources, relatedQuestions) {
    const msgEl = document.createElement('div');
    msgEl.className = 'diary-chat-message ai';

    // 引用来源 HTML
    let sourcesHtml = '';
    if (sources && sources.length > 0) {
      sourcesHtml = `
        <div class="diary-chat-sources">
          <div class="diary-chat-sources-title">📚 引用来源 (${sources.length} 篇日记)</div>
          <div class="diary-chat-sources-list">
            ${sources.map((s, idx) => `
              <div class="diary-chat-source-item" data-entry-id="${s.id}">
                <span class="diary-chat-source-num">${idx + 1}</span>
                <div class="diary-chat-source-info">
                  <span class="diary-chat-source-date">📅 ${s.date}</span>
                  ${s.title ? `<span class="diary-chat-source-title">${escapeHtml(s.title)}</span>` : ''}
                  <span class="diary-chat-source-snippet">${escapeHtml(s.snippet)}</span>
                  <span class="diary-chat-source-score">相似度 ${(s.score * 100).toFixed(1)}%</span>
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    // 相关问题 HTML
    let relatedHtml = '';
    if (relatedQuestions && relatedQuestions.length > 0) {
      relatedHtml = `
        <div class="diary-chat-related">
          <div class="diary-chat-related-title">💡 你可能还想问</div>
          <div class="diary-chat-related-list">
            ${relatedQuestions.map(q => `
              <button class="diary-chat-related-btn" type="button">${escapeHtml(q)}</button>
            `).join('')}
          </div>
        </div>
      `;
    }

    msgEl.innerHTML = `
      <div class="diary-chat-avatar">🤖</div>
      <div class="diary-chat-bubble">
        <div class="diary-chat-content">${formatContent(answer)}</div>
        ${sourcesHtml}
        ${relatedHtml}
      </div>
    `;
    chatMessages.appendChild(msgEl);

    // 绑定相关问题点击
    msgEl.querySelectorAll('.diary-chat-related-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        chatInput.value = btn.textContent;
        sendMessage();
      });
    });

    // 绑定引用来源点击（查看日记详情）
    msgEl.querySelectorAll('.diary-chat-source-item').forEach(item => {
      item.style.cursor = 'pointer';
      item.addEventListener('click', () => {
        const entryId = parseInt(item.dataset.entryId);
        viewEntryDetail(entryId);
      });
    });

    scrollToBottom();
    return msgEl;
  }

  // 添加思考中消息
  function addThinkingMessage() {
    const id = 'thinking-' + Date.now();
    const msgEl = document.createElement('div');
    msgEl.id = id;
    msgEl.className = 'diary-chat-message ai';
    msgEl.innerHTML = `
      <div class="diary-chat-avatar">🤖</div>
      <div class="diary-chat-bubble">
        <div class="diary-chat-thinking">
          <div class="diary-chat-typing">
            <span></span><span></span><span></span>
          </div>
          <p>正在检索你的日记，思考中...</p>
        </div>
      </div>
    `;
    chatMessages.appendChild(msgEl);
    scrollToBottom();
    return id;
  }

  // 移除消息
  function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  // 查看日记详情
  function viewEntryDetail(entryId) {
    const event = new CustomEvent('diary:view-entry', { detail: { entryId } });
    document.dispatchEvent(event);
    const listTab = document.querySelector('.diary-subtab[data-view="list"]');
    if (listTab) listTab.click();
  }

  // 格式化内容（简单的 Markdown 转换）
  function formatContent(text) {
    if (!text) return '';
    let html = escapeHtml(text);
    // 换行
    html = html.replace(/\n/g, '<br>');
    // 粗体 **text**
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // 列表项 - text
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    // 数字列表 1. text
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    return html;
  }

  // 滚动到底部
  function scrollToBottom() {
    setTimeout(() => {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }, 50);
  }

  // 工具函数
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
  }

  // 页面加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
