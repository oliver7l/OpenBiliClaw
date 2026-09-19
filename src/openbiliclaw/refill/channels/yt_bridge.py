"""yt_bridge 通道：AgentLimb 登录态 Chrome 抓 YouTube 字幕/简介。

ytdlp 通道的桥接兜底：数据中心代理出口被 YouTube 判 bot（POT token 机制）时，
登录态 Chrome 是天然可用的抓取环境。已验证管线（2026-09-19）：

1. 桥接 navigate 到 watch 页 → 读 ``ytInitialPlayerResponse``（标题/简介/字幕轨道
   数/playability）；
2. 有字幕轨 → 驱动播放器 ``movie_player`` 加载字幕（``loadModule('captions')`` +
   ``setOption``），播放器自身携带 POT 发起 timedtext 请求 → 从
   ``performance.getEntriesByType('resource')`` 捞该 URL 重新 fetch → json3 解析；
3. 无字幕轨 → 简介兜底（≥50 字）；两者皆无 / playability 终态 → ``PERMANENT``。

**桥接 ``javascript_eval`` 不支持 ``await`` 关键字**（报 SyntaxError），所有页内
JS 用同步 IIFE；返回 Promise 的 harvest 由桥接自动 resolve（``bridge.py::_wait``
实测成立）。抓字幕会临时打开播放器字幕并在 4s 后 ``pauseVideo()``（避免打扰用户）。
"""

from __future__ import annotations

import json
import time

from openbiliclaw.refill.channels.base import PERMANENT
from openbiliclaw.refill.channels.bridge import AgentLimbBridge, _evaljs
from openbiliclaw.refill.channels.ytdlp import _VID_RE

_YOUTUBE = "youtube"
_MIN_BODY = 50
_MIN_DESC = 50
_HARVEST_TRIES = 2
_BODY_CAP = 50000
_TERMINAL_PLAYABILITY = {"LOGIN_REQUIRED", "ERROR", "UNPLAYABLE"}


def _extract_vid(url: str) -> str | None:
    m = _VID_RE.search(url or "")
    return m.group(1) if m else None


# 元数据（同步 IIFE）：playability + 简介 + 字幕轨道数。
_META_JS = """(() => {
  const pr = window.ytInitialPlayerResponse;
  if (!pr) return JSON.stringify({err: 'no player response'});
  const vd = pr.videoDetails || {};
  const tracks = (((pr.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks || []);
  return JSON.stringify({
    playability: (pr.playabilityStatus || {}).status || 'OK',
    title: vd.title || '',
    vid: vd.videoId || '',
    desc: (vd.shortDescription || '').slice(0, 5000),
    trackCount: tracks.length
  });
})()"""

# 驱动播放器加载字幕（同步）：选 zh→en 轨道 setOption 触发 timedtext 请求，
# 4 秒后暂停播放避免打扰用户。
_DRIVE_JS = """(() => {
  const p = document.getElementById('movie_player');
  if (!p || !p.loadModule) return JSON.stringify({err: 'no player api'});
  try {
    const tracklist = p.getOption('captions', 'tracklist') || [];
    if (!tracklist.length) return JSON.stringify({err: 'no tracklist'});
    let pick = null;
    for (const lang of ['zh-Hans', 'zh-CN', 'zh', 'zh-TW', 'zh-Hant', 'en']) {
      pick = tracklist.find(t => t.languageCode === lang && t.kind !== 'asr') ||
             tracklist.find(t => t.languageCode === lang);
      if (pick) break;
    }
    if (!pick) pick = tracklist[0];
    p.loadModule('captions');
    p.setOption('captions', 'track', pick);
    setTimeout(function () { try { p.pauseVideo(); } catch (e) {} }, 4000);
    return JSON.stringify({ok: true, lang: pick.languageCode, kind: pick.kind || 'manual'});
  } catch (e) { return JSON.stringify({err: String(e).slice(0, 100)}); }
})()"""

