#!/usr/bin/env python3
"""通过 AgentLimb 桥在微信读书阅读器页分页拉取 AlgorithmDog 全部文章列表。"""
import json, re, subprocess, sys, time

NODE = "/Users/imac/.workbuddy/binaries/node/versions/22.22.2-3/bin/node"
CLI = "/Volumes/固态硬盘1T/002-探索项目/1172-github项目/AgentLimb/agentlimb-extension/agentlimb-chrome-v0.1.4/kernel/bridge/mvp/terminal-client.mjs"
OUT = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/03_万声科技/01_原始情报/二面面试官李立/公众号文章列表-weread.json"
BOOK_ID = "MP_WXS_3260212422"
TAB_ID = 1972143383  # weread reader 页


def eval_js(expr):
    params = json.dumps({"expression": expr, "tabId": TAB_ID}, ensure_ascii=False)
    r = subprocess.run([NODE, CLI, "call", "--tool", "javascript_eval", "--params", params],
                       capture_output=True, text=True, timeout=90)
    m = re.search(r'"value": (".*?")\n', r.stdout, re.S)
    if not m:
        raise RuntimeError("no value: " + r.stdout[-400:] + r.stderr[-200:])
    return json.loads(json.loads(m.group(1)))


PAGE_JS = ('fetch("/web/mp/articles?bookId=%s&offset=%d",{credentials:"include"})'
           '.then(r=>r.json())'
           '.then(o=>{if(o.errCode)return JSON.stringify({errCode:o.errCode});'
           'const items=[];(o.reviews||[]).forEach(g=>{(g.subReviews||[]).forEach(s=>{'
           'const r=s.review||{},mi=r.mpInfo||{};if(mi.title)items.push({t:r.createTime||g.createTime||0,'
           'title:mi.title,url:mi.originalId?("https://mp.weixin.qq.com/s/"+mi.originalId.replace(/~/g,"_")):"",'
           'rid:r.reviewId||""})})});'
           'return JSON.stringify({reviews:(o.reviews||[]).length,items:items})})')

all_items, seen, offset, page = [], set(), 0, 0
while True:
    page += 1
    try:
        res = eval_js(PAGE_JS % (BOOK_ID, offset))
    except Exception as e:
        print(f"page {page} eval fail: {str(e)[:150]}")
        time.sleep(5)
        res = eval_js(PAGE_JS % (BOOK_ID, offset))  # 重试一次
    if res.get("errCode") is not None:
        print(f"page {page} errCode={res['errCode']}, stop")
        break
    items = res.get("items", [])
    new = [it for it in items if it["url"] and it["url"] not in seen]
    for it in new:
        seen.add(it["url"])
    all_items += new
    reviews = res.get("reviews", 0)
    print(f"page {page} offset={offset} reviews={reviews} new={len(new)} cum={len(all_items)}", flush=True)
    if reviews == 0 or not new:
        break
    offset += reviews
    time.sleep(3)

all_items.sort(key=lambda x: x["t"])
json.dump(all_items, open(OUT, "w"), ensure_ascii=False, indent=1)
print(f"DONE total={len(all_items)} saved={OUT}")
