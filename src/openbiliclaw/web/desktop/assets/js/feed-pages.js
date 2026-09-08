// ── 推荐流页面（从 app.js 抽离，减少主文件体积）────────────────────────

/* ---------------------------------------------------------------- */
/*  Feed page open functions                                        */
/* ---------------------------------------------------------------- */

function openXhsFeedPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  showMainPage("xhsFeedPage");
  loadXhsFeedData();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openZhihuFeedPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  showMainPage("zhihuFeedPage");
  loadZhihuFeedData();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openBiliFeedPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  showMainPage("biliFeedPage");
  loadBiliFeedData();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openYoutubeFeedPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  showMainPage("youtubeFeedPage");
  loadYoutubeFeedData();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openV2exFeedPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  showMainPage("v2exFeedPage");
  loadV2exFeedData();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openXiaoyuzhouFeedPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  showMainPage("xiaoyuzhouFeedPage");
  loadXiaoyuzhouFeedData();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openAgentRecommendPage() {
  closeMobileMenu();
  document.querySelectorAll(".drawer.is-open, .overlay.is-open").forEach((panel) => closePanel(panel.id));
  showMainPage("agentRecommendPage");
  setTimeout(() => {
    const input = $("#agentRecommendInput");
    if (input) input.focus();
  }, 100);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------------------------------------------------------- */
/*  Feed meta bar helper                                            */
/* ---------------------------------------------------------------- */

function updateFeedMetaBar(sourceLabel, total, refreshFn) {
  const count = document.getElementById("feedMetaCount");
  const fab = document.getElementById("fabRefreshBtn");
  if (!count) return;
  count.removeAttribute("hidden");
  if (fab) {
    fab.removeAttribute("hidden");
    fab.onclick = () => refreshFn(true);
  }
  count.innerHTML = `<span>${sourceLabel} · 共 <strong>${total}</strong> 条推荐内容</span>`;
}

/* ---------------------------------------------------------------- */
/*  小红书推荐流                                                    */
/* ---------------------------------------------------------------- */

function loadXhsFeedData(bust = false) {
  const body = $("#xhsFeedBody");
  if (!body) return;
  body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

  const params = new URLSearchParams();
  params.set("source", "xhs-feed");
  if (bust) params.set("_", String(Date.now()));
  params.set("shuffle", "true");
  params.set("limit", "40");

  requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
    const items = data?.items || [];
    const total = data?.total || 0;
    updateFeedMetaBar("小红书推荐流", total, loadXhsFeedData);
    if (!items.length) {
      body.innerHTML = `
        <div class="obs-section">
          <div class="empty-state">
            <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
          </div>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="obs-section">
        <div class="card-grid" id="xhsFeedGrid"></div>
      </div>`;
    const grid = $("#xhsFeedGrid");
    if (!grid) return;
    grid.replaceChildren(
      ...items.map((item) => {
        const card = document.createElement("div");
        card.className = "video-card is-minimal";
        card.__itemData = item;
        card.innerHTML = xhsFeedCardHtml(item);
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          openRecommendation(item, card);
        });
        return card;
      })
    );
  }).catch(() => {
    body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
  });
}

function xhsFeedCardHtml(item) {
  const title = window.escapeHtml(item.title || "无标题");
  const author = window.escapeHtml(item.up_name || item.author_name || "");
  const url = window.escapeHtml(item.content_url || "");
  const status = item.pool_status || "";
  const score = item.quality_score || 0;
  const topic = window.escapeHtml(item.topic_group || "");
  const bvid = window.escapeHtml(item.bvid || "");

  return `
    <div class="video-card-cover is-empty">
      <div class="video-card-cover-ph">${window.platformLabelHtml("xiaohongshu")}</div>
    </div>
    <div class="video-card-body">
      <p class="video-card-title">${title}</p>
      <div class="video-card-meta">
        <span class="video-card-author">${author}</span>
        <span class="video-card-platform">${window.platformLabelHtml("xiaohongshu")}</span>
      </div>
      <div class="video-card-footer">
        <span class="video-card-status ${status}">${status}</span>
        ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
        ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
      </div>
    </div>
    <div class="video-card-actions">
      <button class="feedback-btn like-btn" data-bvid="${bvid}" data-action="like" title="喜欢">👍</button>
      <button class="feedback-btn dislike-btn" data-bvid="${bvid}" data-action="dislike" title="不喜欢">👎</button>
    </div>
    <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
  `;
}