# 收割（返回 Promise，桥接自动 resolve）：从 performance 资源捞播放器发出的
# timedtext URL（自带 POT），页内 fetch + json3 解析 → 去重拼接正文。
_HARVEST_JS = """(() => {
  const sep = String.fromCharCode(10);
  const entries = performance.getEntriesByType('resource')
    .map(e => e.name).filter(n => n.indexOf('/api/timedtext') >= 0);
  if (!entries.length) return JSON.stringify({err: 'no timedtext request', body: ''});
  const url = entries[entries.length - 1];
  return fetch(url).then(r => r.text().then(raw => {
    try {
      const j = JSON.parse(raw);
      const lines = [];
      for (const ev of (j.events || [])) {
        if (!ev.segs) continue;
        const s = ev.segs.map(x => x.utf8 || '').join('').trim();
        if (s) lines.push(s);
      }
      const dedup = [];
      for (const line of lines) { if (!dedup.length || line !== dedup[dedup.length - 1]) dedup.push(line); }
      const body = dedup.join(sep).slice(0, 50000);
      return JSON.stringify({events: lines.length, body: body});
    } catch (e2) { return JSON.stringify({err: 'json parse: ' + String(e2).slice(0, 50), body: ''}); }
  })).catch(e => JSON.stringify({err: 'fetch: ' + String(e).slice(0, 60), body: ''}));
})()"""


def _eval_json(expression: str, timeout: float = 60) -> dict | None:
    """页内 evaljs 并解析 JSON 返回值；失败返回 None（不抛桥接错误）。"""
    status, val = _evaljs(expression, timeout)
    if status != "OK" or val is None:
        return None
    try:
        data = json.loads(val) if isinstance(val, str) else val
    except ValueError:
        return None
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


class YtBridgeChannel:
    """YouTube 桥接通道：登录态 Chrome 播放器字幕 / 简介兜底。"""

    name = "yt_bridge"
    requires_bridge = True

    def __init__(self, bridge: AgentLimbBridge) -> None:
        self.bridge = bridge

    def supports(self, source_type: str, url: str) -> bool:
        return source_type == _YOUTUBE and _extract_vid(url) is not None

    def fetch(self, item) -> tuple[bool, str, str]:
        url = (item.get("url") or "").strip()
        vid = _extract_vid(url)
        if not vid:
            return False, "", f"{PERMANENT}URL 无 YouTube ID"
        self.bridge._navigate(f"https://www.youtube.com/watch?v={vid}")
        time.sleep(4)

        # watch 页可能被广告/慢加载拖住，meta 允许一次补试
        meta: dict | None = None
        for _ in range(2):
            meta = _eval_json(_META_JS, 30)
            if meta and not meta.get("err"):
                break
            time.sleep(3)
        if meta is None or meta.get("err"):
            return False, "", "watch 页元数据未就绪（可重试）"
        if meta.get("trackCount", 0) == 0 and meta.get("playability") in _TERMINAL_PLAYABILITY:
            return False, "", f"{PERMANENT}视频不可播放（{meta.get('playability')}）"

        # 1) 字幕轨 → 驱动播放器 → performance 收割
        if meta.get("trackCount", 0) > 0:
            drive = _eval_json(_DRIVE_JS, 30)
            if drive and drive.get("ok"):
                for _ in range(_HARVEST_TRIES):
                    time.sleep(3)
                    res = _eval_json(_HARVEST_JS, 60)
                    body = (res or {}).get("body") or ""
                    if len(body) >= _MIN_BODY:
                        lang = drive.get("lang", "")
                        kind = drive.get("kind", "manual")
                        return True, body, f"captions lang={lang} kind={kind}"
            # 字幕轨存在但收割失败 → 落到简介兜底；简介也不够则计数重试

        # 2) 简介兜底
        desc = (meta.get("desc") or "").strip()
        if len(desc) >= _MIN_DESC:
            return True, f"【视频简介】{desc}", "fallback=description"

        if meta.get("trackCount", 0) > 0:
            return False, "", "字幕轨存在但抓取失败（可重试）"
        return False, "", f"{PERMANENT}无字幕/无简介/不可抓"
