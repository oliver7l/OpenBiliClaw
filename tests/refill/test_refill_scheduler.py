"""RefillScheduler 单元测试：路由 / 不重复写 / attempts 累计 / 桥接关断。

用注入的 FakeBridge（实现 probe/grab/xhs_search_click 三接口）驱动真实
DirectChannel / SearchClickChannel；getnote 注入 ``_infra_raise`` runner（视为
getnote 基础设施关断，只算 bridge_off 不计数），避免单元测试触真实 getnote 子进程。
``write_content`` 用记录型回调统计一次成功只写一次。队列以 refill_queue 手动 seed。
"""

from __future__ import annotations

import subprocess

from openbiliclaw.config import RefillQuota
from openbiliclaw.refill import RefillQueue, RefillScheduler
from openbiliclaw.refill.channels.base import BridgeUnavailableError
from openbiliclaw.refill.channels.bridge import _XHS_READ_JS

_BODY = "好的，这是一段超过三十个字符的正文内容，用来通过最小正文字数的判定阈值。"


def _infra_raise(*_args, **_kwargs):  # noqa: ANN202
    raise BridgeUnavailableError("getnote 基础设施关断（测试注入）")


class FakeBridge:
    """三接口桥接桩：test 可配置 grab / xhs 行为。"""

    def __init__(self, *, probe=True, grab=None, xhs=None):
        self._probe = probe
        self._grab = grab
        self._xhs = xhs
        self.probe_calls = 0

    def probe(self) -> bool:
        self.probe_calls += 1
        return self._probe

    def grab(self, url: str):
        if callable(self._grab):
            return self._grab(url)
        return {"title": "标题", "author": "", "pub": "", "text": _BODY, "tags": [], "url": url}

    def xhs_search_click(self, nid: str, title: str):
        if callable(self._xhs):
            return self._xhs(nid, title)
        return "OK", _BODY


def _seed_pending(queue: RefillQueue, rows: list[dict]) -> list[int]:
    ids = []
    for r in rows:
        cur = queue.conn.execute(
            "INSERT INTO refill_queue (source_type, url, title, state, created_at, updated_at)"
            " VALUES (?, ?, ?, 'pending', datetime('now','localtime'), datetime('now','localtime'))"
            " RETURNING id",
            (r["source_type"], r["url"], r.get("title", "")),
        ).fetchone()
        ids.append(int(cur["id"]))
    queue.conn.commit()
    return ids


def _quota(per_cycle: int) -> dict:
    return {"xiaohongshu": RefillQuota(per_cycle=per_cycle, interval_min=0)}


def _make_queue(tmp_path) -> RefillQueue:
    return RefillQueue(tmp_path / "refill.db", tmp_path / "content.db")


def _build_scheduler(queue, *, bridge=None, write=None, per_cycle=1, getnote_runner=_infra_raise, quota=None):
    return RefillScheduler(
        queue,
        min_body_len=30,
        quota=quota or _quota(per_cycle),
        write_content=write or (lambda item, body: None),
        bridge=bridge,
        getnote_runner=getnote_runner,
    )


