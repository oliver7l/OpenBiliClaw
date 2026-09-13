"""``PUT`` / ``GET /api/config`` 的 provider 段覆盖回归（2026-09-13）。

``llm/_compat`` 与 ``obc_llm`` registry 一直支持 10 个 provider，但 HTTP 层
只认识 7 个：``_apply_llm_update`` 的循环和 ``LLMConfigOut`` 响应模型都漏了
``zhipu`` / ``modelscope`` / ``siliconflow``。后果是这两条 ——
PUT 提交它们的凭据**静默无效**，GET 也**读不回来**，于是这三家在 API 上
形同不存在。config 层的等价回归见 ``tests/config/test_llm_provider_sections.py``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import LLM_PROVIDER_NAMES, Config, save_config


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """隔离到临时项目根，避免读到本机真实 config.toml 与数据库。"""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(Config(data_dir=str(tmp_path / "data")), tmp_path / "config.toml")
    return TestClient(create_app(), raise_server_exceptions=False)


def _leave_degraded(client: TestClient) -> None:
    """先配置一个可用 provider，否则后端处于降级态、``PUT`` 会被校验拦下。"""
    r = client.put(
        "/api/config",
        json={
            "suppress_background_llm_work": True,
            "llm": {
                "default_provider": "deepseek",
                "deepseek": {"api_key": "sk-baseline-123456", "model": "deepseek-chat"},
            },
        },
    )
    assert r.status_code == 200, r.text


def test_put_persists_every_declared_provider(client: TestClient, tmp_path: Path) -> None:
    """PUT 携带的每一个 provider 段都必须写进 config.toml。"""
    _leave_degraded(client)

    payload_llm = {
        name: {
            "api_key": f"{name}-KEY-123456",
            "model": f"{name}-model",
            "base_url": f"https://{name}.example.invalid/v1",
        }
        for name in LLM_PROVIDER_NAMES
    }
    r = client.put(
        "/api/config",
        json={"suppress_background_llm_work": True, "llm": payload_llm},
    )
    assert r.status_code == 200, r.text

    saved = (tmp_path / "config.toml").read_text(encoding="utf-8")
    for name in LLM_PROVIDER_NAMES:
        assert f"[llm.{name}]" in saved, f"缺少 [llm.{name}] 段"
        assert f"{name}-KEY-123456" in saved, f"{name} 的 api_key 被静默丢弃"
        assert f"{name}-model" in saved, f"{name} 的 model 被静默丢弃"


def test_get_returns_every_declared_provider(client: TestClient) -> None:
    """``GET /api/config`` 的 llm 段必须包含全部 provider，且凭据仍被掩码。"""
    llm = client.get("/api/config").json()["llm"]
    missing = sorted(name for name in LLM_PROVIDER_NAMES if name not in llm)
    assert not missing, f"GET /api/config 未回传这些 provider: {missing}"

    # 掩码不得回退：新补的段同样只能返回星号形态
    for name in LLM_PROVIDER_NAMES:
        assert "api_key" in llm[name]


def test_domestic_provider_survives_a_later_unrelated_save(client: TestClient, tmp_path: Path) -> None:
    """配好智谱后再保存任意无关设置，其段与凭据都必须保留。"""
    _leave_degraded(client)
    r = client.put(
        "/api/config",
        json={
            "suppress_background_llm_work": True,
            "llm": {
                "zhipu": {
                    "api_key": "zp-KEEP-ME-123456",
                    "model": "glm-4.7-flash",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                }
            },
        },
    )
    assert r.status_code == 200, r.text

    # 一次无关的保存（等价于设置页改了语言）
    r2 = client.put("/api/config", json={"suppress_background_llm_work": True, "language": "zh"})
    assert r2.status_code == 200, r2.text

    saved = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "[llm.zhipu]" in saved
    assert "zp-KEEP-ME-123456" in saved
    assert "https://open.bigmodel.cn/api/paas/v4" in saved


# ── Ollama num_ctx（此前只能手改 config.toml）────────────────────


def _section(text: str, name: str) -> str:
    """取出 ``[llm.<name>]`` 段的正文。"""
    marker = f"[llm.{name}]"
    assert marker in text, f"config.toml 缺少 {marker}"
    body = text.split(marker, 1)[1]
    return body.split("\n[", 1)[0]


def test_put_persists_ollama_num_ctx(client: TestClient, tmp_path: Path) -> None:
    """PUT 的 num_ctx 必须落盘并能回读（此前 API 层整条链都没接线）。"""
    _leave_degraded(client)

    r = client.put(
        "/api/config",
        json={
            "suppress_background_llm_work": True,
            "llm": {
                "ollama": {
                    "model": "qwen3:8b",
                    "base_url": "http://127.0.0.1:11434",
                    "num_ctx": 16384,
                }
            },
        },
    )
    assert r.status_code == 200, r.text

    assert "num_ctx = 16384" in _section((tmp_path / "config.toml").read_text(encoding="utf-8"), "ollama")
    assert client.get("/api/config").json()["llm"]["ollama"]["num_ctx"] == 16384


def test_put_clamps_negative_num_ctx(client: TestClient, tmp_path: Path) -> None:
    """负数收敛到 0（= 用 Ollama 服务端默认），不得写成非法值。"""
    _leave_degraded(client)

    r = client.put(
        "/api/config",
        json={"suppress_background_llm_work": True, "llm": {"ollama": {"num_ctx": -5}}},
    )
    assert r.status_code == 200, r.text
    assert "num_ctx = 0" in _section((tmp_path / "config.toml").read_text(encoding="utf-8"), "ollama")


def test_put_rejects_non_integer_num_ctx(client: TestClient, tmp_path: Path) -> None:
    """非整数必须显式 400，而不是被字符串化后写进 TOML。"""
    _leave_degraded(client)

    r = client.put(
        "/api/config",
        json={"suppress_background_llm_work": True, "llm": {"ollama": {"num_ctx": "abc"}}},
    )
    assert r.status_code == 400, r.text
    assert "num_ctx" in r.text
    # 校验失败不得污染 TOML：仍是默认 0
    assert "num_ctx = 0" in _section((tmp_path / "config.toml").read_text(encoding="utf-8"), "ollama")


def test_num_ctx_not_persisted_for_non_ollama_provider(client: TestClient, tmp_path: Path) -> None:
    """num_ctx 只有 ollama 会落盘；其他 provider 提交它不得出现在其配置段里。"""
    _leave_degraded(client)

    r = client.put(
        "/api/config",
        json={"suppress_background_llm_work": True, "llm": {"deepseek": {"num_ctx": 4096}}},
    )
    assert r.status_code == 200, r.text
    assert "num_ctx" not in _section((tmp_path / "config.toml").read_text(encoding="utf-8"), "deepseek")
