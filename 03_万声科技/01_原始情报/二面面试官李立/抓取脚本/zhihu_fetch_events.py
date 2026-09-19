#!/usr/bin/env python3
"""拉取知乎用户「算法一只狗」(li-luo-qin-91) 的全部创作活动流 → /tmp/zhihu_events.json。
依赖 zhihu-cli 已登录（复用其 session 调 api.zhihu.com）。
用法: env -u PYTHONHOME -u PYTHONPATH /Users/imac/.local/share/uv/tools/zhihu-toolkit/bin/python3 zhihu_fetch_events.py
"""
import json, sys

sys.path.insert(0, '/Users/imac/.local/share/uv/tools/zhihu-toolkit/lib/python3.13/site-packages')
from zhihu_cli.content.handlers.people import fetch_member_activities

items = fetch_member_activities('li-luo-qin-91', max_items=4000, creations_only=True)
arts = [i for i in items if i.get('target_type') == 'article']
print('total events:', len(items), '| articles:', len(arts),
      '| answers:', sum(1 for i in items if i.get('target_type') == 'answer'))
json.dump(items, open('/tmp/zhihu_events.json', 'w'), ensure_ascii=False)
print('DONE')
