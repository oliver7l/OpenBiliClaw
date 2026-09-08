#!/usr/bin/env python3
"""
安全地重组日记页面 HTML - 使用行号定界，避免正则陷阱
"""
import re

SRC = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/src/openbiliclaw/web/desktop/index.html"
ORIG = "/tmp/original_index.html"

# 从 git 恢复原始文件
import shutil
shutil.copy2(ORIG, SRC)

with open(SRC, "r", encoding="utf-8") as f:
    html = f.read()
    lines = html.split("\n")

print(f"总行数: {len(lines)}")

# ─── 1. 替换统计栏（基于固定文本） ───
old_stats_marker = '<!-- 统计概览 -->'
new_stats = '''\
        <!-- 统计概览（紧凑版） -->
        <div class="diary-stats-mini" id="diaryStats">
          <span class="dsm-item"><strong id="diaryStatTotal">&mdash;</strong> 日记</span>
          <span class="dsm-sep" aria-hidden="true">&middot;</span>
          <span class="dsm-item"><strong id="diaryStatWords">&mdash;</strong> 字</span>
          <span class="dsm-sep" aria-hidden="true">&middot;</span>
          <span class="dsm-item"><strong id="diaryStatAvg">&mdash;</strong> 均字</span>
          <span class="dsm-sep" aria-hidden="true">&middot;</span>
          <span class="dsm-item"><strong id="diaryStatAnalyzed">&mdash;</strong> 已分析</span>
          <span class="dsm-sep" aria-hidden="true">&middot;</span>
          <span class="dsm-item"><strong id="diaryStatRange">&mdash;</strong> 跨度</span>
        </div>'''

# 找到统计栏的起始和结束
start_idx = html.find(old_stats_marker)
if start_idx == -1:
    print("FATAL: 找不到统计栏标记")
    exit(1)

# 统计栏结束于下一个 </div>\n 后跟 <!-- 子 Tab
end_marker = '<!-- 子 Tab'
end_idx = html.find(end_marker, start_idx)
if end_idx == -1:
    print("FATAL: 找不到子Tab标记")
    exit(1)

# 统计栏的 </div> 在 end_marker 之前
# 往回找最后一个 </div>
stats_div_end = html.rfind('</div>', start_idx, end_idx)
if stats_div_end == -1:
    print("FATAL: 找不到统计栏的结束div")
    exit(1)

before = html[:start_idx]
after = html[stats_div_end + len('</div>'):]
html = before + new_stats + after
print("OK: 统计栏替换完成")

# ─── 2. 替换子Tab ───
old_subtabs_marker = '<!-- 子 Tab 切换'
new_subtabs = '''\
        <!-- 子 Tab 切换（5组扁平化） -->
        <div class="diary-subtabs">
          <button class="diary-subtab active" data-view="list" type="button">📝 日记</button>
          <button class="diary-subtab" data-view="insights" type="button">📊 洞察</button>
          <button class="diary-subtab" data-view="people" type="button">👤 人物</button>
          <button class="diary-subtab" data-view="memory" type="button">🧠 记忆</button>
          <button class="diary-subtab" data-view="search" type="button">🔍 搜索</button>
          <div class="diary-subtab-search" id="diarySubtabSearch">
            <input type="search" id="diarySearchInput" placeholder="搜索日记内容..." class="diary-search-input-compact">
            <select id="diaryMoodFilter" class="diary-filter-select-xs">
              <option value="">情绪</option>
              <option value="very_happy">😄</option>
              <option value="happy">🙂</option>
              <option value="neutral">😐</option>
              <option value="sad">😔</option>
              <option value="very_sad">😢</option>
              <option value="angry">😠</option>
              <option value="anxious">😰</option>
              <option value="unknown">—</option>
            </select>
            <select id="diarySourceFilter" class="diary-filter-select-xs">
              <option value="">来源</option>
              <option value="manual">手动</option>
              <option value="import_lele">乐乐</option>
              <option value="import_text">文本</option>
              <option value="import_markdown">MD</option>
              <option value="api">API</option>
            </select>
            <button class="pill-btn-xs" id="diaryResetFilterBtn" type="button">重置</button>
          </div>
        </div>'''

