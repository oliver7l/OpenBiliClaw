#!/usr/bin/env python3
"""91160 挂号监控：深圳宝安妇幼 · 李艳芬 · 口腔修复门诊（成人补牙镶牙）。

监控未来 14 天内「周六/周日」的可约号源（上午优先，其次下午/晚上）。
数据源：健康160 官方接口（wechatgate.91160.com），只读查询，不做任何预约操作。

凭证安全：登录令牌（user_key）与 user_id 不入库、不进 git，
从以下位置读取（优先级从高到低）：
    1. 环境变量 OBC_91160_USER_KEY / OBC_91160_USER_ID
    2. 凭证文件 ~/.workbuddy/91160-monitor/user_key.txt（第 1 行 user_key，第 2 行 user_id）

令牌获取方式：浏览器登录 weixin.91160.com 后导出 .91160.com 的 Cookie，
取 user_key_v2 的值做 base64 解码即为 user_key（有效期一般 30 天）。

用法：
    .venv/bin/python scripts/health/91160_check_slots.py          # 只看周六/周日
    .venv/bin/python scripts/health/91160_check_slots.py all      # 看所有日期

输出：人类可读简报 + 最后一行 JSON（found/slots/tip/auth_expired），供自动化判断。
"""

import base64
import json
import os
import sys
import urllib.request
from datetime import date
from pathlib import Path

# ── 目标配置（非敏感） ──────────────────────────────────────
UNIT_ID = "8"            # 深圳市宝安区妇幼保健院（91160 医院 ID）
DOC_ID = "17022"         # 李艳芬
DEP_ID = "200073410"     # 口腔修复（成人补牙、镶牙）
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
DOC_URL = "https://www.91160.com/doc/show/depid-200028293/docid-17022.html"

CRED_FILE = Path.home() / ".workbuddy" / "91160-monitor" / "user_key.txt"

WEEKDAY_FILTER = {"周六", "周日"}   # 用户要求：只监控周末
PERIOD_ORDER = {"am": 0, "pm": 1, "em": 2}
PERIOD_NAME = {"am": "上午", "pm": "下午", "em": "晚上"}


def load_credentials() -> tuple[str, str] | None:
    """读 (user_key, user_id)；user_key_v2 cookie 值是 base64，自动解码。"""
    user_key = os.environ.get("OBC_91160_USER_KEY", "").strip()
    user_id = os.environ.get("OBC_91160_USER_ID", "").strip()
    if not user_key and CRED_FILE.exists():
        lines = [ln.strip() for ln in CRED_FILE.read_text().splitlines() if ln.strip()]
        if len(lines) >= 2:
            user_key, user_id = lines[0], lines[1]
    if not user_key:
        return None
    # 兼容直接贴 cookie 原值（base64）的情况：能解码且像令牌就解
    try:
        decoded = base64.b64decode(user_key).decode()
        if all(c.isalnum() for c in decoded) and len(decoded) >= 40:
            user_key = decoded
    except Exception:  # noqa: BLE001 — 已是解码原文则跳过
        pass
    return user_key, user_id


def fetch_schedules(user_key: str, user_id: str) -> dict:
    """调 91160 接口取未来 14 天排班。"""
    url = (
        "https://wechatgate.91160.com/guahao/v1-1/sch/union/doctor"
        f"?cid=16&user_key={user_key}&account_user_id=0&dep_id={DEP_ID}"
        f"&doctor_id={DOC_ID}&all_point=1&page=1&select_date="
        f"&user_id={user_id}&unit_id={UNIT_ID}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)


def main() -> None:
    all_days = len(sys.argv) > 1 and sys.argv[1] == "all"
    today = date.today().isoformat()

    creds = load_credentials()
    if creds is None:
        print("缺少凭证：请设置 OBC_91160_USER_KEY / OBC_91160_USER_ID，"
              f"或写入 {CRED_FILE}（第1行 user_key，第2行 user_id）。")
        print(json.dumps({"found": False, "auth_expired": True}, ensure_ascii=False))
        sys.exit(0)

    try:
        payload = fetch_schedules(*creds)
    except Exception as exc:  # noqa: BLE001
        print(f"查询失败：{exc}")
        print(json.dumps({"found": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(0)

    data = payload.get("data") or {}
    err = payload.get("error_code")
    if err not in (None, "200", 200):
        msg = payload.get("error_msg", "")
        if str(err) in ("10021", "10022"):
            print("⚠️ 登录态失效（user_key 已过期或被风控），需重新导出 91160 Cookie，"
                  f"更新 {CRED_FILE}。")
            print(json.dumps({"found": False, "auth_expired": True}, ensure_ascii=False))
        else:
            print(f"接口异常 error_code={err}: {msg}")
            print(json.dumps({"found": False, "error": msg}, ensure_ascii=False))
        sys.exit(0)

    tip = data.get("put_sch_tip", "")
    slots = []
    total_weekend = 0
    for item in data.get("sch_list", []):
        day = item.get("day", "")
        week = item.get("week", "")
        sch = item.get("sch") or {}
        if not all_days and week not in WEEKDAY_FILTER:
            continue
        for pk, lst in sch.items():
            for s in lst or []:
                if day < today:
                    continue  # 跳过已过期
                if not all_days:
                    total_weekend += 1
                left = int(s.get("left_num") or 0)
                if left <= 0 or int(s.get("y_state") or 0) == -1:
                    continue
                slots.append({
                    "date": day,
                    "week": week,
                    "period": PERIOD_NAME.get(pk, pk),
                    "left": left,
                    "max": s.get("yuyue_max"),
                    "fee": s.get("guahao_amt"),
                    "schedule_id": s.get("schedule_id"),
                })

    slots.sort(key=lambda x: (x["date"], PERIOD_ORDER.get(
        next((k for k, v in PERIOD_NAME.items() if v == x["period"]), "em"))))

    if slots:
        print(f"🎉 有号了！李艳芬（口腔修复门诊）可约号源 {len(slots)} 个：")
        for s in slots:
            print(f"  · {s['date']}（{s['week']}）{s['period']} | 余号 {s['left']}/{s['max']} | ¥{s['fee']}")
        print("马上预约：健康160 App / 微信小程序「深圳市宝安区妇幼保健院」")
        print(f"医生页：{DOC_URL}")
    else:
        scope = "近 14 天" if all_days else "近 14 天内的周六/周日"
        print(f"暂无可约号源（{scope}，检查了 {total_weekend} 个周末班次）。")
        if tip:
            print(f"放号提示：{tip}")

    print(json.dumps({
        "found": bool(slots),
        "slots": slots,
        "tip": tip,
        "checked_at": today,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
