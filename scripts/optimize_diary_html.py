#!/usr/bin/env python3
"""
重组日记页面 HTML：
1. 统计栏 → 迷你条
2. 子Tab → 5个扁平Tab + 搜索栏融合
3. 14个视图 → 5个组视图（每组内嵌子Tab）
"""

import re

with open("/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/src/openbiliclaw/web/desktop/index.html", "r", encoding="utf-8") as f:
    html = f.read()

# ─── 1. 替换统计栏 ───
old_stats = re.search(
    r'<!-- 统计概览 -->\s*<div class="diary-stats" id="diaryStats">.*?</div>\s*',
    html, re.DOTALL
)
if old_stats:
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
        </div>
'''
    html = html[:old_stats.start()] + new_stats + html[old_stats.end():]
    print("OK: 统计栏已替换为迷你条")
else:
    print("WARN: 未找到统计栏")

# ─── 2. 替换子Tab + 筛选栏 ───
old_subtabs = re.search(
    r'<!-- 子 Tab 切换（精简版） -->.*?<div class="diary-subtabs">.*?</div>\s*',
    html, re.DOTALL
)
if old_subtabs:
    # 同时移除后面的筛选栏（在 diary-list-view 内的第一段）
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
        </div>
'''
    html = html[:old_subtabs.start()] + new_subtabs + html[old_subtabs.end():]
    print("OK: 子Tab已替换为扁平化版本")
else:
    print("WARN: 未找到子Tab")

# ─── 3. 移除 diary-list-view 内的独立筛选栏 ───
# 筛选栏在 <div class="diary-list-view" id="diaryListView"> 之后，紧跟着的
# <div class="diary-filter-bar">...</div>
old_filter = re.search(
    r'(<div class="diary-list-view" id="diaryListView">)\s*<!-- 搜索和筛选 -->\s*<div class="diary-filter-bar">.*?</div>\s*',
    html, re.DOTALL
)
if old_filter:
    html = html[:old_filter.start()] + old_filter.group(1) + '\n' + html[old_filter.end():]
    print("OK: 列表视图内筛选栏已移除")
else:
    print("WARN: 未找到列表视图内筛选栏")

# ─── 4. 重组视图为5个组 ───
# 定义视图分组（按原始ID搜索）
groups = {
    "insights": {
        "name": "洞察",
        "icon": "📊",
        "views": [
            ("diaryInsightsView", "📊 数据洞察"),
            ("diaryEmotionView", "💖 情绪中心"),
            ("diaryTimelineView", "📅 时间线"),
            ("diaryReflectionView", "📈 反思回顾"),
            ("diaryInsightsCenterView", "💡 洞察中心"),
        ]
    },
    "people": {
        "name": "人物",
        "icon": "👤",
        "views": [
            ("diaryPeopleView", "👤 人物与标签"),
            ("diaryFragmentsView", "✨ 随手记"),
        ]
    },
    "memory": {
        "name": "记忆",
        "icon": "🧠",
        "views": [
            ("diaryKnowledgeView", "🕸️ 知识网络"),
            ("diaryMemoryView", "🧠 记忆中心"),
            ("diaryAdvancedMemoryView", "🧬 高级记忆"),
            ("diarySelfEvolutionView", "🧬 自进化"),
        ]
    },
    "search": {
        "name": "搜索",
        "icon": "🔍",
        "views": [
            ("diarySemanticView", "🔍 语义搜索"),
            ("diaryChatView", "💬 日记对话"),
        ]
    }
}

# 提取每个视图的 HTML 内容
view_html = {}
for group_key, group_info in groups.items():
    for view_id, view_label in group_info["views"]:
        # 找到 <div class="diary-*-view" id="viewId" ...> ... </div>
        pattern = re.compile(
            r'(<div[^>]*\bid="' + re.escape(view_id) + r'"[^>]*>.*?</div>\s*)(?=\s*<!--|\s*<div class="diary-)',
            re.DOTALL
        )
        match = pattern.search(html)
        if match:
            view_html[view_id] = match.group(1)
            print(f"  OK: 提取视图 {view_id}")
        else:
            # 尝试宽松匹配：找到id=viewId的div，到下一个</div>后面跟注释或下一个视图
            loose = re.search(
                r'<div[^>]*\bid="' + re.escape(view_id) + r'"[^>]*>.*?(?=<div class="diary-\w+-view"|\s*<!--\s*\w+\s*-->|\s*</section>)',
                html, re.DOTALL
            )
            if loose:
                view_html[view_id] = loose.group(0)
                print(f"  OK(宽松): 提取视图 {view_id}")
            else:
                print(f"  WARN: 未找到视图 {view_id}")

