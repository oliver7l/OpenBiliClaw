"""智联招聘岗位扫描（只读，不投递）。

用 AgentLimb 桥（127.0.0.1:7791）驱动已登录 Chrome，从页面 SSR 数据
window.__INITIAL_STATE__.positionList 直接提取结构化职位（含 JD 正文），
不做任何投递/打招呼动作。

用法：python3 zp_harvest.py <city_id> <kw1> [kw2 ...]
输出：/tmp/obc/zp_raw.json（追加去重）
"""
import json
import os
import sys
import time
from urllib.parse import quote

sys.path.insert(0, "/Users/imac/.workbuddy/skills/boss-zhipin-jobs/scripts")
from alib import call, val  # noqa: E402

RAW = "/tmp/obc/zp_raw.json"

JS = r"""JSON.stringify((window.__INITIAL_STATE__ && window.__INITIAL_STATE__.positionList || []).map(function(x){
  var b = (x.jobDetailData||{}).position || {};
  var base = b.base||{}; var desc = b.desc||{};
  var proxy = (x.jobDetailData||{}).companyProxy || {};
  var labels = desc.labels || [];
  var jd = desc.description || '';
  return {
    name: base.positionName || '',
    num: base.positionNumber || '',
    sal: base.salary || x.salary60 || '',
    exp: base.positionWorkingExp || '',
    edu: base.education || '',
    company: x.companyName || '',
    realCompany: proxy.companyName || '',
    district: x.cityDistrict || '',
    cityId: x.cityId || '',
    industry: x.industryName || '',
    size: x.companySize || '',
    fin: x.financingStage || '',
    welfare: (desc.welfareLabel||[]).join(','),
    labels: labels.join(','),
    jd: jd.slice(0, 2500),
    url: base.positionNumber ? ('https://www.zhaopin.com/jobdetail/' + base.positionNumber + '.htm') : ''
  };
}))"""


def load_raw():
    if os.path.exists(RAW):
        return json.load(open(RAW, encoding="utf-8"))
    return {}


def save_raw(d):
    json.dump(d, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def harvest(city, kws, pages=2):
    raw = load_raw()
    total_new = 0
    for kw in kws:
        for p in range(1, pages + 1):
            url = f"https://www.zhaopin.com/jobs?jl={city}&kw={quote(kw)}&p={p}"
            call("navigate", {"url": url})
            time.sleep(6.5)
            out = val(JS)
            try:
                items = json.loads(out) if isinstance(out, str) else (out or [])
            except Exception:
                items = []
            if not items:
                print(f"  !! {kw} p{p}: 0 条（可能未加载）")
            for it in items:
                it["_kw"] = kw
                it["_city"] = city
                if it["num"] and it["num"] not in raw:
                    raw[it["num"]] = it
                    total_new += 1
            print(f"  {kw} p{p}: {len(items)} 条（累计新 {total_new}）")
            time.sleep(1.2)
    save_raw(raw)
    return total_new, len(raw)


if __name__ == "__main__":
    city = sys.argv[1]
    kws = sys.argv[2:]
    n, tot = harvest(city, kws)
    print(f"完成：新增 {n}，库内共 {tot}")
