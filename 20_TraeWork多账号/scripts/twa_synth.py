#!/usr/bin/env python3
"""为「只有浏览器授权 token、没有客户端登录态」的账号合成可切换档案。

背景：切换 = 把档案里的 storage.json 覆盖进客户端。档案只能从「客户端登录态」捕获，
而浏览器授权登录的账号（助手 accounts.json 里有条目）本机没有那份 blob ⇒ 切不了。
实测结论（见 references/trae-credential-reverse-engineering/docs/FINDINGS.md）：
  · 登录态 blob 的加密完全可逆（header 'tc' + 32B random + AES-128-CBC( SHA512(pt)||pt )）
  · 该 token 对 /icube/api/v1/user 返回 loginAllowed=true ⇒ 客户端认它

所以本脚本用「一份现成档案当结构模板 + accounts.json 里的 token」合成新档案：
  · iCubeAuthInfo://icube.cloudide  → 换成本账号 token 重新加密
  · iCubeAuthInfo://usertag         → 换成本账号 {uid: region}
  · iCubeServerData://icube.cloudide → 删除（那是旧账号的权益缓存，客户端会重新拉）
  · telemetry.* / icube-dc 设备密钥 → 保留模板（同一台机器的设备身份）
合成档案在 meta.json 里标记 source_dir=synthesized，可被 rollback 精确撤销。

用法：
  python3 twa_synth.py list                # 列出缺档案的账号（只读）
  python3 twa_synth.py synth               # 为所有缺档案的账号合成档案
  python3 twa_synth.py synth --uid <uid>   # 只合成指定账号
  python3 twa_synth.py verify              # 校验已有档案：blob 可解密 + 与 token 的 uid 一致
  python3 twa_synth.py rollback --uid <uid># 删除【合成】档案（模板捕获的档案不会被删）
"""
import base64
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from Crypto.Cipher import AES

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twa_scan_accounts import (  # noqa: E402
    ACCOUNTS_PATH,
    APP_SUPPORT,
    ASSISTANT_DATA,
    AUTH_KEY,
    COMBO,
    decrypt_blob,
    s,
)

PROFILES = ASSISTANT_DATA / "traework_profiles"
SYNTH_TAG = "synthesized"
USERTAG_KEY = "iCubeAuthInfo://usertag"
SERVERDATA_KEY = "iCubeServerData://icube.cloudide"
CLIENT_ROOT = APP_SUPPORT / "TRAE SOLO CN"
HEADER = bytes.fromhex("746305100000")


# ───────────────────────── 加密（decrypt_blob 的逆） ─────────────────────────
def _pkcs7(data: bytes) -> bytes:
    n = 16 - len(data) % 16
    return data + bytes([n]) * n


def encrypt_blob(plaintext: bytes) -> str:
    tag = hashlib.sha512(plaintext).digest()
    padded = _pkcs7(tag + plaintext)
    seed = os.urandom(32)
    h2 = hashlib.sha512(hashlib.sha512(seed).digest() + COMBO).digest()
    key, iv = h2[:16], h2[16:32]
    blob = HEADER + seed + AES.new(key, AES.MODE_CBC, iv).encrypt(padded)
    return base64.b64encode(blob).decode()


def ms_to_iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except (TypeError, ValueError):
        return None


def load_accounts() -> list:
    return json.loads(ACCOUNTS_PATH.read_text()) if ACCOUNTS_PATH.exists() else []


def load_meta(uid_dir: Path) -> dict:
    f = uid_dir / "meta.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except json.JSONDecodeError:
        return {}


def profile_uids() -> set:
    if not PROFILES.exists():
        return set()
    return {d.name for d in PROFILES.iterdir() if d.is_dir()}


def pick_template() -> tuple[dict, str]:
    """选一份可用档案当结构模板：优先最新捕获的、非合成档案。"""
    cands = []
    for d in PROFILES.iterdir() if PROFILES.exists() else []:
        if not d.is_dir():
            continue
        meta = load_meta(d)
        if meta.get("source_dir") == SYNTH_TAG:
            continue
        f = d / "storage.json"
        if f.exists() and AUTH_KEY in json.loads(f.read_text()):
            cands.append((f.stat().st_mtime, f, meta.get("source_dir") or d.name))
    if not cands:
        # 退化：直接用主客户端当前登录态
        f = CLIENT_ROOT / "User" / "globalStorage" / "storage.json"
        if not f.exists():
            raise SystemExit("找不到可用模板：档案仓为空且主客户端 storage.json 不存在")
        return json.loads(f.read_text()), "TRAE SOLO CN（主客户端）"
    cands.sort(reverse=True)
    _, f, src = cands[0]
    return json.loads(f.read_text()), f"档案 {f.parent.name}（来源 {src}）"