/* ---------------------------------------------------------------- */
/*  知乎推荐流                                                      */
/* ---------------------------------------------------------------- */

function loadZhihuFeedData(bust = false) {
  const body = $("#zhihuFeedBody");
  if (!body) return;
  body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

  const params = new URLSearchParams();
  params.set("source", "zhihu-feed");
  if (bust) params.set("_", String(Date.now()));
  params.set("shuffle", "true");
  params.set("limit", "40");

  requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
    const items = data?.items || [];
    const total = data?.total || 0;
    updateFeedMetaBar("知乎推荐流", total, loadZhihuFeedData);
    if (!items.length) {
      body.innerHTML = `
        <div class="obs-section">
          <div class="empty-state">
            <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
          </div>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="obs-section">
        <div class="card-grid" id="zhihuFeedGrid"></div>
      </div>`;
    const grid = $("#zhihuFeedGrid");
    if (!grid) return;
    grid.replaceChildren(
      ...items.map((item) => {
        const card = document.createElement("div");
        card.className = "video-card is-minimal";
        card.innerHTML = zhihuFeedCardHtml(item);
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          openRecommendation(item, card);
        });
        card.querySelector("[data-action]")?.addEventListener("click", (e) => {
          e.stopPropagation();
          openRecommendation(item, card);
        });
        return card;
      })
    );
  }).catch(() => {
    body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
  });
}

function zhihuFeedCardHtml(item) {
  const title = window.escapeHtml(item.title || "无标题");
  const author = window.escapeHtml(item.up_name || item.author_name || "");
  const url = window.escapeHtml(item.content_url || "");
  const status = item.pool_status || "";
  const score = item.quality_score || 0;
  const topic = window.escapeHtml(item.topic_group || "");

  return `
    <div class="video-card-cover is-empty">
      <div class="video-card-cover-ph">${window.platformLabelHtml("zhihu")}</div>
    </div>
    <div class="video-card-body">
      <p class="video-card-title">${title}</p>
      <div class="video-card-meta">
        <span class="video-card-author">${author}</span>
        <span class="video-card-platform">${window.platformLabelHtml("zhihu")}</span>
      </div>
      <div class="video-card-footer">
        <span class="video-card-status ${status}">${status}</span>
        ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
        ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
      </div>
    </div>
    <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
  `;
}

/* ---------------------------------------------------------------- */
/*  B站推荐流                                                       */
/* ---------------------------------------------------------------- */

function loadBiliFeedData(bust = false) {
  const body = $("#biliFeedBody");
  if (!body) return;
  body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

  const params = new URLSearchParams();
  params.set("source", "bili-feed");
  if (bust) params.set("_", String(Date.now()));
  params.set("shuffle", "true");
  params.set("limit", "40");

  requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
    const items = data?.items || [];
    const total = data?.total || 0;
    updateFeedMetaBar("B站推荐流", total, loadBiliFeedData);
    if (!items.length) {
      body.innerHTML = `
        <div class="obs-section">
          <div class="empty-state">
            <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
          </div>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="obs-section">
        <div class="card-grid" id="biliFeedGrid"></div>
      </div>`;
    const grid = $("#biliFeedGrid");
    if (!grid) return;
    grid.replaceChildren(
      ...items.map((item) => {
        const card = document.createElement("div");
        card.className = "video-card is-minimal";
        card.innerHTML = biliFeedCardHtml(item);
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          openRecommendation(item, card);
        });
        card.querySelector("[data-action]")?.addEventListener("click", (e) => {
          e.stopPropagation();
          openRecommendation(item, card);
        });
        return card;
      })
    );
  }).catch(() => {
    body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
  });
}

function biliFeedCardHtml(item) {
  const title = window.escapeHtml(item.title || "无标题");
  const author = window.escapeHtml(item.up_name || item.author_name || "");
  const url = window.escapeHtml(item.content_url || "");
  const status = item.pool_status || "";
  const score = item.quality_score || 0;
  const topic = window.escapeHtml(item.topic_group || "");

  return `
    <div class="video-card-cover is-empty">
      <div class="video-card-cover-ph">${window.platformLabelHtml("bilibili")}</div>
    </div>
    <div class="video-card-body">
      <p class="video-card-title">${title}</p>
      <div class="video-card-meta">
        <span class="video-card-author">${author}</span>
        <span class="video-card-platform">${window.platformLabelHtml("bilibili")}</span>
      </div>
      <div class="video-card-footer">
        <span class="video-card-status ${status}">${status}</span>
        ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
        ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
      </div>
    </div>
    <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
  `;
}

