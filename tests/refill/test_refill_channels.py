"""refill 通道单测：getnote（同步 save + 异步回收）/ ytdlp / bili / zhihu。

各通道通过 runner 注入规避真实子进程；ytdlp 只测纯逻辑（分类/vid 解析/vtt 解析），
它的子进程编排由既有脚本内核承担、不在主干单测范围。
"""

from __future__ import annotations

import json
import subprocess

import pytest

from openbiliclaw.refill.channels.base import BridgeUnavailableError, PERMANENT
from openbiliclaw.refill.channels.bili_cli import BiliCliChannel
from openbiliclaw.refill.channels.getnote import GetnoteChannel
from openbiliclaw.refill.channels.ytdlp import _classify, _extract_vid, _parse_vtt
from openbiliclaw.refill.channels.zhihu_api import ZhihuApiChannel

_BODY = "好的，这是一段超过三十个字符的正文内容，用来通过最小正文字数的判定阈值。"


def _ok_runner(args, timeout):
    payload = '{"data":{"note":{"content":"%s"}}}' % _BODY
    return subprocess.CompletedProcess(args, 0, payload, "")


def _empty_runner(args, timeout):
    return subprocess.CompletedProcess(args, 0, "{}", "")


def _quota_runner(args, timeout):
    payload = '{"error":{"reason":"quota_daily_exceeded","code":10203}}'
    return subprocess.CompletedProcess(args, 0, payload, "")


def _timeout_runner(args, timeout):
    return subprocess.CompletedProcess(args, 124, "", "timeout")


def _item(url):
    return {"id": 1, "source_type": "douyin", "url": url, "title": "标题",
            "attempts": 0, "max_attempts": 3}


def test_getnote_success_returns_body():
    ch = GetnoteChannel(runner=_ok_runner)
    ok, body, detail = ch.fetch(_item("https://v.douyin.com/abc/"))
    assert ok is True and body == _BODY
    assert detail


def test_getnote_quota_exhausted_raises():
    ch = GetnoteChannel(runner=_quota_runner)
    with pytest.raises(BridgeUnavailableError):
        ch.fetch(_item("https://v.douyin.com/abc/"))


def test_getnote_no_body_counts_as_failure():
    ch = GetnoteChannel(runner=_empty_runner)
    ok, body, detail = ch.fetch(_item("https://v.douyin.com/abc/"))
    assert ok is False and body == ""


def test_getnote_timeout_counts_as_failure():
    ch = GetnoteChannel(runner=_timeout_runner)
    ok, body, detail = ch.fetch(_item("https://v.douyin.com/abc/"))
    assert ok is False and "超时" in detail


def _getnote_dispatch(body: str, save_payload: dict):
    """按 getnote 子命令分发：save/task/note 分别回不同响应。"""

    def runner(args, timeout):
        cmd = args[1]
        if cmd == "save":
            stdout = json.dumps(save_payload, ensure_ascii=False)
        elif cmd == "task":
            stdout = '{"data":{"note_id":"n123"}}'
        elif cmd == "note":
            stdout = '{"data":{"content":"%s"}}' % body
        else:
            stdout = "{}"
        return subprocess.CompletedProcess(args, 0, stdout, "")

    return runner


def test_getnote_async_reclaim_via_note_id():
    # save 只返回 note_id（无同步正文）→ note 取回正文。
    ch = GetnoteChannel(runner=_getnote_dispatch(_BODY, {"data": {"note_id": "n123"}}))
    ok, body, detail = ch.fetch(_item("https://v.douyin.com/abc/"))
    assert ok is True and body == _BODY
    assert "回收" in detail


def test_getnote_async_reclaim_via_task_poll():
    # save 只返回 task_id → task 轮询拿 note_id → note 取回正文。
    ch = GetnoteChannel(
        runner=_getnote_dispatch(_BODY, {"data": {"task_id": "t1"}}),
        poll_rounds=1, poll_interval=0,
    )
    ok, body, detail = ch.fetch(_item("https://v.douyin.com/abc/"))
    assert ok is True and body == _BODY
    assert "回收" in detail


def test_getnote_async_no_reclaim_fails():
    # save 既无正文也无 note_id/task_id → 失败。
    ch = GetnoteChannel(runner=_getnote_dispatch(_BODY, {}), poll_rounds=0, poll_interval=0)
    ok, body, detail = ch.fetch(_item("https://v.douyin.com/abc/"))
    assert ok is False and body == ""


def test_getnote_supports_known_sources():
    ch = GetnoteChannel(runner=_ok_runner)
    assert ch.supports("douyin", "x") is True
    assert ch.supports("xiaoyuzhou", "x") is True
    assert ch.supports("youtube", "x") is True


# -- ytdlp 纯函数 ----------------------------------------------------------

