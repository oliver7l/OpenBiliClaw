#!/usr/bin/env python3
"""批量下载知乎「算法一只狗」全部文章全文（基于 zhihu-cli 内部 scrape_article，幂等）。"""
import json, os, re, sys, time

sys.path.insert(0, '/Users/imac/.local/share/uv/tools/zhihu-toolkit/lib/python3.13/site-packages')
from zhihu_cli.content.handlers.article import scrape_article

OUT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/03_万声科技/01_原始情报/二面面试官李立/知乎文章存档-算法一只狗"
os.makedirs(OUT, exist_ok=True)

events = json.load(open('/tmp/zhihu_events.json'))
arts = [a for a in events if a.get('target_type') == 'article']
# 去重 + 按时间正序
seen, uniq = set(), []
for a in arts:
    tid = a.get('target_id') or a.get('id')
    if tid in seen:
        continue
    seen.add(tid)
    uniq.append(a)
uniq.sort(key=lambda x: x.get('created_time') or '')


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', s)[:80]


index, ok, fail = [], 0, 0
for i, a in enumerate(uniq):
    tid = a['target_id']
    url = a.get('url') or f"https://zhuanlan.zhihu.com/p/{tid}"
    fname = f"{(a.get('created_time') or '')[:10]}_{safe_name(a.get('title') or tid)}.md"
    fpath = os.path.join(OUT, fname)
    rec = {'id': tid, 'title': a.get('title'), 'url': url,
           'created': a.get('created_time'), 'voteup': a.get('voteup_count'),
           'file': fname, 'excerpt': (a.get('excerpt') or '')[:200]}
    index.append(rec)
    if os.path.exists(fpath) and os.path.getsize(fpath) > 200:
        continue
    try:
        meta, md = scrape_article(url)
        header = (f"# {a.get('title')}\n\n- 链接: {url}\n- 发布: {a.get('created_time')}\n"
                  f"- 赞同: {a.get('voteup_count')} | 评论: {a.get('comment_count')}\n\n---\n\n")
        with open(fpath, 'w') as f:
            f.write(header + md)
        ok += 1
        print(f"[{i+1}/{len(uniq)}] OK {fname}", flush=True)
    except Exception as e:
        fail += 1
        print(f"[{i+1}/{len(uniq)}] FAIL {tid}: {str(e)[:120]}", flush=True)
    time.sleep(2.0)

json.dump(index, open(os.path.join(OUT, '_index.json'), 'w'), ensure_ascii=False, indent=1)
print(f"DONE ok={ok} fail={fail} total={len(uniq)}")