/* ---------------------------------------------------------------- */
/*  YouTube 推荐流                                                  */
/* ---------------------------------------------------------------- */

function loadYoutubeFeedData(bust = false) {
  const body = $("#youtubeFeedBody");
  if (!body) return;
  body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

  const params = new URLSearchParams();
  params.set("source", "youtube-feed");
  if (bust) params.set("_", String(Date.now()));
  params.set("shuffle", "true");
  params.set("limit", "40");

  requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
    const items = data?.items || [];
    const total = data?.total || 0;
    updateFeedMetaBar("YouTube推荐流", total, loadYoutubeFeedData);
    if (!items.length) {
      body.innerHTML = `
        <div class="obs-section">
          <div class="empty-state">
            <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
          </div>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="obs-section">
        <div class="card-grid" id="youtubeFeedGrid"></div>
      </div>`;
    const grid = $("#youtubeFeedGrid");
    if (!grid) return;
    grid.replaceChildren(
      ...items.map((item) => {
        const card = document.createElement("div");
        card.className = "video-card is-minimal";
        card.innerHTML = youtubeFeedCardHtml(item);
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          openRecommendation(item, card);
        });
        card.querySelector("[data-action]")?.addEventListener("click", (e) => {
          e.stopPropagation();
          openRecommendation(item, card);
        });
        return card;
      })
    );
  }).catch(() => {
    body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
  });
}

function youtubeFeedCardHtml(item) {
  const title = window.escapeHtml(item.title || "无标题");
  const author = window.escapeHtml(item.up_name || item.author_name || "");
  const url = window.escapeHtml(item.content_url || "");
  const status = item.pool_status || "";
  const score = item.quality_score || 0;
  const topic = window.escapeHtml(item.topic_group || "");

  return `
    <div class="video-card-cover is-empty">
      <div class="video-card-cover-ph">${window.platformLabelHtml("youtube")}</div>
    </div>
    <div class="video-card-body">
      <p class="video-card-title">${title}</p>
      <div class="video-card-meta">
        <span class="video-card-author">${author}</span>
        <span class="video-card-platform">${window.platformLabelHtml("youtube")}</span>
      </div>
      <div class="video-card-footer">
        <span class="video-card-status ${status}">${status}</span>
        ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
        ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
      </div>
    </div>
    <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
  `;
}

/* ---------------------------------------------------------------- */
/*  V2EX 推荐流                                                     */
/* ---------------------------------------------------------------- */

function loadV2exFeedData(bust = false) {
  const body = $("#v2exFeedBody");
  if (!body) return;
  body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

  const params = new URLSearchParams();
  params.set("source", "v2ex-feed");
  if (bust) params.set("_", String(Date.now()));
  params.set("shuffle", "true");
  params.set("limit", "40");

  requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
    const items = data?.items || [];
    const total = data?.total || 0;
    updateFeedMetaBar("V2EX推荐流", total, loadV2exFeedData);
    if (!items.length) {
      body.innerHTML = `
        <div class="obs-section">
          <div class="empty-state">
            <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
          </div>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="obs-section">
        <div class="card-grid" id="v2exFeedGrid"></div>
      </div>`;
    const grid = $("#v2exFeedGrid");
    if (!grid) return;
    grid.replaceChildren(
      ...items.map((item) => {
        const card = document.createElement("div");
        card.className = "video-card is-minimal";
        card.innerHTML = v2exFeedCardHtml(item);
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          openRecommendation(item, card);
        });
        card.querySelector("[data-action]")?.addEventListener("click", (e) => {
          e.stopPropagation();
          openRecommendation(item, card);
        });
        return card;
      })
    );
  }).catch(() => {
    body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
  });
}

