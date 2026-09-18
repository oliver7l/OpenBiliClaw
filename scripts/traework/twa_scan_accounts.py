#!/usr/bin/env python3
"""扫描本机所有 TRAE SOLO CN* 副本的登录态并解析账号（waxilo/TraeWorkAssistant 的账号发现增强版）。

原理与 waxilo/TraeWorkAssistant src-tauri/src/trae_auth.rs 完全一致：
  blob = base64decode(storage.json["iCubeAuthInfo://icube.cloudide"])
  seed = blob[6:38]; sha = SHA512(seed)
  combo = URE XOR DRE（两段 64 字节常量）
  hash = SHA512(sha || combo)
  key=hash[:16], iv=hash[16:32] → AES-128-CBC 解密 blob[38:]（PKCS7）
  明文前 64 字节为摘要，正文为登录态 JSON

用法：
  python3 twa_scan_accounts.py            # 只读试跑，打印各副本账号
  python3 twa_scan_accounts.py --inject   # 把新账号合并进 TraeWorkAssistant 的 accounts.json（先备份）
"""
import base64
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from Crypto.Cipher import AES

HOME = Path.home()
APP_SUPPORT = HOME / "Library" / "Application Support"
ASSISTANT_DATA = APP_SUPPORT / "cn.traework.assistant"
ACCOUNTS_PATH = ASSISTANT_DATA / "accounts.json"
AUTH_KEY = "iCubeAuthInfo://icube.cloudide"

# 与 trae_auth.rs 一致的逆向常量
URE = bytes([
    82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251, 124, 227, 57,
    130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203, 84, 123, 148, 50, 166,
    194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78, 8, 46, 161, 102, 40, 217, 36, 178,
    118, 91, 162, 73, 109, 139, 209, 37,
])
DRE = bytes([
    31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95, 96, 81, 127, 169,
    25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239, 160, 224, 59, 77, 174, 42,
    245, 176, 200, 235, 187, 60, 131, 83, 153, 97, 23, 43, 4, 126, 186, 119, 214, 38, 225,
    105, 20, 99, 85, 33, 12, 125,
])
COMBO = bytes(a ^ b for a, b in zip(URE, DRE))


def unpad(data: bytes) -> bytes:
    return data[: -data[-1]]


def decrypt_blob(b64: str) -> dict:
    blob = base64.b64decode(b64)
    if len(blob) < 38 + 16:
        raise ValueError("blob 过短")
    seed = blob[6:38]
    sha = hashlib.sha512(seed).digest()
    h2 = hashlib.sha512(sha + COMBO).digest()
    key, iv = h2[:16], h2[16:32]
    pt = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(blob[38:]))
    body = pt[64:]
    return json.loads(body.decode("utf-8"))


def s(v, key):
    x = v.get(key)
    return x.strip() if isinstance(x, str) and x.strip() else None


def parse_storage(path: Path):
    data = json.loads(path.read_text())
    blob = data.get(AUTH_KEY)
    if not isinstance(blob, str):
        return None
    auth = decrypt_blob(blob)
    token = s(auth, "token") or ""
    if len(token) < 40:
        return None
    account = auth.get("account") or {}
    return {
        "source_dir": path.parent.parent.parent.name,
        "user_id": s(auth, "userId"),
        "token": token,
        "refresh_token": s(auth, "refreshToken"),
        "host": s(auth, "host"),
        "nickname": s(account, "nickname"),
        "phone": s(account, "nonPlainTextMobile") or s(account, "mobile"),
        "region": (auth.get("userRegion") or {}).get("region") or s(account, "region"),
        "device_id": s(data, "telemetry.devDeviceId"),
        "machine_id": s(data, "telemetry.machineId"),
        "expires_at": auth.get("expiredAt"),
        "refresh_expires_at": auth.get("refreshExpiredAt"),
    }


def mask(p):
    if not p:
        return "-"
    return p[:3] + "****" + p[-2:] if len(p) >= 7 else p[:2] + "****"


