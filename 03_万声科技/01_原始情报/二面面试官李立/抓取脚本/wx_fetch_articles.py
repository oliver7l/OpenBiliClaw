#!/usr/bin/env python3
"""批量抓取 AlgorithmDog 公众号文章正文（mp.weixin 直链公开可访问）。"""
import html as htmlmod
import json
import os
import re
import sys
import time

sys.path.insert(0, "/Users/imac/.workbuddy/binaries/python/envs/default/lib/python3.13/site-packages")
from curl_cffi import requests

BASE = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/03_万声科技/01_原始情报/二面面试官李立"
LIST = os.path.join(BASE, "公众号文章列表-weread.json")
OUT = os.path.join(BASE, "公众号文章存档-weread")
os.makedirs(OUT, exist_ok=True)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 MicroMessenger")

BLOCK = re.compile(r"</?(p|div|section|li|h[1-6]|tr|br|blockquote|pre)[^>]*>", re.I)


def strip_html(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.S | re.I)
    s = re.sub(r"<img[^>]*?src=\"([^\"]+)\"[^>]*>", r"\n![](\1)\n", s, flags=re.I)
    s = BLOCK.sub("\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmlmod.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s)[:80]


arts = json.load(open(LIST))
ok = fail = skip = 0
fails = []
for i, a in enumerate(arts):
    day = time.strftime("%Y-%m-%d", time.gmtime(a["t"]))
    fname = f"{day}_{safe_name(a['title'])}.md"
    fpath = os.path.join(OUT, fname)
    if os.path.exists(fpath) and os.path.getsize(fpath) > 300:
        skip += 1
        continue
    try:
        r = requests.get(a["url"], headers={"User-Agent": UA}, timeout=25, impersonate="safari17_0")
        t = r.text
        if "js_content" not in t and "page-content" not in t:
            raise RuntimeError("no content marker, len=%d" % len(t))
        mt = re.search(r"<h1[^>]*id=\"activity-name\"[^>]*>(.*?)</h1>", t, re.S) or \
             re.search(r"var msg_title = '(.*?)';", t) or \
             re.search(r"<meta property=\"og:title\" content=\"([^\"]+)\"", t)
        title = htmlmod.unescape(re.sub(r"<[^>]+>", "", mt.group(1)).strip()) if mt else a["title"]
        mc = re.search(r"<div[^>]*id=\"js_content\"[^>]*>(.*)?<div[^>]*id=\"js_tags\"|<div[^>]*id=\"js_content\"[^>]*>(.*)</div>\s*<script", t, re.S) or \
             re.search(r"<div[^>]*id=\"js_content\"[^>]*>(.*)", t, re.S)
        body = strip_html(mc.group(1) or mc.group(0)) if mc else ""
        header = (f"# {title}\n\n- 链接: {a['url']}\n- 发布: {day}\n"
                  f"- 来源: 微信公众号 AlgorithmDog\n\n---\n\n")
        with open(fpath, "w") as f:
            f.write(header + body)
        ok += 1
        print(f"[{i+1}/{len(arts)}] OK {fname} ({len(body)}字)", flush=True)
    except Exception as e:
        fail += 1
        fails.append({"url": a["url"], "title": a["title"], "err": str(e)[:150]})
        print(f"[{i+1}/{len(arts)}] FAIL {a['title'][:30]}: {str(e)[:100]}", flush=True)
    time.sleep(2)

json.dump(fails, open(os.path.join(OUT, "_fails.json"), "w"), ensure_ascii=False, indent=1)
print(f"DONE ok={ok} skip={skip} fail={fail}")