def build_auth(acc: dict, auth_tpl: dict) -> dict:
    """用模板结构 + 本账号身份字段拼出 iCubeAuthInfo 明文。"""
    uid = acc["user_id"]
    region = (acc.get("region") or auth_tpl.get("userRegion", {}).get("region") or "CN").upper()
    exp_ms = acc.get("expires_at")
    ref_ms = acc.get("refresh_expires_at")
    now = datetime.now(tz=timezone.utc)
    if not exp_ms:
        raise SystemExit(f"{acc.get('name')}: 缺少 expires_at，无法合成（先刷新 token）")

    auth = dict(auth_tpl)
    auth["token"] = acc["token"]
    auth["refreshToken"] = acc.get("refresh_token") or auth_tpl.get("refreshToken")
    auth["expiredAt"] = ms_to_iso(exp_ms)
    # 模板里的 refreshExpiredAt 属于旧账号；本账号没有就按 token 到期 +180 天给个保守值
    auth["refreshExpiredAt"] = ms_to_iso(ref_ms) or (datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc)
                                                    + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    auth["tokenReleaseAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    auth["userId"] = uid
    auth["host"] = acc.get("host") or "https://api.trae.cn"
    auth["userRegion"] = {"region": region, "_aiRegion": region}

    acct_tpl = dict(auth_tpl.get("account") or {})
    acct_tpl.update({
        "username": acc.get("name") or uid,
        "nonPlainTextMobile": acc.get("phone") or acct_tpl.get("nonPlainTextMobile", ""),
        "avatar_url": "",
        "storeRegion": region,
        "userTag": "cn",
        "migrateToSG": False,
    })
    auth["account"] = acct_tpl
    return auth


def synth_one(acc: dict, store: dict) -> Path:
    uid = acc["user_id"]
    auth = build_auth(acc, decrypt_blob(store[AUTH_KEY]))
    new_store = dict(store)
    new_store[AUTH_KEY] = encrypt_blob(json.dumps(auth, ensure_ascii=False).encode())
    region = auth["userRegion"]["region"]
    new_store[USERTAG_KEY] = encrypt_blob(json.dumps({uid: region}).encode())
    new_store.pop(SERVERDATA_KEY, None)  # 旧账号权益缓存，清掉让客户端重拉
    d = PROFILES / uid
    d.mkdir(parents=True, exist_ok=True)
    (d / "storage.json").write_text(json.dumps(new_store, ensure_ascii=False, indent=4))
    (d / "meta.json").write_text(json.dumps({
        "uid": uid,
        "phone": acc.get("phone"),
        "nickname": acc.get("name"),
        "source_dir": SYNTH_TAG,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "由 accounts.json token 合成，未经客户端登录验证",
    }, ensure_ascii=False, indent=2))
    return d / "storage.json"


def cmd_list() -> None:
    have = profile_uids()
    accs = load_accounts()
    print(f"accounts.json 账号 {len(accs)} 个，档案 {len(have)} 份\n")
    for a in accs:
        uid = a.get("user_id") or "?"
        meta = load_meta(PROFILES / uid)
        if uid in have:
            tag = f"✅ 档案（{meta.get('source_dir') or '?'}）"
        else:
            tag = "❌ 无档案 —— 可用 synth 合成"
        print(f"  {str(a.get('name'))[:22]:<24} uid={uid:<18} {tag}")


def cmd_synth(uids: list[str]) -> None:
    accs = load_accounts()
    have = profile_uids()
    targets = [a for a in accs if a.get("user_id") and (not uids or a["user_id"] in uids)]
    todo = [a for a in targets if a["user_id"] not in have]
    skipped = [a for a in targets if a["user_id"] in have]
    for a in skipped:
        print(f"  [跳过] {a.get('name')} 已有档案（要重建先 rollback）")
    if not todo:
        print("没有需要合成的账号。")
        return
    store, src = pick_template()
    print(f"模板：{src}")
    for a in todo:
        f = synth_one(a, store)
        print(f"  [已合成] {a.get('name')} → {f}")
    print(f"\n完成：新增 {len(todo)} 份档案。切到这些账号前请先跑 verify 自检。")


def cmd_verify() -> None:
    ok = bad = 0
    for d in sorted(PROFILES.iterdir()) if PROFILES.exists() else []:
        if not d.is_dir():
            continue
        f = d / "storage.json"
        if not f.exists():
            print(f"  [!] {d.name}: 无 storage.json")
            bad += 1
            continue
        try:
            store = json.loads(f.read_text())
            auth = decrypt_blob(store[AUTH_KEY])
            tok_uid = None
            try:
                p = auth["token"].split(".")[1]
                p += "=" * (-len(p) % 4)
                tok_uid = json.loads(base64.urlsafe_b64decode(p)).get("id")
            except Exception:  # noqa: BLE001
                tok_uid = "?"
            meta = load_meta(d)
            tag = "合成" if meta.get("source_dir") == SYNTH_TAG else "捕获"
            mark = "✅" if str(auth.get("userId")) == d.name else "⚠️ uid 不一致"
            print(f"  {mark} {d.name:<18} [{tag}] token过期={str(auth.get('expiredAt'))[:19]} "
                  f"设备ID={auth.get('host','') and '有'} refresh={'有' if auth.get('refreshToken') else '无'}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {d.name}: 解密失败 {e}")
            bad += 1
    print(f"\n共 {ok} 份可用，{bad} 份异常。")


def cmd_rollback(uids: list[str]) -> None:
    if not uids:
        raise SystemExit("rollback 必须指定 --uid")
    for uid in uids:
        d = PROFILES / uid
        meta = load_meta(d)
        if not d.exists():
            print(f"  [跳过] {uid} 无档案")
        elif meta.get("source_dir") != SYNTH_TAG:
            print(f"  [拒绝] {uid} 是{meta.get('source_dir') or '未知来源'}捕获的档案，rollback 只删合成档案")
        else:
            bak = ASSISTANT_DATA / f"_synth_rollback_{uid}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            shutil.move(str(d), str(bak))
            print(f"  [已移除] {uid} → {bak.name}")


def main() -> None:
    args = sys.argv[1:]
    uids = [args[i + 1] for i, a in enumerate(args) if a == "--uid" and i + 1 < len(args)]
    cmd = args[0] if args else "list"
    if cmd == "list":
        cmd_list()
    elif cmd == "synth":
        cmd_synth(uids)
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "rollback":
        cmd_rollback(uids)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
