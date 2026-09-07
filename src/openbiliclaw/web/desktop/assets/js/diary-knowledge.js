/**
 * 日记知识网络可视化
 * 包含：标签关联网络、人物关系图谱、混合知识网络、网络统计
 * 使用 Canvas 实现简化的力导向布局
 */

(function () {
  "use strict";

  let currentKgView = "mixed";
  let graphData = { nodes: [], edges: [] };
  let canvas = null;
  let ctx = null;
  let animationId = null;
  let selectedNode = null;
  let draggingNode = null;
  let mousePos = { x: 0, y: 0 };
  let _initialized = false;

  // 节点颜色
  const NODE_COLORS = {
    tag: "#6366f1",
    person: "#ec4899",
    theme: "#10b981",
  };

  // 初始化（幂等：只执行一次，避免重复绑定事件和重复加载）
  function init() {
    if (_initialized) return;
    _initialized = true;

    canvas = document.getElementById("diaryKnowledgeCanvas");
    if (!canvas) return;
    ctx = canvas.getContext("2d");

    initKgSubtabs();
    initKgControls();
    initKgDetailPanel();
    initCanvasEvents();

    // 默认加载混合网络
    loadKnowledgeGraph();
  }

  // 子 Tab 切换
  function initKgSubtabs() {
    document.querySelectorAll(".diary-knowledge-subtab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.kgView;
        switchKgView(view);
      });
    });
  }

  function switchKgView(view) {
    currentKgView = view;
    document.querySelectorAll(".diary-knowledge-subtab").forEach((t) => {
      t.classList.toggle("active", t.dataset.kgView === view);
    });

    // 显示/隐藏统计内容
    const statsContent = document.getElementById("kgStatsContent");
    const canvasContainer = document.querySelector(".diary-knowledge-canvas-container");
    if (view === "stats") {
      if (statsContent) statsContent.hidden = false;
      if (canvasContainer) canvasContainer.style.display = "none";
      loadNetworkStats();
    } else {
      if (statsContent) statsContent.hidden = true;
      if (canvasContainer) canvasContainer.style.display = "block";
      loadKnowledgeGraph();
    }
  }

  // 控制栏
  function initKgControls() {
    document.getElementById("kgLoadBtn").addEventListener("click", loadKnowledgeGraph);
  }

  // 详情面板
  function initKgDetailPanel() {
    document.getElementById("kgDetailClose").addEventListener("click", () => {
      document.getElementById("kgDetailPanel").hidden = true;
      selectedNode = null;
    });
  }

  // Canvas 事件
  function initCanvasEvents() {
    canvas.addEventListener("mousedown", onMouseDown);
    canvas.addEventListener("mousemove", onMouseMove);
    canvas.addEventListener("mouseup", onMouseUp);
    canvas.addEventListener("click", onClick);
    canvas.addEventListener("wheel", onWheel, { passive: false });
  }

  function onMouseDown(e) {
    const pos = getCanvasPos(e);
    const node = findNodeAt(pos.x, pos.y);
    if (node) {
      draggingNode = node;
      node.vx = 0;
      node.vy = 0;
    }
  }

  function onMouseMove(e) {
    mousePos = getCanvasPos(e);
    if (draggingNode) {
      draggingNode.x = mousePos.x;
      draggingNode.y = mousePos.y;
    }
  }

  function onMouseUp() {
    draggingNode = null;
  }

  function onClick(e) {
    if (draggingNode) return;
    const pos = getCanvasPos(e);
    const node = findNodeAt(pos.x, pos.y);
    if (node) {
      selectedNode = node;
      showNodeDetail(node);
    } else {
      document.getElementById("kgDetailPanel").hidden = true;
      selectedNode = null;
    }
  }

  function onWheel(e) {
    e.preventDefault();
    // 简单的缩放（通过调整节点间距）
    const scale = e.deltaY > 0 ? 0.9 : 1.1;
    graphData.nodes.forEach((n) => {
      n.x = canvas.width / 2 + (n.x - canvas.width / 2) * scale;
      n.y = canvas.height / 2 + (n.y - canvas.height / 2) * scale;
    });
  }

  function getCanvasPos(e) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * canvas.width,
      y: ((e.clientY - rect.top) / rect.height) * canvas.height,
    };
  }

  function findNodeAt(x, y) {
    for (let i = graphData.nodes.length - 1; i >= 0; i--) {
      const n = graphData.nodes[i];
      const dx = n.x - x;
      const dy = n.y - y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < n.size + 5) {
        return n;
      }
    }
    return null;
  }

  // 加载知识图谱
  async function loadKnowledgeGraph() {
    const loading = document.getElementById("kgLoading");
    const empty = document.getElementById("kgEmpty");
    loading.hidden = false;
    empty.hidden = true;

    const startDate = document.getElementById("kgStartDate").value;
    const endDate = document.getElementById("kgEndDate").value;
    const minCount = parseInt(document.getElementById("kgMinCount").value) || 2;

    let endpoint = "";
    if (currentKgView === "tags") {
      endpoint = `/api/diary/knowledge-graph/tag-network?min_count=${minCount}&max_nodes=50`;
    } else if (currentKgView === "persons") {
      endpoint = `/api/diary/knowledge-graph/person-network?min_count=1&max_nodes=30`;
    } else {
      endpoint = `/api/diary/knowledge-graph/mixed?min_count=${minCount}&max_nodes=60`;
    }

    if (startDate) endpoint += `&start_date=${startDate}`;
    if (endDate) endpoint += `&end_date=${endDate}`;

    try {
      const res = await fetch(endpoint);
      const data = await res.json();
      if (data.ok && data.data.nodes.length > 0) {
        graphData = data.data;
        initNodePositions();
        startAnimation();
        empty.hidden = true;
      } else {
        graphData = { nodes: [], edges: [] };
        stopAnimation();
        empty.hidden = false;
      }
    } catch (e) {
      console.error("加载知识图谱失败", e);
      empty.hidden = false;
    } finally {
      loading.hidden = true;
    }
  }

  // 初始化节点位置（圆形分布）
  function initNodePositions() {
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const radius = Math.min(canvas.width, canvas.height) * 0.35;

    graphData.nodes.forEach((node, i) => {
      const angle = (i / graphData.nodes.length) * Math.PI * 2;
      node.x = centerX + Math.cos(angle) * radius * (0.5 + Math.random() * 0.5);
      node.y = centerY + Math.sin(angle) * radius * (0.5 + Math.random() * 0.5);
      node.vx = 0;
      node.vy = 0;
    });
  }

  // 力导向布局动画
  function startAnimation() {
    stopAnimation();
    animate();
  }

  function stopAnimation() {
    if (animationId) {
      cancelAnimationFrame(animationId);
      animationId = null;
    }
  }

  function animate() {
    applyForces();
    updatePositions();
    draw();
    animationId = requestAnimationFrame(animate);
  }

  function applyForces() {
    const nodes = graphData.nodes;
    const edges = graphData.edges;

    // 斥力（节点之间）
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = 2000 / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        nodes[i].vx -= fx;
        nodes[i].vy -= fy;
        nodes[j].vx += fx;
        nodes[j].vy += fy;
      }
    }

    // 引力（边连接的节点）
    edges.forEach((edge) => {
      const source = nodes.find((n) => n.id === edge.source);
      const target = nodes.find((n) => n.id === edge.target);
      if (!source || !target) return;

      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - 150) * 0.01 * Math.min(edge.weight, 5);
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      source.vx += fx;
      source.vy += fy;
      target.vx -= fx;
      target.vy -= fy;
    });

    // 中心引力
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    nodes.forEach((node) => {
      node.vx += (centerX - node.x) * 0.001;
      node.vy += (centerY - node.y) * 0.001;
    });
  }

  function updatePositions() {
    graphData.nodes.forEach((node) => {
      if (node === draggingNode) return;
      // 阻尼
      node.vx *= 0.9;
      node.vy *= 0.9;
      // 速度限制
      const speed = Math.sqrt(node.vx * node.vx + node.vy * node.vy);
      if (speed > 10) {
        node.vx = (node.vx / speed) * 10;
        node.vy = (node.vy / speed) * 10;
      }
      node.x += node.vx;
      node.y += node.vy;
      // 边界限制
      node.x = Math.max(node.size, Math.min(canvas.width - node.size, node.x));
      node.y = Math.max(node.size, Math.min(canvas.height - node.size, node.y));
    });
  }

  // 绘制
  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 绘制边
    graphData.edges.forEach((edge) => {
      const source = graphData.nodes.find((n) => n.id === edge.source);
      const target = graphData.nodes.find((n) => n.id === edge.target);
      if (!source || !target) return;

      const isHighlighted = selectedNode && (selectedNode.id === edge.source || selectedNode.id === edge.target);
      const alpha = isHighlighted ? 0.8 : 0.2;
      const lineWidth = isHighlighted ? 2 : Math.min(edge.weight, 3);

      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.lineTo(target.x, target.y);
      ctx.strokeStyle = edge.type === "tag-person" ? `rgba(16, 185, 129, ${alpha})` : `rgba(100, 116, 139, ${alpha})`;
      ctx.lineWidth = lineWidth;
      ctx.stroke();
    });

    // 绘制节点
    graphData.nodes.forEach((node) => {
      const isSelected = selectedNode && selectedNode.id === node.id;
      const isHovered = findNodeAt(mousePos.x, mousePos.y) === node;
      const color = NODE_COLORS[node.type] || "#6366f1";

      // 光晕
      if (isSelected || isHovered) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.size + 8, 0, Math.PI * 2);
        ctx.fillStyle = color + "33";
        ctx.fill();
      }

      // 节点圆
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.size, 0, Math.PI * 2);
      const gradient = ctx.createRadialGradient(node.x - node.size / 3, node.y - node.size / 3, 0, node.x, node.y, node.size);
      gradient.addColorStop(0, lightenColor(color, 30));
      gradient.addColorStop(1, color);
      ctx.fillStyle = gradient;
      ctx.fill();
      ctx.strokeStyle = isSelected ? "#fff" : color;
      ctx.lineWidth = isSelected ? 3 : 1;
      ctx.stroke();

      // 节点标签
      ctx.fillStyle = "#1f2937";
      ctx.font = `${Math.max(11, node.size / 3)}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const label = node.label.length > 6 ? node.label.substring(0, 6) + "..." : node.label;
      ctx.fillText(label, node.x, node.y);
    });
  }

  function lightenColor(color, percent) {
    const num = parseInt(color.replace("#", ""), 16);
    const amt = Math.round(2.55 * percent);
    const R = Math.min(255, (num >> 16) + amt);
    const G = Math.min(255, ((num >> 8) & 0x00ff) + amt);
    const B = Math.min(255, (num & 0x0000ff) + amt);
    return "#" + (0x1000000 + R * 0x10000 + G * 0x100 + B).toString(16).slice(1);
  }

  // 显示节点详情
  async function showNodeDetail(node) {
    const panel = document.getElementById("kgDetailPanel");
    const title = document.getElementById("kgDetailTitle");
    const content = document.getElementById("kgDetailContent");

    title.textContent = `${node.type === "tag" ? "🏷️" : "👤"} ${node.label}`;
    content.innerHTML = '<div class="kg-detail-loading">加载中...</div>';
    panel.hidden = false;

    try {
      const res = await fetch(`/api/diary/knowledge-graph/node/${encodeURIComponent(node.id)}?limit=10`);
      const data = await res.json();
      if (data.ok) {
        const detail = data.data;
        let html = `
          <div class="kg-detail-stats">
            <div class="kg-detail-stat">
              <span class="kg-detail-stat-number">${detail.total_appearances}</span>
              <span class="kg-detail-stat-label">次出现</span>
            </div>
            <div class="kg-detail-stat">
              <span class="kg-detail-stat-number">${detail.related_nodes.length}</span>
              <span class="kg-detail-stat-label">个关联节点</span>
            </div>
            <div class="kg-detail-stat">
              <span class="kg-detail-stat-number">${detail.timeline.length}</span>
              <span class="kg-detail-stat-label">个月有记录</span>
            </div>
          </div>
        `;

        if (detail.related_nodes.length > 0) {
          html += `<div class="kg-detail-section"><h5>🔗 关联节点</h5><div class="kg-detail-tags">`;
          detail.related_nodes.forEach((n) => {
            html += `<span class="kg-detail-tag">${escapeHtml(n.label)} (${n.count})</span>`;
          });
          html += `</div></div>`;
        }

        if (detail.related_entries.length > 0) {
          html += `<div class="kg-detail-section"><h5>📝 相关日记</h5>`;
          detail.related_entries.forEach((entry) => {
            html += `
              <div class="kg-detail-entry">
                <div class="kg-detail-entry-date">${entry.date}</div>
                <div class="kg-detail-entry-title">${escapeHtml(entry.title)}</div>
                <div class="kg-detail-entry-preview">${escapeHtml(entry.content_preview)}</div>
              </div>
            `;
          });
          html += `</div>`;
        }

        content.innerHTML = html;
      } else {
        content.innerHTML = `<div class="kg-detail-error">加载失败：${data.error}</div>`;
      }
    } catch (e) {
      content.innerHTML = `<div class="kg-detail-error">加载失败：${e.message}</div>`;
    }
  }

  // 加载网络统计
  async function loadNetworkStats() {
    const content = document.getElementById("kgStatsContent");
    content.innerHTML = '<div class="kg-stats-loading">加载中...</div>';

    const startDate = document.getElementById("kgStartDate").value;
    const endDate = document.getElementById("kgEndDate").value;

    let endpoint = "/api/diary/knowledge-graph/stats";
    const params = [];
    if (startDate) params.push(`start_date=${startDate}`);
    if (endDate) params.push(`end_date=${endDate}`);
    if (params.length > 0) endpoint += "?" + params.join("&");

    try {
      const res = await fetch(endpoint);
      const data = await res.json();
      if (data.ok) {
        const stats = data.data;
        content.innerHTML = `
          <div class="kg-stats-grid">
            <div class="kg-stats-card">
              <div class="kg-stats-number">${stats.total_entries}</div>
              <div class="kg-stats-label">📝 总日记数</div>
            </div>
            <div class="kg-stats-card">
              <div class="kg-stats-number">${stats.total_tags}</div>
              <div class="kg-stats-label">🏷️ 总标签数</div>
            </div>
            <div class="kg-stats-card">
              <div class="kg-stats-number">${stats.total_persons}</div>
              <div class="kg-stats-label">👥 总人物数</div>
            </div>
            <div class="kg-stats-card">
              <div class="kg-stats-number">${stats.tag_relations}</div>
              <div class="kg-stats-label">🔗 标签关系</div>
            </div>
            <div class="kg-stats-card">
              <div class="kg-stats-number">${stats.person_relations}</div>
              <div class="kg-stats-label">💑 人物关系</div>
            </div>
            <div class="kg-stats-card">
              <div class="kg-stats-number">${stats.avg_tags_per_entry}</div>
              <div class="kg-stats-label">📊 平均标签/日记</div>
            </div>
          </div>

          <div class="kg-stats-sections">
            <div class="kg-stats-section">
              <h4>🔥 热门标签</h4>
              <div class="kg-stats-tag-list">
                ${stats.top_tags.map((t) => `<span class="kg-stats-tag">${escapeHtml(t.tag)} <span class="kg-stats-tag-count">${t.count}</span></span>`).join("")}
              </div>
            </div>
            <div class="kg-stats-section">
              <h4>👥 热门人物</h4>
              <div class="kg-stats-tag-list">
                ${stats.top_persons.map((p) => `<span class="kg-stats-tag person">${escapeHtml(p.person)} <span class="kg-stats-tag-count">${p.count}</span></span>`).join("")}
              </div>
            </div>
          </div>
        `;
      }
    } catch (e) {
      content.innerHTML = `<div class="kg-stats-error">加载失败：${e.message}</div>`;
    }
  }

  function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // 暴露到全局
  window.initDiaryKnowledge = init;
})();