start_idx = html.find(old_subtabs_marker, start_idx)
if start_idx == -1:
    print("FATAL: 找不到子Tab标记2")
    exit(1)

# 子Tab结束于紧跟着的 </div>（即 diary-subtabs 的关闭标签）
# 找第三个 </div> 后的位置（统计栏的div, 子tab的container div, 子tab的div）
subtabs_marker_end = html.find('</div>', start_idx)
subtabs_marker_end = html.find('</div>', subtabs_marker_end + 6)
subtabs_marker_end = html.find('</div>', subtabs_marker_end + 6)
# 再往后找直到下一个视图
after_marker = '<!-- 日记列表视图'
next_marker_idx = html.find(after_marker, subtabs_marker_end)
if next_marker_idx == -1:
    next_marker_idx = html.find('diary-list-view', subtabs_marker_end)

# 找子Tab区域的结束位置（在子Tab div之后，在下一个视图注释之前）
# 从 subtabs_marker_end 往后找 </div>\n\n        <!-- 日记列表
search_end = html.find('</div>', subtabs_marker_end + 6)
search_end = html.find('</div>', search_end + 6)

before = html[:start_idx]
after = html[search_end + 6:]
html = before + new_subtabs + after
print("OK: 子Tab替换完成")

# ─── 3. 移除 diary-list-view 内的筛选栏 ───
# 找到 <div class="diary-list-view" id="diaryListView"> 后面的 <div class="diary-filter-bar">
dlv_marker = 'diary-list-view" id="diaryListView"'
dlv_start = html.find(dlv_marker)
if dlv_start == -1:
    print("FATAL: 找不到 diary-list-view")
    exit(1)

# 找到 dlv 的 > 结束
dlv_tag_end = html.find('>', dlv_start)
filter_start = html.find('<!-- 搜索和筛选 -->', dlv_tag_end)
if filter_start != -1:
    filter_div_end = html.find('</div>', filter_start)
    filter_div_end = html.find('</div>', filter_div_end + 6)
    # 移除从 filter_start 到 filter_div_end+6
    after_filter = html[filter_div_end + 6:]
    html = html[:filter_start] + '\n' + after_filter[after_filter.startswith('\n'):]
    print("OK: 移除列表视图内筛选栏")
else:
    print("WARN: 找不到列表视图内筛选栏")

# ─── 4. 提取各视图并重组为5个组 ───
# 先找到所有视图的起止位置
views_order = [
    "diaryInsightsView",
    "diaryPeopleView",
    "diaryFragmentsView",
    "diaryReflectionView",
    "diaryKnowledgeView",
    "diarySelfEvolutionView",
    "diaryInsightsCenterView",
    "diaryMemoryView",
    "diaryEmotionView",
    "diaryAdvancedMemoryView",
    "diaryTimelineView",
    "diarySemanticView",
    "diaryChatView",
]

# 找到每个视图的起始和结束位置
view_positions = {}
for vid in views_order:
    # 找 <div class="..." id="vid">
    pattern = f'id="{vid}"'
    pos = html.find(pattern)
    if pos == -1:
        print(f"  WARN: 找不到 {vid}")
        continue
    # 回退到 <div
    div_start = html.rfind('<div', 0, pos)
    # 找到对应的 </div> 结束位置
    # 简单方法：找下一个 <div class="diary- 或 </section>
    next_div = html.find('\n        <div class="diary-', pos)
    next_section = html.find('</section>', pos)
    if next_div != -1 and next_div < next_section:
        # 结束于上一个 </div> 在 next_div 之前
        end_pos = html.rfind('</div>', pos, next_div)
        if end_pos != -1:
            end_pos += 7  # len('</div>') + possible newline
    else:
        end_pos = next_section
    
    view_positions[vid] = (div_start, end_pos)
    print(f"  OK: {vid} 位于 {div_start}-{end_pos}")

