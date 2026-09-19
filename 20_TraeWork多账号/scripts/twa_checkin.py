#!/usr/bin/env python3
"""TRAE 账号签到 / 余额刷新（纯脚本，不需要打开助手 App）。

鉴权方式（逆向实证，见 references/trae-credential-reverse-engineering/docs/FINDINGS.md）：
    Authorization: Cloud-IDE-JWT <token>     ← 注意前缀不是 Bearer，用错会返回 code 1001
    x-device-id: <数字设备ID>                 ← 取自 storage.json 里 iCubeAuthInfo://icube-dc:<did> 的键名

接口：
    POST /trae/api/v2/ug/checkin_credits/status   → {enable, checked_in, credits}
    POST /trae/api/v2/ug/checkin_credits/claim    → code=0 为成功；9090 = 活动暂不可用
    POST /trae/api/v2/pay/ide_user_ent_usage      → 余额 = total_amount - consumed_amount

用法：
    python3 twa_checkin.py checkin           # 只签到
    python3 twa_checkin.py credits           # 只刷新 accounts.json 里的额度快照
    python3 twa_checkin.py all               # 签到 + 刷新快照（默认）
    python3 twa_checkin.py all --dry-run     # 只看不写
    python3 twa_checkin.py checkin --uid X   # 只处理一个账号
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twa_scan_accounts import (  # noqa: E402
    ACCOUNTS_PATH,
    APP_SUPPORT,
    backup_accounts,
    validate_entry,
)

HOST = "https://api.trae.cn"
CHECKIN_STATUS = "/trae/api/v2/ug/checkin_credits/status"
CHECKIN_CLAIM = "/trae/api/v2/ug/checkin_credits/claim"
ENT_USAGE = "/trae/api/v2/pay/ide_user_ent_usage"
UA = "TRAE SOLO CN/1.107.1"


def find_device_id() -> str:
    """真实设备ID = icube-dc 键名里的数字；主客户端优先，其次任意档案。"""
    cands = [APP_SUPPORT / "TRAE SOLO CN" / "User" / "globalStorage" / "storage.json"]
    prof = APP_SUPPORT / "cn.traework.assistant" / "traework_profiles"
    if prof.exists():
        cands += sorted(prof.glob("*/storage.json"))
    for p in cands:
        if not p.exists():
            continue
        try:
            for k in json.loads(p.read_text()):
                if k.startswith("iCubeAuthInfo://icube-dc:"):
                    return k.rsplit(":", 1)[1]
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("找不到设备ID：本机没有可读的 icube-dc 键")


def api(token: str, path: str, did: str) -> tuple[int | str, dict]:
    req = urllib.request.Request(
        HOST + path,
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Cloud-IDE-JWT {token}",
            "x-device-id": did,
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return "ERR", {"message": str(e)[:120]}


def snapshot_from_usage(j: dict) -> dict | None:
    """把 ide_user_ent_usage 的响应折算成助手的 credit_snapshot 格式。"""
    us = j.get("usage_summary") or {}
    total, consumed = us.get("total_amount"), us.get("consumed_amount")
    if total is None:
        return None
    earliest = None
    now = datetime.now().timestamp()
    for p in j.get("user_entitlement_pack_list") or []:
        end = ((p.get("entitlement_base_info") or {}).get("end_time"))
        if end and end > now and (earliest is None or end < earliest):
            earliest = end
    return {
        "credits": int(round(float(total) - float(consumed or 0))),
        "unlimited": False,
        "earliest_expiry_ms": int(earliest * 1000) if earliest else None,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def checkin_account(acc: dict, did: str, dry_run: bool) -> dict:
    name = acc.get("name") or acc.get("phone") or acc.get("user_id")
    st, sj = api(acc["token"], CHECKIN_STATUS, did)
    if st != 200 or sj.get("code") not in (0, None):
        return {"name": name, "state": "鉴权失败", "detail": f"HTTP {st} {sj.get('message') or sj.get('code')}"}
    if not sj.get("enable", True):
        return {"name": name, "state": "活动不可用", "detail": "服务端 enable=false（通常需先在客户端登录激活）"}
    if sj.get("checked_in") or sj.get("did_checked_in"):
        return {"name": name, "state": "已签到", "detail": f"累计 {sj.get('credits')}"}
    if dry_run:
        return {"name": name, "state": "待签到", "detail": "(dry-run)"}
    cs, cj = api(acc["token"], CHECKIN_CLAIM, did)
    if cs == 200 and cj.get("code") == 0:
        _, sj2 = api(acc["token"], CHECKIN_STATUS, did)
        return {"name": name, "state": "✅ 签到成功", "detail": f"累计 {sj2.get('credits')}"}
    return {"name": name, "state": "签到失败", "detail": f"code={cj.get('code')} {cj.get('message')}"}


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    uids = [args[i + 1] for i, a in enumerate(args) if a == "--uid" and i + 1 < len(args)]
    cmd = next((a for a in args if a in ("checkin", "credits", "all")), "all")

    accounts = json.loads(ACCOUNTS_PATH.read_text())
    targets = [a for a in accounts if a.get("token") and (not uids or a.get("user_id") in uids)]
    did = find_device_id()
    print(f"设备ID {did} ／ 目标账号 {len(targets)} 个 ／ 模式 {cmd}{'（dry-run）' if dry_run else ''}\n")

    failed = 0
    if cmd in ("checkin", "all"):
        for a in targets:
            r = checkin_account(a, did, dry_run)
            if r["state"] in ("鉴权失败", "签到失败"):
                failed += 1
            print(f"  {str(r['name'])[:22]:<24} {r['state']:<10} {r['detail']}")
            print()

    if cmd in ("credits", "all"):
        print("── 余额刷新 ──")
        changed = 0
        for a in targets:
            st, j = api(a["token"], ENT_USAGE, did)
            snap = snapshot_from_usage(j) if st == 200 else None
            old = (a.get("credit_snapshot") or {}).get("credits")
            if snap is None:
                print(f"  {str(a.get('name'))[:22]:<24} 无法获取（HTTP {st} {j.get('message') or ''}）")
                continue
            if snap["credits"] != old:
                changed += 1
                if not dry_run:
                    a["credit_snapshot"] = snap
            print(f"  {str(a.get('name'))[:22]:<24} {old} → {snap['credits']}")
        if changed and not dry_run:
            backup_accounts(accounts)
            problems = [(i, e) for i, en in enumerate(accounts) for e in [validate_entry(en)] if e]
            if problems:
                raise SystemExit(f"⚠️ 自检未通过，拒绝写入：{problems[:2]}")
            ACCOUNTS_PATH.write_text(json.dumps(accounts, ensure_ascii=False, indent=2))
            print(f"\n已写回 accounts.json（{changed} 个账号余额更新，已备份）")
        elif changed:
            print(f"\n（dry-run：{changed} 个账号余额会更新）")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
