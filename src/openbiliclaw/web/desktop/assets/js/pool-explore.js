// ── 池子探索 — extracted from app.js
(function() {
const OBC = window.OBC;
if (!OBC) { console.error("pool-explore.js: window.OBC not found — load app.js first"); return; }

// ── 池子探索 ──────────────────────────────────────────────────────────

    function loadPoolExploreData() {
      const body = $("#poolExploreBody");
      if (!body) return;
      body.innerHTML = `<div class="observability-loading">正在加载筛选条件…</div>`;

      // 先获取观测数据中的 topic_group 列表
      OBC.requestJson("/observability", { timeoutMs: 30000 }).then((obs) => {
        const topicGroups = (obs?.topic_groups || []).map(t => t.topic);
        renderPoolExploreFilters(body, topicGroups);
        doPoolExploreQuery(body);
      }).catch(() => {
        body.innerHTML = `<div class="empty-OBC.state">请求失败，请检查后端连接。</div>`;
      });
    }

    function renderPoolExploreFilters(container, topicGroups) {
      const f = OBC.state.poolExploreFilters || {};
      const platforms = ["bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "v2ex", "reddit", "wechat", "xiaoyuzhou", "rss"];
      const statuses = ["fresh", "shown", "stale", "suppressed", "feedbacked"];

      container.innerHTML = `
        <div class="obs-section">
          <h3 class="obs-section-title">筛选条件</h3>
          <div class="pex-filter-grid">
            <div class="pex-filter-group">
              <label class="pex-filter-label">平台</label>
              <select class="pex-filter-select" id="pexPlatform">
                <option value="">全部</option>
                ${platforms.map(p => `<option value="${p}"${f.platform === p ? " selected" : ""}>${platformLabelHtml(p)}</option>`).join("")}
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">状态</label>
              <select class="pex-filter-select" id="pexStatus">
                <option value="">全部</option>
                ${statuses.map(s => `<option value="${s}"${f.status === s ? " selected" : ""}>${s}</option>`).join("")}
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">最低评分</label>
              <select class="pex-filter-select" id="pexMinScore">
                <option value="">不限</option>
                <option value="0"${f.min_score === 0 ? " selected" : ""}>0+ (全部)</option>
                <option value="0.2"${f.min_score === 0.2 ? " selected" : ""}>0.2+</option>
                <option value="0.4"${f.min_score === 0.4 ? " selected" : ""}>0.4+</option>
                <option value="0.6"${f.min_score === 0.6 ? " selected" : ""}>0.6+</option>
                <option value="0.8"${f.min_score === 0.8 ? " selected" : ""}>0.8+</option>
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">评分状态</label>
              <select class="pex-filter-select" id="pexScored">
                <option value="">全部</option>
                <option value="scored"${f.scored_only ? " selected" : ""}>已评分</option>
                <option value="unscored"${f.unscored_only ? " selected" : ""}>未评分</option>
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">内容链接</label>
              <select class="pex-filter-select" id="pexHasUrl">
                <option value="">全部</option>
                <option value="yes"${f.has_url === true ? " selected" : ""}>有链接</option>
                <option value="no"${f.has_url === false ? " selected" : ""}>无链接</option>
              </select>
            </div>
            <div class="pex-filter-group">
              <label class="pex-filter-label">主题标签</label>
              <select class="pex-filter-select" id="pexTopicGroup">
                <option value="">全部</option>
                ${topicGroups.map(t => `<option value="${t}"${f.topic_group === t ? " selected" : ""}>${OBC.escapeHtml(t)}</option>`).join("")}
              </select>
            </div>
          </div>
          <div class="pex-filter-actions">
            <button class="pill-btn dark" id="pexApplyBtn" type="button">应用筛选</button>
            <button class="pill-btn" id="pexResetBtn" type="button">重置</button>
            <span class="pex-filter-hint" id="pexFilterHint">共 <strong id="pexTotalCount">—</strong> 条匹配</span>
          </div>
        </div>
        <div class="obs-section">
          <h3 class="obs-section-title">筛选结果</h3>
          <div class="card-grid" id="pexResultGrid">
            <div class="empty-OBC.state">点击"应用筛选"查看结果</div>
          </div>
          <div class="pool-all-footer">随机展示 6 条，共 <span id="pexResultTotal">—</span> 条匹配</div>
        </div>
      `;

      OBC.safeBind("#pexApplyBtn", "click", () => {
        readPoolExploreFilters();
        doPoolExploreQuery(container);
      });
      OBC.safeBind("#pexResetBtn", "click", () => {
        OBC.state.poolExploreFilters = {};
        loadPoolExploreData();
      });
    }

    function readPoolExploreFilters() {
      const f = {};
      const plat = $("#pexPlatform")?.value;
      if (plat) f.platform = plat;
      const status = $("#pexStatus")?.value;
      if (status) f.status = status;
      const minScore = $("#pexMinScore")?.value;
      if (minScore !== "" && minScore !== undefined) f.min_score = parseFloat(minScore);
      const scored = $("#pexScored")?.value;
      if (scored === "scored") f.scored_only = true;
      if (scored === "unscored") f.unscored_only = true;
      const hasUrl = $("#pexHasUrl")?.value;
      if (hasUrl === "yes") f.has_url = true;
      if (hasUrl === "no") f.has_url = false;
      const topic = $("#pexTopicGroup")?.value;
      if (topic) f.topic_group = topic;
      OBC.state.poolExploreFilters = f;
    }

    function doPoolExploreQuery(container) {
      const f = OBC.state.poolExploreFilters || {};
      const params = new URLSearchParams();
      params.set("shuffle", "true");
      params.set("limit", "6");
      if (f.platform) params.set("platform", f.platform);
      if (f.status) params.set("status", f.status);
      if (f.min_score !== undefined && f.min_score !== null) params.set("min_score", String(f.min_score));
      if (f.scored_only) params.set("scored_only", "true");
      if (f.unscored_only) params.set("unscored_only", "true");
      if (f.has_url === true || f.has_url === false) params.set("has_url", f.has_url ? "true" : "false");
      if (f.topic_group) params.set("topic_group", f.topic_group);

      const hint = $("#pexFilterHint");
      const grid = $("#pexResultGrid");
      const totalEl = $("#pexResultTotal");
      if (hint) hint.innerHTML = "查询中…";
      if (grid) grid.innerHTML = `<div class="empty-OBC.state">正在查询…</div>`;

      OBC.requestJson(`${OBC.ENDPOINTS.poolAll}?${params.toString()}`, { timeoutMs: 30000 }).then((data) => {
        const items = data?.items || [];
        const total = data?.total || 0;
        if (hint) hint.innerHTML = `共 <strong>${total}</strong> 条匹配`;
        if (totalEl) totalEl.textContent = String(total);
        if (grid) {
          if (!items.length) {
            grid.innerHTML = `<div class="empty-OBC.state">没有匹配的内容</div>`;
          } else {
            grid.replaceChildren(
              ...items.map((item) => {
                const card = document.createElement("div");
                card.className = "video-card is-minimal";
                card.innerHTML = poolExploreCardHtml(item);
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
          }
        }
      }).catch(() => {
        if (hint) hint.innerHTML = "查询失败";
        if (grid) grid.innerHTML = `<div class="empty-OBC.state">请求失败，请检查后端连接</div>`;
      });
    }

    // --- Implicit feedback: dwell-time tracking (停留时长) ---
    const DWELL_CAP_SECONDS = 1800; // cap at 30 min per view
    const DWELL_MIN_SECONDS = 1;    // ignore accidental clicks

    function getPendingDwell() {
      try { return JSON.parse(sessionStorage.getItem("pendingDwell") || "null"); }
      catch { return null; }
    }

    function setPendingDwell(entry) {
      try { sessionStorage.setItem("pendingDwell", JSON.stringify(entry)); }
      catch { /* ignore */ }
    }

    function clearPendingDwell() {
      try { sessionStorage.removeItem("pendingDwell"); }
      catch { /* ignore */ }
    }

    function sendDwellReport(bvid, dwellSeconds, useBeacon) {
      if (!bvid || dwellSeconds < DWELL_MIN_SECONDS) return;
      const payload = JSON.stringify({ bvid, dwell_seconds: Math.round(dwellSeconds) });
      if (useBeacon && navigator.sendBeacon) {
        try {
          // sendBeacon 不走 requestJson 的 base 拼接，这里显式补上 /api 前缀
          navigator.sendBeacon("/api" + OBC.ENDPOINTS.viewDwell, new Blob([payload], { type: "application/json" }));
          return;
        } catch { /* fall through to fetch */ }
      }
      void OBC.requestJson(OBC.ENDPOINTS.viewDwell, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
      }).catch(() => {});
    }

    // Settle the pending view: compute dwell since open and report it.
    function finalizePendingDwell(useBeacon = false) {
      const pending = getPendingDwell();
      if (!pending || !pending.openedAt) { clearPendingDwell(); return; }
      clearPendingDwell();
      const dwellSeconds = Math.min((Date.now() - pending.openedAt) / 1000, DWELL_CAP_SECONDS);
      sendDwellReport(pending.bvid, dwellSeconds, useBeacon);
    }

    // Record the view immediately, then track dwell until user returns.
    function beginDwellTracking(item) {
      if (!item || !item.bvid) return;
      finalizePendingDwell();
      void OBC.requestJson(OBC.ENDPOINTS.viewRecord, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bvid: item.bvid,
          title: item.title || "",
          source_platform: item.source_platform || item.platform || "",
          topic_group: item.topic_group || item.topic || item.topic_label || "",
          content_url: item.content_url || "",
          up_name: item.up_name || item.up || "",
          quality_score: item.quality_score || 0,
        }),
      }).catch(() => {});
      setPendingDwell({ bvid: item.bvid, openedAt: Date.now() });
    }

    // User returns to this tab → settle the pending dwell.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") finalizePendingDwell();
    });
    // Page closed with a pending view → best-effort beacon report.
    window.addEventListener("pagehide", () => finalizePendingDwell(true));

    function sendFeedback(bvid, action, item) {
      const payload = { bvid, action };
      if (item) {
        payload.source_platform = item.source_platform || "";
        payload.title = item.title || "";
        payload.topic_group = item.topic_group || "";
        payload.body_text = (item.body_text || "").slice(0, 200);
      }
      OBC.requestJson(OBC.ENDPOINTS.userFeedback, {
        method: "POST",
        body: JSON.stringify(payload),
        headers: { "Content-Type": "application/json" },
      }).then((res) => {
        if (res && res.ok) {
          // Toggle visual OBC.state
          const btn = document.querySelector(`.feedback-btn[data-bvid="${bvid}"][data-action="${action}"]`);
          if (btn) btn.classList.add("is-active");
          // Also remove the opposite active OBC.state
          const opposite = action === "like" ? "dislike" : "like";
          const oppBtn = document.querySelector(`.feedback-btn[data-bvid="${bvid}"][data-action="${opposite}"]`);
          if (oppBtn) oppBtn.classList.remove("is-active");
        }
      }).catch(() => {});
    }

    function removeFeedback(bvid, action) {
      OBC.requestJson(`${OBC.ENDPOINTS.userFeedback}?bvid=${encodeURIComponent(bvid)}&action=${action}`, {
        method: "DELETE",
      }).then((res) => {
        if (res && res.ok) {
          const btn = document.querySelector(`.feedback-btn[data-bvid="${bvid}"][data-action="${action}"]`);
          if (btn) btn.classList.remove("is-active");
        }
      }).catch(() => {});
    }
    window.removeFeedback = removeFeedback;
    window.sendFeedback = sendFeedback;
    
    function poolExploreCardHtml(item) {
      const title = OBC.escapeHtml(item.title || "无标题");
      const author = OBC.escapeHtml(item.up_name || item.author_name || "");
      const platform = OBC.escapeHtml(item.source_platform || "");
      const platLabel = platformLabelHtml(platform);
      const status = item.pool_status || "";
      const score = item.quality_score ? item.quality_score.toFixed(3) : "—";
      const topic = OBC.escapeHtml(item.topic_group || "");
      return `
        <p class="video-card-title">${title}</p>
        <div class="video-card-meta">
          <span class="pex-badge pex-badge-${platform}">${platLabel}</span>
          ${status ? `<span class="pool-status-badge">${status}</span>` : ""}
          ${author ? `<span class="video-card-author">${author}</span>` : ""}
        </div>
        <div class="video-card-stats" style="margin-top:4px;font-size:11px;color:var(--text-secondary)">
          评分: ${score}${topic ? ` · ${topic}` : ""}
        </div>
        <div class="video-card-actions">
          <button class="feedback-icon-btn" data-action="open" type="button" aria-label="打开原文" title="打开原文">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          </button>
        </div>
      `;
    }

    function openRecommendation(item, card) {
      const url = contentUrl(item);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      trackRecommendationClick(item);
      beginDwellTracking(item);
      const statusLine = card?.querySelector(".status-line");
      if (statusLine) statusLine.textContent = url ? "已打开真实内容链接，点击信号会在后台记录。" : "后端没有返回可打开链接；点击信号会在后台记录。";
      OBC.showToast(url ? `打开：${item.title}` : "后端没有返回可打开链接");
    }
    window.openRecommendation = openRecommendation;
    
    async function submitFeedback(item, feedback_type, note = "") {
      return await OBC.requestJsonStrict(OBC.ENDPOINTS.feedback, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendation_id: item.id, feedback_type, note }),
        timeoutMs: 30000
      });
    }

    function recommendationRemoveDelay() {
      return isMobileViewport() ? 1000 : 2400;
    }

    function removeRecommendationCard(item, card, message, delayMs = recommendationRemoveDelay()) {
      const key = recommendationKey(item);
      window.setTimeout(() => {
        if (card) card.classList.add("is-removing");
        window.setTimeout(() => {
          OBC.state.videos = OBC.state.videos.filter((video) => recommendationKey(video) !== key);
          renderAll();
          if (message) OBC.showToast(message);
        }, card ? 180 : 0);
      }, card ? delayMs : 0);
    }

    function finishRecommendationFeedback(card, feedbackType = "") {
      if (!card) return;
      delete card.dataset.feedbackPending;
      card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
      const normalized = String(feedbackType || "").trim().toLowerCase();
      if (normalized !== "like") return;
      const button = card.querySelector('[data-action="like"]');
      if (!button) return;
      button.setAttribute("aria-pressed", "true");
      button.classList.add("is-active");
      button.disabled = true;
    }

    const sendIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M4 12 20 4l-5 16-3.2-6.8L4 12Z"/><path d="m11.8 13.2 3.7-3.7"/></svg>';

    function openCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.action = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送");
      button.setAttribute("title", "发送");
      requestAnimationFrame(() => card.querySelector(".comment-field input")?.focus());
    }

    function closeCardComposer(card) {
      const actions = card.querySelector(".card-actions");
      const button = card.querySelector(".chat-action");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.action = "comment";
      button.textContent = "聊一聊";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
    }

    // Collapse an open composer back to the 聊一聊 button when focus leaves it
    // (user clicked 聊一聊 then changed their mind). The typed draft stays in the
    // input, so reopening restores it. Deferred so a click on the send / cancel
    // button — which blurs the input first in some browsers — still wins.
    function autoCollapseComposer(container, event, closeFn) {
      if (!container || !container.classList.contains("is-composing")) return;
      const next = event.relatedTarget;
      if (next && container.contains(next)) return;
      window.setTimeout(() => {
        if (!container.classList.contains("is-composing")) return;
        if (container.contains(document.activeElement)) return;
        closeFn();
      }, 120);
    }

    function openDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button || !OBC.state.delight) return;
      shell?.classList.add("is-composing");
      actions.classList.add("is-composing");
      button.classList.add("is-send");
      button.dataset.delight = "send-comment";
      button.innerHTML = sendIcon;
      button.setAttribute("aria-label", "发送");
      button.setAttribute("title", "发送");
      scheduleActivityRailHeightSync();
      requestAnimationFrame(() => $("#delightCommentInput")?.focus());
    }

    function closeDelightComposer() {
      const actions = document.querySelector(".delight-main-actions");
      const shell = actions?.closest(".delight-actions");
      const button = actions?.querySelector(".chat-action");
      if (!actions || !button) return;
      shell?.classList.remove("is-composing");
      actions.classList.remove("is-composing");
      button.classList.remove("is-send");
      button.dataset.delight = "chat";
      button.textContent = "聊一聊";
      button.removeAttribute("aria-label");
      button.removeAttribute("title");
      scheduleActivityRailHeightSync();
    }

    async function handleCardAction(action, item, card) {
      const status = card.querySelector(".status-line");
      if (card.dataset.feedbackPending === "true") return;
      if (action === "open") return openRecommendation(item, card);
      if (action === "comment") { openCardComposer(card); return; }
      if (action === "cancel-comment") { closeCardComposer(card); return; }
      if (action === "watch-later") {
        const btn = card.querySelector('[data-action="watch-later"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "\u7A0D\u540E\u518D\u770B" : "\u53D6\u6D88\u6536\u85CF";
        try {
          const bvid = item.bvid || item.id;
          if (wasSaved) {
            await OBC.requestJson(`${OBC.ENDPOINTS.watchLater}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
          } else {
            await OBC.requestJson(OBC.ENDPOINTS.watchLater, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u7A0D\u540E\u518D\u770B" : "\u7A0D\u540E\u518D\u770B";
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (action === "favorite") {
        const btn = card.querySelector('[data-action="favorite"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        btn.title = wasSaved ? "\u6536\u85CF" : "\u53D6\u6D88\u6536\u85CF";
        try {
          const bvid = item.bvid || item.id;
          if (wasSaved) {
            await OBC.requestJson(`${OBC.ENDPOINTS.favorites}/${encodeURIComponent(bvid)}`, { method: "DELETE" });
          } else {
            await OBC.requestJson(OBC.ENDPOINTS.favorites, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
          btn.title = wasSaved ? "\u53D6\u6D88\u6536\u85CF" : "\u6536\u85CF";
        } finally {
          btn.disabled = false;
        }
        return;
      }
      card.dataset.feedbackPending = "true";
      card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = true; });
      try {
        if (action === "send-comment") {
          const input = card.querySelector(".comment-field input");
          const note = input.value.trim();
          if (!note) {
            delete card.dataset.feedbackPending;
            card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
            status.textContent = "先写一句想聊的内容，再提交这条反馈。";
            input?.focus();
            return;
          }
          await submitFeedback(item, "comment", note);
          if (input) input.value = "";
          closeCardComposer(card);
          item.feedback_type = "comment";
          status.textContent = "已提交聊天线索，推荐会继续保留在当前列表。";
          finishRecommendationFeedback(card, "comment");
          OBC.showToast("已提交聊天线索");
          return;
        }
        const feedbackType = action === "like" ? "like" : action === "dismiss" ? "dismiss" : "dislike";
        await submitFeedback(item, feedbackType);
        const feedbackCopy = {
          like: ["已记录喜欢，推荐会继续保留在当前列表。", "已记录喜欢"],
          dislike: ["已记录不感兴趣，几秒后从当前列表移除。", "已记录不感兴趣"],
          dismiss: ["已忽略这条推荐，几秒后从当前列表移除。", "已忽略推荐"]
        }[feedbackType];
        status.textContent = feedbackCopy[0];
        if (shouldRemoveRecommendationAfterFeedback(feedbackType)) {
          removeRecommendationCard(item, card, feedbackCopy[1]);
          return;
        }
        item.feedback_type = feedbackType;
        finishRecommendationFeedback(card, feedbackType);
        OBC.showToast(feedbackCopy[1]);
      } catch (error) {
        delete card.dataset.feedbackPending;
        card.querySelectorAll(".card-actions button, .card-actions input").forEach((control) => { control.disabled = false; });
        status.textContent = configErrorMessage(error?.details) || error?.message || "反馈提交失败，请稍后重试。";
        OBC.showToast(status.textContent);
      }
    }

    function renderRail() {
      const profile = OBC.state.profile;
      const portraitText = profile?.personality_portrait ? valueList(profile.personality_portrait) : "偏好结构化解释、长视频和跨学科桥接，对“为什么”比“是什么”更敏感。";
      if ($("#profilePortrait")) $("#profilePortrait").textContent = portraitText;
      if ($("#mobileProfilePortrait")) $("#mobileProfilePortrait").textContent = portraitText;
      const chips = [
        ...asArray(profile?.core_traits),
        ...asArray(profile?.cognitive_style),
        ...asArray(profile?.likes).map((item) => typeof item === "object" ? item.domain || item.name || item.title || valueList(item) : item)
      ].map(valueList).filter((text) => text && text.length <= 10 && !/[，。；、,.]/.test(text)).slice(0, 8);
      const chipTexts = chips.length ? chips : ["长解释", "机制控", "跨平台", "反信息茧房"];
      ["#profileChips", "#mobileProfileChips"].forEach((selector) => {
        const target = $(selector);
        if (!target) return;
        target.replaceChildren(...chipTexts.map((text) => {
          const chip = document.createElement("span"); chip.className = "chip"; chip.textContent = text; return chip;
        }));
      });
      const mbtiText = formatPersonalityType(profile?.mbti || profile?.personality_type) || "—";
      const opennessText = formatPercent(profile?.exploration_openness ?? profile?.openness) || "—";
      const depthText = formatPercent(profile?.style?.depth_preference ?? profile?.depth_preference ?? profile?.deep_preference ?? profile?.long_video_affinity) || "—";
      [["#railMbti", mbtiText], ["#mobileRailMbti", mbtiText], ["#railOpenness", opennessText], ["#mobileRailOpenness", opennessText], ["#railDepth", depthText], ["#mobileRailDepth", depthText]].forEach(([selector, value]) => {
        const target = $(selector);
        if (target) target.textContent = value;
      });
      const activityItems = OBC.state.activityItems.length ? OBC.state.activityItems : asArray(OBC.state.activity?.items);
      const activityHtml = activityItems.length
        ? activityItems.slice(0, 5).map((item) => `<div class="activity-item"><p>${OBC.escapeHtml(typeof item === "object" ? item.summary || item.detail || item.kind || valueList(item) : item)}</p></div>`).join("")
        : `<div class="empty-OBC.state">还没有新的动态；实时流收到 activity.added 后会自动刷新。</div>`;
      ["#activityList", "#mobileActivityList"].forEach((selector) => {
        const target = $(selector);
        if (target) target.innerHTML = activityHtml;
      });
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(getRenderableMessages().length);
    }

    function renderActivityHistory() {
      const list = $("#activityHistory");
      if (!list) return;
      if (!OBC.state.activityItems.length) {
        list.innerHTML = `<div class="empty-OBC.state">暂无历史动态。</div>`;
      } else {
        list.innerHTML = OBC.state.activityItems.map((item) => `<article class="activity-item"><p class="eyebrow">${OBC.escapeHtml(item.kind || "activity")}</p><h3>${OBC.escapeHtml(item.summary || "后台动态")}</h3><p class="video-meta">${OBC.escapeHtml(item.detail || item.created_at || "")}</p></article>`).join("");
      }
      const more = $("#activityMoreBtn");
      if (more) more.disabled = !OBC.state.activityHasMore;
    }

    async function loadActivityPage({ reset = false } = {}) {
      const cursor = reset ? "" : OBC.state.activityCursor;
      const query = new URLSearchParams({ limit: "10" });
      if (cursor) query.set("before", cursor);
      const payload = await OBC.requestJson(`${OBC.ENDPOINTS.activityFeed}?${query.toString()}`);
      if (!payload) { OBC.showToast("动态加载失败：后端不可用"); return; }
      const items = Array.isArray(payload.items) ? payload.items : [];
      OBC.state.activity = payload;
      OBC.state.activityItems = reset ? items : OBC.state.activityItems.concat(items);
      OBC.state.activityCursor = payload.next_cursor || payload.next || "";
      OBC.state.activityHasMore = Boolean(payload.has_more && OBC.state.activityCursor);
      renderRail();
      renderActivityHistory();
    }

    function formatPercent(value) {
      if (value == null || value === "") return "";
      if (typeof value === "string" && value.trim().endsWith("%")) return value.trim();
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      const normalized = Math.abs(number) <= 1 ? number * 100 : number;
      return `${Math.round(normalized)}%`;
    }

    function score01(value, fallback = 0.5) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.max(0, Math.min(1, Math.abs(number) <= 1 ? number : number / 100));
    }

    function formatPersonality(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      const type = value.type || value.mbti || value.name || value.label;
      const confidence = formatPercent(value.confidence);
      if (type && confidence) return `${type}（置信度 ${confidence}）`;
      if (type) return String(type);
      return valueList(value);
    }

    function formatPersonalityType(value) {
      if (!value) return "";
      if (typeof value !== "object") return String(value);
      return String(value.type || value.mbti || value.name || value.label || "");
    }

    function formatProfileObject(value) {
      const preferred = value.domain || value.summary || value.name || value.title || value.label || value.value || value.text || value.reason || value.hypothesis || value.observation;
      if (preferred) return String(preferred);
      return Object.entries(value)
        .filter(([, val]) => val != null && val !== "")
        .map(([key, val]) => {
          if (key === "confidence") return `置信度 ${formatPercent(val)}`;
          if (key === "dimensions" && typeof val === "object") return "维度已在 MBTI 图表中展示";
          return `${key}: ${valueList(val)}`;
        })
        .filter(Boolean)
        .join(" / ");
    }

    function valueList(value) {
      if (value == null || value === "") return "";
      if (Array.isArray(value)) return value.map((item) => valueList(item)).filter(Boolean).join("、");
      if (typeof value === "object") return formatProfileObject(value);
      return String(value);
    }

    function asArray(value) {
      if (value == null || value === "") return [];
      if (Array.isArray(value)) return value;
      if (typeof value === "object") {
        if (Array.isArray(value.items)) return value.items;
        if (Array.isArray(value.domains)) return value.domains;
        if (Array.isArray(value.values)) return value.values;
        return Object.entries(value).map(([key, val]) => {
          if (val == null || val === "" || val === false) return "";
          if (val === true) return key;
          if (typeof val === "object" && !Array.isArray(val)) return { name: key, ...val };
          return `${key}: ${valueList(val)}`;
        }).filter(Boolean);
      }
      return String(value).split(/[、,\n]+/).map((item) => item.trim()).filter(Boolean);
    }
    window.asArray = asArray;
    window.valueList = valueList;

    function formatTimeAgo(dateStr) {
      if (!dateStr) return "";
      try {
        const date = new Date(dateStr.replace(" ", "T") + (dateStr.includes("Z") ? "" : "Z"));
        if (isNaN(date.getTime())) return dateStr;
        const now = new Date();
        const diffMs = now.getTime() - date.getTime();
        const diffMin = Math.floor(diffMs / 60000);
        const diffHour = Math.floor(diffMs / 3600000);
        const diffDay = Math.floor(diffMs / 86400000);
        if (diffMin < 1) return "刚刚";
        if (diffMin < 60) return `${diffMin} 分钟前`;
        if (diffHour < 24) return `${diffHour} 小时前`;
        if (diffDay < 7) return `${diffDay} 天前`;
        const month = date.getMonth() + 1;
        const day = date.getDate();
        return `${month}月${day}日`;
      } catch {
        return dateStr;
      }
    }

    function firstValue(...values) {
      return values.find((value) => value != null && value !== "" && (!Array.isArray(value) || value.length));
    }

    function chipsHtml(value, fallback = "这部分还在慢慢补。") {
      const items = Array.isArray(value) ? value.map(valueList).filter(Boolean) : valueList(value).split("、").filter(Boolean);
      if (!items.length) return `<p class="video-meta">${OBC.escapeHtml(fallback)}</p>`;
      return `<div class="profile-chip-list">${items.map((item) => `<span class="chip">${OBC.escapeHtml(item)}</span>`).join("")}</div>`;
    }

    function paragraphsHtml(value, fallback = "这部分还在观察，先不急着下结论。") {
      const text = valueList(value);
      if (!text) return `<p class="video-meta">${OBC.escapeHtml(fallback)}</p>`;
      return `<div class="profile-portrait-copy">${String(text).split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => `<p class="video-meta">${OBC.escapeHtml(line)}</p>`).join("")}</div>`;
    }

    function profileItem(title, html, extraClass = "") {
      return `<article class="profile-item ${extraClass}"><h3>${OBC.escapeHtml(title)}</h3>${html}</article>`;
    }

    function profileLayer(label, items) {
      const body = items.filter(Boolean).join("");
      if (!body) return "";
      return `<div class="profile-layer"><div class="profile-layer-label">${OBC.escapeHtml(label)}</div>${body}</div>`;
    }

    function dimensionData(mbti, key) {
      if (!mbti?.dimensions) return null;
      return mbti.dimensions[key] || mbti.dimensions[`${key[0]}_${key[1]}`] || mbti.dimensions[key.toLowerCase()] || mbti.dimensions[`${key[0].toLowerCase()}_${key[1].toLowerCase()}`];
    }

    function normalizedPole(rawPole, key) {
      const pole = String(rawPole || "").trim().toUpperCase();
      if (pole.includes(key[0])) return key[0];
      if (pole.includes(key[1])) return key[1];
      return "";
    }

    function mbtiAxisHtml(mbti, config) {
      const dim = dimensionData(mbti, config.key);
      if (!dim) return "";
      const pole = normalizedPole(dim.pole, config.key) || config.key[1];
      const strength = score01(dim.strength, 0.5);
      const marker = pole === config.key[0] ? 50 - strength * 50 : 50 + strength * 50;
      const start = Math.min(50, marker);
      const width = Math.abs(marker - 50);
      return `<div class="mbti-axis">
        <span class="mbti-axis-side${pole === config.key[0] ? " is-active" : ""}">${config.left}<span> ${config.leftName}</span></span>
        <div class="mbti-axis-track" style="--start:${start}%;--width:${width}%;--marker:${marker}%"><span class="mbti-axis-fill"></span><span class="mbti-axis-marker"></span></div>
        <span class="mbti-axis-side${pole === config.key[1] ? " is-active" : ""}">${config.right}<span> ${config.rightName}</span></span>
        <span class="mbti-axis-pct">${OBC.escapeHtml(pole)} ${Math.round(strength * 100)}%</span>
      </div>`;
    }

    function mbtiHtml(value) {
      if (!value) return `<p class="video-meta">MBTI 还没推断出来，再多看一阵。</p>`;
      if (typeof value !== "object") return `<p class="video-meta">${OBC.escapeHtml(value)}</p>`;
      const type = value.type || value.mbti || value.name || "—";
      const axes = [
        { key: "EI", left: "E", right: "I", leftName: "外向", rightName: "内向" },
        { key: "SN", left: "S", right: "N", leftName: "实感", rightName: "直觉" },
        { key: "TF", left: "T", right: "F", leftName: "思考", rightName: "情感" },
        { key: "JP", left: "J", right: "P", leftName: "判断", rightName: "知觉" }
      ].map((config) => mbtiAxisHtml(value, config)).filter(Boolean).join("");
      return `<div class="mbti-block"><div class="mbti-type-row"><span class="mbti-type-label">${OBC.escapeHtml(type)}</span>${value.confidence ? `<span class="mbti-confidence">整体可信度 ${formatPercent(value.confidence)}</span>` : ""}</div>${axes ? `<div class="mbti-dimensions">${axes}</div>` : ""}</div>`;
    }

    function interestTreeHtml(value, fallback) {
      const domains = asArray(value);
      if (!domains.length) return `<p class="video-meta">${OBC.escapeHtml(fallback)}</p>`;
      return `<div class="profile-interest-tree">${domains.map((item) => {
        if (typeof item !== "object") return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${OBC.escapeHtml(item)}</span></div></div>`;
        const title = item.domain || item.name || item.title || valueList(item);
        const weight = item.weight != null ? `<span class="profile-domain-weight">${formatPercent(item.weight)}</span>` : "";
        const specifics = asArray(item.specifics).map((s) => s?.name || s?.label || valueList(s)).filter(Boolean);
        return `<div class="profile-domain"><div class="profile-domain-head"><span class="profile-domain-title">${OBC.escapeHtml(title)}</span>${weight}</div>${specifics.length ? `<div class="profile-chip-list">${specifics.map((s) => `<span class="chip">${OBC.escapeHtml(s)}</span>`).join("")}</div>` : ""}</div>`;
      }).join("")}</div>`;
    }

    function meterHtml(label, value) {
      const score = score01(value);
      return `<div class="profile-meter"><div class="profile-meter-head"><span>${OBC.escapeHtml(label)}</span><strong>${Math.round(score * 100)}%</strong></div><div class="profile-meter-track"><div class="profile-meter-fill" style="width:${score * 100}%"></div></div></div>`;
    }

    function styleHtml(style) {
      if (!style || typeof style !== "object" || Array.isArray(style)) return paragraphsHtml(style, "内容口味还在继续归拢。");
      const textRows = [
        ["偏好时长", style.preferred_duration],
        ["偏好节奏", style.preferred_pace]
      ].filter(([, value]) => value).map(([label, value]) => `<div class="profile-context-row"><span>${OBC.escapeHtml(label)}</span><strong>${OBC.escapeHtml(value)}</strong></div>`).join("");
      const bars = [
        ["质量敏感度", style.quality_sensitivity],
        ["幽默偏好", style.humor_preference],
        ["深度偏好", style.depth_preference]
      ].filter(([, value]) => value != null).map(([label, value]) => meterHtml(label, value)).join("");
      return `<div class="profile-bars profile-style-bars">${textRows}${bars}</div>`;
    }

    function contextHtml(context) {
      if (!context || typeof context !== "object" || Array.isArray(context)) return paragraphsHtml(context, "使用场景还在继续观察。");
      const rows = [
        ["工作日", context.weekday_patterns],
        ["周末", context.weekend_patterns],
        ["一天中的时段", context.time_of_day_patterns],
        ["观看会话", context.session_type]
      ].filter(([, value]) => value).map(([label, value]) => `<div class="profile-context-row"><span>${OBC.escapeHtml(label)}</span><strong>${OBC.escapeHtml(value)}</strong></div>`).join("");
      return rows ? `<div class="profile-context">${rows}</div>` : paragraphsHtml("", "使用场景还在继续观察。");
    }

    function speculativeHtml(items, options = {}) {
      const isAvoidance = options.kind === "avoidance";
      const probeType = isAvoidance ? "avoidance.probe" : "interest.probe";
      const list = asArray(items).filter((item) => {
        if (typeof item !== "object") return !OBC.state.handledProbeKeys.has(probeKey(probeType, item));
        const domain = item.domain || item.name || item.title;
        if (!domain || OBC.state.handledProbeKeys.has(probeKey(probeType, domain))) return false;
        const status = String(item.status || "active").trim().toLowerCase();
        return status === "active" || status === "pending";
      });
      if (!list.length) return `<p class="video-meta">${isAvoidance ? "阿B 暂时没有待确认的避雷方向。" : "阿B 还没有正在试探的新方向。"}</p>`;
      const statusLabels = { active: "待确认", pending: "待观察", confirmed: "已确认", deprecated: "已弃", rejected: "已排除" };
      const fallbackTitle = isAvoidance ? "猜测避雷" : "猜测兴趣";
      return `<div class="speculative-list">${list.map((item) => {
        if (typeof item !== "object") return `<div class="speculative-item"><div class="spec-header"><span class="spec-domain">${OBC.escapeHtml(item)}</span></div></div>`;
        const domain = item.domain || item.name || item.title || fallbackTitle;
        const status = item.status || "active";
        const count = Number(item.confirmation_count ?? 0);
        const threshold = Number(item.confirmation_threshold ?? 3);
        const progress = `${count}/${threshold} 次确认`;
        const confidence = score01(item.confidence, 0);
        const specifics = asArray(item.specifics).map((s) => ({
          name: s?.name || s?.label || valueList(s),
          count: Number(s?.confirmation_count ?? 0)
        })).filter((s) => s.name);
        return `<div class="speculative-item is-status-${OBC.escapeHtml(status)}" data-spec-domain="${OBC.escapeHtml(domain)}">
          <div class="spec-header">
            <span class="spec-domain">${OBC.escapeHtml(domain)}</span>
            ${statusLabels[status] ? `<span class="spec-status">${OBC.escapeHtml(statusLabels[status])}</span>` : ""}
            <span class="spec-progress">${OBC.escapeHtml(progress)}</span>
          </div>
          ${confidence > 0 ? `<div class="spec-confidence-row"><div class="spec-confidence-bar"><div class="spec-confidence-fill" style="width:${Math.round(confidence * 100)}%"></div></div><span class="spec-confidence-label">置信度 ${Math.round(confidence * 100)}%</span></div>` : ""}
          ${item.reason ? `<p class="video-meta">${OBC.escapeHtml(item.reason)}</p>` : ""}
          ${specifics.length ? `<div class="spec-specifics">${specifics.map((s) => `<span class="spec-specific-chip">${OBC.escapeHtml(s.name)}${s.count > 0 ? `<span class="spec-specific-count">${s.count}</span>` : ""}</span>`).join("")}</div>` : ""}
          <p class="spec-help">${isAvoidance ? `置信度表示阿B认为你会避开这个方向的把握；确认次数来自后端累计的避雷确认信号，达到 ${threshold} 次后会进入更稳定的避雷画像。` : `置信度表示阿B认为你会喜欢这个方向的把握；确认次数来自后端累计的正向确认信号（包括但不限于这里的“喜欢”），达到 ${threshold} 次后会进入更稳定的兴趣画像。`}</p>
          ${status === "active" && domain ? `<div class="spec-actions"><button class="probe-btn is-confirm" type="button" data-spec-response="confirm" data-spec-type="${isAvoidance ? "avoidance.probe" : "interest.probe"}">${isAvoidance ? "确实不喜欢" : "喜欢"}</button><button class="probe-btn is-reject" type="button" data-spec-response="reject" data-spec-type="${isAvoidance ? "avoidance.probe" : "interest.probe"}">${isAvoidance ? "不是" : "不喜欢"}</button></div>` : ""}
        </div>`;
      }).join("")}</div>`;
    }

    function memoryHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">阿B 还在继续观察，过一阵这里会更具体。</p>`;
      return `<div class="profile-card-list">${list.slice(0, 8).map((item) => {
        if (typeof item !== "object") return `<div class="profile-memory"><p class="video-meta">${OBC.escapeHtml(item)}</p></div>`;
        const meta = item.sourceLabel || item.source_label || item.source || item.created_at || "";
        const details = asArray([item.contextLine || item.context_line, item.impact, item.reasoning, item.evidence]).filter(Boolean).map((line) => `<p class="video-meta">${OBC.escapeHtml(valueList(line))}</p>`).join("");
        return `<div class="profile-memory"><div class="profile-memory-head"><strong>${OBC.escapeHtml(item.summary || item.title || "近期记忆")}</strong>${meta ? `<span class="profile-memory-meta">${OBC.escapeHtml(meta)}</span>` : ""}</div>${details}</div>`;
      }).join("")}</div>`;
    }

    function insightsHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">当前没有需要特别展示的活跃洞察。</p>`;
      return `<div class="profile-card-list">${list.map((item, idx) => {
        if (typeof item !== "object") return `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${OBC.escapeHtml(item)}</span></div></div>`;
        const evidenceItems = asArray(item.evidence).map((e) => String(e || "").trim()).filter(Boolean);
        const evidenceHtml = evidenceItems.length
          ? `<details class="profile-insight-evidence"><summary>证据 · ${evidenceItems.length} 条</summary><ul>${evidenceItems.map((e) => `<li>${OBC.escapeHtml(e)}</li>`).join("")}</ul></details>`
          : "";
        const hypothesis = item.hypothesis || "";
        const actions = hypothesis
          ? `<div class="insight-actions"><button class="pill-btn" type="button" data-insight-action="confirm" data-insight-idx="${idx}">准</button><button class="pill-btn" type="button" data-insight-action="reject" data-insight-idx="${idx}">不准</button></div>`
          : "";
        return `<div class="profile-insight" data-insight-idx="${idx}"><div class="profile-insight-head"><span class="profile-insight-title">${OBC.escapeHtml(hypothesis || item.observation || valueList(item))}</span><span class="profile-confidence">${formatPercent(item.confidence)}</span></div>${evidenceHtml}${item.validated ? `<p class="video-meta">已验证</p>` : ""}${actions}</div>`;
      }).join("")}</div>`;
    }

    function awarenessHtml(items) {
      const list = asArray(items);
      if (!list.length) return `<p class="video-meta">近期观察还在沉淀。</p>`;
      return `<div class="profile-card-list">${list.map((item) => typeof item === "object" ? `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${OBC.escapeHtml(item.observation || valueList(item))}</span>${item.date ? `<span class="profile-confidence">${OBC.escapeHtml(item.date)}</span>` : ""}</div>${item.trend ? `<p class="video-meta">趋势：${OBC.escapeHtml(item.trend)}</p>` : ""}${item.emotion_guess ? `<p class="video-meta">情绪猜测：${OBC.escapeHtml(item.emotion_guess)}</p>` : ""}</div>` : `<div class="profile-insight"><div class="profile-insight-head"><span class="profile-insight-title">${OBC.escapeHtml(item)}</span></div></div>`).join("")}</div>`;
    }

    function loadUserFeedbackAndViews() {
      // Load interest tags from likes
      OBC.requestJson(OBC.ENDPOINTS.interestTags + "?limit=20", { timeoutMs: 10000 })
        .then((data) => {
          const tags = data?.tags || [];
          const container = $("#profileInterestTagsContainer");
          const tagsEl = $("#profileInterestTags");
          if (!container || !tagsEl) return;
          if (!tags.length) {
            container.style.display = "none";
            return;
          }
          container.style.display = "";
          tagsEl.innerHTML = tags.map((t) => {
            const platforms = t.source_platforms?.length
              ? t.source_platforms.map((p) => platformLabelHtml(p)).join(" ")
              : "";
            return `<span class="interest-tag" title="${t.count} 次点赞${platforms ? ' · ' + t.source_platforms.join(', ') : ''}">
              ${OBC.escapeHtml(t.tag)} <small>${t.weight}</small>
              ${platforms ? `<span class="interest-tag-platforms">${platforms}</span>` : ""}
            </span>`;
          }).join("");
        })
        .catch(() => {
          const container = $("#profileInterestTagsContainer");
          if (container) container.style.display = "none";
        });

      // Load recent view history
      OBC.requestJson(OBC.ENDPOINTS.viewHistory + "?limit=30", { timeoutMs: 10000 })
        .then((views) => {
          const el = $("#profileViewHistory");
          if (!el) return;
          if (!views || !views.length) {
            el.innerHTML = `<p class="video-meta">还没有浏览记录，去 Agent 推荐页面逛逛吧。</p>`;
            return;
          }
          el.innerHTML = `<div class="view-history-list">${views.map((v) => {
            const title = OBC.escapeHtml(v.title || "无标题");
            const platform = platformLabelHtml(v.source_platform || "");
            const author = OBC.escapeHtml(v.up_name || "");
            const time = v.viewed_at ? formatTimeAgo(v.viewed_at) : "";
            const url = v.content_url || "";
            const topic = OBC.escapeHtml(v.topic_group || "");
            return `<div class="view-history-item">
              <a href="${OBC.escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="view-history-link">${title}</a>
              <div class="view-history-meta">
                ${platform ? `<span class="pex-badge pex-badge-${v.source_platform || ''}">${platform}</span>` : ""}
                ${author ? `<span class="view-history-author">${OBC.escapeHtml(author)}</span>` : ""}
                ${topic ? `<span class="view-history-topic">${OBC.escapeHtml(topic)}</span>` : ""}
                ${time ? `<span class="view-history-time">${OBC.escapeHtml(time)}</span>` : ""}
              </div>
            </div>`;
          }).join("")}</div>`;
        })
        .catch(() => {
          const el = $("#profileViewHistory");
          if (el) el.innerHTML = `<p class="video-meta">浏览记录加载失败。</p>`;
        });
    }

    function updateProfileMemoryButton() {
      const button = $("#profileMemoryMoreBtn");
      if (!button) return;
      button.hidden = !OBC.state.profileCognitionHasMore;
      button.disabled = !OBC.state.profileCognitionHasMore;
    }

    function syncProfileCognitionState(profile) {
      const cursor = profile?.next_cognition_cursor || profile?.next_cursor || "";
      OBC.state.profileCognitionCursor = cursor;
      OBC.state.profileCognitionHasMore = Boolean(profile?.has_more_cognition_updates && cursor);
      updateProfileMemoryButton();
    }

    function renderProfileDetails() {
      const profile = OBC.state.profile;
      if (!profile) {
        $("#profileDetails").innerHTML = profileItem("画像还没攒起来", paragraphsHtml("后端未连接或画像尚未初始化。连接 FastAPI 后会展示完整画像。"));
        OBC.state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      if (OBC.state.editingProfile) {
        $("#profileDetails").innerHTML = renderProfileEditPanel();
        bindProfileEditActions();
        OBC.state.profileCognitionHasMore = false;
        updateProfileMemoryButton();
        return;
      }
      syncProfileCognitionState(profile);
      // Load user feedback and view history for profile page
      loadUserFeedbackAndViews();
      const html = [
        profileItem("这会儿的你", paragraphsHtml(profile.personality_portrait || profile.summary), "profile-portrait-block"),
        profileLayer("Core — 比较稳定的底色", [
          profileItem("核心特质", chipsHtml(profile.core_traits, "这部分还在慢慢补。")),
          profileItem("深层需求", chipsHtml(profile.deep_needs, "这块还要再多看一点。")),
          profileItem("MBTI / 人格推断", mbtiHtml(firstValue(profile.mbti, profile.personality_type)))
        ]),
        profileLayer("Values — 你在内容里长期在找什么", [
          profileItem("价值偏好", chipsHtml(firstValue(profile.values, profile.value_preferences), "价值偏好还在继续归拢。")),
          profileItem("内在驱动力", chipsHtml(firstValue(profile.motivational_drivers, profile.intrinsic_drives, profile.motivations), "这块还要再多看一点。"))
        ]),
        profileLayer("Interest — 你最近在看什么", [
          profileItem("感兴趣的方向", interestTreeHtml(profile.likes, "再刷一阵，这里会更准。")),
          profileItem("明显会避开", interestTreeHtml(profile.dislikes, "这块还在继续确认，先别急着下死结论。")),
          profileItem("常看的 UP 主", chipsHtml(firstValue(profile.favorite_up_users, profile.favorite_creators, profile.creators, profile.up_names), "常看的 UP 主还在统计。"))
        ]),
        profileLayer("Role — 这阵子的状态", [
          profileItem("大致处在什么阶段", paragraphsHtml(profile.life_stage, "这块还在观察，先不急着定论。")),
          profileItem("这阵子更像在经历什么", paragraphsHtml(firstValue(profile.current_phase, profile.current_stage), "这阵子的变化还在继续看。"))
        ]),
        profileLayer("Surface — 你怎么看内容", [
          profileItem("认知风格", chipsHtml(profile.cognitive_style, "这层还在继续归拢。")),
          profileItem("内容口味", styleHtml(firstValue(profile.style, profile.content_style, profile.content_preferences))),
          profileItem("使用场景", contextHtml(firstValue(profile.context, profile.current_context))),
          profileItem("探索开放度", meterHtml("愿意走出既有兴趣圈", firstValue(profile.exploration_openness, profile.openness)))
        ]),
        profileLayer("Feedback — 显式反馈和浏览记录", [
          profileItem("点赞标签聚合", `<div id="profileInterestTagsContainer" style="display:none;"><div id="profileInterestTags" class="profile-interest-tags"></div></div>`, "feedback-section"),
          profileItem("近期浏览记录", `<div id="profileViewHistory" class="profile-view-history">正在加载...</div>`, "feedback-section")
        ]),
        profileLayer("Speculate — 阿B 在试探的方向", [
          profileItem("猜测兴趣", speculativeHtml(profile.speculative_interests)),
          profileItem("猜测避雷", speculativeHtml(profile.speculative_avoidances, { kind: "avoidance" })),
          profileItem("阿B 最近新记住了什么", memoryHtml(firstValue(profile.recent_cognition_updates, profile.recent_memories)))
        ]),
        profileLayer("Signals — 正在推断中", [
          profileItem("当前活跃的洞察", insightsHtml(profile.active_insights)),
          profileItem("近期观察到的", awarenessHtml(profile.recent_awareness))
        ])
      ].join("");
      const profileEditBar = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="enter">✏️ 编辑画像</button></div>`;
      $("#profileDetails").innerHTML = profileEditBar + html;
      window.bindSpeculativeActions();
      bindInsightActions();
      window.bindProfileEditToggle();
    }

    function bindInsightActions() {
      document.querySelectorAll("[data-insight-action]").forEach((button) => {
        button.addEventListener("click", () => respondInsightFeedback(button));
      });
    }

    async function respondInsightFeedback(button) {
      const signal = button.dataset.insightAction;
      const idx = Number(button.dataset.insightIdx);
      const insight = OBC.state.profile?.active_insights?.[idx];
      const hypothesis = insight && insight.hypothesis;
      if (!signal || !hypothesis) return;
      const row = button.closest(".profile-insight");
      row?.querySelectorAll("[data-insight-action]").forEach((btn) => { btn.disabled = true; });
      try {
        await OBC.requestJson(OBC.ENDPOINTS.insightFeedback, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hypothesis, signal }),
        });
        OBC.showToast(signal === "confirm" ? "已确认这条洞察" : "已记下，会少推这类");
        setTimeout(() => { void refreshProfile(); }, 1200);
      } catch (error) {
        row?.querySelectorAll("[data-insight-action]").forEach((btn) => { btn.disabled = false; });
        OBC.showToast("没存上，稍后再试");
      }
    }

    // Expose functions to window for cross-module access
    window.renderRail = renderRail;
    window.renderActivityHistory = renderActivityHistory;
    window.loadActivityPage = loadActivityPage;
    window.loadPoolExploreData = loadPoolExploreData;
    window.handleCardAction = handleCardAction;
    window.renderProfileDetails = renderProfileDetails;
    window.submitFeedback = submitFeedback;
    window.openDelightComposer = openDelightComposer;
    window.closeDelightComposer = closeDelightComposer;
    window.updateProfileMemoryButton = updateProfileMemoryButton;
    window.syncProfileCognitionState = syncProfileCognitionState;

})();