# 定义5个组
groups = {
    "insights": {
        "views": [
            ("diaryInsightsView", "📊 数据洞察"),
            ("diaryEmotionView", "💖 情绪中心"),
            ("diaryTimelineView", "📅 时间线"),
            ("diaryReflectionView", "📈 反思回顾"),
            ("diaryInsightsCenterView", "💡 洞察中心"),
        ]
    },
    "people": {
        "views": [
            ("diaryPeopleView", "👤 人物与标签"),
            ("diaryFragmentsView", "✨ 随手记"),
        ]
    },
    "memory": {
        "views": [
            ("diaryKnowledgeView", "🕸️ 知识网络"),
            ("diaryMemoryView", "🧠 记忆中心"),
            ("diaryAdvancedMemoryView", "🧬 高级记忆"),
            ("diarySelfEvolutionView", "🧬 自进化"),
        ]
    },
    "search": {
        "views": [
            ("diarySemanticView", "🔍 语义搜索"),
            ("diaryChatView", "💬 日记对话"),
        ]
    }
}

# 从原始HTML中移除所有视图（按位置从后往前删除）
view_html_map = {}
sorted_views = sorted(view_positions.items(), key=lambda x: -x[1][0])  # 从后往前
for vid, (start, end) in sorted_views:
    if end == -1:
        print(f"  SKIP: {vid} 位置不完整")
        continue
    view_html_map[vid] = html[start:end]
    html = html[:start] + html[end:]
    print(f"  REMOVED: {vid}")

# 生成组视图HTML
all_groups_html = ""
for gk, ginfo in groups.items():
    tabs_lines = []
    for i, (vid, label) in enumerate(ginfo["views"]):
        cls = ' class="diary-inner-tab active"' if i == 0 else ' class="diary-inner-tab"'
        tabs_lines.append(f'            <button{cls} data-inner-view="{vid}" type="button">{label}</button>')
    
    views_content = ""
    for vid, _ in ginfo["views"]:
        if vid in view_html_map:
            views_content += view_html_map[vid] + "\n"
    
    group_html = f'''\
        <div class="diary-group-view" id="diary{gk.capitalize()}GroupView" hidden>
          <div class="diary-inner-tabs">
            {"\n".join(tabs_lines)}
          </div>
          {views_content}        </div>
'''
    all_groups_html += group_html

# 在 diary-list-view 关闭标签后插入
list_end_marker = '<!-- /diary-list-view -->'
insert_pos = html.find(list_end_marker)
if insert_pos == -1:
    print("FATAL: 找不到插入点")
    exit(1)
insert_pos = html.find('\n', insert_pos) + 1

html = html[:insert_pos] + '\n' + all_groups_html + html[insert_pos:]
print(f"OK: 组视图已插入，共 {len(groups)} 组")

# ─── 5. 清理 intro 文字 ───
intro_patterns = [
    (r'<div class="diary-people-intro">.*?</div>\s*', ''),
    (r'<div class="diary-fragments-intro">.*?</div>\s*', ''),
    (r'<div class="diary-reflection-intro">.*?</div>\s*', ''),
    (r'<div class="diary-knowledge-intro">.*?</div>\s*', ''),
    (r'<div class="diary-se-intro">.*?</div>\s*', '<div class="diary-se-intro-compact">🧬 自进化中心</div>'),
    (r'<div class="diary-insights-intro">.*?</div>\s*', ''),
    (r'<div class="diary-memory-intro">.*?</div>\s*', ''),
    (r'<div class="diary-emotion-intro">.*?</div>\s*', ''),
    (r'<div class="diary-advanced-memory-intro">.*?</div>\s*', ''),
    (r'<div class="diary-timeline-intro">.*?</div>\s*', ''),
    (r'<div class="diary-semantic-intro">.*?</div>\s*', ''),
    (r'<div class="diary-chat-intro">.*?</div>\s*', ''),
]

for pattern, replacement in intro_patterns:
    new_html = re.sub(pattern, replacement, html, count=1, flags=re.DOTALL)
    if new_html != html:
        html = new_html
        print(f"  OK: 替换 intro")

# 写入
with open(SRC, "w", encoding="utf-8") as f:
    f.write(html)

print("\n=== 完成! ===")