def test_success_writes_once_and_marks_done(tmp_path):
    queue = _make_queue(tmp_path)
    with queue:
        (row_id,) = _seed_pending(
            queue,
            [{"source_type": "xiaohongshu", "url": "https://xhs.com/explore/noteA", "title": "搜得到"}],
        )
        written: list[tuple] = []
        bridge = FakeBridge()  # xhs 返回 OK+正文
        scheduler = _build_scheduler(queue, bridge=bridge, write=lambda i, b: written.append((i["url"], b)))
        summary = scheduler.run_cycle()
        assert summary["xiaohongshu"]["done"] == 1
        assert summary["xiaohongshu"]["picked"] == 1
        assert len(written) == 1 and written[0][0] == "https://xhs.com/explore/noteA"
        row = queue.conn.execute("SELECT state, fetched_len, channel FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        assert row["state"] == "done" and row["channel"] == "search_click"
        assert row["fetched_len"] == len(_BODY)

        summary2 = scheduler.run_cycle()
        assert summary2["xiaohongshu"]["picked"] == 0
        assert len(written) == 1  # 并发跑两轮同一 URL，不重复写


def test_attempts_accumulate_then_drop(tmp_path):
    queue = _make_queue(tmp_path)
    with queue:
        (row_id,) = _seed_pending(
            queue,
            [{"source_type": "xiaohongshu", "url": "https://xhs.com/explore/noteB?xsec_token=k", "title": "抓不到"}],
        )
        written = []
        bridge = FakeBridge(grab=lambda url: {"title": "标题", "text": ""})  # direct 正文为空 → 失败
        # getnote 注入「未返回正文」runner → 也记一次失败（验证 fallback 链计数）。
        empty = lambda *a, **k: subprocess.CompletedProcess(list(a[0]), 0, "{}", "")  # noqa: E731
        scheduler = _build_scheduler(queue, bridge=bridge, write=lambda i, b: written.append(i["url"]),
                                     getnote_runner=empty)
        # 第一轮：direct 失败 + getnote 失败 → attempts=2，仍 pending
        s1 = scheduler.run_cycle()
        assert s1["xiaohongshu"]["failed"] == 1  # 本轮记一次失败（direct 后 getnote 兜底）
        assert s1["xiaohongshu"]["done"] == 0
        row = queue.conn.execute("SELECT attempts, state FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        assert row["attempts"] == 2 and row["state"] == "pending"
        # 第二轮：direct attempts 达 max_attempts(=3) → dropped
        s2 = scheduler.run_cycle()
        assert s2["xiaohongshu"]["dropped"] == 1
        row = queue.conn.execute("SELECT attempts, state FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        assert row["attempts"] == 3 and row["state"] == "dropped"
        assert written == []


def test_bridge_probe_off_skips_agentlimb_item_without_count(tmp_path):
    queue = _make_queue(tmp_path)
    with queue:
        # v2ex → 路由仅 direct（requires_bridge）。桥接关断 → 整条不能用 AgentLimb，标 bridge_off。
        (row_id,) = _seed_pending(
            queue,
            [{"source_type": "v2ex", "url": "https://v2ex.com/t/1", "title": "任意"}],
        )
        bridge = FakeBridge(probe=False)
        quota = {"v2ex": RefillQuota(per_cycle=1, interval_min=0)}
        scheduler = _build_scheduler(queue, bridge=bridge, quota=quota)
        summary = scheduler.run_cycle(sources=("v2ex",))
        assert summary["v2ex"]["bridge_off"] == 1
        assert summary["v2ex"]["picked"] == 1
        assert summary["v2ex"]["done"] == 0
        row = queue.conn.execute("SELECT attempts, state FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        assert row["attempts"] == 0 and row["state"] == "pending"  # 未消耗重试


def test_agentlimb_off_does_not_block_getnote(tmp_path):
    queue = _make_queue(tmp_path)
    with queue:
        # 带 token 的小红书 → 路由 direct→getnote。桥接关断时 direct 跳过，getnote 仍能跑成功。
        (row_id,) = _seed_pending(
            queue,
            [{"source_type": "xiaohongshu", "url": "https://xhs.com/explore/gnA?xsec_token=k", "title": "getnote 兜底"}],
        )
        written = []

        def runner(args, timeout):
            payload = '{"data":{"note":{"content":"%s"}}}' % _BODY
            return subprocess.CompletedProcess(args, 0, payload, "")

        bridge = FakeBridge(probe=False)
        scheduler = _build_scheduler(queue, bridge=bridge, write=lambda i, b: written.append(i["url"]),
                                     getnote_runner=runner)
        summary = scheduler.run_cycle()
        assert summary["xiaohongshu"]["done"] == 1
        assert summary["xiaohongshu"]["bridge_off"] == 0  # AgentLimb 关断不阻塞 getnote
        assert written == ["https://xhs.com/explore/gnA?xsec_token=k"]
        row = queue.conn.execute("SELECT state, channel FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        assert row["state"] == "done" and row["channel"] == "getnote"
        assert row["channel"] == "getnote"


def test_bridge_unavailable_mid_run_counts_off(tmp_path):
    queue = _make_queue(tmp_path)
    with queue:
        (row_id,) = _seed_pending(
            queue,
            [{"source_type": "xiaohongshu", "url": "https://xhs.com/explore/abortA?xsec_token=k", "title": "读到一半桥断"}],
        )
        written = []
        bridge = FakeBridge(grab=lambda url: (_ for _ in ()).throw(BridgeUnavailableError("桥接断")))
        scheduler = _build_scheduler(queue, bridge=bridge, write=lambda i, b: written.append(i["url"]))
        summary = scheduler.run_cycle()
        assert summary["xiaohongshu"]["bridge_off"] == 1
        assert summary["xiaohongshu"]["done"] == 0
        row = queue.conn.execute("SELECT attempts, state FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        assert row["attempts"] == 0 and row["state"] == "pending"  # 基础设施故障不计数
        assert written == []


def test_direct_channel_guard_fake_signature_counts_one_attempt(tmp_path):
    queue = _make_queue(tmp_path)
    with queue:
        (row_id,) = _seed_pending(
            queue,
            [{"source_type": "xiaohongshu", "url": "https://xhs.com/explore/noteD?xsec_token=k", "title": "过期"}],
        )
        bridge = FakeBridge(grab=lambda url: {"title": "小红书 - 你的生活兴趣社区", "text": _BODY})
        scheduler = _build_scheduler(queue, bridge=bridge)
        summary = scheduler.run_cycle()
        assert summary["xiaohongshu"]["done"] == 0
        row = queue.conn.execute("SELECT attempts, state FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        # direct 假命中拦截已计数 1 次（DB 态）；getnote 基础设施关断（_infra_raise）不额外计数。
        assert row["attempts"] == 1 and row["state"] == "pending"


def test_bili_permanent_marks_skipped(tmp_path):
    queue = _make_queue(tmp_path)
    with queue:
        (row_id,) = _seed_pending(
            queue,
            [{"source_type": "bilibili", "url": "https://www.bilibili.com/video/BV1xx411c7mD", "title": "无字幕视频"}],
        )
        # 注入带 runner 的 bili_cli 通道：返回「非空 data 但无内容」→ 永久 skipped。
        from openbiliclaw.refill.channels.bili_cli import BiliCliChannel

        bili = BiliCliChannel(
            runner=lambda args, timeout: subprocess.CompletedProcess(
                args, 0, '{"data":{"subtitle":{"available":false},"video":{"description":""}}}', ""
            )
        )
        quota = {"bilibili": RefillQuota(per_cycle=1, interval_min=0)}
        scheduler = RefillScheduler(
            queue, min_body_len=30, quota=quota, write_content=lambda i, b: None,
            channels={"bili_cli": bili},
        )
        summary = scheduler.run_cycle(sources=("bilibili",))
        assert summary["bilibili"]["skipped"] == 1  # 永久不可抓
        assert summary["bilibili"]["done"] == 0
        assert summary["bilibili"]["failed"] == 0
        assert summary["bilibili"]["dropped"] == 0
        row = queue.conn.execute("SELECT state, attempts FROM refill_queue WHERE id=?", (row_id,)).fetchone()
        assert row["state"] == "skipped" and row["attempts"] == 0  # 未消耗重试


def test_xhs_read_js_placeholder_injected_without_format_error():
    built = _XHS_READ_JS.replace("__NID__", "abc123")
    assert "__NID__" not in built
    assert "abc123" in built
    assert "JSON.stringify({" in built