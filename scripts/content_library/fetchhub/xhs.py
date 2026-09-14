"""小红书通道：xhslink 短链自动解析（自带 xsec_token）→ xhs CLI read。"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
import urllib.request

from .core import ChannelError, UnifiedDoc, bj_ts, now_bj

XHS_BIN = os.environ.get("OBC_XHS_BIN", "/Users/imac/.local/bin/xhs")
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def resolve_shortlink(url: str) -> str:
    """xhslink.cn 短链 → 真实地址（urllib 默认跟随 302；2026-09-14 实测可解析出带 token 的 URL）。"""
    if "xhslink" not in (url or ""):
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            final = r.geturl()
    except Exception as e:
        raise ChannelError("network", f"短链解析失败: {e}", "xhs-cli") from e
    if "xiaohongshu.com" not in final:
        raise ChannelError("parse", f"短链解析结果异常: {final[:120]}", "xhs-cli")
    return final


def extract_token(url: str):
    qs = urllib.parse.urlparse(url).query
    toks = urllib.parse.parse_qs(qs).get("xsec_token", [])
    return toks[0] if toks else None


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}


def cli_fetch(url: str) -> UnifiedDoc:
    real = resolve_shortlink(url)
    token = extract_token(real)
    if not token:
        raise ChannelError(
            "parse",
            "URL 中无 xsec_token——需要分享短链（xhslink.cn）或带 token 的完整链接（user_posts 列表 token 不可用）",
            "xhs-cli",
        )
    try:
        proc = subprocess.run(
            [XHS_BIN, "read", real, "--xsec-token", token, "--json"],
            capture_output=True,
            text=True,
            env=_clean_env(),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise ChannelError("network", "xhs CLI 超时（120s）", "xhs-cli") from None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        raise ChannelError("parse", f"xhs CLI 退出码 {proc.returncode}: {err}", "xhs-cli")
    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ChannelError("parse", f"xhs CLI 输出非 JSON: {e}", "xhs-cli") from e
    items = ((d.get("data") or {}).get("items")) or []
    if not items:
        raise ChannelError("parse", f"xhs 返回无 items: {json.dumps(d, ensure_ascii=False)[:200]}", "xhs-cli")
    item = items[0]
    nc = item.get("note_card") or {}
    desc = (nc.get("desc") or "").strip()
    title = (nc.get("title") or "").strip() or (desc[:30] + "…" if desc else "")
    if not desc and not title:
        raise ChannelError("parse", "xhs 笔记无标题无正文（可能已删除或风控）", "xhs-cli")
    user = nc.get("user") or {}
    published = bj_ts(nc.get("time") or nc.get("timestamp"))
    interact = nc.get("interact_info") or {}
    images = [im.get("url_default") or im.get("url") for im in nc.get("image_list") or [] if isinstance(im, dict)]
    images = [u for u in images if u]
    return UnifiedDoc(
        url=url,
        platform="xhs",
        title=title or "(无标题)",
        author=user.get("nick_name") or user.get("nickname") or user.get("user_id"),
        published_at=published,
        content_md=desc,
        replies=[],
        images=images,
        fetched_via="xhs-cli",
        fetched_at=now_bj(),
        confidence="full" if published else "partial",
        extra={
            "note_id": item.get("id"),
            "user_id": user.get("user_id"),
            "liked_count": interact.get("liked_count"),
            "collected_count": interact.get("collected_count"),
            "comment_count": interact.get("comment_count"),
            "resolved_url": real,
            "note": "评论需另行抓取；图片 CDN 带签名会过期，长期保存须落盘",
        },
    )
