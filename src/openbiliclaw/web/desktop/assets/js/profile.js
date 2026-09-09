// ── Editable profile (Phase 3, desktop) — extracted from app.js
(function() {
const OBC = window.OBC;
if (!OBC) { console.error("profile.js: window.OBC not found — load app.js first"); return; }

// ── Editable profile (Phase 3, desktop) ──────────────────────
    const PROFILE_EDIT_LABELS = {
      personality_portrait: "人格素描",
      "core.core_traits": "核心特质",
      "core.deep_needs": "深层需求",
      "values_layer.values": "价值偏好",
      "values_layer.motivational_drivers": "内在驱动力",
      likes: "感兴趣的方向",
      dislikes: "明显会避开",
      "interest.favorite_up_users": "常看的 UP 主",
      "role.life_stage": "大致处在什么阶段",
      "role.current_phase": "这阵子更像在经历什么",
      "surface.cognitive_style": "认知风格",
      "surface.exploration_openness": "探索开放度",
      "surface.style.quality_sensitivity": "质量敏感度",
      "surface.style.humor_preference": "幽默偏好",
      "surface.style.depth_preference": "深度偏好"
    };
    const PROFILE_EDIT_ORDER = [
      "personality_portrait",
      "core.core_traits",
      "core.deep_needs",
      "values_layer.values",
      "values_layer.motivational_drivers",
      "likes",
      "dislikes",
      "interest.favorite_up_users",
      "role.life_stage",
      "role.current_phase",
      "surface.cognitive_style",
      "surface.exploration_openness",
      "surface.style.quality_sensitivity",
      "surface.style.humor_preference",
      "surface.style.depth_preference"
    ];

    function bindProfileEditToggle() {
      const btn = document.querySelector('#profileDetails [data-profile-edit-toggle="enter"]');
      if (btn) btn.addEventListener("click", () => { void enterProfileEdit(); });
    }
    window.bindProfileEditToggle = bindProfileEditToggle;

    async function enterProfileEdit() {
      OBC.state.editingProfile = true;
      OBC.state.profileEditState = null;
      window.renderProfileDetails();
      OBC.state.profileEditState = await OBC.requestJson(OBC.ENDPOINTS.profileEditState);
      window.renderProfileDetails();
    }

    async function exitProfileEdit() {
      OBC.state.editingProfile = false;
      OBC.state.profileEditState = null;
      const fresh = await OBC.requestJson(OBC.ENDPOINTS.profile);
      if (fresh) OBC.state.profile = fresh;
      window.renderProfileDetails();
    }

    async function applyProfileEdit(payload) {
      const res = await OBC.requestJson(OBC.ENDPOINTS.profileEdit, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (res && res.edit_state && res.edit_state.initialized) {
        OBC.state.profileEditState = res.edit_state;
      } else {
        const refreshed = await OBC.requestJson(OBC.ENDPOINTS.profileEditState);
        if (refreshed) OBC.state.profileEditState = refreshed;
        if (!res) OBC.showToast("修改未保存：请检查输入或后端状态");
      }
      window.renderProfileDetails();
    }

    function profileEditTextField(path, label, field) {
      const pinned = Boolean(field.pinned);
      const rows = path === "personality_portrait" ? 4 : 2;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${OBC.escapeHtml(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <textarea class="edit-text-input" data-edit-text="${OBC.escapeHtml(path)}" rows="${rows}">${OBC.escapeHtml(field.value || "")}</textarea>
          ${field.ai_suggestion ? `<p class="edit-drift-hint">AI 当前想更新为：${OBC.escapeHtml(field.ai_suggestion)}</p>` : ""}
          <div class="edit-field-actions">
            <button class="pill-btn primary" type="button" data-edit-save="${OBC.escapeHtml(path)}">保存</button>
            ${pinned ? `<button class="edit-reset-btn" type="button" data-edit-reset="${OBC.escapeHtml(path)}">恢复 AI 建议</button>` : ""}
          </div>
        </div>`;
    }

    function profileEditScalarField(path, label, field) {
      const pinned = Boolean(field.pinned);
      const pct = Math.round((Number(field.value) || 0) * 100);
      const aiPct = typeof field.ai_suggestion === "number" ? Math.round(field.ai_suggestion * 100) : null;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${OBC.escapeHtml(label)}</span>${pinned ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-scalar-row">
            <input class="edit-scalar-input" type="range" min="0" max="100" step="1" value="${pct}" data-edit-scalar="${OBC.escapeHtml(path)}" />
            <span class="edit-scalar-value" data-edit-scalar-value="${OBC.escapeHtml(path)}">${pct}%</span>
          </div>
          ${aiPct !== null ? `<p class="edit-drift-hint">AI 当前想更新为：${aiPct}%</p>` : ""}
          <div class="edit-field-actions">
            <button class="pill-btn primary" type="button" data-edit-save-scalar="${OBC.escapeHtml(path)}">保存</button>
            ${pinned ? `<button class="edit-reset-btn" type="button" data-edit-reset="${OBC.escapeHtml(path)}">恢复 AI 建议</button>` : ""}
          </div>
        </div>`;
    }

    function profileEditListField(path, label, field) {
      const items = Array.isArray(field.items) ? field.items : [];
      const edited = (field.added?.length || 0) > 0 || (field.removed?.length || 0) > 0;
      const chips = items.length
        ? items.map((it) => `<span class="edit-chip">${OBC.escapeHtml(it)}<button class="edit-chip-remove" type="button" data-edit-remove="${OBC.escapeHtml(path)}" data-edit-value="${OBC.escapeHtml(it)}">✕</button></span>`).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${OBC.escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-chip-list">${chips}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${OBC.escapeHtml(path)}" placeholder="添加一项" />
            <button class="pill-btn" type="button" data-edit-add="${OBC.escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${OBC.escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function profileEditInterestField(path, label, field) {
      const domains = Array.isArray(field.domains) ? field.domains : [];
      const edited = (field.removed_domains?.length || 0) > 0 || domains.some((d) => d?.user_added);
      const chips = domains.length
        ? domains.map((d) => `<span class="edit-chip">${OBC.escapeHtml(d.domain)}${d.user_added ? " ＋" : ""}<button class="edit-chip-remove" type="button" data-edit-remove="${OBC.escapeHtml(path)}" data-edit-value="${OBC.escapeHtml(d.domain)}">✕</button></span>`).join("")
        : `<p class="video-meta">还没有，添加一个吧</p>`;
      const placeholder = path === "dislikes" ? "添加要避开的领域" : "添加感兴趣的领域";
      return `
        <div class="edit-field">
          <div class="edit-field-head"><span class="edit-field-label">${OBC.escapeHtml(label)}</span>${edited ? `<span class="edit-badge">已编辑</span>` : ""}</div>
          <div class="edit-chip-list">${chips}</div>
          <div class="edit-add-row">
            <input class="edit-add-input" data-edit-add-input="${OBC.escapeHtml(path)}" placeholder="${OBC.escapeHtml(placeholder)}" />
            <button class="pill-btn" type="button" data-edit-add="${OBC.escapeHtml(path)}">添加</button>
          </div>
          ${edited ? `<div class="edit-field-actions"><button class="edit-reset-btn" type="button" data-edit-reset="${OBC.escapeHtml(path)}">恢复 AI 建议</button></div>` : ""}
        </div>`;
    }

    function renderProfileEditPanel() {
      const editState = OBC.state.profileEditState;
      let html = `<div class="profile-edit-bar"><button class="pill-btn" type="button" data-profile-edit-toggle="exit">✓ 完成</button></div>`;
      if (!editState) {
        html += `<p class="video-meta">加载中…</p>`;
        return html;
      }
      if (!editState.initialized || !editState.fields) {
        html += `<p class="video-meta">画像还没攒起来，回到首页推荐区点「开始初始化」后再回来编辑。</p>`;
        return html;
      }
      html += `<p class="video-meta profile-edit-note">标签 / 兴趣类增删即时生效；文本与滑杆类改完点「保存」才生效。改动都不会被后续自动重建覆盖，删错了点「恢复 AI 建议」即可。</p>`;
      for (const path of PROFILE_EDIT_ORDER) {
        const field = editState.fields[path];
        if (!field || typeof field !== "object") continue;
        const label = PROFILE_EDIT_LABELS[path] || path;
        if (field.type === "text") html += profileEditTextField(path, label, field);
        else if (field.type === "scalar") html += profileEditScalarField(path, label, field);
        else if (field.type === "list") html += profileEditListField(path, label, field);
        else if (field.type === "interest") html += profileEditInterestField(path, label, field);
      }
      return html;
    }

    function bindProfileEditActions() {
      const root = $("#profileDetails");
      if (!root) return;
      root.querySelector('[data-profile-edit-toggle="exit"]')?.addEventListener("click", () => { void exitProfileEdit(); });
      root.querySelectorAll("[data-edit-remove]").forEach((btn) => {
        btn.addEventListener("click", () => void applyProfileEdit({ target: btn.dataset.editRemove, op: "remove", value: btn.dataset.editValue }));
      });
      root.querySelectorAll("[data-edit-reset]").forEach((btn) => {
        btn.addEventListener("click", () => void applyProfileEdit({ target: btn.dataset.editReset, op: "reset" }));
      });
      root.querySelectorAll("[data-edit-add]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editAdd;
          const input = root.querySelector(`[data-edit-add-input="${path}"]`);
          const value = input?.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: path, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-add-input]").forEach((input) => {
        input.addEventListener("keydown", (event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          const value = input.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: input.dataset.editAddInput, op: "add", value });
        });
      });
      root.querySelectorAll("[data-edit-save]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editSave;
          const textarea = root.querySelector(`[data-edit-text="${path}"]`);
          const value = textarea?.value.trim();
          if (!value) return;
          void applyProfileEdit({ target: path, op: "set", value });
        });
      });
      root.querySelectorAll("[data-edit-scalar]").forEach((input) => {
        input.addEventListener("input", () => {
          const out = root.querySelector(`[data-edit-scalar-value="${input.dataset.editScalar}"]`);
          if (out) out.textContent = `${input.value}%`;
        });
      });
      root.querySelectorAll("[data-edit-save-scalar]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const path = btn.dataset.editSaveScalar;
          const input = root.querySelector(`[data-edit-scalar="${path}"]`);
          if (!input) return;
          void applyProfileEdit({ target: path, op: "set", value: Number(input.value) / 100 });
        });
      });
    }

    async function loadMoreProfileMemory() {
      if (!OBC.state.profileCognitionCursor) return;
      const button = $("#profileMemoryMoreBtn");
      if (button) button.disabled = true;
      const query = new URLSearchParams({ cursor: OBC.state.profileCognitionCursor });
      const nextPage = await OBC.requestJson(`${OBC.ENDPOINTS.profile}?${query.toString()}`);
      if (!nextPage) {
        OBC.showToast("近期记忆加载失败：后端不可用");
        updateProfileMemoryButton();
        return;
      }
      const current = Array.isArray(OBC.state.profile?.recent_cognition_updates) ? OBC.state.profile.recent_cognition_updates : [];
      const incoming = Array.isArray(nextPage.recent_cognition_updates) ? nextPage.recent_cognition_updates : [];
      OBC.state.profile = {
        ...(OBC.state.profile || {}),
        ...nextPage,
        recent_cognition_updates: current.concat(incoming)
      };
      syncProfileCognitionState(OBC.state.profile);
      window.renderProfileDetails();
      OBC.showToast(incoming.length ? `已加载 ${incoming.length} 条近期记忆` : "没有更多近期记忆");
    }

    function messageType(msg) {
      const type = msg?.type === "probe" ? "interest.probe" : (msg?.type || "interest.probe");
      return type === "avoidance" ? "avoidance.probe" : type;
    }

    function isAvoidanceProbe(type) {
      return messageType({ type }) === "avoidance.probe";
    }

    function isChallengeProbe(item) {
      const mode = String(item?.probe_mode || "").toLowerCase();
      return Boolean(item?.challenge) || mode === "lateral" || mode === "bridge" || mode === "wildcard";
    }

    function probeKey(type, domain) {
      const normalizedDomain = String(domain || "").trim().toLowerCase();
      return normalizedDomain ? `${messageType({ type })}:${normalizedDomain}` : "";
    }

    function messageKey(msg) {
      const type = messageType(msg);
      if (type === "interest.probe" || type === "avoidance.probe") {
        return probeKey(type, msg?.domain || msg?.title);
      }
      return `${type}:${msg?.bvid || msg?.domain || msg?.title || msg?.reason || ""}`;
    }

    function normalizeMessageItem(item) {
      if (!item) return null;
      const type = messageType(item);
      if (type === "delight") {
        return null;
      }
      if (type === "notification") {
        const bvid = item.bvid || item.id || item.recommendation_id;
        if (!bvid) return null;
        return {
          type: "notification",
          bvid: String(bvid),
          title: item.title || "有一条值得通知你的推荐",
          reason: item.reason || item.expression || "这条推荐达到了通知阈值。",
          content_url: item.content_url || (item.bvid ? `https://www.bilibili.com/video/${encodeURIComponent(item.bvid)}` : "")
        };
      }
      const domain = item.domain || item.name || item.title;
      if (!domain) return null;
      const probeType = type === "avoidance.probe" || item.kind === "avoidance" ? "avoidance.probe" : "interest.probe";
      if (OBC.state.handledProbeKeys.has(probeKey(probeType, domain))) return null;
      const status = String(item.status || "active").trim().toLowerCase();
      if (status !== "active" && status !== "pending") return null;
      return {
        type: probeType,
        domain: String(domain),
        reason: item.reason || item.message || item.description || (probeType === "avoidance.probe" ? "后端希望确认这个避雷方向。" : "后端希望确认这个兴趣方向。"),
        specifics: window.asArray(item.specifics || item.examples || item.children).map((s) => s?.name || s?.label || window.valueList(s)).filter(Boolean),
        probe_mode: item.probe_mode || "",
        challenge: Boolean(item.challenge),
        chat_status: item.chat_status || item.status_text || "",
        chat_reply: item.chat_reply || item.reply || ""
      };
    }

    function syncMessageCount() {
      const count = getRenderableMessages(OBC.state.messageListSnapshot && isMessagesDrawerOpen() ? OBC.state.messageListSnapshot : OBC.state.messages).length;
      if (OBC.state.runtimeStatus) OBC.state.runtimeStatus.unread_count = count;
      const metric = $("#metricUnread");
      if (metric) metric.textContent = String(count);
      const dot = $("#messagesDot");
      if (dot) dot.hidden = count <= 0;
      const mobileCount = $("#mobileMessageCount");
      if (mobileCount) mobileCount.textContent = String(count);
      return count;
    }

    function getRenderableMessages(source = OBC.state.messages) {
      const seen = new Set();
      const items = [];
      for (const raw of source || []) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!key || seen.has(key)) continue;
        seen.add(key);
        items.push(item);
      }
      return items;
    }

    function isMessagesDrawerOpen() {
      return Boolean($("#messagesDrawer")?.classList.contains("is-open"));
    }

    function hydrateInboxFromSpeculations(speculations, type = "interest.probe") {
      if (speculations == null || speculations === "") return;
      const normalizedType = messageType({ type });
      const items = window.asArray(speculations);
      const active = items.filter((item) => item && item.domain && (!item.status || item.status === "active") && !OBC.state.handledProbeKeys.has(probeKey(normalizedType, item.domain)));
      const activeKeys = new Set(active.map((item) => probeKey(normalizedType, item.domain)));
      const preserveCurrentProbeList = isMessagesDrawerOpen();
      OBC.state.messages = OBC.state.messages.filter((msg) => {
        if (messageType(msg) !== normalizedType) return true;
        const domain = String(msg.domain || "");
        if (!domain || OBC.state.handledProbeKeys.has(probeKey(normalizedType, domain))) return false;
        if (OBC.state.resolvingMessageKeys.has(messageKey(msg))) return true;
        return preserveCurrentProbeList || activeKeys.has(probeKey(normalizedType, domain));
      });
      const existing = new Set(OBC.state.messages.filter((msg) => messageType(msg) === normalizedType).map((msg) => probeKey(normalizedType, msg.domain)));
      for (const item of active) {
        const domain = String(item.domain);
        const key = probeKey(normalizedType, domain);
        if (!key || OBC.state.handledProbeKeys.has(key) || existing.has(key)) continue;
        OBC.state.messages.push(normalizeMessageItem({ ...item, type: normalizedType }));
        existing.add(key);
      }
      syncMessageCount();
    }

    function isMessageListLocked() {
      return Boolean(document.querySelector("#messageList .message-item.is-resolving, #messageList .message-item.is-resolved, #messageList .message-item.is-dismissing"));
    }

    function renderMessages() {
      const list = $("#messageList");
      if (OBC.state.messageListDomLocked || isMessageListLocked()) {
        syncMessageCount();
        return;
      }
      const source = OBC.state.messageListSnapshot && isMessagesDrawerOpen() ? OBC.state.messageListSnapshot : OBC.state.messages;
      const messages = getRenderableMessages(source);
      if (OBC.state.messageListSnapshot && isMessagesDrawerOpen()) OBC.state.messageListSnapshot = messages;
      else OBC.state.messages = messages;
      syncMessageCount();
      if (!messages.length) {
        list.innerHTML = `<div class="empty-OBC.state">暂无通知。兴趣确认、避雷确认和待通知候选都会出现在这里。</div>`;
        return;
      }
      list.replaceChildren(...messages.map((msg) => {
        const el = document.createElement("article");
        const key = messageKey(msg);
        const resolvedResult = OBC.state.resolvedMessageResults.get(key);
        el.className = "message-item";
        el.dataset.messageKey = key;
        if (messageType(msg) === "notification") {
          el.classList.add("is-notification");
          el.innerHTML = `<p class="eyebrow">待通知候选</p><h3>${OBC.escapeHtml(msg.title)}</h3><p class="video-meta">${OBC.escapeHtml(msg.reason)}</p><div class="message-note">这类消息来自后端挑出的高置信推荐，用于插件通知；标记已通知后不会反复出现。</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="通知候选状态"><button class="feedback-icon-btn" data-notification-msg="dismiss" type="button" aria-label="标记已通知" title="标记已通知"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg></button></div><div class="message-primary-actions"><button class="small-btn" data-notification-msg="view">去看看</button></div></div>`;
          el.querySelectorAll("[data-notification-msg]").forEach((btn) => btn.addEventListener("click", () => respondNotification(msg, btn.dataset.notificationMsg, el)));
        } else {
          const isAvoidance = messageType(msg) === "avoidance.probe";
          const isChallenge = !isAvoidance && isChallengeProbe(msg);
          el.classList.add(isAvoidance ? "is-avoidance-probe" : isChallenge ? "is-challenge-probe" : "is-interest-probe");
          const eyebrow = isAvoidance ? "避雷确认" : isChallenge ? "挑战探针" : "兴趣确认";
          const actionsLabel = isAvoidance ? "确认或排除这个避雷方向" : isChallenge ? "确认或排除这个挑战方向" : "确认或排除这个兴趣";
          const confirmLabel = isAvoidance ? "确实不喜欢" : "喜欢";
          const rejectLabel = isAvoidance ? "不是" : "不喜欢";
          const kindCopy = isAvoidance
            ? "想少看这类，就确认这是雷点；如果阿B猜错了，点不是。"
            : isChallenge
              ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就点不喜欢。"
            : "想继续探索这个方向，就点喜欢；不准就点不喜欢。";
          el.innerHTML = `<p class="eyebrow">${eyebrow}</p><div class="message-note probe-kind-copy">${OBC.escapeHtml(kindCopy)}</div><h3>${OBC.escapeHtml(msg.domain)}</h3><p class="video-meta">${OBC.escapeHtml(msg.reason)}</p><div class="profile-chip-row">${window.asArray(msg.specifics).map((s) => `<span class="chip">${OBC.escapeHtml(s)}</span>`).join("")}</div><div class="message-card-actions"><div class="card-feedback-icons" aria-label="${actionsLabel}"><button class="feedback-icon-btn" data-probe="confirm" type="button" aria-label="${confirmLabel}" title="${confirmLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg></button><span class="feedback-separator" aria-hidden="true">/</span><button class="feedback-icon-btn" data-probe="reject" type="button" aria-label="${rejectLabel}" title="${rejectLabel}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M17 14V4"/><path d="M9 18.8 10 14H4.6a1.8 1.8 0 0 1-1.7-2.2l1.5-6A2.4 2.4 0 0 1 6.7 4H17"/><path d="M17 14l-4.5 5.3A2 2 0 0 1 9 18v-4"/></svg></button></div><div class="message-primary-actions"><button class="small-btn" data-probe="chat">多聊聊</button></div></div>`;
          if (resolvedResult) {
            el.classList.add("is-resolved");
            const resolvedActions = el.querySelector(".message-card-actions");
            if (resolvedActions) resolvedActions.outerHTML = `<div class="message-note is-success">${OBC.escapeHtml(resolvedResult)}</div>`;
          } else {
            el.querySelectorAll("[data-probe]").forEach((btn) => btn.addEventListener("click", () => respondProbe(msg, btn.dataset.probe, el)));
          }
        }
        return el;
      }));
    }

    async function respondNotification(msg, response, el) {
      if (response === "view" && msg.content_url) window.open(msg.content_url, "_blank", "noopener,noreferrer");
      await OBC.requestJson(OBC.ENDPOINTS.notificationSent, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: msg.bvid }) });
      OBC.state.messages = OBC.state.messages.filter((item) => !(messageType(item) === "notification" && String(item.bvid) === String(msg.bvid)));
      renderMessages();
      if (el) el.remove();
      OBC.showToast(response === "view" ? "已打开并标记这条通知" : "已标记这条通知");
    }

    function collapseMessageItem(key, fallbackEl, onDone) {
      const target = fallbackEl?.isConnected ? fallbackEl : Array.from(document.querySelectorAll("#messageList .message-item")).find((item) => item.dataset.messageKey === key);
      const finish = () => { onDone?.(); };
      if (!target) {
        finish();
        return;
      }
      target.style.height = `${target.getBoundingClientRect().height}px`;
      target.style.minHeight = "0px";
      target.style.overflow = "hidden";
      target.style.transition = `height 240ms var(--ease-standard), opacity 180ms var(--ease-standard), padding 240ms var(--ease-standard), border-width 240ms var(--ease-standard)`;
      target.getBoundingClientRect();
      target.classList.add("is-dismissing");
      target.style.height = "0px";
      window.setTimeout(() => {
        target.remove();
        finish();
      }, 260);
    }

    function appendInlineChatBubble(container, role, text) {
      if (!container) return null;
      const bubble = document.createElement("div");
      bubble.className = `inline-chat-bubble ${role}${role === "reply" ? " inline-chat-reply" : ""}`;
      bubble.textContent = text;
      container.appendChild(bubble);
      return bubble;
    }

    function messageProbeChatPrompt(msg, isAvoidance) {
      return msg.domain
        ? `我想多聊聊「${msg.domain}」这个${isAvoidance ? "避雷" : "兴趣"}方向。`
        : `我想多聊聊这个${isAvoidance ? "避雷" : "兴趣"}方向。`;
    }

    async function pollInlineMessageChatTurn(turnId, chatArea, thinking, startedAt = Date.now()) {
      const showReply = (text, tone = "reply") => {
        thinking?.remove();
        appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), tone, text);
        chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = false; });
        chatArea.querySelector(".inline-chat-input")?.focus();
      };
      try {
        const latest = await OBC.requestJson(`${OBC.ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`);
        if (latest?.status === "completed" || latest?.reply) {
          showReply(latest.reply || "后端已完成这轮聊天。");
          return;
        }
        if (latest?.status === "failed" || Date.now() - startedAt > 180000) {
          showReply(latest?.error || "聊天处理超时，稍后可以在历史里继续查看。", "error");
          return;
        }
      } catch {
        // Keep polling below; transient disconnects should not collapse the inline composer.
      }
      window.setTimeout(() => pollInlineMessageChatTurn(turnId, chatArea, thinking, startedAt), 1200);
    }

    function openInlineMessageProbeChat(msg, el) {
      if (!el) return;
      const existing = el.querySelector(".inline-chat-area");
      if (existing) {
        existing.querySelector(".inline-chat-input")?.focus();
        return;
      }
      const probeType = messageType(msg);
      const isAvoidance = probeType === "avoidance.probe";
      const domain = String(msg.domain || "");
      const prompt = messageProbeChatPrompt(msg, isAvoidance);
      const actions = el.querySelector(".message-card-actions");
      if (actions) actions.hidden = true;
      const chatArea = document.createElement("div");
      chatArea.className = "inline-chat-area";
      chatArea.innerHTML = `
        <div class="inline-chat-turns" aria-live="polite"></div>
        <div class="inline-chat-compose">
          <textarea class="inline-chat-input" rows="2" placeholder="${OBC.escapeHtml(isAvoidance ? `聊聊你为什么想避开「${domain || "这个方向"}」…` : `聊聊你对「${domain || "这个方向"}」的想法…`)}"></textarea>
          <button class="inline-chat-send" type="button">发送</button>
          <button class="inline-chat-cancel" type="button">返回</button>
        </div>`;
      actions?.insertAdjacentElement("afterend", chatArea);
      const input = chatArea.querySelector(".inline-chat-input");
      const sendBtn = chatArea.querySelector(".inline-chat-send");
      const cancelBtn = chatArea.querySelector(".inline-chat-cancel");
      const closeComposer = () => {
        chatArea.remove();
        if (actions) actions.hidden = false;
      };
      const submit = async () => {
        const message = input?.value?.trim() || "";
        if (!message) {
          input?.focus();
          return;
        }
        chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = true; });
        appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "user", message);
        const thinking = appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "thinking", "阿B 正在结合这条探针思考…");
        const turnId = createClientTurnId(isAvoidance ? "avoidance-probe" : "probe");
        try {
          const turn = await OBC.requestJsonStrict(OBC.ENDPOINTS.chatTurns, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              turn_id: turnId,
              session: "webui",
              scope: isAvoidance ? "avoidance_probe" : "probe",
              subject_id: domain,
              subject_title: domain || (isAvoidance ? "这个避雷方向" : "这个兴趣方向"),
              message: `${prompt}\n\n${message}`
            })
          });
          if (input) input.value = "";
          void pollInlineMessageChatTurn(turn?.turn_id || turnId, chatArea, thinking);
        } catch (error) {
          thinking?.remove();
          appendInlineChatBubble(chatArea.querySelector(".inline-chat-turns"), "error", error?.message || "后台正忙，等一下再聊。");
          chatArea.querySelectorAll(".inline-chat-input, .inline-chat-send, .inline-chat-cancel").forEach((control) => { control.disabled = false; });
          input?.focus();
        }
      };
      sendBtn?.addEventListener("click", () => void submit());
      cancelBtn?.addEventListener("click", closeComposer);
      input?.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          void submit();
        }
        if (event.key === "Escape") closeComposer();
      });
      window.setTimeout(() => input?.focus(), 40);
    }

    async function respondProbe(msg, response, el) {
      if (!el) return;
      const actions = el.querySelector(".message-card-actions");
      if (response === "chat") {
        openInlineMessageProbeChat(msg, el);
        OBC.showToast("已在这条消息里打开聊天输入");
        return;
      }
      const key = messageKey(msg);
      OBC.state.messageListDomLocked = true;
      if (!OBC.state.messageListSnapshot && isMessagesDrawerOpen()) OBC.state.messageListSnapshot = getRenderableMessages();
      el.style.minHeight = `${el.getBoundingClientRect().height}px`;
      el.classList.add("is-resolving");
      OBC.state.resolvingMessageKeys.add(key);
      actions?.querySelectorAll("button").forEach((button) => { button.disabled = true; });
      const probeType = messageType(msg);
      const domain = msg.domain || "";
      const handledKey = probeKey(probeType, domain);
      if (handledKey) OBC.state.handledProbeKeys.add(handledKey);
      try {
        const isAvoidance = probeType === "avoidance.probe";
        const endpoint = isAvoidance ? OBC.ENDPOINTS.avoidanceProbeRespond : OBC.ENDPOINTS.interestProbeRespond;
        const apiResp = await OBC.requestJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domain: msg.domain, response, message: "" }) });
        if (apiResp && apiResp.ok === false) {
          OBC.state.resolvingMessageKeys.delete(key);
          OBC.state.messages = OBC.state.messages.filter((item) => messageKey(item) !== key);
          if (OBC.state.messageListSnapshot) OBC.state.messageListSnapshot = OBC.state.messageListSnapshot.filter((item) => messageKey(item) !== key);
          OBC.state.messageListDomLocked = false;
          renderMessages();
          void refreshProfile();
          return;
        }
        const result = isAvoidance
          ? response === "confirm" ? "已确认避雷方向，后续会减少类似内容。" : "已搁置，暂时不作为避雷方向。"
          : response === "confirm" ? "已确认，后续推荐会提高权重。" : "已搁置，后续会少试探这个方向。";
        OBC.state.resolvedMessageResults.set(key, result);
        el.classList.remove("is-resolving");
        el.classList.add("is-resolved");
        if (actions) {
          actions.classList.add("is-result");
          actions.innerHTML = `<div class="message-action-result" title="${OBC.escapeHtml(result)}">${OBC.escapeHtml(result)}</div>`;
        }
        OBC.showToast(isAvoidance
          ? response === "confirm" ? "已确认这个避雷方向" : "已搁置这个避雷方向"
          : response === "confirm" ? "已确认这个兴趣方向" : "已搁置这个兴趣方向");
        setTimeout(() => {
          collapseMessageItem(key, el, () => {
            OBC.state.resolvingMessageKeys.delete(key);
            OBC.state.resolvedMessageResults.delete(key);
            OBC.state.messages = OBC.state.messages.filter((item) => messageKey(item) !== key);
            if (OBC.state.messageListSnapshot) OBC.state.messageListSnapshot = OBC.state.messageListSnapshot.filter((item) => messageKey(item) !== key);
            OBC.state.messageListDomLocked = false;
            renderMessages();
            void refreshProfile();
          });
        }, 1800);
      } catch (error) {
        OBC.state.resolvingMessageKeys.delete(key);
        OBC.state.resolvedMessageResults.delete(key);
        OBC.state.messageListDomLocked = false;
        el.classList.remove("is-resolving");
        el.style.minHeight = "";
        if (handledKey) OBC.state.handledProbeKeys.delete(handledKey);
        actions?.querySelectorAll("button").forEach((button) => { button.disabled = false; });
        OBC.showToast(`确认反馈失败：${error.message || "后端不可用"}`);
      }
    }

    function bindSpeculativeActions() {
      document.querySelectorAll("[data-spec-response]").forEach((button) => {
        button.addEventListener("click", () => respondSpeculativeInterest(button));
      });
    }
    window.bindSpeculativeActions = bindSpeculativeActions;

    async function respondSpeculativeInterest(button) {
      const row = button.closest("[data-spec-domain]");
      const domain = row?.dataset.specDomain;
      const response = button.dataset.specResponse;
      if (!domain || !response) return;
      row.querySelectorAll("[data-spec-response]").forEach((btn) => { btn.disabled = true; });
      const type = button.dataset.specType || "interest.probe";
      const key = probeKey(type, domain);
      if (key) OBC.state.handledProbeKeys.add(key);
      try {
        const isAvoidance = isAvoidanceProbe(type);
        const endpoint = isAvoidance ? OBC.ENDPOINTS.avoidanceProbeRespond : OBC.ENDPOINTS.interestProbeRespond;
        const payload = { domain, response, message: "" };
        if (!isAvoidance) payload.surface = "profile";
        const apiResp = await OBC.requestJson(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        if (apiResp && apiResp.ok === false) {
          row.remove();
          OBC.state.messages = OBC.state.messages.filter((msg) => messageKey(msg) !== key);
          if (OBC.state.messageListSnapshot) OBC.state.messageListSnapshot = OBC.state.messageListSnapshot.filter((msg) => messageKey(msg) !== key);
          renderMessages();
          void refreshProfile();
          return;
        }
        const result = isAvoidance
          ? (response === "confirm" ? `好，「${OBC.escapeHtml(domain)}」会作为避雷方向处理。` : `好，「${OBC.escapeHtml(domain)}」不记成避雷。`)
          : (response === "confirm" ? `好，「${OBC.escapeHtml(domain)}」记住了。` : `好，「${OBC.escapeHtml(domain)}」先不看了。`);
        row.innerHTML = `<p class="spec-result">${result}</p>`;
        OBC.state.messages = OBC.state.messages.filter((msg) => messageKey(msg) !== key);
        if (OBC.state.messageListSnapshot) OBC.state.messageListSnapshot = OBC.state.messageListSnapshot.filter((msg) => messageKey(msg) !== key);
        renderMessages();
        OBC.showToast(isAvoidance
          ? response === "confirm" ? "已确认这个避雷方向" : "已排除这个避雷方向"
          : response === "confirm" ? "已确认这个猜测兴趣" : "已排除这个猜测兴趣");
        setTimeout(() => { void refreshProfile(); }, 1200);
      } catch (error) {
        if (key) OBC.state.handledProbeKeys.delete(key);
        row.querySelectorAll("[data-spec-response]").forEach((btn) => { btn.disabled = false; });
        OBC.showToast(`确认反馈失败：${error.message || "后端不可用"}`);
      }
    }

    function createClientTurnId(prefix = "webui") {
      const suffix = window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      return `${prefix}-${suffix}`;
    }

    function normalizeDelightTurn(turn) {
      if (!turn) return null;
      const message = String(turn.message ?? turn.user_message ?? "");
      const reply = String(turn.reply ?? turn.assistant_message ?? "");
      const status = String(turn.status || (reply ? "completed" : "pending"));
      const turnId = String(turn.turn_id ?? turn.id ?? "");
      if (!turnId && !message && !reply) return null;
      return {
        turn_id: turnId,
        message,
        reply,
        status,
        error: String(turn.error ?? "")
      };
    }

    function delightTurnList(turns) {
      return window.asArray(turns).map(normalizeDelightTurn).filter(Boolean);
    }

    function upsertDelightTurn(turns, nextTurn) {
      const normalized = normalizeDelightTurn(nextTurn);
      const existing = delightTurnList(turns);
      if (!normalized) return existing;
      const index = existing.findIndex((turn) => turn.turn_id && turn.turn_id === normalized.turn_id);
      if (index < 0) return [...existing, normalized];
      return existing.map((turn, turnIndex) => turnIndex === index ? normalized : turn);
    }

    function mergeDelightTurnLists(currentTurns, incomingTurns) {
      let merged = delightTurnList(currentTurns);
      for (const turn of delightTurnList(incomingTurns)) merged = upsertDelightTurn(merged, turn);
      return merged;
    }

    function mergeDelightItem(current, incoming) {
      if (!current) return incoming;
      return {
        ...current,
        ...incoming,
        chat_turn_id: incoming.chat_turn_id || current.chat_turn_id || "",
        chat_reply: incoming.chat_reply || current.chat_reply || "",
        chat_draft: incoming.chat_draft || current.chat_draft || "",
        response_message: incoming.response_message || current.response_message || "",
        turns: mergeDelightTurnLists(current.turns, incoming.turns)
      };
    }

    function renderDelightTurns(delight) {
      const area = $("#delightTurns");
      if (!area) return;
      area.replaceChildren();
      const turns = delightTurnList(delight?.turns);
      if (!turns.length && !delight?.chat_reply) {
        area.hidden = true;
        OBC.scheduleActivityRailHeightSync();
        return;
      }
      area.hidden = false;
      if (!turns.length && delight?.chat_reply) {
        const bubble = document.createElement("div");
        bubble.className = "delight-turn-bubble is-assistant";
        bubble.textContent = delight.chat_reply;
        area.append(bubble);
        OBC.scheduleActivityRailHeightSync();
        return;
      }
      for (const turn of turns) {
        if (turn.message) {
          const userBubble = document.createElement("div");
          userBubble.className = "delight-turn-bubble is-user";
          userBubble.textContent = turn.message;
          area.append(userBubble);
        }
        const assistantBubble = document.createElement("div");
        const status = String(turn.status || "pending");
        assistantBubble.className = `delight-turn-bubble is-assistant${status === "pending" ? " is-thinking" : ""}${status === "failed" ? " is-error" : ""}`;
        assistantBubble.textContent = status === "pending"
          ? "阿B 正在品你这句话…"
          : status === "failed"
            ? turn.error || "这句还没发出去，稍后再试。"
            : turn.reply || "后端已完成这轮聊天。";
        area.append(assistantBubble);
      }
      OBC.scheduleActivityRailHeightSync();
    }

    function updateDelightState(bvid, updates) {
      const key = String(bvid || "");
      if (!key) return null;
      let current = null;
      OBC.state.delights = OBC.state.delights.map((item) => {
        if (String(item.bvid || "") !== key) return item;
        current = { ...item, ...updates };
        return current;
      });
      if (OBC.state.delight && String(OBC.state.delight.bvid || "") === key) {
        OBC.state.delight = { ...OBC.state.delight, ...updates };
        current = OBC.state.delight;
      }
      if (current && OBC.state.delight && String(OBC.state.delight.bvid || "") === key) {
        renderDelightTurns(OBC.state.delight);
        if ($("#delightStatus")) $("#delightStatus").textContent = OBC.state.delight.response_message || "";
      }
      return current;
    }

    function applyTurnToDelight(turn) {
      const subjectId = String(turn?.subject_id || turn?.bvid || "");
      if (!turn || (turn.scope && turn.scope !== "delight") || !subjectId) return null;
      const existing = OBC.state.delights.find((item) => String(item.bvid || "") === subjectId)
        || (OBC.state.delight && String(OBC.state.delight.bvid || "") === subjectId ? OBC.state.delight : null);
      const entry = normalizeDelightTurn(turn);
      if (!entry) return null;
      const status = String(entry.status || "pending");
      const updates = {
        chat_turn_id: entry.turn_id,
        turns: upsertDelightTurn(existing?.turns, entry),
        response_message: status === "completed" ? "这句已经记下，后面会更会试探。" : status === "failed" ? "这句还没发出去，稍后再试。" : "阿B 正在品你这句话。"
      };
      if (status === "completed") {
        updates.chat_reply = entry.reply || existing?.chat_reply || "";
        updates.chat_draft = "";
      }
      return updateDelightState(subjectId, updates);
    }

    function pollChatTurnUntilSettled(turnId, fallbackTurn) {
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await OBC.requestJson(`${OBC.ENDPOINTS.chatTurns}/${encodeURIComponent(turnId)}`);
        if (latest) {
          const scopedTurn = { ...fallbackTurn, ...latest, scope: latest.scope || "delight", subject_id: latest.subject_id || fallbackTurn.subject_id };
          applyTurnToDelight(scopedTurn);
          if (latest.status === "completed" || latest.status === "failed") return;
        }
        if (Date.now() - startedAt > 180000) {
          applyTurnToDelight({ ...fallbackTurn, status: "failed", error: "聊天处理超时，稍后可以在历史里继续查看。" });
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function respondDelight(delight, response, el = null) {
      if (!delight) return;
      if (response === "chat") { openDelightComposer(); return; }
      if (response === "cancel-comment") { closeDelightComposer(); return; }
      if (response === "watch-later") {
        const btn = el?.closest("[data-action]") || document.querySelector('[data-delight="watch-later"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        try {
          if (wasSaved) {
            await OBC.requestJson(`${OBC.ENDPOINTS.watchLater}/${encodeURIComponent(delight.bvid)}`, { method: "DELETE" });
          } else {
            await OBC.requestJson(OBC.ENDPOINTS.watchLater, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: delight.bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "favorite") {
        const btn = el?.closest("[data-action]") || document.querySelector('[data-delight="favorite"]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const wasSaved = btn.getAttribute("aria-pressed") === "true";
        btn.setAttribute("aria-pressed", wasSaved ? "false" : "true");
        try {
          if (wasSaved) {
            await OBC.requestJson(`${OBC.ENDPOINTS.favorites}/${encodeURIComponent(delight.bvid)}`, { method: "DELETE" });
          } else {
            await OBC.requestJson(OBC.ENDPOINTS.favorites, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bvid: delight.bvid }) });
          }
        } catch {
          btn.setAttribute("aria-pressed", wasSaved ? "true" : "false");
        } finally {
          btn.disabled = false;
        }
        return;
      }
      if (response === "send-comment") {
        const input = $("#delightCommentInput");
        const note = input?.value?.trim() || "";
        if (!note) {
          if ($("#delightStatus")) $("#delightStatus").textContent = "先写一句想聊的内容，再提交这轮对话。";
          input?.focus();
          return;
        }
        const turnId = createClientTurnId("delight");
        const pendingTurn = { turn_id: turnId, session: "webui", scope: "delight", subject_id: delight.bvid, subject_title: delight.title || "", message: note, reply: "", status: "pending", error: "" };
        applyTurnToDelight(pendingTurn);
        if (input) input.value = "";
        closeDelightComposer();
        try {
          const turn = await OBC.requestJsonStrict(OBC.ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pendingTurn) });
          const scopedTurn = { ...pendingTurn, ...(turn || {}), scope: turn?.scope || "delight", subject_id: turn?.subject_id || delight.bvid };
          applyTurnToDelight(scopedTurn);
          if (scopedTurn.turn_id && scopedTurn.status !== "completed" && scopedTurn.status !== "failed") pollChatTurnUntilSettled(scopedTurn.turn_id, scopedTurn);
          OBC.showToast("已提交聊天线索");
        } catch (error) {
          applyTurnToDelight({ ...pendingTurn, status: "failed", error: error.message || "聊天提交失败，请稍后再试。" });
          if (input) input.value = note;
          OBC.showToast(`聊天提交失败：${error.message || "后端不可用"}`);
        }
        return;
      }
      if (response === "view") {
        const url = delight.content_url || (delight.bvid ? `https://www.bilibili.com/video/${encodeURIComponent(delight.bvid)}` : "");
        if (url) window.open(url, "_blank", "noopener,noreferrer");
        trackRecommendationClick(delight);
        // 浏览过即已读：上报 view 让后端标记 delight_notified，下次重灌不再出现。
        // fire-and-forget，不阻塞打开内容；当场卡片仍保留。
        OBC.requestJson(OBC.ENDPOINTS.delightRespond, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bvid: delight.bvid, response: "view", title: delight.title || "", message: "" })
        }).catch(() => {});
        OBC.showToast(url ? "已打开惊喜推荐" : "后端没有返回可打开链接");
        return;
      }
      const feedbackToast = response === "like" ? "惊喜推荐已喜欢" : response === "dislike" ? "这类惊喜先少来点" : "已忽略这条惊喜推荐";
      const toastImmediately = response === "like" || response === "dislike";
      if (toastImmediately) OBC.showToast(feedbackToast);
      await OBC.requestJson(OBC.ENDPOINTS.delightRespond, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bvid: delight.bvid,
          response,
          title: delight.title,
          message: ""
        })
      });
      if (response === "like") {
        updateDelightState(delight.bvid, { response_message: "好，这类多来点。" });
        if (el) el.closest(".video-card")?.classList.add("is-liked");
      }
      if (response === "dislike" || response === "dismiss") {
        OBC.state.delights = OBC.state.delights.filter((item) => item.bvid !== delight.bvid);
        OBC.state.delightPage = null;
        renderDelightGrid();
      }
      if (!toastImmediately) OBC.showToast(feedbackToast);
    }

    function openMessageChat(msg) {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      const input = $("#messageChatInput");
      OBC.state.messageScrollTop = panel?.scrollTop || 0;
      const type = messageType(msg);
      const isAvoidance = type === "avoidance.probe";
      OBC.state.messageChatDomain = msg.domain || "";
      OBC.state.messageChatScope = isAvoidance ? "avoidance_probe" : "probe";
      openPanel("messagesDrawer");
      drawer?.classList.add("is-chatting");
      if (view) view.hidden = false;
      const title = $("#messageChatTitle");
      const context = $("#messageChatContext");
      const prompt = msg.domain
        ? `我想多聊聊「${msg.domain}」这个${isAvoidance ? "避雷" : "兴趣"}方向。`
        : `我想多聊聊这个${isAvoidance ? "避雷" : "兴趣"}方向。`;
      OBC.state.messageChatPrompt = prompt;
      OBC.state.messageChatSubjectTitle = msg.domain || (isAvoidance ? "这个避雷方向" : "这个兴趣方向");
      if (title) title.textContent = msg.domain ? `聊聊${isAvoidance ? "避雷" : "兴趣"}「${msg.domain}」` : `聊聊这个${isAvoidance ? "避雷" : "兴趣"}`;
      if (context) context.textContent = msg.reason || `这轮对话会沿用消息里的${isAvoidance ? "避雷" : "兴趣"}上下文。`;
      if (input) {
        input.value = "";
        input.placeholder = "继续写你想补充的问题、偏好或例子";
      }
      renderChat();
      if (panel) panel.scrollTop = 0;
      window.setTimeout(() => input?.focus(), 80);
    }

    function returnToMessages() {
      const drawer = $("#messagesDrawer");
      const panel = $("#messagesPanel");
      const view = $("#messageChatView");
      drawer?.classList.remove("is-chatting");
      if (view) view.hidden = true;
      OBC.state.messageChatDomain = "";
      OBC.state.messageChatPrompt = "";
      OBC.state.messageChatScope = "probe";
      OBC.state.messageChatSubjectTitle = "";
      window.setTimeout(() => {
        if (panel) panel.scrollTop = OBC.state.messageScrollTop || 0;
      }, 0);
    }

    function chatHtml(messages) {
      return messages.map((msg) => {
        const refs = Array.isArray(msg.references) ? msg.references.filter((item) => item && item.title) : [];
        // Only agent turns carry references, and only when the reply was
        // actually grounded in the crawled library.
        const badge = refs.length
          ? `<div class="chat-refs" title="${OBC.escapeHtml(refs.map((item) => item.title).join("\n"))}">已参考 ${refs.length} 篇收藏</div>`
          : "";
        return `<div class="chat-bubble ${msg.role === "user" ? "user" : "agent"}">${OBC.escapeHtml(msg.text)}${badge}</div>`;
      }).join("");
    }

    function renderChat() {
      const chatLog = $("#chatLog");
      if (chatLog) {
        chatLog.innerHTML = chatHtml(OBC.state.chat);
        chatLog.scrollTop = chatLog.scrollHeight;
      }
      const messageChatLog = $("#messageChatLog");
      if (messageChatLog) {
        const baseMessages = OBC.state.messageChatPrompt
          ? OBC.state.chat.filter((msg) => msg.text !== "你可以直接告诉我最近想多看什么、少看什么，或者评价一条推荐为什么准/不准。")
          : OBC.state.chat;
        const messages = OBC.state.messageChatPrompt ? [{ role: "user", text: OBC.state.messageChatPrompt }, ...baseMessages] : baseMessages;
        messageChatLog.innerHTML = chatHtml(messages);
        messageChatLog.scrollTop = messageChatLog.scrollHeight;
      }
    }

    async function sendChat(message, options = {}) {
      const payloadMessage = options.contextPrefix ? `${options.contextPrefix}\n\n${message}` : message;
      OBC.state.chat.push({ role: "user", text: message });
      OBC.state.chat.push({ role: "agent", text: "正在提交给后端，并等待 durable chat turn 完成。" });
      renderChat();
      const payload = {
        session: "webui",
        scope: options.scope || "chat",
        subject_id: options.subjectId || "",
        subject_title: options.subjectTitle || "",
        message: payloadMessage
      };
      const turn = await OBC.requestJson(OBC.ENDPOINTS.chatTurns, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!turn?.turn_id) {
        OBC.state.chat[OBC.state.chat.length - 1] = { role: "agent", text: "当前没有连上后端，聊天没有提交成功。请检查 FastAPI 地址后重试。" };
        renderChat();
        OBC.showToast("聊天提交失败：后端不可用");
        return;
      }
      const startedAt = Date.now();
      const poll = async () => {
        const latest = await OBC.requestJson(`${OBC.ENDPOINTS.chatTurns}/${encodeURIComponent(turn.turn_id)}`);
        if (latest?.status === "completed" || latest?.reply) {
          OBC.state.chat[OBC.state.chat.length - 1] = {
            role: "agent",
            text: latest.reply || "后端已完成这轮聊天。",
            references: Array.isArray(latest?.references) ? latest.references : []
          };
          renderChat();
          return;
        }
        if (latest?.status === "failed" || Date.now() - startedAt > 180000) {
          OBC.state.chat[OBC.state.chat.length - 1] = { role: "agent", text: latest?.error || "聊天处理超时，稍后可以在历史里继续查看。" };
          renderChat();
          return;
        }
        window.setTimeout(poll, 1200);
      };
      window.setTimeout(poll, 1200);
    }

    async function refreshRecommendations() {
      const result = await OBC.requestJson(OBC.ENDPOINTS.refresh, { method: "POST" });
      if (result) {
        OBC.showToast("已请求后端开始补货");
        await hydrateFromBackend();
      } else {
        OBC.showToast("刷新失败：请检查后端连接");
      }
    }

    async function dismissVisibleRecommendationsBeforeReshuffle() {
      const visibleItems = filteredVideos().filter((item) => item?.id != null);
      if (!visibleItems.length) return { total: 0, ok: 0, failed: 0 };
      OBC.showToast(`正在忽略当前显示的 ${visibleItems.length} 张推荐…`);
      const results = await Promise.allSettled(visibleItems.map((item) => submitFeedback(item, "dismiss")));
      const dismissedKeys = new Set();
      results.forEach((result, index) => {
        if (result.status === "fulfilled") dismissedKeys.add(recommendationKey(visibleItems[index]));
      });
      if (dismissedKeys.size) {
        OBC.state.videos = OBC.state.videos.filter((item) => !dismissedKeys.has(recommendationKey(item)));
      }
      return { total: visibleItems.length, ok: dismissedKeys.size, failed: visibleItems.length - dismissedKeys.size };
    }

    async function reshuffle(platformOverride) {
      const reshuffleButton = $("#reshuffleBtn");
      const dismissToggle = $("#dismissOnReshuffleToggle");
      if (reshuffleButton) reshuffleButton.disabled = true;
      if (dismissToggle) dismissToggle.disabled = true;
      try {
        const dismissResult = OBC.state.dismissOnReshuffle ? await dismissVisibleRecommendationsBeforeReshuffle() : null;
        // 根据当前页面决定平台参数：
        // - 首页 / 全部推荐 → null（零筛选）
        // - 按平台 → 当前选中的平台filter
        // - 自定义筛选 → 勾选的平台（仅一个平台时透传，多选/全不选传null由客户端过滤）
        let platform;
        if (platformOverride !== undefined) {
          platform = platformOverride;
        } else {
          const activePage = document.querySelector(".main-col:not([hidden])");
          const pageId = activePage?.id || "homePage";
          if (pageId === "customFilterPage") {
            platform = currentCustomFilterPlatform();
          } else {
            platform = null;
          }
        }
        // 根据当前页面决定批量大小：
        // - 自定义筛选 → 用户在页面里设置的每批数量
        // - 首页 / 按平台 → 默认 10
        let batchLimit = 10;
        if (platformOverride === undefined) {
          const activePage = document.querySelector(".main-col:not([hidden])");
          const pageId = activePage?.id || "homePage";
          if (pageId === "customFilterPage" && OBC.state.customLimit) {
            batchLimit = OBC.state.customLimit;
          }
        }
        const params = new URLSearchParams();
        if (platform) params.set("platform", platform);
        if (batchLimit && batchLimit !== 10) params.set("limit", String(batchLimit));
        const url = params.size ? `${OBC.ENDPOINTS.reshuffle}?${params.toString()}` : OBC.ENDPOINTS.reshuffle;
        const payload = await OBC.requestJson(url, { method: "POST" });
        if (payload?.items?.length) {
          OBC.state.videos = OBC.normalizeRecommendationList(payload.items);
          renderAll();
          if (dismissResult?.ok) {
            const failedText = dismissResult.failed ? `，${dismissResult.failed} 张忽略失败` : "";
            OBC.showToast(`已忽略 ${dismissResult.ok} 张当前推荐并换一批${failedText}`);
          } else {
            OBC.showToast("已换一批推荐");
          }
        } else {
          renderAll();
          // Distinguish "pool is drained" (200 + empty items) from a real
          // connectivity failure (OBC.requestJson resolves to null). Reporting a
          // drained pool as a connection error sent people hunting for a
          // backend problem that did not exist.
          if (payload) {
            OBC.showToast("暂时没有新内容了，后台正在补货，稍后再试");
          } else {
            OBC.showToast("换一批失败：请检查后端连接");
          }
        }
      } finally {
        if (reshuffleButton) reshuffleButton.disabled = false;
        if (dismissToggle) dismissToggle.disabled = false;
      }
    }

    async function appendMore() {
      const payload = await OBC.requestJson(OBC.ENDPOINTS.append, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ excluded_bvids: OBC.state.videos.map((v) => v.bvid) }) });
      if (payload?.items?.length) {
        const freshItems = OBC.normalizeRecommendationList(payload.items);
        OBC.state.videos = OBC.state.videos.concat(freshItems);
        renderAll();
        // Keep decoding off the interaction path: slow first-miss covers should
        // not delay the new recommendation cards from appearing.
        void warmCoverImages(freshItems, { waitForDecode: true }).catch(() => {});
        OBC.showToast(freshItems.length ? "已加载更多推荐" : "后端返回的内容都已反馈过");
      } else {
        OBC.showToast("加载更多失败：后端没有返回新候选");
      }
    }

    function normalizeRuntimeStatus(status) {
      if (!status) return null;
      const previous = OBC.state.runtimeStatus || {};
      const incomingType = String(status.type || status.runtime_event_type || "");
      const merged = { ...previous, ...status };
      let manualRefreshState = status.manual_refresh_state != null
        ? String(status.manual_refresh_state || "idle")
        : String(previous.manual_refresh_state || "");
      if (status.manual_refresh_state == null) {
        if (incomingType === "refresh.started" || incomingType === "refresh.strategy") manualRefreshState = "running";
        if (incomingType === "refresh.pool_updated") manualRefreshState = "success";
        if (incomingType === "refresh.failed") manualRefreshState = "failed";
      }
      return {
        initialized: merged.initialized !== false,
        recommendation_count: Number(merged.recommendation_count ?? 0),
        pending_signal_events: Number(merged.pending_signal_events ?? 0),
        last_refresh_at: String(merged.last_refresh_at ?? ""),
        last_notification_at: String(merged.last_notification_at ?? ""),
        unread_count: Number(merged.unread_count ?? OBC.state.messages.length ?? 0),
        pool_available_count: Number(merged.pool_available_count ?? merged.pool_available ?? merged.available_count ?? 0),
        pool_pending_count: Number(merged.pool_pending_count ?? 0),
        pool_target_count: Number(merged.pool_target_count ?? OBC.state.config?.scheduler?.pool_target_count ?? 0),
        last_discovered_count: Number(merged.last_discovered_count ?? 0),
        last_replenished_count: Number(merged.last_replenished_count ?? 0),
        recent_pool_topics: Array.isArray(merged.recent_pool_topics) ? merged.recent_pool_topics.map(String).filter(Boolean) : [],
        manual_refresh_state: manualRefreshState || "idle",
        manual_refresh_message: String(merged.manual_refresh_message || ""),
        runtime_event_type: incomingType || String(merged.runtime_event_type || ""),
        live_summary: String(merged.live_summary || merged.message || merged.state || "")
      };
    }

    function hasPostInitRuntimeSignals(runtime) {
      return Boolean(runtime) && (
        runtime.recommendation_count > 0 ||
        runtime.pool_available_count > 0 ||
        runtime.pool_pending_count > 0 ||
        runtime.last_replenished_count > 0 ||
        runtime.last_discovered_count > 0
      );
    }

    function shouldShowInitOnboarding(status) {
      const runtime = normalizeRuntimeStatus(status);
      if (OBC.initWaitingForFirstPool(OBC.state.initStatus)) return true;
      if (Boolean(OBC.state.initStatus?.running)) return true;
      return Boolean(status) && runtime.initialized === false && !hasPostInitRuntimeSignals(runtime);
    }

    function getPoolStatusSummary(status) {
      const runtime = normalizeRuntimeStatus(status);
      if (!runtime || !runtime.initialized) return null;
      const sufficient = runtime.pool_target_count > 0 && runtime.pool_available_count >= runtime.pool_target_count;
      if (runtime.manual_refresh_state === "running") {
        return runtime.pool_available_count > 0
          ? { available: `还有 ${runtime.pool_available_count} 条可换`, replenished: "后台继续在找更多", topics: "可以先换一批，新的随时进" }
          : { available: "暂无可换库存", replenished: "正在补货", topics: "后台还在继续给你找新的" };
      }
      return {
        available: `还有 ${runtime.pool_available_count} 条可换`,
        replenished: runtime.last_replenished_count > 0
          ? `刚补进 ${runtime.last_replenished_count} 条`
          : runtime.last_discovered_count > 0
            ? "这轮找到了内容"
            : sufficient
              ? "这会儿先不补货"
              : "这轮还没补进",
        topics: runtime.recent_pool_topics.length > 0
          ? runtime.recent_pool_topics.join(" / ")
          : runtime.last_discovered_count > 0
            ? "但可立即换的库存还没变"
            : sufficient
              ? "先把这一池给你慢慢换开"
              : "还在继续摸你的口味"
      };
    }

    function configuredSourceCount() {
      const sources = OBC.state.config?.sources;
      if (!sources || typeof sources !== "object") return 0;
      const shares = OBC.state.config?.scheduler?.pool_source_shares || {};
      return Object.entries(sources).reduce((count, [key, value]) => {
        if (!value || typeof value !== "object" || Array.isArray(value)) return count;
        if (Object.prototype.hasOwnProperty.call(value, "enabled")) {
          return count + (value.enabled !== false ? 1 : 0);
        }
        if (Object.prototype.hasOwnProperty.call(shares, key)) {
          return count + (Number(shares[key] ?? 0) > 0 ? 1 : 0);
        }
        return count;
      }, 0);
    }

    function syncSourceMetric() {
      const count = configuredSourceCount();
      $("#metricSources").textContent = count ? String(count) : "—";
    }

    function getPoolRefreshLabel(runtime) {
      if (!runtime) return "—";
      if (runtime.manual_refresh_message) return runtime.manual_refresh_message;
      if (runtime.manual_refresh_state === "running") return runtime.pool_available_count > 0 ? "后台继续补货中" : "正在补货";
      if (runtime.manual_refresh_state === "success") return "刚同步完成";
      if (runtime.manual_refresh_state === "failed") return "刷新失败";
      if (runtime.pending_signal_events > 0) return `已记下 ${runtime.pending_signal_events} 个新动作`;
      if (runtime.runtime_event_type === "refresh.pool_updated") return "刚同步推荐池";
      return runtime.pool_available_count > 0 ? "可直接换一批" : "等待后台补货";
    }

    function renderPoolStatus(status = OBC.state.runtimeStatus) {
      const runtime = normalizeRuntimeStatus(status);
      const summary = getPoolStatusSummary(runtime);
      $("#poolAvailable").textContent = summary?.available || "后端未初始化";
      $("#poolReplenished").textContent = summary?.replenished || "—";
      $("#poolTopics").textContent = summary?.topics || "—";
      $("#poolRefreshState").textContent = getPoolRefreshLabel(runtime);
    }

    function applyRuntimeStatus(payload) {
      if (!payload) return;
      OBC.state.runtimeStatus = normalizeRuntimeStatus(payload);
      const summary = getPoolStatusSummary(OBC.state.runtimeStatus);
      $("#statusLabel").textContent = OBC.state.runtimeStatus.initialized === false ? "后端未初始化" : "已连接本地后端";
      $("#metricPool").textContent = String(OBC.state.runtimeStatus.pool_available_count);
      syncMessageCount();
      syncSourceMetric();
      $("#runtimeSummary").textContent = OBC.state.runtimeStatus.live_summary || summary?.available || "后端在线，推荐池与采集运行时可读取。";
      renderPoolStatus(OBC.state.runtimeStatus);
    }

    function setInput(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    function setCookieOverrideInput(id, currentCookie, platformLabel) {
      const el = document.getElementById(id);
      if (!el) return;
      el.value = "";
      const hasCookie = Boolean(String(currentCookie || "").trim());
      el.placeholder = hasCookie
        ? `已保存${platformLabel} Cookie；留空保存不会覆盖，需要更换时粘贴新的 Cookie`
        : `未保存${platformLabel} Cookie；需要手动覆盖时粘贴 Cookie`;
    }

    function getInput(id) {
      return document.getElementById(id)?.value?.trim() || "";
    }

    function getIntInput(id, fallback) {
      const value = Number.parseInt(getInput(id), 10);
      return Number.isFinite(value) ? value : fallback;
    }

    function getFloatInput(id, fallback) {
      const value = Number.parseFloat(getInput(id));
      return Number.isFinite(value) ? value : fallback;
    }

    const ZHIHU_SOURCE_MODE_FIELDS = [
      ["search", "zhihuModeSearch"],
      ["hot", "zhihuModeHot"],
      ["feed", "zhihuModeFeed"],
      ["creator", "zhihuModeCreator"],
      ["related", "zhihuModeRelated"],
    ];

    function setZhihuSourceModes(rawModes) {
      const fallbackModes = ZHIHU_SOURCE_MODE_FIELDS.map(([mode]) => mode);
      const selected = new Set(
        (Array.isArray(rawModes) && rawModes.length > 0 ? rawModes : fallbackModes)
          .map((mode) => String(mode).trim())
          .filter(Boolean),
      );
      for (const [mode, id] of ZHIHU_SOURCE_MODE_FIELDS) {
        const el = document.getElementById(id);
        if (el) el.checked = selected.has(mode);
      }
    }

    function collectZhihuSourceModes() {
      const selected = ZHIHU_SOURCE_MODE_FIELDS
        .filter(([, id]) => document.getElementById(id)?.checked === true)
        .map(([mode]) => mode);
      return selected.length > 0 ? selected : ["search"];
    }

    function joinPath(directory, filename) {
      const dir = String(directory || "").trim();
      const name = String(filename || "").trim();
      if (!dir) return name;
      if (!name) return dir;
      return dir.endsWith("/") || dir.endsWith("\\") ? `${dir}${name}` : `${dir}/${name}`;
    }

    function resolveLogPath(loggingConfig) {
      if (loggingConfig?.file_path) return loggingConfig.file_path;
      return joinPath(loggingConfig?.directory || "logs", loggingConfig?.filename || "openbiliclaw.log");
    }

    function splitLogPath(rawPath, currentLogging) {
      const fallback = { directory: "logs", filename: "openbiliclaw.log" };
      const trimmed = String(rawPath || "").trim();
      if (!trimmed) return fallback;
      if (currentLogging && trimmed === resolveLogPath(currentLogging)) {
        return { directory: currentLogging.directory || fallback.directory, filename: currentLogging.filename || fallback.filename };
      }
      const normalized = trimmed.replaceAll("\\", "/").replace(/\/+$/, "");
      const slashIndex = normalized.lastIndexOf("/");
      if (slashIndex === -1) return { directory: fallback.directory, filename: normalized || fallback.filename };
      return { directory: normalized.slice(0, slashIndex) || "/", filename: normalized.slice(slashIndex + 1) || fallback.filename };
    }

    function setSelect(id, value) {
      const el = document.getElementById(id);
      if (el && value !== undefined && value !== null) el.value = String(value);
    }

    // Unified per-source login / cookie status (GET /api/sources/status),
    // rendered with separate scheduling and credential/plugin states.
    const SOURCE_STATUS_KEYS = [
      "bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu",
      "v2ex", "rss", "reddit", "wechat", "xiaoyuzhou",
    ];
    const CURRENT_CREDENTIAL_KEYS = [
      "bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu",
      "v2ex", "rss", "reddit", "wechat", "xiaoyuzhou",
    ];
    const SOURCE_ENABLE_SELECT_IDS = {
      bilibili: "bilibiliEnabled",
      xiaohongshu: "xhsEnabled",
      douyin: "douyinEnabled",
      youtube: "youtubeEnabled",
      twitter: "twitterEnabled",
      zhihu: "zhihuEnabled"
    };
    const SOURCE_ACCESS_STATE = {
      ok: { tone: "ready", label: "接入可用" },
      ready: { tone: "ready", label: "接入可用" },
      no_auth: { tone: "public", label: "无需登录" },
      unverified: { tone: "pending", label: "状态待验证" },
      missing: { tone: "warning", label: "需要登录" },
      missing_cookie: { tone: "warning", label: "缺少 Cookie" },
      rate_limited: { tone: "warning", label: "频率受限" },
      partial: { tone: "warning", label: "部分可用" },
      stale: { tone: "warning", label: "需要刷新" },
      expired_cookie: { tone: "danger", label: "Cookie 失效" },
      blocked: { tone: "danger", label: "接入受阻" }
    };

    function setSourceBadge(badge, text, tone) {
      if (!badge) return;
      badge.textContent = text;
      badge.dataset.tone = tone;
    }

    function getPendingSourceEnabled(key, item) {
      const select = document.getElementById(SOURCE_ENABLE_SELECT_IDS[key]);
      const currentEnabled = select ? select.value === "on" : Boolean(item?.enabled);
      const savedEnabled = typeof item?.enabled === "boolean" ? item.enabled : currentEnabled;
      return {
        currentEnabled,
        savedEnabled,
        pending: currentEnabled !== savedEnabled
      };
    }

    function renderSourcesStatusRows(data) {
      const list = $("#sourceStatusList");
      if (!list) return;
      SOURCE_STATUS_KEYS.forEach((key) => {
        const row = list.querySelector(`[data-source-status="${key}"]`);
        if (!row) return;
        const sourceBadge = row.querySelector(".source-source-badge");
        const accessBadge = row.querySelector(".source-access-badge");
        const detail = row.querySelector(".src-detail");
        const item = data?.[key];
        if (!item) {
          setSourceBadge(sourceBadge, "来源：状态未知", "muted");
          setSourceBadge(accessBadge, "接入：后端未连接", "muted");
          if (detail) detail.textContent = "暂时无法读取来源接入状态，请确认后端服务可用。";
          row.classList.remove("source-row-unsaved");
          row.dataset.sourceEnabled = "unknown";
          row.dataset.accessTone = "muted";
          return;
        }
        const enableState = getPendingSourceEnabled(key, item);
        const accessState = SOURCE_ACCESS_STATE[item.state] || { tone: "muted", label: "状态未知" };
        const sourceLabel = enableState.pending
          ? `来源：${enableState.currentEnabled ? "将启用" : "将停用"}，保存后生效`
          : `来源：${enableState.savedEnabled ? "启用" : "停用"}`;
        setSourceBadge(sourceBadge, sourceLabel, enableState.pending ? "pending" : enableState.savedEnabled ? "enabled" : "disabled");
        setSourceBadge(accessBadge, `接入：${accessState.label}`, accessState.tone);
        const detailPrefix = enableState.pending ? "开关已改动，保存配置后才会进入/退出调度。 " : "";
        if (detail) detail.textContent = detailPrefix + (item.detail || "暂无更多状态细节。");
        row.classList.toggle("source-row-unsaved", enableState.pending);
        row.dataset.sourceEnabled = enableState.currentEnabled ? "true" : "false";
        row.dataset.accessTone = accessState.tone;
      });
    }

    async function renderSourcesStatus() {
      let data = null;
      try { data = await OBC.requestJson("/sources/status"); } catch { data = null; }
      OBC.state.sourceStatus = data;
      renderSourcesStatusRows(data);
    }

    function renderSourceCredentialRows(data) {
      const list = $("#sourceCredentialList");
      if (!list) return;
      CURRENT_CREDENTIAL_KEYS.forEach((key) => {
        const row = list.querySelector(`[data-source-credential="${key}"]`);
        if (!row) return;
        const summary = row.querySelector(".source-credential-summary");
        const value = row.querySelector(".source-credential-value");
        const item = data?.[key];
        if (!item) {
          row.dataset.available = "false";
          if (summary) summary.textContent = "状态暂不可用";
          if (value) value.value = "暂时无法读取当前 Cookie / 登录凭据。";
          return;
        }
        row.dataset.available = item.available ? "true" : "false";
        if (summary) {
          summary.textContent = item.available
            ? `${item.label || "Cookie"} 已保存，展开查看`
            : item.detail || "当前没有可展示 Cookie";
        }
        if (value) {
          value.value = item.value || item.detail || "当前没有可展示 Cookie / 登录凭据。";
        }
      });
    }

    async function renderSourceCredentials() {
      let data = null;
      try { data = await OBC.requestJson(OBC.ENDPOINTS.sourceCredentials); } catch { data = null; }
      OBC.state.sourceCredentials = data;
      renderSourceCredentialRows(data);
    }

    // Login happens outside this page (user signs into a platform in another
    // tab), so a one-shot render on settings open goes stale — re-poll while
    // the status list is actually visible.
    setInterval(() => {
      if (document.hidden) return;
      const list = $("#sourceStatusList");
      if (!list || list.offsetParent === null) return;
      void renderSourcesStatus();
    }, 30000);

    // LAN password-gate control. The web UI is served from 127.0.0.1, so it is a
    // trusted-local client (same-origin loopback) and may manage /api/auth/admin,
    // exactly like the extension's popup-auth-control.
    let lanAuthControl = null;
    let bootAutostartControl = null;

    function initLanAuthControl() {
      const checkbox = $("#authEnabled");
      const password = $("#authPassword");
      const passwordField = $("#authPasswordField");
      const saveRow = $("#authSaveRow");
      const saveBtn = $("#authSave");
      const hint = $("#authHint");
      if (!checkbox) return { reload: async () => {} };
      let current = null;
      const setHint = (msg) => { if (hint) hint.textContent = msg; };
      function syncEditing() {
        const can = Boolean(current && current.can_manage);
        const enabling = checkbox.checked;
        if (passwordField) passwordField.hidden = !(can && enabling);
        if (saveRow) saveRow.hidden = !(can && enabling);
      }
      function applyServerState() {
        const can = Boolean(current && current.can_manage);
        checkbox.checked = Boolean(current && current.enabled);
        checkbox.disabled = !can;
        syncEditing();
        if (!current) setHint("无法读取后端鉴权状态。");
        else if (!can) setHint(current.env_managed ? "由环境变量管理，请改环境变量并重启后端。" : "仅本机 / 浏览器插件可修改此设置。");
        else if (current.enabled) setHint("已开启：局域网 / 远程设备访问需要登录密码（本机与插件免登录）。");
        else setHint("已关闭：局域网访问无需密码。");
      }
      async function load() {
        current = await OBC.requestJson("/auth/status");
        applyServerState();
        return current;
      }
      async function apply(enabled) {
        const pwd = password ? String(password.value || "") : "";
        if (enabled && !pwd.trim()) { setHint("请输入要设置的访问密码。"); if (password?.focus) password.focus(); return; }
        setHint("保存中…");
        try {
          const payload = enabled ? { enabled: true, password: pwd } : { enabled: false };
          const result = await OBC.requestJsonStrict("/auth/admin", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
          if (result && result.ok === false) { setHint("保存失败，请重试。"); await load(); return; }
          if (password) password.value = "";
          await load();
        } catch (err) {
          const status = err?.status;
          if (status === 403) setHint("仅本机 / 插件可修改此设置。");
          else if (status === 409) setHint("由环境变量管理，无法在此修改。");
          else if (status === 400) setHint("开启密码门禁需要先设置密码。");
          else setHint("无法连接后端或保存失败，请重试。");
          await load();
        }
      }
      checkbox.addEventListener("change", () => {
        if (!checkbox.checked) void apply(false);
        else { syncEditing(); if (password?.focus) password.focus(); }
      });
      saveBtn?.addEventListener("click", () => void apply(true));
      void load();
      return { reload: load };
    }

    // Boot autostart control — mirrors the extension's popup-autostart-control.
    function initBootAutostartControl() {
      const checkbox = $("#autostartEnabled");
      const hint = $("#autostartHint");
      if (!checkbox) return { reload: async () => {} };
      let current = null;
      let busy = false;
      const setHint = (msg) => { if (hint) hint.textContent = msg; };
      function disabledHint(status) {
        const reason = status?.reason || "";
        if (reason === "env_managed") return "检测到环境变量配置，登录会话可能拿不到这些值；请先写入 config.toml。";
        if (reason === "shadowed") return "config.local.toml 正在覆盖开关，无法在此修改。";
        if (reason === "unsupported_docker_runtime") return "当前在 Docker / 容器环境中，不能注册桌面登录自启动。";
        if (reason === "unsupported_platform") return "当前平台暂不支持开机自启动。";
        if (reason === "local_only") return "仅本机 / 浏览器插件可修改此设置。";
        return "当前环境不能在这里修改开机自启动。";
      }
      function enabledHint(status) {
        const ollama = status?.manage_ollama ? "；本机 Ollama 配置会在需要时顺带拉起" : "";
        if (status?.registered === false) return `配置已开启，但系统注册缺失；下次后端启动会尝试修复${ollama}。`;
        return `已开启：下次登录系统会拉起后端，不启停当前进程${ollama}。`;
      }
      function activeHint(status) {
        if (!status) return "无法读取开机自启动状态。";
        if (!status.can_manage) return disabledHint(status);
        if (status.enabled) return enabledHint(status);
        return "已关闭：不会注册登录自启动；当前后端进程不受影响。";
      }
      function applyServerState() {
        const can = Boolean(current && current.can_manage);
        checkbox.checked = Boolean(current && current.enabled);
        checkbox.disabled = busy || !can;
        setHint(activeHint(current));
      }
      async function load() {
        current = await OBC.requestJson("/autostart-status");
        applyServerState();
        return current;
      }
      async function apply(enabled) {
        busy = true;
        checkbox.disabled = true;
        setHint(enabled ? "正在开启开机自启动…" : "正在关闭开机自启动…");
        try {
          const result = await OBC.requestJsonStrict("/autostart/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: Boolean(enabled) }) });
          current = result || current;
          busy = false;
          applyServerState();
          await load();
        } catch (err) {
          busy = false;
          const status = err?.status;
          current = err?.details || current;
          if (status === 403) setHint("仅本机 / 浏览器插件可修改此设置。");
          else if (status === 409) setHint(disabledHint(current));
          else setHint("无法连接后端或保存失败，请重试。");
          await load();
        }
      }
      checkbox.addEventListener("change", () => void apply(Boolean(checkbox.checked)));
      void load();
      return { reload: load };
    }

    function applyConfig(config) {
      if (!config || typeof config !== "object") return;
      OBC.state.config = config;
      const scheduler = config.scheduler || {};
      setSelect("schedulerEnabled", scheduler.enabled === false ? "off" : "on");
      setSelect("pauseDisconnect", scheduler.pause_on_extension_disconnect === false ? "keep" : "pause");
      OBC.setInput("extensionDisconnectGrace", scheduler.extension_disconnect_grace_seconds);
      OBC.setInput("poolTarget", scheduler.pool_target_count);
      OBC.setInput("accountSyncInterval", scheduler.account_sync_interval_hours);
      OBC.setInput("refreshCheckInterval", scheduler.refresh_check_interval_seconds);
      OBC.setInput("signalEventThreshold", scheduler.signal_event_threshold);
      OBC.setInput("feedbackBatchThreshold", scheduler.feedback_batch_threshold);
      OBC.setInput("trendingRefreshHours", scheduler.trending_refresh_hours);
      OBC.setInput("exploreRefreshHours", scheduler.explore_refresh_hours);
      OBC.setInput("discoveryLimit", scheduler.discovery_limit);
      OBC.setInput("proactivePushInterval", scheduler.proactive_push_interval_seconds);
      OBC.setInput("speculatorIdleInterval", scheduler.speculator_idle_interval_minutes);
      setSelect("autoUpdate", scheduler.auto_update_enabled === true ? "on" : "off");
      OBC.setInput("autoUpdateInterval", scheduler.auto_update_check_interval_hours);
      OBC.setInput("shareBilibili", scheduler.pool_source_shares?.bilibili);
      OBC.setInput("shareXhs", scheduler.pool_source_shares?.xiaohongshu);
      OBC.setInput("shareDouyin", scheduler.pool_source_shares?.douyin);
      OBC.setInput("shareYoutube", scheduler.pool_source_shares?.youtube);
      OBC.setInput("shareTwitter", scheduler.pool_source_shares?.twitter);
      OBC.setInput("shareZhihu", scheduler.pool_source_shares?.zhihu);
      OBC.setInput("speculationInterval", scheduler.speculation_interval_minutes);
      OBC.setInput("speculationTtl", scheduler.speculation_ttl_days);
      OBC.setInput("speculationCooldown", scheduler.speculation_cooldown_days);
      OBC.setInput("speculationThreshold", scheduler.speculation_confirmation_threshold);
      OBC.setInput("speculationMaxActive", scheduler.speculation_max_active);
      OBC.setInput("speculationMaxPrimary", scheduler.speculation_max_primary_interests);
      OBC.setInput("speculationMaxSecondary", scheduler.speculation_max_secondary_interests);

      const discovery = config.discovery || {};
      setSelect("multimodalEvaluationEnabled", discovery.multimodal_evaluation_enabled ? "on" : "off");
      OBC.setInput("multimodalBatchSize", discovery.multimodal_batch_size);
      OBC.setInput("multimodalImageMaxPx", discovery.multimodal_image_max_px);
      OBC.setInput("multimodalImageQuality", discovery.multimodal_image_quality);
      OBC.setInput("multimodalImageTimeout", discovery.multimodal_image_timeout_seconds);
      const multimodalStatus = $("#multimodalEvaluationStatus");
      if (multimodalStatus) {
        multimodalStatus.textContent = discovery.multimodal_evaluation_enabled ? "开启" : "关闭";
      }

      setSelect("language", config.language || "zh");
      OBC.setInput("dataDir", config.data_dir);
      OBC.setInput("storageDbPath", config.storage?.db_path);

      const llm = config.llm || {};
      const provider = llm.default_provider || llm.provider;
      setSelect("llmProvider", provider);
      const fallbackProvider = llm.fallback_provider || "";
      setSelect("llmFallbackProvider", fallbackProvider);
      OBC.setInput("llmConcurrency", llm.concurrency ?? 3);
      OBC.setInput("llmTimeout", llm.timeout);
      setSelect("llmAuthMode", llm.openai?.auth_mode || "api_key");
      if (provider) {
        OBC.setInput("llmModel", llm[provider]?.model);
        OBC.setInput("llmApiKey", llm[provider]?.api_key);
        OBC.setInput("llmBaseUrl", llm[provider]?.base_url);
      }
      if (fallbackProvider) {
        setSelect("llmFallbackAuthMode", llm[fallbackProvider]?.auth_mode || "api_key");
        OBC.setInput("llmFallbackModel", llm[fallbackProvider]?.model);
        OBC.setInput("llmFallbackApiKey", llm[fallbackProvider]?.api_key);
        OBC.setInput("llmFallbackBaseUrl", llm[fallbackProvider]?.base_url);
      } else {
        setSelect("llmFallbackAuthMode", "api_key");
        OBC.setInput("llmFallbackModel", "");
        OBC.setInput("llmFallbackApiKey", "");
        OBC.setInput("llmFallbackBaseUrl", "");
      }
      OBC.setInput("openrouterReferer", llm.openrouter?.http_referer);
      OBC.setInput("openrouterTitle", llm.openrouter?.x_title);
      setSelect("deepseekReasoning", llm.deepseek?.reasoning_effort || "");
      setSelect("embeddingProvider", llm.embedding?.provider || "");
      const embeddingFallbackProvider = llm.embedding?.fallback_provider || "";
      setSelect("embeddingFallbackProvider", embeddingFallbackProvider);
      OBC.setInput("embeddingModel", llm.embedding?.model);
      OBC.setInput("embeddingApiKey", llm.embedding?.api_key);
      OBC.setInput("embeddingBaseUrl", llm.embedding?.base_url);
      OBC.setInput("embeddingOutputDimensionality", llm.embedding?.output_dimensionality ?? 1024);
      OBC.setInput("embeddingSimilarity", llm.embedding?.similarity_threshold);
      if (embeddingFallbackProvider) {
        OBC.setInput("embeddingFallbackModel", llm[embeddingFallbackProvider]?.model);
        OBC.setInput("embeddingFallbackApiKey", llm[embeddingFallbackProvider]?.api_key);
        OBC.setInput("embeddingFallbackBaseUrl", llm[embeddingFallbackProvider]?.base_url);
      } else {
        OBC.setInput("embeddingFallbackModel", "");
        OBC.setInput("embeddingFallbackApiKey", "");
        OBC.setInput("embeddingFallbackBaseUrl", "");
      }
      setSelect("moduleSoulProvider", llm.soul?.provider || "");
      OBC.setInput("moduleSoulModel", llm.soul?.model);
      setSelect("moduleDiscoveryProvider", llm.discovery?.provider || "");
      OBC.setInput("moduleDiscoveryModel", llm.discovery?.model);
      setSelect("moduleRecommendationProvider", llm.recommendation?.provider || "");
      OBC.setInput("moduleRecommendationModel", llm.recommendation?.model);
      setSelect("moduleEvaluationProvider", llm.evaluation?.provider || "");
      OBC.setInput("moduleEvaluationModel", llm.evaluation?.model);

      setSelect("biliAuth", config.bilibili?.auth_method || "cookie");
      setCookieOverrideInput("biliCookie", config.bilibili?.cookie, " B 站");
      OBC.setInput("biliBrowserExecutable", config.bilibili?.browser_executable);
      setSelect("biliBrowserHeaded", config.bilibili?.browser_headed === true ? "on" : "off");
      setSelect("bilibiliEnabled", config.sources?.bilibili?.enabled === false ? "off" : "on");
      OBC.setInput("sourcesBrowserCdp", config.sources?.browser?.cdp_url);
      setSelect("sourcesBrowserHeaded", config.sources?.browser?.headed === true ? "on" : "off");
      setSelect("xhsEnabled", config.sources?.xiaohongshu?.enabled === true ? "on" : "off");
      OBC.setInput("xhsDailySearchBudget", config.sources?.xiaohongshu?.daily_search_budget);
      OBC.setInput("xhsDailyCreatorBudget", config.sources?.xiaohongshu?.daily_creator_budget);
      OBC.setInput("xhsTaskInterval", config.sources?.xiaohongshu?.task_interval_seconds);
      setSelect("douyinEnabled", config.sources?.douyin?.enabled === true ? "on" : "off");
      setCookieOverrideInput("douyinCookie", config.sources?.douyin?.cookie, "抖音");
      OBC.setInput("douyinCookieEnv", config.sources?.douyin?.cookie_env);
      OBC.setInput("douyinDailySearchBudget", config.sources?.douyin?.daily_search_budget);
      OBC.setInput("douyinDailyHotBudget", config.sources?.douyin?.daily_hot_budget);
      OBC.setInput("douyinDailyFeedBudget", config.sources?.douyin?.daily_feed_budget);
      OBC.setInput("douyinRequestInterval", config.sources?.douyin?.request_interval_seconds);
      setSelect("youtubeEnabled", config.sources?.youtube?.enabled === true ? "on" : "off");
      OBC.setInput("youtubeDailySearchBudget", config.sources?.youtube?.daily_search_budget);
      OBC.setInput("youtubeDailyTrendingBudget", config.sources?.youtube?.daily_trending_budget);
      OBC.setInput("youtubeDailyChannelBudget", config.sources?.youtube?.daily_channel_budget);
      OBC.setInput("youtubeRequestInterval", config.sources?.youtube?.request_interval_seconds);
      OBC.setInput("youtubeMinInterval", config.sources?.youtube?.min_interval_minutes);
      setSelect("twitterEnabled", config.sources?.twitter?.enabled === true ? "on" : "off");
      setCookieOverrideInput("twitterCookie", config.sources?.twitter?.cookie, " X");
      OBC.setInput("twitterCookieEnv", config.sources?.twitter?.cookie_env);
      OBC.setInput("twitterDailySearchBudget", config.sources?.twitter?.daily_search_budget);
      OBC.setInput("twitterDailyFeedBudget", config.sources?.twitter?.daily_feed_budget);
      OBC.setInput("twitterDailyCreatorBudget", config.sources?.twitter?.daily_creator_budget);
      OBC.setInput("twitterRequestInterval", config.sources?.twitter?.request_interval_seconds);
      OBC.setInput("twitterMinInterval", config.sources?.twitter?.min_interval_minutes);
      setSelect("zhihuEnabled", config.sources?.zhihu?.enabled === true ? "on" : "off");
      setZhihuSourceModes(config.sources?.zhihu?.source_modes);
      OBC.setInput("zhihuDailySearchBudget", config.sources?.zhihu?.daily_search_budget);
      OBC.setInput("zhihuDailyHotBudget", config.sources?.zhihu?.daily_hot_budget);
      OBC.setInput("zhihuDailyFeedBudget", config.sources?.zhihu?.daily_feed_budget);
      OBC.setInput("zhihuDailyCreatorBudget", config.sources?.zhihu?.daily_creator_budget);
      OBC.setInput("zhihuDailyRelatedBudget", config.sources?.zhihu?.daily_related_budget);
      OBC.setInput("zhihuRequestInterval", config.sources?.zhihu?.request_interval_seconds);
      OBC.setInput("zhihuMinInterval", config.sources?.zhihu?.min_interval_minutes);
      void renderSourcesStatus();
      void renderSourceCredentials();

      setSelect("logLevel", config.logging?.level || "INFO");
      setSelect("logFileLevel", config.logging?.file_level || "DEBUG");
      OBC.setInput("logPath", resolveLogPath(config.logging));
      OBC.setInput("logMaxFileSize", config.logging?.max_file_size_mb);
      OBC.setInput("logBackupCount", config.logging?.backup_count);
      OBC.setInput("logAggregateBudget", config.logging?.aggregate_budget_mb);
      OBC.setInput("logUnmanagedTruncate", config.logging?.unmanaged_truncate_mb);
      OBC.setInput("logUnmanagedMaxAge", config.logging?.unmanaged_max_age_days);

      if ($("#configStatus")) $("#configStatus").value = "配置已从后端加载。";
      if (OBC.state.runtimeStatus) applyRuntimeStatus(OBC.state.runtimeStatus);
      OBC.restoreFrontendSettings();
    }

    function normalizeDelight(item) {
      if (!item) return null;
      // 后端 pending-batch 对喜欢过的候选下发 OBC.state="liked"，重灌后恢复
      // 「已喜欢」文案，让用户看出这条已经表过态。
      const serverState = String(item.state ?? "");
      const fallbackMessage = serverState === "liked" ? "好，这类多来点。" : "";
      return {
        type: "delight",
        bvid: String(item.bvid ?? item.content_id ?? ""),
        title: decodeHtmlEntities(item.title ?? "发现了一条你可能会意外喜欢的内容"),
        reason: decodeHtmlEntities(item.delight_reason ?? item.reason ?? item.delight_hook ?? item.message ?? "这条来自后端高惊喜分候选。"),
        cover_url: normalizeImageUrl(item.cover_url ?? item.cover ?? item.pic ?? item.thumbnail_url ?? item.thumbnail ?? item.image_url),
        content_url: String(item.content_url ?? ""),
        source_platform: String(item.source_platform ?? item.platform ?? "bilibili"),
        chat_turn_id: String(item.chat_turn_id ?? ""),
        chat_reply: String(item.chat_reply ?? item.reply ?? ""),
        chat_draft: String(item.chat_draft ?? ""),
        state: serverState,
        response_message: String(item.response_message ?? "") || fallbackMessage,
        turns: delightTurnList(item.turns)
      };
    }

    function renderDelightCover(delight) {
      const thumb = $("#delightBanner .thumb");
      if (!thumb) return;
      const url = imageProxyUrl(delight?.cover_url);
      thumb.replaceChildren();
      thumb.classList.toggle("has-image", Boolean(url));
      if (!url) return;
      const image = document.createElement("img");
      if (OBC.isCrossOriginBase()) image.crossOrigin = "anonymous";
      image.alt = "";
      image.loading = "eager";
      image.fetchPriority = "high";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.src = url;
      image.addEventListener("error", () => {
        image.remove();
        thumb.classList.remove("has-image");
      });
      thumb.append(image);
    }

    // 惊喜页：一页 6 张卡片网格 + 换一换。展示层从 OBC.state.delights 取一页，
    // 换一换只在展示层打乱取页，不破坏实时流合并的队列本身。
    const DELIGHT_PAGE_SIZE = 6;

    function delightCardHtml(item) {
      const title = OBC.escapeHtml(item.title || "无标题");
      const reason = OBC.escapeHtml(item.reason || item.delight_reason || "");
      const platform = String(item.source_platform || "bilibili");
      const url = OBC.escapeHtml(item.content_url || "");
      const liked = item.state === "liked" ? " is-liked" : "";
      return `
        <div class="video-card-cover is-empty">
          <div class="video-card-cover-ph">${window.platformLabelHtml(platform)}</div>
        </div>
        <div class="video-card-body">
          <p class="video-card-title">${title}</p>
          <div class="video-card-meta">
            <span class="video-card-author">${reason.slice(0, 48)}</span>
            <span class="video-card-platform">${window.platformLabelHtml(platform)}</span>
          </div>
          <div class="video-card-footer${liked}">
            <button class="feedback-icon-btn" data-action="like" type="button" aria-label="喜欢" title="喜欢">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M7 10v10"/><path d="M15 5.2 14 10h5.4a1.8 1.8 0 0 1 1.7 2.2l-1.5 6A2.4 2.4 0 0 1 17.3 20H7"/><path d="M7 10l4.5-5.3A2 2 0 0 1 15 6v4"/></svg>
            </button>
            <button class="feedback-icon-btn" data-action="dismiss" type="button" aria-label="忽略" title="忽略">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18M9.84 9.91A3 3 0 0 0 12 15c.82 0 1.57-.33 2.11-.87M6.5 6.65A10.45 10.45 0 0 0 2.46 12C3.73 16.06 7.52 19 12 19c1.99 0 3.84-.58 5.4-1.58M11 5.05c.33-.03.66-.05 1-.05 4.48 0 8.27 2.94 9.54 7a10.5 10.5 0 0 1-1.19 2.5"/></svg>
            </button>
            <button class="feedback-icon-btn watch-later-btn" data-action="watch-later" type="button" aria-label="稍后再看" title="稍后再看">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>
            </button>
            <button class="feedback-icon-btn favorite-btn" data-action="favorite" type="button" aria-label="收藏" title="收藏">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>
            </button>
          </div>
        </div>
        <a class="video-card-link" href="${url}" target="_blank" rel="noopener" title="在浏览器中打开">↗</a>
      `;
    }

    function renderDelightGrid() {
      const grid = $("#delightGrid");
      if (!grid) return;
      const all = OBC.state.delights || [];
      const count = $("#delightCount");
      if (count) count.textContent = `${all.length} 条候选`;
      const countTopbar = $("#delightCountTopbar");
      if (countTopbar) countTopbar.textContent = all.length;
      if (!all.length) {
        grid.innerHTML = `
          <div class="obs-section">
            <div class="empty-OBC.state">
              <p>暂无惊喜候选，后端产生新的高惊喜候选后会出现在这里。</p>
            </div>
          </div>`;
        OBC.scheduleActivityRailHeightSync();
        return;
      }
      const page = Array.isArray(OBC.state.delightPage) && OBC.state.delightPage.length
        ? OBC.state.delightPage
        : all.slice(0, DELIGHT_PAGE_SIZE);
      grid.replaceChildren(
        ...page.map((item) => {
          const card = document.createElement("div");
          card.className = "video-card is-minimal";
          card.innerHTML = delightCardHtml(item);
          card.addEventListener("click", (e) => {
            if (e.target.closest("[data-action]")) return;
            respondDelight(item, "view");
          });
          card.querySelectorAll("[data-action]").forEach((btn) => {
            btn.addEventListener("click", (e) => {
              e.stopPropagation();
              respondDelight(item, btn.dataset.action, btn);
            });
          });
          return card;
        })
      );
      OBC.scheduleActivityRailHeightSync();
    }

    function shuffleDelights() {
      const all = (OBC.state.delights || []).slice();
      if (!all.length) return;
      for (let i = all.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [all[i], all[j]] = [all[j], all[i]];
      }
      OBC.state.delightPage = all.slice(0, DELIGHT_PAGE_SIZE);
      renderDelightGrid();
    }

    function applyDelights(payload) {
      const hasQueuePayload = Array.isArray(payload?.items) || Boolean(payload?.item);
      if (!hasQueuePayload) return;
      const items = Array.isArray(payload?.items) ? payload.items : payload.item ? [payload.item] : [];
      const normalized = items.map(normalizeDelight).filter(Boolean);
      const existingByBvid = new Map(OBC.state.delights.map((item) => [String(item.bvid || ""), item]));
      OBC.state.delights = [];
      for (const item of normalized) {
        const key = String(item.bvid || "");
        if (!key) continue;
        const existingIndex = OBC.state.delights.findIndex((current) => String(current.bvid || "") === key);
        const merged = mergeDelightItem(existingByBvid.get(key) || OBC.state.delights[existingIndex], item);
        if (existingIndex >= 0) OBC.state.delights[existingIndex] = merged;
        else OBC.state.delights.push(merged);
      }
      OBC.state.delightPage = null;
      renderDelightGrid();
    }

    function mergeMessages(items) {
      for (const raw of items) {
        const item = normalizeMessageItem(raw);
        if (!item) continue;
        const key = messageKey(item);
        if (!OBC.state.messages.some((msg) => messageKey(msg) === key)) OBC.state.messages.push(item);
      }
      renderMessages();
      applyRuntimeStatus({ unread_count: getRenderableMessages().length });
    }

    async function fetchDelightQueue() {
      const payload = await OBC.requestJson(OBC.ENDPOINTS.delightBatch);
      applyDelights(payload);
    }

    function handleRuntimeEvent(event) {
      if (!event?.type) return;
      applyRuntimeStatus({ ...event, live_summary: event.message || event.live_summary || event.type });
      // refresh.pool_updated / recommendation.reshuffled are pool-status signals, not
      // list-replacement signals: hydrating here would wipe locally appended cards
      // (/api/recommendations only returns the latest top window). Header/pool counts
      // still update via applyRuntimeStatus above; user-initiated 换一批 / 加载更多 replace
      // the list explicitly. Matches recommend.js + popup.js (fix 79042ce).
      if (["config_reloaded"].includes(event.type)) OBC.scheduleBackendHydration();
      if (["init_progress", "init_failed", "init_completed"].includes(event.type)) {
        void refreshInitStatus({ schedule: event.type === "init_progress" });
      }
      if (event.type === "refresh.pool_updated" && Boolean(OBC.state.initStatus?.initialized)) {
        void refreshInitStatus({ schedule: false });
      }
      if (event.type === "activity.added") OBC.scheduleActivityPageRefresh();
      if (
        event.type === "profile_updated" ||
        event.type === "interest.confirmed" ||
        event.type === "interest.rejected" ||
        event.type === "interest.chat" ||
        event.type === "avoidance.confirmed" ||
        event.type === "avoidance.rejected" ||
        event.type === "avoidance.chat"
      ) void refreshProfile();
      if (event.type === "delight.candidate" && event.bvid) {
        const delight = normalizeDelight(event);
        if (delight) {
          const key = String(delight.bvid || "");
          const existingIndex = OBC.state.delights.findIndex((item) => String(item.bvid || "") === key);
          if (existingIndex >= 0) {
            OBC.state.delights[existingIndex] = mergeDelightItem(OBC.state.delights[existingIndex], delight);
          } else {
            OBC.state.delights.push(delight);
          }
          OBC.state.delightPage = null;
          renderDelightGrid();
        }
      }
      if (
        event.type === "backend_update_available" ||
        event.type === "backend_restart_pending" ||
        event.type === "backend_update_failed"
      ) void refreshUpdateStatus();
      if (event.type === "backend_update_available") {
        const newVersion = event.latest_version ? `v${event.latest_version}` : "新版本";
        // desktop-v* tags = installer releases for frozen bundles; guide the
        // user to download instead of implying an in-place update will happen.
        OBC.showToast(String(event.latest_tag || "").startsWith("desktop-v")
          ? `发现新版安装包 ${newVersion}，请前往 GitHub Releases 下载升级`
          : `发现后端新版本 ${newVersion}`);
      }
      if (event.type === "delight.refreshed") OBC.scheduleDelightQueueRefresh();
      if (event.type === "notification.pending" && event.bvid) mergeMessages([{ ...event, type: "notification" }]);
      if (event.type === "interest.probe" && event.domain) mergeMessages([{ type: "interest.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个兴趣方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
      if (event.type === "avoidance.probe" && event.domain) mergeMessages([{ type: "avoidance.probe", domain: event.domain, reason: event.reason || event.message || "后端希望确认这个避雷方向。", specifics: event.specifics || event.examples || [], probe_mode: event.probe_mode || "", challenge: Boolean(event.challenge) }]);
    }

    function connectRuntimeStream() {
      if (OBC.state.runtimeSocket) OBC.state.runtimeSocket.close();
      try {
        const socket = new WebSocket(OBC.getRuntimeStreamUrl());
        OBC.state.runtimeSocket = socket;
        socket.addEventListener("open", () => { $("#statusLabel").textContent = "实时连接中"; });
        socket.addEventListener("message", (event) => {
          try { handleRuntimeEvent(JSON.parse(event.data)); } catch {}
        });
        socket.addEventListener("close", () => {
          if (OBC.state.runtimeSocket === socket) window.setTimeout(connectRuntimeStream, 3000);
        });
        socket.addEventListener("error", () => { $("#statusLabel").textContent = "实时流断开"; });
      } catch {
        $("#statusLabel").textContent = "实时流不可用";
      }
    }

    async function refreshProfile() {
      const payload = await OBC.requestJson(OBC.ENDPOINTS.profile);
      const profile = payload?.profile || payload;
      if (profile && profile.initialized !== false) {
        OBC.state.profile = profile;
        hydrateInboxFromSpeculations(profile.speculative_interests);
        hydrateInboxFromSpeculations(profile.speculative_avoidances, "avoidance.probe");
        if (typeof OBC.renderRail === "function") OBC.renderRail();
        window.renderProfileDetails();
        renderMessages();
      }
    }

    async function hydrateFromBackend() {
      // 先并行获取推荐和健康检查，尽快渲染
      // 每次打开/刷新首页都重新换一批（POST /recommendations/reshuffle 会从池子
      // serve 新一批并写入历史；GET 是幂等的，刷新会一直看到同一批，不符合预期）。
      const [health, recs] = await Promise.all([
        OBC.requestJson(OBC.ENDPOINTS.health).catch(() => null),
        OBC.requestJson(OBC.ENDPOINTS.reshuffle, { method: "POST" }).catch(() => null)
      ]);
      if (health) $("#statusLabel").textContent = "已连接本地后端";
      let recommendationItems = Array.isArray(recs) ? recs : window.asArray(recs?.items);
      // 池子为空（罕见，如首次部署/刚耗尽）时，回退到 GET 的 bootstrap 逻辑保证首屏有内容
      if (!recommendationItems.length) {
        const fallback = await OBC.requestJson(OBC.ENDPOINTS.recommendations).catch(() => null);
        recommendationItems = Array.isArray(fallback) ? fallback : window.asArray(fallback?.items);
      }
      OBC.state.videos = OBC.normalizeRecommendationList(recommendationItems);
      // 推荐先渲染，其他数据后台加载
      renderAll();

      // 后台加载其余数据，不阻塞页面展示
      const [runtime, activity, profile, delights, notification, chatTurns, delightChatTurns, config, initStatus] = await Promise.all([
        OBC.requestJson(OBC.ENDPOINTS.runtimeStatus).catch(() => null),
        OBC.requestJson(`${OBC.ENDPOINTS.activityFeed}?limit=5`).catch(() => null),
        OBC.requestJson(OBC.ENDPOINTS.profile).catch(() => null),
        OBC.requestJson(OBC.ENDPOINTS.delightBatch).catch(() => null),
        OBC.requestJson(OBC.ENDPOINTS.notificationPending).catch(() => null),
        OBC.requestJson(`${OBC.ENDPOINTS.chatTurns}?session=webui&scope=chat&limit=20`).catch(() => null),
        OBC.requestJson(`${OBC.ENDPOINTS.chatTurns}?session=webui&scope=delight&limit=80`).catch(() => null),
        OBC.requestJson(OBC.ENDPOINTS.config).catch(() => null),
        OBC.requestJson(OBC.ENDPOINTS.initStatus).catch(() => null)
      ]);
      if (initStatus) OBC.state.initStatus = initStatus;
      if (activity) {
        OBC.state.activity = activity;
        OBC.state.activityItems = window.asArray(activity.items);
        OBC.state.activityCursor = activity.next_cursor || activity.next || "";
        OBC.state.activityHasMore = Boolean(activity.has_more && OBC.state.activityCursor);
      }
      const profilePayload = profile?.profile || profile;
      if (profilePayload && profilePayload.initialized !== false) {
        OBC.state.profile = profilePayload;
        hydrateInboxFromSpeculations(profilePayload.speculative_interests);
        hydrateInboxFromSpeculations(profilePayload.speculative_avoidances, "avoidance.probe");
      }
      const chatItems = Array.isArray(chatTurns) ? chatTurns : window.asArray(chatTurns?.items);
      if (chatItems.length) {
        OBC.state.chat = chatItems.flatMap((turn) => [
          { role: "user", text: turn.message || turn.user_message || "" },
          { role: "agent", text: turn.reply || turn.assistant_message || turn.status || "等待后端回复中。" }
        ]).filter((item) => item.text);
      }
      const effectiveRuntime = await OBC.requestJson(OBC.ENDPOINTS.runtimeStatus).catch(() => runtime?.status || runtime);
      applyRuntimeStatus(effectiveRuntime?.status || effectiveRuntime);
      applyDelights(delights);
      const delightChatItems = Array.isArray(delightChatTurns) ? delightChatTurns : window.asArray(delightChatTurns?.items);
      for (const turn of delightChatItems.filter(Boolean)) applyTurnToDelight({ ...turn, scope: turn.scope || "delight" });
      if (notification?.item) mergeMessages([{ ...notification.item, type: "notification" }]);
      applyConfig(config?.config || config);
      // 后台数据加载完成后，重新渲染非推荐部分
      renderAll();
    }

    function renderAll() {
      const steps = [OBC.renderViewTabs, OBC.renderReshuffleToggle, OBC.renderFilters, OBC.renderVideos, syncSourceMetric, (typeof window.renderRail === "function" ? window.renderRail : () => {}), (typeof window.renderProfileDetails === "function" ? window.renderProfileDetails : () => {}), renderMessages, renderChat, (typeof renderPoolStatus === "function" ? renderPoolStatus : () => {})];
      for (const step of steps) {
        try { step(); } catch (error) { OBC.showFatal(error, step.name || "渲染"); }
      }
      OBC.scheduleActivityRailHeightSync();
    }

    function buildConfigUpdate() {
      const provider = $("#llmProvider").value;
      const fallbackProvider = getInput("llmFallbackProvider");
      const llmProviderConfig = { model: getInput("llmModel") };
      if (provider === "openai") llmProviderConfig.auth_mode = getInput("llmAuthMode") || "api_key";
      if (getInput("llmApiKey")) llmProviderConfig.api_key = getInput("llmApiKey");
      if (getInput("llmBaseUrl")) llmProviderConfig.base_url = getInput("llmBaseUrl");
      const llmFallbackConfig = { model: getInput("llmFallbackModel") };
      if (fallbackProvider === "openai") llmFallbackConfig.auth_mode = getInput("llmFallbackAuthMode") || "api_key";
      if (getInput("llmFallbackApiKey")) llmFallbackConfig.api_key = getInput("llmFallbackApiKey");
      if (getInput("llmFallbackBaseUrl")) llmFallbackConfig.base_url = getInput("llmFallbackBaseUrl");
      const logPath = splitLogPath(getInput("logPath"), OBC.state.config?.logging);
      const embeddingFallbackProvider = getInput("embeddingFallbackProvider");
      const embeddingFallbackConfig = { model: getInput("embeddingFallbackModel") };
      if (getInput("embeddingFallbackApiKey")) embeddingFallbackConfig.api_key = getInput("embeddingFallbackApiKey");
      if (getInput("embeddingFallbackBaseUrl")) embeddingFallbackConfig.base_url = getInput("embeddingFallbackBaseUrl");
      const embedding = {
        provider: $("#embeddingProvider").value,
        fallback_enabled: Boolean(embeddingFallbackProvider),
        fallback_provider: embeddingFallbackProvider,
        model: getInput("embeddingModel"),
        output_dimensionality: Math.max(0, getIntInput("embeddingOutputDimensionality", 1024)),
        similarity_threshold: getFloatInput("embeddingSimilarity", 0.82)
      };
      if (getInput("embeddingApiKey")) embedding.api_key = getInput("embeddingApiKey");
      if (getInput("embeddingBaseUrl")) embedding.base_url = getInput("embeddingBaseUrl");
      const cookie = getInput("biliCookie");
      const douyinCookie = getInput("douyinCookie");
      const twitterCookie = getInput("twitterCookie");
      const llm = {
        ...(OBC.state.config?.llm || {}),
        default_provider: provider,
        fallback_enabled: Boolean(fallbackProvider),
        fallback_provider: fallbackProvider,
        concurrency: getIntInput("llmConcurrency", 3),
        timeout: getIntInput("llmTimeout", 60),
        [provider]: { ...(OBC.state.config?.llm?.[provider] || {}), ...llmProviderConfig },
        embedding: { ...(OBC.state.config?.llm?.embedding || {}), ...embedding },
        soul: { ...(OBC.state.config?.llm?.soul || {}), provider: getInput("moduleSoulProvider"), model: getInput("moduleSoulModel") },
        discovery: { ...(OBC.state.config?.llm?.discovery || {}), provider: getInput("moduleDiscoveryProvider"), model: getInput("moduleDiscoveryModel") },
        recommendation: { ...(OBC.state.config?.llm?.recommendation || {}), provider: getInput("moduleRecommendationProvider"), model: getInput("moduleRecommendationModel") },
        evaluation: { ...(OBC.state.config?.llm?.evaluation || {}), provider: getInput("moduleEvaluationProvider"), model: getInput("moduleEvaluationModel") }
      };
      if (fallbackProvider && fallbackProvider !== provider) {
        llm[fallbackProvider] = {
          ...(OBC.state.config?.llm?.[fallbackProvider] || {}),
          ...llmFallbackConfig
        };
      }
      if (embeddingFallbackProvider) {
        llm[embeddingFallbackProvider] = {
          ...(llm[embeddingFallbackProvider] || OBC.state.config?.llm?.[embeddingFallbackProvider] || {}),
          ...embeddingFallbackConfig
        };
      }
      if (getInput("openrouterReferer") || getInput("openrouterTitle")) {
        llm.openrouter = {
          ...(llm.openrouter || {}),
          http_referer: getInput("openrouterReferer"),
          x_title: getInput("openrouterTitle")
        };
      }
      const deepseekReasoning = getInput("deepseekReasoning");
      llm.deepseek = {
        ...(llm.deepseek || OBC.state.config?.llm?.deepseek || {}),
        reasoning_effort: deepseekReasoning
      };
      return {
        language: getInput("language") || "zh",
        data_dir: getInput("dataDir"),
        llm,
        bilibili: {
          auth_method: $("#biliAuth").value,
          ...(cookie ? { cookie } : {}),
          browser_executable: getInput("biliBrowserExecutable"),
          browser_headed: $("#biliBrowserHeaded").value === "on"
        },
        sources: {
          browser: {
            cdp_url: getInput("sourcesBrowserCdp"),
            headed: $("#sourcesBrowserHeaded").value === "on"
          },
          bilibili: {
            enabled: $("#bilibiliEnabled").value === "on"
          },
          xiaohongshu: {
            enabled: $("#xhsEnabled").value === "on",
            daily_search_budget: getIntInput("xhsDailySearchBudget", 0),
            daily_creator_budget: getIntInput("xhsDailyCreatorBudget", 0),
            task_interval_seconds: getIntInput("xhsTaskInterval", 45)
          },
          douyin: {
            enabled: $("#douyinEnabled").value === "on",
            mode: "direct",
            ...(douyinCookie ? { cookie: douyinCookie } : {}),
            cookie_env: getInput("douyinCookieEnv"),
            daily_search_budget: getIntInput("douyinDailySearchBudget", 0),
            daily_hot_budget: getIntInput("douyinDailyHotBudget", 0),
            daily_feed_budget: getIntInput("douyinDailyFeedBudget", 0),
            request_interval_seconds: getIntInput("douyinRequestInterval", 2)
          },
          youtube: {
            enabled: $("#youtubeEnabled").value === "on",
            daily_search_budget: getIntInput("youtubeDailySearchBudget", 0),
            daily_trending_budget: getIntInput("youtubeDailyTrendingBudget", 0),
            daily_channel_budget: getIntInput("youtubeDailyChannelBudget", 0),
            request_interval_seconds: getIntInput("youtubeRequestInterval", 2),
            min_interval_minutes: getIntInput("youtubeMinInterval", 60)
          },
          twitter: {
            enabled: $("#twitterEnabled").value === "on",
            mode: "cookie",
            ...(twitterCookie ? { cookie: twitterCookie } : {}),
            cookie_env: getInput("twitterCookieEnv"),
            daily_search_budget: getIntInput("twitterDailySearchBudget", 0),
            daily_feed_budget: getIntInput("twitterDailyFeedBudget", 0),
            daily_creator_budget: getIntInput("twitterDailyCreatorBudget", 0),
            request_interval_seconds: getIntInput("twitterRequestInterval", 3),
            min_interval_minutes: getIntInput("twitterMinInterval", 60)
          },
          zhihu: {
            enabled: $("#zhihuEnabled").value === "on",
            source_modes: collectZhihuSourceModes(),
            daily_search_budget: getIntInput("zhihuDailySearchBudget", 0),
            daily_hot_budget: getIntInput("zhihuDailyHotBudget", 0),
            daily_feed_budget: getIntInput("zhihuDailyFeedBudget", 0),
            daily_creator_budget: getIntInput("zhihuDailyCreatorBudget", 0),
            daily_related_budget: getIntInput("zhihuDailyRelatedBudget", 0),
            request_interval_seconds: getIntInput("zhihuRequestInterval", 3),
            min_interval_minutes: getIntInput("zhihuMinInterval", 60)
          }
        },
        scheduler: {
          enabled: $("#schedulerEnabled").value === "on",
          pause_on_extension_disconnect: $("#pauseDisconnect").value === "pause",
          extension_disconnect_grace_seconds: getIntInput("extensionDisconnectGrace", 90),
          pool_target_count: getIntInput("poolTarget", 300),
          account_sync_interval_hours: getIntInput("accountSyncInterval", 6),
          refresh_check_interval_seconds: getIntInput("refreshCheckInterval", 60),
          signal_event_threshold: getIntInput("signalEventThreshold", 6),
          feedback_batch_threshold: getIntInput("feedbackBatchThreshold", 3),
          trending_refresh_hours: getIntInput("trendingRefreshHours", 3),
          explore_refresh_hours: getIntInput("exploreRefreshHours", 12),
          discovery_limit: getIntInput("discoveryLimit", 30),
          delight_queue_limit: OBC.getDelightQueueLimit(),
          proactive_push_interval_seconds: getIntInput("proactivePushInterval", 120),
          speculator_idle_interval_minutes: getIntInput("speculatorIdleInterval", 30),
          pool_source_shares: {
            bilibili: getIntInput("shareBilibili", 5),
            xiaohongshu: getIntInput("shareXhs", 1),
            douyin: getIntInput("shareDouyin", 1),
            youtube: getIntInput("shareYoutube", 1),
            twitter: getIntInput("shareTwitter", 1),
            zhihu: getIntInput("shareZhihu", 1)
          },
          speculation_interval_minutes: getIntInput("speculationInterval", 10),
          speculation_ttl_days: getIntInput("speculationTtl", 3),
          speculation_cooldown_days: getIntInput("speculationCooldown", 7),
          speculation_confirmation_threshold: getIntInput("speculationThreshold", 3),
          speculation_max_active: getIntInput("speculationMaxActive", 5),
          speculation_max_primary_interests: getIntInput("speculationMaxPrimary", 15),
          speculation_max_secondary_interests: getIntInput("speculationMaxSecondary", 60),
          auto_update_enabled: $("#autoUpdate").value === "on",
          auto_update_check_interval_hours: getIntInput("autoUpdateInterval", 6)
        },
        discovery: {
          ...(OBC.state.config?.discovery || {}),
          multimodal_evaluation_enabled: $("#multimodalEvaluationEnabled").value === "on",
          multimodal_batch_size: getIntInput("multimodalBatchSize", 8),
          multimodal_image_max_px: getIntInput("multimodalImageMaxPx", 384),
          multimodal_image_quality: getIntInput("multimodalImageQuality", 72),
          multimodal_image_timeout_seconds: getIntInput("multimodalImageTimeout", 6)
        },
        storage: { db_path: getInput("storageDbPath") },
        logging: {
          level: getInput("logLevel") || "INFO",
          file_level: getInput("logFileLevel") || "DEBUG",
          directory: logPath.directory,
          filename: logPath.filename,
          file_path: getInput("logPath"),
          max_file_size_mb: getIntInput("logMaxFileSize", 100),
          backup_count: getIntInput("logBackupCount", 1),
          aggregate_budget_mb: getIntInput("logAggregateBudget", 500),
          unmanaged_truncate_mb: getIntInput("logUnmanagedTruncate", 200),
          unmanaged_max_age_days: getIntInput("logUnmanagedMaxAge", 30)
        }
      };
    }

    const UPDATE_REASON_TEXT = {
      dirty_worktree: "代码目录有未提交改动，更新被阻止",
      unsupported_install_mode: "当前安装方式不支持自动更新",
      untrusted_remote: "git 远端不在允许列表，更新被阻止",
      branch_not_fast_forwardable: "本地代码与发布版本分叉，无法快进更新",
      merge_or_rebase_in_progress: "代码目录正在合并 / 变基，更新暂缓",
      github_rate_limited: "GitHub API 限流，请稍后再试",
      github_unreachable: "无法访问 GitHub 检查更新",
      missing_target_tag: "远端未找到目标版本标签",
      dependency_sync_failed: "更新后依赖安装失败",
      restart_failed: "更新后重启失败",
      no_backend_tag_yet: "远端暂无后端发布标签",
      prerelease_ignored: "仅有预发布版本，已忽略",
      already_applying: "正在更新中"
    };

    function formatUpdateCheckTime(iso) {
      if (!iso) return "";
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return "";
      return date.toLocaleString("zh-CN", { hour12: false });
    }

    function describeUpdateStatus(backend) {
      const reasonKey = backend.reason && backend.reason !== "none" ? String(backend.reason) : "";
      const reasonText = UPDATE_REASON_TEXT[reasonKey] || reasonKey;
      const current = backend.current_version ? `v${backend.current_version}` : "";
      const latest = backend.latest_version ? `v${backend.latest_version}` : "";
      const checkedAt = formatUpdateCheckTime(backend.last_check_at);
      const suffix = checkedAt ? `（${checkedAt} 检查）` : "";
      switch (backend.state) {
        case "disabled":
          return { text: `自动更新未开启${current ? `，当前版本 ${current}` : ""}。`, tone: "" };
        case "checking":
          return { text: "正在检查更新…", tone: "" };
        case "up_to_date":
          return { text: `已是最新版本${current ? ` ${current}` : ""}${reasonText ? `（${reasonText}）` : ""}${suffix}`, tone: "success" };
        case "update_available":
          return { text: `发现新版本 ${latest}（当前 ${current}），${backend.auto_update_enabled ? "将在下个检查周期自动更新" : "开启自动更新后将自动升级"}${suffix}`, tone: "" };
        case "applying":
          return { text: `正在更新到 ${latest || "新版本"}…`, tone: "" };
        case "restart_pending":
          return { text: "更新完成，等待后端重启生效。", tone: "success" };
        case "blocked":
          return { text: `更新被阻止：${reasonText || "未知原因"}${suffix}`, tone: "error" };
        case "unsupported":
          return { text: reasonText || "当前安装方式不支持自动更新。", tone: "error" };
        case "error":
          return { text: `更新检查出错：${reasonText || backend.last_error || "未知错误"}${suffix}`, tone: "error" };
        default:
          return { text: `尚未检查更新${current ? `，当前版本 ${current}` : ""}。`, tone: "" };
      }
    }

    // Frozen desktop bundles can't self-apply — the backend runs a check-only
    // loop against desktop-v* installer tags and the UI guides the user to
    // download the new installer instead.
    function describeFrozenUpdateStatus(backend) {
      const reasonKey = backend.reason && backend.reason !== "none" ? String(backend.reason) : "";
      const reasonText = UPDATE_REASON_TEXT[reasonKey] || reasonKey;
      const current = backend.current_version ? `v${backend.current_version}` : "";
      const latest = backend.latest_version ? `v${backend.latest_version}` : "";
      const checkedAt = formatUpdateCheckTime(backend.last_check_at);
      const suffix = checkedAt ? `（${checkedAt} 检查）` : "";
      switch (backend.state) {
        case "checking":
          return { text: "正在检查新版安装包…", tone: "" };
        case "up_to_date":
          return { text: `当前安装包已是最新${current ? ` ${current}` : ""}${suffix}`, tone: "success" };
        case "update_available":
          return { text: `发现新版安装包 ${latest}（当前 ${current}），桌面安装包不支持自动更新，请下载新版安装包完成升级${suffix}`, tone: "" };
        case "error":
          return { text: `检查新版安装包出错：${reasonText || backend.last_error || "未知错误"}${suffix}`, tone: "error" };
        default:
          return { text: `桌面安装包不支持自动应用更新；后台会定期检查新版安装包并在这里提醒下载${current ? `（当前 ${current}）` : ""}。`, tone: "" };
      }
    }

    function renderUpdateStatus(backend) {
      const line = $("#updateStatusLine");
      const actions = $("#updateActions");
      const checkBtn = $("#updateCheckBtn");
      const applyBtn = $("#updateApplyBtn");
      const downloadLink = $("#updateDownloadLink");
      if (!line) return;
      if (!backend || typeof backend !== "object") {
        line.hidden = true;
        if (actions) actions.hidden = true;
        return;
      }
      const mode = String(backend.install_mode || "");
      // Older backends predate install_mode — keep the toggle usable there.
      const unsupportedInstall = Boolean(mode) && mode !== "git";
      const isFrozen = mode === "frozen";
      const toggle = $("#autoUpdate");
      const interval = $("#autoUpdateInterval");
      // The toggle governs auto-apply, which non-git installs can never do —
      // frozen check-reminders run unconditionally on the backend side.
      if (toggle) toggle.disabled = unsupportedInstall;
      if (interval) interval.disabled = unsupportedInstall;
      if (isFrozen) {
        const { text, tone } = describeFrozenUpdateStatus(backend);
        line.dataset.tone = tone;
        line.textContent = text;
      } else if (unsupportedInstall) {
        line.dataset.tone = "error";
        line.textContent = "当前安装方式不支持自动更新（需要 git 克隆的安装目录）。";
      } else {
        const { text, tone } = describeUpdateStatus(backend);
        line.dataset.tone = tone;
        line.textContent = text;
      }
      line.hidden = false;
      // 立即检查 works on git checkouts AND frozen bundles (check-only there);
      // 立即应用 only when a newer tag is ready to fast-forward on git; the
      // download link replaces 立即应用 on frozen when a new installer exists.
      const lockActions = unsupportedInstall && !isFrozen;
      if (actions) actions.hidden = lockActions;
      if (checkBtn) checkBtn.disabled = lockActions || backend.state === "checking" || backend.state === "applying";
      if (applyBtn) {
        const canApply = !unsupportedInstall && backend.state === "update_available" && Boolean(backend.latest_tag);
        applyBtn.hidden = !canApply;
        applyBtn.disabled = !canApply || backend.state === "applying";
        if (canApply) applyBtn.dataset.tag = String(backend.latest_tag);
      }
      if (downloadLink) {
        const showDownload = isFrozen && backend.state === "update_available";
        downloadLink.hidden = !showDownload;
        if (showDownload) {
          downloadLink.href = backend.latest_tag
            ? `https://github.com/whiteguo233/OpenBiliClaw/releases/tag/${encodeURIComponent(String(backend.latest_tag))}`
            : "https://github.com/whiteguo233/OpenBiliClaw/releases";
        }
      }
    }

    async function refreshUpdateStatus() {
      const line = $("#updateStatusLine");
      wireUpdateActions();
      try {
        const payload = await OBC.requestJson(OBC.ENDPOINTS.updateStatus);
        renderUpdateStatus(payload?.backend || null);
      } catch {
        if (line) line.hidden = true;
      }
    }

    // Wire the 立即检查 / 立即应用 buttons once. Manual check runs /api/update/check
    // (ignores the auto-update toggle); apply posts the latest tag and the backend
    // fast-forwards + restarts — the runtime-stream events refresh the line live.
    function wireUpdateActions() {
      const checkBtn = $("#updateCheckBtn");
      const applyBtn = $("#updateApplyBtn");
      if (checkBtn && !checkBtn.dataset.wired) {
        checkBtn.dataset.wired = "1";
        checkBtn.addEventListener("click", async () => {
          const prev = checkBtn.textContent;
          checkBtn.disabled = true;
          checkBtn.textContent = "检查中…";
          try {
            const payload = await OBC.requestJsonStrict(OBC.ENDPOINTS.updateCheck, {
              method: "POST",
              timeoutMs: 60000,
              headers: { "Content-Type": "application/json" },
              body: "{}"
            });
            renderUpdateStatus(payload?.backend || null);
          } catch (error) {
            OBC.showToast("检查更新失败：" + (error?.message || "未知错误"));
          } finally {
            checkBtn.textContent = prev;
            checkBtn.disabled = false;
          }
        });
      }
      if (applyBtn && !applyBtn.dataset.wired) {
        applyBtn.dataset.wired = "1";
        applyBtn.addEventListener("click", async () => {
          const tag = applyBtn.dataset.tag || "";
          if (!tag) return;
          const prev = applyBtn.textContent;
          applyBtn.disabled = true;
          applyBtn.textContent = "应用中…";
          try {
            const body = await OBC.requestJsonStrict(OBC.ENDPOINTS.updateApply, {
              method: "POST",
              timeoutMs: 60000,
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ target: "backend", tag })
            });
            if (body?.accepted) {
              OBC.showToast("已开始更新，后端将在完成后自动重启…");
            } else {
              const reason = body?.reason;
              OBC.showToast("更新未开始：" + (UPDATE_REASON_TEXT[reason] || reason || "未知原因"));
            }
          } catch (error) {
            const reason = error?.details?.reason;
            OBC.showToast("更新未开始：" + (UPDATE_REASON_TEXT[reason] || reason || error?.message || "未知原因"));
          } finally {
            applyBtn.textContent = prev;
            applyBtn.disabled = false;
            void refreshUpdateStatus();
          }
        });
      }
    }

    function formatProbeResult(result) {
      const ok = Boolean(result?.ok);
      const provider = result?.provider ? ` ${result.provider}` : "";
      const model = result?.model ? ` / ${result.model}` : "";
      const latency = Number.isFinite(Number(result?.latency_ms)) && Number(result.latency_ms) > 0
        ? ` (${Math.round(Number(result.latency_ms))}ms)`
        : "";
      const detail = result?.message || result?.error || (ok ? "服务可用" : "服务不可用");
      return `${ok ? "可用" : "不可用"}${provider}${model}${latency}: ${detail}`;
    }

    async function probeConfigService(kind, config) {
      return await OBC.requestJsonStrict(OBC.ENDPOINTS.configProbe, {
        method: "POST",
        timeoutMs: 35000,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, config })
      });
    }

    function renderProbeResult(statusEl, result) {
      if (!statusEl) return;
      statusEl.dataset.tone = result?.ok ? "success" : "error";
      statusEl.textContent = formatProbeResult(result);
      const configStatus = $("#configStatus");
      if (configStatus) configStatus.value = formatProbeResult(result);
    }

    function renderProbePending(statusEl, label) {
      if (!statusEl) return;
      statusEl.dataset.tone = "pending";
      statusEl.textContent = `${label} 探测中…`;
    }

    async function runLlmConfigProbe() {
      const button = $("#probeLlm");
      const statusEl = $("#probeLlmStatus");
      if (button) button.disabled = true;
      renderProbePending(statusEl, "LLM");
      try {
        const result = await probeConfigService("llm", buildConfigUpdate());
        renderProbeResult(statusEl, result);
      } catch (error) {
        renderProbeResult(statusEl, {
          ok: false,
          error: OBC.configErrorMessage(error?.details) || error?.message || "LLM 探测失败"
        });
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function runEmbeddingConfigProbe() {
      const button = $("#probeEmbedding");
      const statusEl = $("#probeEmbeddingStatus");
      if (button) button.disabled = true;
      renderProbePending(statusEl, "Embedding");
      try {
        const result = await probeConfigService("embedding", buildConfigUpdate());
        renderProbeResult(statusEl, result);
      } catch (error) {
        renderProbeResult(statusEl, {
          ok: false,
          error: OBC.configErrorMessage(error?.details) || error?.message || "Embedding 探测失败"
        });
      } finally {
        if (button) button.disabled = false;
      }
    }

    document.addEventListener("click", (event) => {
      const closeId = event.target?.dataset?.close;
      if (closeId) closePanel(closeId);
    });

    // Expose functions to window for cross-module access
    window.loadMoreProfileMemory = loadMoreProfileMemory;
    window.reshuffle = reshuffle;
    window.returnToMessages = returnToMessages;
    window.shouldShowInitOnboarding = shouldShowInitOnboarding;
    window.normalizeRuntimeStatus = normalizeRuntimeStatus;
    window.initLanAuthControl = initLanAuthControl;
    window.initBootAutostartControl = initBootAutostartControl;
    window.probeKey = probeKey;
    window.getRenderableMessages = getRenderableMessages;
    window.hydrateInboxFromSpeculations = hydrateInboxFromSpeculations;
    window.renderMessages = renderMessages;
    window.renderChat = renderChat;
    window.sendChat = sendChat;
    window.renderSourcesStatusRows = renderSourcesStatusRows;
    window.renderSourcesStatus = renderSourcesStatus;
    window.renderSourceCredentials = renderSourceCredentials;
    window.applyConfig = applyConfig;
    window.renderDelightGrid = renderDelightGrid;
    window.shuffleDelights = shuffleDelights;
    window.fetchDelightQueue = fetchDelightQueue;
    window.refreshProfile = refreshProfile;
    window.hydrateFromBackend = hydrateFromBackend;
    window.connectRuntimeStream = connectRuntimeStream;
    window.renderAll = renderAll;
    window.buildConfigUpdate = buildConfigUpdate;
    window.refreshUpdateStatus = refreshUpdateStatus;
    window.runLlmConfigProbe = runLlmConfigProbe;
    window.runEmbeddingConfigProbe = runEmbeddingConfigProbe;
    window.SOURCE_ENABLE_SELECT_IDS = SOURCE_ENABLE_SELECT_IDS;
    window.renderProfileEditPanel = renderProfileEditPanel;
    window.bindProfileEditActions = bindProfileEditActions;

})();