def scan_all():
    roots = sorted(APP_SUPPORT.glob("TRAE SOLO CN*")) + [
        p for p in [APP_SUPPORT / n for n in ("TRAE", "Trae TRAE", "TRAE CN")] if p.exists()
    ]
    out = []
    for root in roots:
        p = root / "User" / "globalStorage" / "storage.json"
        if not p.exists():
            continue
        try:
            acc = parse_storage(p)
        except Exception as e:  # noqa: BLE001
            print(f"  [!] {root.name}: 解密失败 {e}")
            continue
        if acc:
            out.append(acc)
    return out


def to_ms_int(v):
    """TraeWork 登录态里 expiredAt/refreshExpiredAt 是字符串毫秒时间戳；
    应用的 Account 结构体（serde）要求 Option<i64> —— 字符串会让整个
    Vec<Account> 反序列化失败，load_accounts 静默返回空列表，账号全丢。"""
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def to_account_entry(acc):
    return {
        "id": str(uuid.uuid4()),
        "name": acc.get("nickname") or acc.get("phone") or "未命名账号",
        "phone": acc.get("phone"),
        "region": acc.get("region"),
        "user_id": acc.get("user_id"),
        "token": acc["token"],
        "refresh_token": acc.get("refresh_token"),
        "host": acc.get("host"),
        "expires_at": to_ms_int(acc.get("expires_at")),
        "refresh_expires_at": to_ms_int(acc.get("refresh_expires_at")),
        "device_id": acc.get("device_id"),
        "machine_id": acc.get("machine_id"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "credit_snapshot": None,
    }


def validate_entry(e):
    """按 src-tauri/src/accounts.rs 的 Account 结构体校验类型（缺失/类型错都会让
    应用端整个 Vec<Account> 解析失败 → 账号静默清空）。返回错误信息列表。"""
    errs = []
    for k, typ in (("id", str), ("name", str), ("token", str), ("created_at", str)):
        if not isinstance(e.get(k), str) or not e.get(k):
            errs.append(f"{k} 必须是非空字符串")
    for k in ("phone", "region", "user_id", "refresh_token", "host",
              "device_id", "machine_id", "credit_snapshot"):
        v = e.get(k)
        if v is not None and not isinstance(v, str) and k != "credit_snapshot":
            errs.append(f"{k} 必须是字符串或 null")
    for k in ("expires_at", "refresh_expires_at"):
        v = e.get(k)
        if v is not None and not isinstance(v, int):
            errs.append(f"{k} 必须是整数毫秒时间戳或 null（当前 {type(v).__name__}）")
    return errs


def main():
    inject = "--inject" in sys.argv
    found = scan_all()
    print(f"共发现 {len(found)} 个登录态：")
    for a in found:
        try:
            exp = int(a.get("expires_at"))
            exp_s = datetime.fromtimestamp(exp / 1000).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            exp_s = "?"
        print(f"  [{a['source_dir']}] {mask(a['phone'])} 昵称={a.get('nickname') or '-'} "
              f"uid={(a.get('user_id') or '-')[:8]}... token过期={exp_s} "
              f"refresh={'有' if a.get('refresh_token') else '无'}")
    if not inject:
        print("\n（只读试跑，未写入任何文件；加 --inject 合并进 accounts.json）")
        return
    existing = json.loads(ACCOUNTS_PATH.read_text()) if ACCOUNTS_PATH.exists() else []
    known_phones = {a.get("phone") for a in existing if a.get("phone")}
    known_tokens = {a.get("token") for a in existing}
    new = [a for a in found
           if a.get("phone") not in known_phones and a["token"] not in known_tokens]
    if not new:
        print("没有需要新增的账号。")
        return
    bak = ACCOUNTS_PATH.with_name(f"accounts.json.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    bak.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    print(f"\n已备份 → {bak.name}")
    merged = existing + [to_account_entry(a) for a in new]
    problems = [(i, errs) for i, e in enumerate(merged) for errs in [validate_entry(e)] if errs]
    if problems:
        print("⚠️ 自检未通过，拒绝写入（否则应用端会静默清空账号）：")
        for i, errs in problems:
            print(f"  条目 {i}: {'; '.join(errs)}")
        return
    ACCOUNTS_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    print(f"已写入 {len(new)} 个新账号 → {ACCOUNTS_PATH}（共 {len(merged)} 个）")


if __name__ == "__main__":
    main()
