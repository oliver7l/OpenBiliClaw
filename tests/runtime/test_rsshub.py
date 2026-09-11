"""RSSHub 本地自部署 runtime 测试。

只测决策分支（已存活跳过 / 无 docker 提示），不真正 docker run（触网）。
"""

from __future__ import annotations

from openbiliclaw.runtime import rsshub


def test_ensure_rsshub_short_circuits_when_up(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """RSSHub 已监听时直接返回 True，不触碰 docker。"""
    called: dict = {}

    def fake_rsshub_is_up(**kw):  # type: ignore[no-untyped-def]
        called["rsshub_is_up"] = True
        return True

    monkeypatch.setattr(rsshub, "rsshub_is_up", fake_rsshub_is_up)
    assert rsshub.ensure_rsshub(wait_seconds=0) is True
    # 存活路径不打 docker
    assert "dock" not in called


def test_ensure_rsshub_no_docker_returns_false(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """未监听且无 docker 时返回 False 并给出提示。"""
    msgs: list[str] = []

    def fake_rsshub_is_up(**kw):  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(rsshub, "rsshub_is_up", fake_rsshub_is_up)
    monkeypatch.setattr(rsshub, "_docker_available", lambda: False)
    monkeypatch.setattr(rsshub, "_start_container", lambda **kw: True)

    assert rsshub.ensure_rsshub(wait_seconds=0, logger_fn=msgs.append) is False
    assert msgs and "无 docker" in msgs[0]


def test_proxy_env_injected_when_reachable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """host.docker.internal:7890 可达时注入 HTTP(S)/ALL_PROXY 到容器 env。"""
    monkeypatch.setattr(
        rsshub,
        "can_connect",
        lambda host, port, timeout: host == "host.docker.internal" and port == 7890,
    )
    env = rsshub._inject_proxy_env()
    assert env.get("HTTP_PROXY") == "http://host.docker.internal:7890"
    assert env.get("HTTPS_PROXY") == "http://host.docker.internal:7890"
    assert "ALL_PROXY" in env


def test_proxy_env_empty_when_unreachable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(rsshub, "can_connect", lambda host, port, timeout: False)
    assert rsshub._inject_proxy_env() == {}