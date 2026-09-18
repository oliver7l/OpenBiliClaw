#!/usr/bin/env python3
"""生成「乐仔相册 · 手机入口」一张图（局域网 + 公网两枚二维码）。

为什么要有这个脚本：入口图里的**局域网地址会把本机 IP 写死**，而家用路由器
的 DHCP 租约一变（换网 / 重启路由），旧二维码就扫不通了。留个脚本，换网后
一条命令重新出一张，不必去翻聊天记录找当时的命令。

二维码按**整数模块尺寸**绘制（先 probe 出 ``modules_count`` 再定 ``box_size``），
避免整图缩放造成模块边缘模糊、手机扫不出来。

用法::

    .venv/bin/python scripts/build_album_entry_card.py
    .venv/bin/python scripts/build_album_entry_card.py --domain lezai.odn.cc --port 8420
    .venv/bin/python scripts/build_album_entry_card.py --out /tmp/card.png
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

import qrcode  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

W, H = 1240, 1030
BG = "#f4f6f9"
CARD = "#ffffff"
INK = "#16181d"
MUTED = "#6b7280"
FAINT = "#9aa3af"
GREEN = "#0e9f6e"
BLUE = "#3b6fd4"

HAN = "/System/Library/Fonts/STHeiti Medium.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"

CARD_Y0, CARD_Y1 = 190, 870
QR_TARGET = 400


def detect_lan_ip() -> str:
    """取本机在局域网里的 IPv4：先问 ipconfig，再退回 UDP 探路法。"""
    for iface in ("en0", "en1"):
        try:
            out = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
            if out:
                return out
        except Exception:  # noqa: BLE001 - 探测失败就换下一种方式
            pass
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))  # 不发包，只为让内核选出出口网卡
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def build_qr(url: str, target: int = QR_TARGET, border: int = 3) -> Image.Image:
    """按整数模块尺寸生成二维码（缩放会毁掉可扫性）。"""
    probe = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=border)
    probe.add_data(url)
    probe.make(fit=True)
    total = probe.modules_count + 2 * border
    box = max(4, target // total)

    q = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, border=border, box_size=box
    )
    q.add_data(url)
    q.make(fit=True)
    return q.make_image(fill_color=INK, back_color="white").convert("RGB")


def render(lan_url: str, pub_url: str, out: Path) -> None:
    f_title = ImageFont.truetype(HAN, 58)
    f_sub = ImageFont.truetype(HAN, 27)
    f_label = ImageFont.truetype(HAN, 33)
    f_url = ImageFont.truetype(MONO, 25)
    f_hint = ImageFont.truetype(HAN, 26)
    f_foot = ImageFont.truetype(HAN, 25)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((W // 2, 72), "乐仔相册 · 手机入口", font=f_title, fill=INK, anchor="mm")
    d.text(
        (W // 2, 132),
        "扫码 → 输密码 → 建议「添加到主屏幕」，用起来跟 App 一样",
        font=f_sub,
        fill=MUTED,
        anchor="mm",
    )

    cards = [
        {
            "x0": 40,
            "x1": 600,
            "accent": GREEN,
            "label": "① 家里 WiFi · 推荐",
            "url": lan_url,
            "hint": "手机连自家 WiFi 后扫这张",
            "hint2": "无证书提示，微信里也能直接开",
        },
        {
            "x0": 640,
            "x1": 1200,
            "accent": BLUE,
            "label": "② 外网 / 蜂窝流量",
            "url": pub_url,
            "hint": "在任何网络下都能扫",
            "hint2": "会提示「不安全」→ 点「继续访问」",
        },
    ]

    for c in cards:
        cx = (c["x0"] + c["x1"]) // 2
        d.rounded_rectangle(
            (c["x0"], CARD_Y0, c["x1"], CARD_Y1),
            radius=30,
            fill=CARD,
            outline="#e3e7ee",
            width=2,
        )

        lw = d.textlength(c["label"], font=f_label)
        d.rounded_rectangle((cx - lw / 2 - 34, 214, cx + lw / 2 + 34, 272), radius=29, fill=c["accent"])
        d.text((cx, 243), c["label"], font=f_label, fill="#ffffff", anchor="mm")

        qr = build_qr(c["url"])
        qx, qy = cx - qr.width // 2, 302
        img.paste(qr, (qx, qy))
        d.rectangle((qx, qy, qx + qr.width - 1, qy + qr.height - 1), outline="#e3e7ee", width=1)

        d.text((cx, qy + qr.height + 42), c["url"], font=f_url, fill=INK, anchor="mm")
        d.text((cx, qy + qr.height + 92), c["hint"], font=f_hint, fill=MUTED, anchor="mm")
        d.text((cx, qy + qr.height + 130), c["hint2"], font=f_hint, fill=FAINT, anchor="mm")

    foot_y = 918
    d.line((60, foot_y - 26, W - 60, foot_y - 26), fill="#e3e7ee", width=2)
    foot = [
        "密码就是你平时访问 8420 的那个密码（两条路共用）。",
        "公网那条现在用的是临时自签证书，浏览器警告点「继续访问」即可，9/24 前会换成正式证书。",
        "iPhone：Safari 打开 → 分享 → 添加到主屏幕；安卓：Chrome → 三点菜单 → 添加到主屏幕。",
    ]
    for i, line in enumerate(foot):
        d.text((W // 2, foot_y + i * 34), line, font=f_foot, fill=MUTED, anchor="mm")

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成乐仔相册手机入口卡（双二维码）")
    ap.add_argument("--port", default="8420", help="本机服务端口（默认 8420）")
    ap.add_argument("--domain", default="lezai.odn.cc", help="公网域名（默认 lezai.odn.cc）")
    ap.add_argument("--lan-ip", default=None, help="手动指定局域网 IP，默认自动探测")
    ap.add_argument(
        "--out",
        default=str(_REPO_ROOT / "docs" / "album-手机入口.png"),
        help="输出图片路径",
    )
    args = ap.parse_args()

    lan_ip = args.lan_ip or detect_lan_ip()
    lan_url = f"http://{lan_ip}:{args.port}/album/"
    pub_url = f"https://{args.domain}/album/"

    out = Path(args.out)
    render(lan_url, pub_url, out)

    print(f"已生成: {out}")
    print(f"  局域网: {lan_url}")
    print(f"  公网  : {pub_url}")
    print("提示: 二维码务必用解码器实测一次（cv2.QRCodeDetector），别只看外观。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