# 生成5个组视图的HTML
group_html = {}
for group_key, group_info in groups.items():
    tabs_html = "".join(
        f'<button class="diary-inner-tab{" active" if i==0 else ""}" data-inner-view="{vid}" type="button">{label}</button>\n'
        for i, (vid, label) in enumerate(group_info["views"])
    )
    contents_html = "".join(
        view_html.get(vid, f"<!-- {vid} not found -->")
        for vid, _ in group_info["views"]
    )
    group_html[group_key] = f'''\
        <div class="diary-group-view" id="diary{group_key.capitalize()}GroupView" hidden>
          <div class="diary-inner-tabs">
            {tabs_html}          </div>
          {contents_html}        </div>
'''

# 从原始HTML中移除所有被提取的视图
for group_key, group_info in groups.items():
    for view_id, view_label in group_info["views"]:
        if view_id in view_html:
            html = html.replace(view_html[view_id], "", 1)

# 在 diary-list-view 关闭标签后插入组视图
# 找到 </div><!-- /diary-list-view -->
insert_point = html.find('</div><!-- /diary-list-view -->')
if insert_point == -1:
    insert_point = html.find('<!-- /diary-list-view -->')
if insert_point == -1:
    # 找个更稳定的锚点
    insert_point = html.find('<!-- 数据洞察视图 -->')
    if insert_point != -1:
        # 移除旧的注释标记
        pass

if insert_point != -1:
    # 找到行尾
    end_of_line = html.find('\n', insert_point)
    if end_of_line != -1:
        insert_pos = end_of_line + 1
    else:
        insert_pos = insert_point + len('</div><!-- /diary-list-view -->')
    
    all_groups = ""
    for gk in ["insights", "people", "memory", "search"]:
        all_groups += group_html[gk]
    
    html = html[:insert_pos] + '\n' + all_groups + html[insert_pos:]
    print("OK: 组视图已插入")
else:
    print("WARN: 未找到插入点")

# ─── 5. 清理各视图内部的冗余介绍文字 ───
# 替换 intro 部分为更紧凑的版本
intro_replacements = [
    # 人物视图
    (r'<div class="diary-people-intro">.*?<p>.*?</p>\s*</div>', 
     ''),
    # 随手记视图
    (r'<div class="diary-fragments-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 反思回顾视图
    (r'<div class="diary-reflection-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 知识网络视图
    (r'<div class="diary-knowledge-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 自进化视图
    (r'<div class="diary-se-intro">.*?<p>.*?</p>\s*</div>',
     '<div class="diary-se-intro-compact">🧬 自进化中心</div>'),
    # 洞察中心视图
    (r'<div class="diary-insights-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 记忆中心视图
    (r'<div class="diary-memory-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 情绪中心视图
    (r'<div class="diary-emotion-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 高级记忆视图
    (r'<div class="diary-advanced-memory-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 时间线视图
    (r'<div class="diary-timeline-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 语义搜索视图
    (r'<div class="diary-semantic-intro">.*?<p>.*?</p>\s*</div>',
     ''),
    # 日记对话视图
    (r'<div class="diary-chat-intro">.*?<p>.*?</p>\s*</div>',
     ''),
]

for pattern, replacement in intro_replacements:
    new_html = re.sub(pattern, replacement, html, count=1, flags=re.DOTALL)
    if new_html != html:
        print(f"  OK: 替换 intro 匹配 {pattern[:50]}...")
        html = new_html
    else:
        pass  # 可能已经被前一步处理了

# ─── 写入结果 ───
with open("/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/src/openbiliclaw/web/desktop/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("\n=== 完成! ===")