def test_ytdlp_classify():
    assert _classify("ERROR: 429 Too Many Requests") == "retryable"
    assert _classify("timed out during extraction") == "retryable"
    assert _classify("Sign in to confirm you're not a bot") == "bot"
    assert _classify("not a bot") == "bot"
    assert _classify("Video unavailable") == "permanent"
    assert _classify("Subtitles are not available") == "permanent"
    assert _classify("Private video") == "permanent"
    assert _classify("Generic download error") == "unknown"


def test_ytdlp_extract_vid():
    assert _extract_vid("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_vid("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_vid("https://x.com/not-a-video") is None


def test_ytdlp_parse_vtt(tmp_path):
    vtt = tmp_path / "sub.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n<00:00:00.000>你好\n\n"
        "00:00:02.000 --> 00:00:04.000\n你好\n\nworld\n",
        encoding="utf-8",
    )
    out = _parse_vtt(str(vtt))
    assert "你好" in out
    assert out.count("你好") == 1  # 连续重复已去重


def test_ytdlp_permanent_marker():
    # 通道以 PERMANENT 前缀标记永久不可抓，供 scheduler 标 skipped。
    assert PERMANENT.endswith(":")
    assert "PERM:" in f"{PERMANENT}无字幕/不可抓"


# -- bili_cli 通道 ---------------------------------------------------------

def _bili_run(stdout: str):
    return lambda args, timeout: subprocess.CompletedProcess(args, 0, stdout, "")


def _bili_item():
    return {"id": 1, "source_type": "bilibili", "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "标题", "attempts": 0, "max_attempts": 3}


def test_bili_subtitle_success():
    stdout = '{"data":{"subtitle":{"available":true,"text":"%s"}}}' % _BODY
    ch = BiliCliChannel(runner=_bili_run(stdout))
    ok, body, detail = ch.fetch(_bili_item())
    assert ok is True and body == _BODY
    assert "字幕" in detail


def test_bili_ai_desc_fallback():
    # 简介需 ≥50 字（channel 内置阈值，镜像旧脚本）。
    long_desc = _BODY + "……补充内容以超过五十字符的阈值判定，确保简介兜底路径命中。"
    stdout = '{"data":{"video":{"description":"%s"}}}' % long_desc
    ch = BiliCliChannel(runner=_bili_run(stdout))
    ok, body, _ = ch.fetch(_bili_item())
    assert ok is True and long_desc in body


def test_bili_permanent_no_content():
    # 非空 data 但无字幕/无AI/无简介 → 永久（与「空 data=调用失败」区分开）。
    stdout = '{"data":{"subtitle":{"available":false},"video":{"description":""}}}'
    ch = BiliCliChannel(runner=_bili_run(stdout))
    ok, body, detail = ch.fetch(_bili_item())
    assert ok is False and body == ""
    assert detail.startswith(PERMANENT)  # 无字幕/无AI/无简介 → 永久


def test_bili_call_failed_is_retryable_not_permanent():
    ch = BiliCliChannel(runner=_bili_run("{}"))  # 无 data → 调用失败
    ok, body, detail = ch.fetch(_bili_item())
    assert ok is False and not detail.startswith(PERMANENT)


def test_bili_supports_and_bv_required():
    ch = BiliCliChannel(runner=_bili_run('{"data":{}}'))
    assert ch.supports("bilibili", "https://b23.tv/BV1xx411c7mD") is True
    assert ch.supports("bilibili", "https://bilibili.com/space/123") is False  # 无 BV


# -- zhihu_api 通道 --------------------------------------------------------

def _zhihu_run(stdout: str):
    return lambda args, timeout: subprocess.CompletedProcess(args, 0, stdout, "")


def _zhihu_item(url):
    return {"id": 1, "source_type": "zhihu", "url": url, "title": "标题",
            "attempts": 0, "max_attempts": 3}


def test_zhihu_answer_success():
    ch = ZhihuApiChannel(runner=_zhihu_run(_BODY))
    ok, body, detail = ch.fetch(_zhihu_item("https://www.zhihu.com/question/1/answer/2033884547610387219"))
    assert ok is True and body == _BODY
    assert "answer" in detail


def test_zhihu_article_success():
    ch = ZhihuApiChannel(runner=_zhihu_run(_BODY))
    ok, body, detail = ch.fetch(_zhihu_item("https://zhuanlan.zhihu.com/p/1234567"))
    assert ok is True
    assert "article" in detail


def test_zhihu_empty_failure():
    ch = ZhihuApiChannel(runner=_zhihu_run(""))
    ok, body, detail = ch.fetch(_zhihu_item("https://www.zhihu.com/answer/203"))
    assert ok is False and body == ""


def test_zhihu_supports_urls():
    ch = ZhihuApiChannel(runner=_zhihu_run(""))
    assert ch.supports("zhihu", "https://www.zhihu.com/answer/123") is True
    assert ch.supports("zhihu", "https://zhuanlan.zhihu.com/p/456") is True
    assert ch.supports("zhihu", "https://www.zhihu.com/people/abc") is False