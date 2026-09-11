/**
 * Conversation archive view — display the recorded dialogues between the user
 * and the AI: user questions, extracted Zhihu originals, and my analyses.
 *
 * Each card expands to reveal the verbatim original (markdown) and the analysis
 * (markdown). Markdown is rendered via the global OpenBiliClawDialogueConfirmation
 * helper loaded from /shared/dialogue-confirmation.js.
 */

import { fetchConversationArchive, fetchConversationArchiveStats } from "../api.js";

const dialogueConfirmation = globalThis.OpenBiliClawDialogueConfirmation;

let $root = null;
let loaded = false;
let loading = false;
let items = [];
let total = 0;
let search = "";
let expanded = new Set(); // seq values currently expanded

const KIND_LABELS = {
  zhihu_eval: "知乎评析",
  concept_explain: "概念讲解",
};

const SOURCE_LABELS = {
  answer: "回答",
  pin: "想法",
  article: "文章",
};

function esc(s) {
  const el = document.createElement("span");
  el.textContent = s == null ? "" : String(s);
  return el.innerHTML;
}

function renderMd(md) {
  if (!md) return "";
  if (dialogueConfirmation && typeof dialogueConfirmation.renderMarkdown === "function") {
    return dialogueConfirmation.renderMarkdown(md);
  }
  return `<pre class="conv-md-fallback">${esc(md)}</pre>`;
}

function kindBadge(item) {
  const k = KIND_LABELS[item.kind] || item.kind || "";
  return k ? `<span class="conv-kind" data-kind="${esc(item.kind)}">${esc(k)}</span>` : "";
}

function sourceBadge(item) {
  const s = SOURCE_LABELS[item.source_type] || item.source_type || "";
  return s ? `<span class="conv-source">${esc(s)}</span>` : "";
}

function metaLine(item) {
  const parts = [];
  if (item.author) parts.push(`<span class="conv-author">${esc(item.author)}</span>`);
  if (item.voteup_count) parts.push(`<span class="conv-meta">👍 ${item.voteup_count}</span>`);
  if (item.comment_count) parts.push(`<span class="conv-meta">💬 ${item.comment_count}</span>`);
  if (item.published_at) parts.push(`<span class="conv-meta">🗓 ${esc(item.published_at)}</span>`);
  return parts.join("");
}

function tagsLine(item) {
  const tags = Array.isArray(item.tags) ? item.tags : [];
  if (!tags.length) return "";
  return `<div class="conv-tags">${tags
    .map((t) => `<span class="conv-tag">${esc(t)}</span>`)
    .join("")}</div>`;
}

function cardHtml(item) {
  const isOpen = expanded.has(item.seq);
  const hasOriginal = item.extracted_original_md && item.extracted_original_md.length > 50;
  const hasAnalysis = item.my_analysis_md && item.my_analysis_md.length > 50;
  const titleHtml = item.question_title
    ? `<div class="conv-title">${esc(item.question_title)}</div>`
    : "";
  const linkHtml = item.source_url
    ? `<a class="conv-link" href="${esc(item.source_url)}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>`
    : "";
  const questionHtml = item.user_question
    ? `<div class="conv-question">${esc(item.user_question)}</div>`
    : "";
  return `<article class="conv-card" data-seq="${item.seq}">
    <button class="conv-head" type="button" aria-expanded="${isOpen}" data-seq="${item.seq}">
      <span class="conv-seq">${item.seq}</span>
      <span class="conv-head-main">
        ${kindBadge(item)}
        ${sourceBadge(item)}
        <span class="conv-author-line">${metaLine(item)}</span>
      </span>
      <span class="conv-chevron" aria-hidden="true">${isOpen ? "▾" : "▸"}</span>
    </button>
    <div class="conv-body">
      ${titleHtml}
      ${questionHtml}
      ${linkHtml}
      ${tagsLine(item)}
      ${isOpen ? detailHtml(item, hasOriginal, hasAnalysis) : ""}
    </div>
  </article>`;
}

function detailHtml(item, hasOriginal, hasAnalysis) {
  let html = "";
  if (hasOriginal) {
    html += `<details class="conv-detail" open>
      <summary>📄 提取的原文</summary>
      <div class="conv-markdown">${renderMd(item.extracted_original_md)}</div>
    </details>`;
  }
  if (hasAnalysis) {
    html += `<details class="conv-detail" open>
      <summary>💡 我的分析</summary>
      <div class="conv-markdown conv-analysis">${renderMd(item.my_analysis_md)}</div>
    </details>`;
  }
  return html;
}

function render() {
  if (loading && !loaded) {
    $root.innerHTML = `<div class="conv-view"><div style="padding:40px"><div class="spinner"></div></div></div>`;
    return;
  }
  const head = `<div class="conv-head-bar">
    <span class="conv-head-icon">💬</span>
    <span class="conv-head-title">对话归档</span>
    <span class="conv-head-count">${total > 0 ? total : ""}</span>
  </div>
  <div class="conv-search">
    <input id="convSearch" class="conv-search-input" type="search" placeholder="搜索问题 / 作者 / 原文 / 分析…" value="${esc(search)}" aria-label="搜索对话归档">
  </div>`;
  if (!items.length && loaded) {
    $root.innerHTML = `<div class="conv-view">${head}<div class="conv-empty">${search ? "没有匹配的对话。" : "还没有归档的对话。"}</div></div>`;
    bindSearch();
    return;
  }
  const cards = items.map(cardHtml).join("");
  $root.innerHTML = `<div class="conv-view">${head}<div class="conv-list">${cards}</div></div>`;
  bindSearch();
  for (const head of $root.querySelectorAll(".conv-head")) {
    head.addEventListener("click", () => {
      const seq = Number(head.dataset.seq);
      if (expanded.has(seq)) expanded.delete(seq);
      else expanded.add(seq);
      render();
    });
  }
}

function bindSearch() {
  const input = $root.querySelector("#convSearch");
  if (!input) return;
  let t = null;
  input.addEventListener("input", () => {
    clearTimeout(t);
    t = setTimeout(() => {
      search = input.value.trim();
      loaded = false;
      void load();
    }, 250);
  });
}

async function load() {
  loading = true;
  render();
  try {
    const data = await fetchConversationArchive({
      limit: 100,
      offset: 0,
      search,
      sortBy: "seq",
      sortOrder: "ASC",
    });
    items = Array.isArray(data?.items) ? data.items : [];
    total = Number(data?.total) || items.length;
    loaded = true;
  } catch (err) {
    items = [];
    loaded = true;
    total = 0;
  }
  loading = false;
  render();
}

export function initConversationView(rootEl) {
  $root = rootEl;
  expanded = new Set();
  if (!loaded) {
    load();
  } else {
    render();
  }
}