function v2exFeedCardHtml(item) {
  const title = window.escapeHtml(item.title || "无标题");
  const author = window.escapeHtml(item.up_name || item.author_name || "");
  const url = window.escapeHtml(item.content_url || "");
  const status = item.pool_status || "";
  const score = item.quality_score || 0;
  const topic = window.escapeHtml(item.topic_group || "");

  return `
    <div class="video-card-cover is-empty">
      <div class="video-card-cover-ph">${window.platformLabelHtml("v2ex")}</div>
    </div>
    <div class="video-card-body">
      <p class="video-card-title">${title}</p>
      <div class="video-card-meta">
        <span class="video-card-author">${author}</span>
        <span class="video-card-platform">${window.platformLabelHtml("v2ex")}</span>
      </div>
      <div class="video-card-footer">
        <span class="video-card-status ${status}">${status}</span>
        ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
        ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
      </div>
    </div>
    <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
  `;
}

/* ---------------------------------------------------------------- */
/*  小宇宙推荐流                                                    */
/* ---------------------------------------------------------------- */

function loadXiaoyuzhouFeedData(bust = false) {
  const body = $("#xiaoyuzhouFeedBody");
  if (!body) return;
  body.innerHTML = `<div class="observability-loading">正在加载…</div>`;

  const params = new URLSearchParams();
  params.set("source", "xiaoyuzhou-feed");
  if (bust) params.set("_", String(Date.now()));
  params.set("shuffle", "true");
  params.set("limit", "40");

  requestJson(`${ENDPOINTS.poolFeed}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
    const items = data?.items || [];
    const total = data?.total || 0;
    updateFeedMetaBar("小宇宙推荐流", total, loadXiaoyuzhouFeedData);
    if (!items.length) {
      body.innerHTML = `
        <div class="obs-section">
          <div class="empty-state">
            <p>暂无内容，推荐流将在下次抓取后更新（每 3 小时一次）</p>
          </div>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="obs-section">
        <div class="card-grid" id="xiaoyuzhouFeedGrid"></div>
      </div>`;
    const grid = $("#xiaoyuzhouFeedGrid");
    if (!grid) return;
    grid.replaceChildren(
      ...items.map((item) => {
        const card = document.createElement("div");
        card.className = "video-card is-minimal";
        card.innerHTML = xiaoyuzhouFeedCardHtml(item);
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          openRecommendation(item, card);
        });
        card.querySelector("[data-action]")?.addEventListener("click", (e) => {
          e.stopPropagation();
          openRecommendation(item, card);
        });
        return card;
      })
    );
  }).catch(() => {
    body.innerHTML = `<div class="empty-state">请求失败，请检查后端连接</div>`;
  });
}

function xiaoyuzhouFeedCardHtml(item) {
  const title = window.escapeHtml(item.title || "无标题");
  const author = window.escapeHtml(item.up_name || item.author_name || "");
  const url = window.escapeHtml(item.content_url || "");
  const status = item.pool_status || "";
  const score = item.quality_score || 0;
  const topic = window.escapeHtml(item.topic_group || "");

  return `
    <div class="video-card-cover is-empty">
      <div class="video-card-cover-ph">${window.platformLabelHtml("xiaoyuzhou")}</div>
    </div>
    <div class="video-card-body">
      <p class="video-card-title">${title}</p>
      <div class="video-card-meta">
        <span class="video-card-author">${author}</span>
        <span class="video-card-platform">${window.platformLabelHtml("xiaoyuzhou")}</span>
      </div>
      <div class="video-card-footer">
        <span class="video-card-status ${status}">${status}</span>
        ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
        ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
      </div>
    </div>
    <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
  `;
}

/* ---------------------------------------------------------------- */
/*  Agent 推荐 - 辅助函数                                           */
/* ---------------------------------------------------------------- */

let _agentSessionId = "sess_" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);

function resetAgentSession() {
  _agentSessionId = "sess_" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);
  localStorage.setItem("agentSessionId", _agentSessionId);
  const ctx = $("#agentSessionContext");
  if (ctx) ctx.style.display = "none";
}

function loadAgentRecommendData(query) {
  const body = $("#agentRecommendBody");
  if (!body) return;
  body.innerHTML = `<div class="observability-loading">正在搜索「${window.escapeHtml(query)}」…</div>`;

  saveAgentSearchHistory(query);

  const params = new URLSearchParams();
  if (query) params.set("query", query);
  params.set("_", String(Date.now()));
  params.set("limit", "40");

  requestJson(`${ENDPOINTS.agentRecommend}?${params.toString()}`, { timeoutMs: 60000 }).then((data) => {
    const items = data?.items || [];
    const total = data?.total || 0;
    if (!items.length) {
      body.innerHTML = `
        <div class="obs-section">
          <div class="empty-state">
            <p style="font-size:16px;margin-bottom:8px;">没有找到相关内容</p>
            <p style="font-size:13px;color:var(--text-secondary);">试试换一个搜索词，或者调整搜索方向</p>
          </div>
        </div>`;
      return;
    }
    body.innerHTML = `
      <div class="obs-section">
        <div class="obs-section-header">
          <span>共找到 <strong>${total}</strong> 条结果</span>
          <button class="pill-btn dark" id="agentRecommendRefreshBtn" type="button" data-query="${window.escapeHtml(query)}">换一批</button>
        </div>
        <div class="card-grid" id="agentRecommendGrid"></div>
      </div>`;
    const grid = $("#agentRecommendGrid");
    if (!grid) return;
    grid.replaceChildren(
      ...items.map((item) => {
        const card = document.createElement("div");
        card.className = "video-card is-minimal";
        card.__itemData = item;
        card.innerHTML = agentRecommendCardHtml(item, query);
        card.addEventListener("click", (e) => {
          if (e.target.closest("[data-action]")) return;
          openRecommendation(item, card);
        });
        card.querySelector("[data-action]")?.addEventListener("click", (e) => {
          e.stopPropagation();
          openRecommendation(item, card);
        });
        return card;
      })
    );
  }).catch(() => {
    body.innerHTML = `<div class="empty-state">搜索失败，请检查后端连接</div>`;
  });
}

function agentRecommendCardHtml(item, query) {
  const title = window.escapeHtml(item.title || "无标题");
  const author = window.escapeHtml(item.up_name || item.author_name || "");
  const url = window.escapeHtml(item.content_url || "");
  const status = item.pool_status || "";
  const score = item.quality_score || 0;
  const platform = item.source_platform || "";
  const topic = window.escapeHtml(item.topic_group || "");
  const excerpt = window.escapeHtml((item.body_text || "").slice(0, 120));

  let highlightedTitle = title;
  if (query) {
    const keywords = query.split(/[,，、\s]+/).filter(Boolean);
    for (const kw of keywords) {
      if (kw.length < 2) continue;
      const escapedKw = kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      highlightedTitle = highlightedTitle.replace(
        new RegExp(escapedKw, "gi"),
        (match) => `<mark class="kw-highlight">${window.escapeHtml(match)}</mark>`
      );
    }
  }

  return `
    <div class="video-card-cover is-empty">
      <div class="video-card-cover-ph">${window.platformLabelHtml(platform)}</div>
    </div>
    <div class="video-card-body">
      <p class="video-card-title">${highlightedTitle}</p>
      ${excerpt ? `<p class="video-card-excerpt">${excerpt}</p>` : ""}
      <div class="video-card-meta">
        <span class="video-card-author">${author}</span>
        <span class="video-card-platform">${window.platformLabelHtml(platform)}</span>
      </div>
      <div class="video-card-footer">
        <span class="video-card-status ${status}">${status}</span>
        ${score > 0 ? `<span class="video-card-score">${(score * 100).toFixed(0)}</span>` : ""}
        ${topic ? `<span class="video-card-topic">${topic}</span>` : ""}
      </div>
    </div>
    <div class="video-card-actions">
      <button class="feedback-btn like-btn" data-bvid="${window.escapeHtml(item.bvid)}" data-action="like" title="喜欢">👍</button>
      <button class="feedback-btn dislike-btn" data-bvid="${window.escapeHtml(item.bvid)}" data-action="dislike" title="不喜欢">👎</button>
    </div>
    <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
  `;
}

/* ---------------------------------------------------------------- */
/*  Agent 搜索历史                                                  */
/* ---------------------------------------------------------------- */

function getAgentSearchHistory() {
  try {
    return JSON.parse(localStorage.getItem("agentSearchHistory") || "[]");
  } catch { return []; }
}

function saveAgentSearchHistory(query) {
  const q = query.trim();
  if (!q) return;
  let history = getAgentSearchHistory();
  history = history.filter((h) => h !== q);
  history.unshift(q);
  if (history.length > 10) history = history.slice(0, 10);
  try {
    localStorage.setItem("agentSearchHistory", JSON.stringify(history));
  } catch { /* ignore */ }
  renderAgentSearchHistory();
}

function renderAgentSearchHistory() {
  const history = getAgentSearchHistory();
  const container = $("#recentSearchTags");
  const hints = $("#agentRecentHints");
  if (!container || !hints) return;
  if (history.length === 0) {
    hints.style.display = "none";
    return;
  }
  hints.style.display = "";
  container.innerHTML = history
    .map((h) => `<span class="agent-hint" data-hint="${window.escapeHtml(h)}">${window.escapeHtml(h)}</span>`)
    .join("");
}

/* ---------------------------------------------------------------- */
/*  Agent 兴趣标签                                                  */
/* ---------------------------------------------------------------- */

function loadInterestTags() {
  const container = $("#agentInterestTags");
  if (!container) return;
  requestJson(ENDPOINTS.interestTags + "?limit=20", { timeoutMs: 10000 })
    .then((data) => {
      const tags = data?.tags || [];
      if (!tags.length) {
        container.style.display = "none";
        return;
      }
      container.style.display = "";
      container.innerHTML = tags.map((t) => {
        const platforms = t.source_platforms?.length
          ? t.source_platforms.map((p) => window.platformLabelHtml(p)).join(" ")
          : "";
        return `<span class="interest-tag" title="${t.count} 次点赞${platforms ? ' · ' + t.source_platforms.join(', ') : ''}">
          ${window.escapeHtml(t.tag)} <small>${t.weight}</small>
          ${platforms ? `<span class="interest-tag-platforms">${platforms}</span>` : ""}
        </span>`;
      }).join("");
    })
    .catch(() => { container.style.display = "none"; });
}

/* ---------------------------------------------------------------- */
/*  注册页面路由（DESKTOP_PAGE_ROUTES）                             */
/* ---------------------------------------------------------------- */

// Register routes after app.js has initialized (app.js loads before feed-pages.js, both with defer)
// app.js defines DESKTOP_PAGE_ROUTES and all helper functions, but feed routes were removed from there.
// We register them here, and re-route if the current URL matches a feed page.
(function registerFeedRoutes() {
  const FEED_ROUTES = {
    "xhs-feed": openXhsFeedPage,
    "zhihu-feed": openZhihuFeedPage,
    "bili-feed": openBiliFeedPage,
    "youtube-feed": openYoutubeFeedPage,
    "v2ex-feed": openV2exFeedPage,
    "xiaoyuzhou-feed": openXiaoyuzhouFeedPage,
    "agent-recommend": openAgentRecommendPage,
  };

  if (window.DESKTOP_PAGE_ROUTES) {
    for (const [key, fn] of Object.entries(FEED_ROUTES)) {
      window.DESKTOP_PAGE_ROUTES[key] = fn;
    }
  }

  // Re-route if the current URL matches a feed page (the initial routeFromPath
  // in app.js already ran but couldn't find these routes)
  const match = (location.pathname || "/web").match(/^\/web\/([a-zA-Z0-9-]+)\/?$/);
  const page = match ? match[1] : null;
  if (page && FEED_ROUTES[page]) {
    FEED_ROUTES[page]();
  }
})();

/* ---------------------------------------------------------------- */
/*  Feed 事件绑定                                                   */
/* ---------------------------------------------------------------- */

(function bindFeedPageEvents() {
  // All helper functions are available from app.js scope via window
  const safeBind = window.safeBind;
  const eventDelegation = window.eventDelegation;
  const closeFeedDropdown = window.closeFeedDropdown;
  const navigateTo = window.navigateTo;

  safeBind("#xhsFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/xhs-feed"); });
  eventDelegation("#xhsFeedBody", "#xhsFeedRefreshBtn", "click", () => loadXhsFeedData(true));
  eventDelegation("#xhsFeedBody", ".feedback-btn", "click", (e) => {
    e.stopPropagation();
    const btn = e.target.closest(".feedback-btn");
    if (!btn) return;
    const bvid = btn.getAttribute("data-bvid") || "";
    const action = btn.getAttribute("data-action") || "";
    if (!bvid || !action) return;
    if (btn.classList.contains("is-active")) {
      window.removeFeedback(bvid, action);
    } else {
      const card = btn.closest(".video-card");
      window.sendFeedback(bvid, action, card ? card.__itemData : null);
    }
  });
  safeBind("#zhihuFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/zhihu-feed"); });
  safeBind("#biliFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/bili-feed"); });
  eventDelegation("#zhihuFeedBody", "#zhihuFeedRefreshBtn", "click", () => loadZhihuFeedData(true));
  eventDelegation("#biliFeedBody", "#biliFeedRefreshBtn", "click", () => loadBiliFeedData(true));
  safeBind("#youtubeFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/youtube-feed"); });
  eventDelegation("#youtubeFeedBody", "#youtubeFeedRefreshBtn", "click", () => loadYoutubeFeedData(true));
  safeBind("#v2exFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/v2ex-feed"); });
  eventDelegation("#v2exFeedBody", "#v2exFeedRefreshBtn", "click", () => loadV2exFeedData(true));
  safeBind("#xiaoyuzhouFeedBtn", "click", () => { closeFeedDropdown(); navigateTo("/web/xiaoyuzhou-feed"); });
  eventDelegation("#xiaoyuzhouFeedBody", "#xiaoyuzhouFeedRefreshBtn", "click", () => loadXiaoyuzhouFeedData(true));
  safeBind("#agentRecommendBtn", "click", () => {
    navigateTo("/web/agent-recommend");
    setTimeout(() => loadInterestTags(), 200);
  });
  safeBind("#agentRecommendSearchBtn", "click", () => {
    const input = window.$("#agentRecommendInput");
    if (input && input.value.trim()) {
      loadAgentRecommendData(input.value.trim());
    }
  });
  safeBind("#agentRecommendInput", "keydown", (e) => {
    if (e.key === "Enter" && e.target.value.trim()) {
      loadAgentRecommendData(e.target.value.trim());
    }
    if (e.key === "Escape") {
      e.target.value = "";
      const clearBtn = window.$("#agentRecommendClearBtn");
      if (clearBtn) clearBtn.style.display = "none";
    }
  });
  safeBind("#agentRecommendInput", "input", (e) => {
    const clearBtn = window.$("#agentRecommendClearBtn");
    if (clearBtn) clearBtn.style.display = e.target.value.trim() ? "inline-block" : "none";
  });
  safeBind("#agentRecommendClearBtn", "click", () => {
    const input = window.$("#agentRecommendInput");
    if (input) {
      input.value = "";
      input.focus();
    }
    const clearBtn = window.$("#agentRecommendClearBtn");
    if (clearBtn) clearBtn.style.display = "none";
  });
  safeBind("#agentResetSessionBtn", "click", () => {
    resetAgentSession();
    const input = window.$("#agentRecommendInput");
    if (input) input.value = "";
    const body = window.$("#agentRecommendBody");
    if (body) body.innerHTML = "";
    window.showToast("已重置筛选条件");
  });
  eventDelegation("#agentRecommendBody", "#agentRecommendRefreshBtn", "click", (e) => {
    const query = e.target.getAttribute("data-query") || "";
    if (query) loadAgentRecommendData(query);
  });
  eventDelegation("#agentRecommendPage", ".agent-hint", "click", (e) => {
    const hint = e.target.getAttribute("data-hint") || "";
    if (hint) {
      const input = window.$("#agentRecommendInput");
      if (input) input.value = hint;
      const clearBtn = window.$("#agentRecommendClearBtn");
      if (clearBtn) clearBtn.style.display = "inline-block";
      loadAgentRecommendData(hint);
    }
  });
  renderAgentSearchHistory();

  eventDelegation("#agentRecommendPage", ".feedback-btn", "click", (e) => {
    e.stopPropagation();
    const btn = e.target.closest(".feedback-btn");
    if (!btn) return;
    const bvid = btn.getAttribute("data-bvid") || "";
    const action = btn.getAttribute("data-action") || "";
    if (!bvid || !action) return;
    if (btn.classList.contains("is-active")) {
      window.removeFeedback(bvid, action);
    } else {
      const card = btn.closest(".video-card");
      let item = null;
      if (card) {
        const idx = Array.from(card.parentNode?.children || []).indexOf(card);
        const grid = card.closest("#agentRecommendGrid");
        if (grid) {
          item = card.__itemData;
        }
      }
      window.sendFeedback(bvid, action, item);
    }
  });
})();