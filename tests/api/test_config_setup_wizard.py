"""setup 向导与后端契约的回归测试（2026-09-13 审计 F1 残余）。

三件事在这里被永久锁定：

1. ``POST /api/config/discover-models`` 必须注册，并且从**未保存**的表单凭据
   （或已保存的 ``[llm.<provider>]``）返回模型列表，而不是 404。
2. 向导保存 LLM 的第一步必须真的落盘。它此前提交的是上游 ``llm.instances``
   routing payload，而本 fork 的 ``_apply_llm_update`` 只认 provider-name
   形状 —— 于是 ``api_key`` 从未写入，``PUT /api/config`` 恒 400
   「配置校验失败，未写入 config.toml」，所有新装用户卡在第一步。
3. 向导页不得再引用本 fork 后端不存在的端点/字段（``apply-status``、
   ``embedding/repair``、routing v2、``orcarouter``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config

_WEB_DIR = Path(__file__).resolve().parents[2] / "src" / "openbiliclaw" / "web"
_SETUP_HTML = _WEB_DIR / "setup" / "index.html"


@pytest.fixture()
def isolated_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """把项目根指向临时目录，避免读到本机真实 config.toml / 数据库。"""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    save_config(Config(data_dir=str(tmp_path / "data")), tmp_path / "config.toml")
    return TestClient(create_app(), raise_server_exceptions=False)


def _setup_html() -> str:
    return _SETUP_HTML.read_text(encoding="utf-8")


# ── 端点存在性 ────────────────────────────────────────────────


def test_discover_models_route_is_registered() -> None:
    paths = {
        (getattr(route, "path", ""), method)
        for route in create_app().routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert ("/api/config/discover-models", "POST") in paths


def test_setup_wizard_calls_no_unregistered_endpoint() -> None:
    """向导页引用的每个 /api 路径都必须存在于真实路由表。"""
    import re

    registered = {getattr(route, "path", "") for route in create_app().routes}
    referenced = {
        match.group(1) for match in re.finditer(r"[\"'`](/api/[^\"'`\s]*)", _setup_html()) if "${" not in match.group(1)
    }
    assert referenced, "向导页未引用任何 /api 路径（正则失效？）"
    missing = sorted(
        path
        for path in referenced
        if path not in registered
        and not any(re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", route), path) for route in registered)
    )
    assert missing == [], f"向导页仍在调用后端不存在的端点：{missing}"


# ── discover-models 行为 ──────────────────────────────────────


def test_discover_models_returns_endpoint_catalogue(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openbiliclaw.llm import model_discovery

    async def fake_get(url: str, **_kwargs: Any) -> httpx.Response:
        assert url == "https://gw.example.com/v1/models"
        return httpx.Response(200, json={"data": [{"id": "m-b"}, {"id": "m-a"}]})

    monkeypatch.setattr(model_discovery, "_get", fake_get)

    response = isolated_client.post(
        "/api/config/discover-models",
        json={
            "provider_type": "openai_compatible",
            "api_key": "sk-typed",
            "base_url": "https://gw.example.com/v1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["models"] == ["m-a", "m-b"]
    assert body["reasoning_efforts"], "推理强度建议表必须回传，供 datalist 使用"
    assert body["error"] == ""


def test_discover_models_reachable_while_backend_is_degraded(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级模式必须放行本端点。

    首次运行（还没有可用 LLM）时后端**按定义**处于降级模式，而 degraded
    中间件默认拒绝一切未列入白名单的路径。若不放行，修复这一状态的向导页
    自己会被 503 挡住。
    """
    from openbiliclaw.llm import model_discovery

    async def fake_get(url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    monkeypatch.setattr(model_discovery, "_get", fake_get)

    response = isolated_client.post(
        "/api/config/discover-models",
        json={"provider_type": "openai", "api_key": "sk-typed"},
    )

    assert response.status_code == 200, response.json()
    # 控制组：同一次运行里，未列入白名单的路径仍应被 503 拒绝。
    blocked = isolated_client.get("/api/soul")
    assert blocked.status_code == 503
    assert blocked.json()["status"] == "degraded"


def test_discover_models_is_soft_failure_not_4xx(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端点不可达时返回 ok=False 的内联错误，而不是让向导报红。"""
    from openbiliclaw.llm import model_discovery

    async def boom(url: str, **_kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(model_discovery, "_get", boom)

    response = isolated_client.post(
        "/api/config/discover-models",
        json={"provider_type": "openai", "api_key": "sk-typed"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "ConnectError" in body["error"]


def test_discover_models_falls_back_to_saved_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """重启用「留空则沿用当前 Key」时，凭据取自已保存的 config.toml。"""
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(tmp_path))
    cfg = Config(data_dir=str(tmp_path / "data"))
    cfg.llm.deepseek.api_key = "sk-saved-in-file"
    cfg.llm.deepseek.base_url = "https://saved.example.com"
    save_config(cfg, tmp_path / "config.toml")
    client = TestClient(create_app(), raise_server_exceptions=False)

    from openbiliclaw.llm import model_discovery

    seen: dict[str, str] = {}

    async def fake_discover(**kwargs: Any) -> model_discovery.ModelDiscoveryResult:
        seen.update({k: str(v) for k, v in kwargs.items()})
        return model_discovery.ModelDiscoveryResult(True, models=("deepseek-chat",))

    monkeypatch.setattr(model_discovery, "discover_models", fake_discover)

    response = client.post(
        "/api/config/discover-models",
        json={"provider_type": "deepseek", "api_key": "", "base_url": ""},
    )

    assert response.status_code == 200
    assert response.json()["models"] == ["deepseek-chat"]
    assert seen["api_key"] == "sk-saved-in-file"
    assert seen["base_url"] == "https://saved.example.com"


def test_discover_models_never_writes_config(
    isolated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openbiliclaw.llm import model_discovery

    async def fake_get(url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    monkeypatch.setattr(model_discovery, "_get", fake_get)
    before = (tmp_path / "config.toml").read_bytes()

    isolated_client.post(
        "/api/config/discover-models",
        json={
            "provider_type": "openai_compatible",
            "api_key": "sk-typed",
            "base_url": "https://gw.example.com/v1",
        },
    )

    assert (tmp_path / "config.toml").read_bytes() == before


# ── 向导保存链路（F1 核心）─────────────────────────────────────


def test_wizard_llm_payload_shape_is_persisted(
    isolated_client: TestClient,
    tmp_path: Path,
) -> None:
    """向导第 0 步提交的 provider-name payload 必须真的落盘。"""
    response = isolated_client.put(
        "/api/config",
        json={
            "suppress_background_llm_work": True,
            "llm": {
                "default_provider": "deepseek",
                "deepseek": {
                    "model": "deepseek-chat",
                    "api_key": "sk-wizard-1234567890",
                    "reasoning_effort": "high",
                },
            },
        },
    )

    body = response.json()
    assert response.status_code == 200, body.get("message")
    assert body["ok"] is True

    saved = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "sk-wizard-1234567890" in saved
    assert "deepseek-chat" in saved
    assert 'default_provider = "deepseek"' in saved
    assert 'reasoning_effort = "high"' in saved

    from openbiliclaw.config import load_config

    reloaded = load_config(tmp_path / "config.toml")
    assert reloaded.llm.default_provider == "deepseek"
    assert reloaded.llm.deepseek.api_key == "sk-wizard-1234567890"
    assert reloaded.llm.deepseek.model == "deepseek-chat"


def test_reasoning_effort_empty_string_is_a_writable_value(
    isolated_client: TestClient,
    tmp_path: Path,
) -> None:
    """空字符串对 reasoning_effort 是有效值（关闭 thinking），必须能写回。

    这解释了向导为什么**无条件**发送该字段：若加 ``if (effort)`` 守卫，
    用户清空输入框后旧的 effort 会一直留在 config.toml 里。
    """
    base = {
        "suppress_background_llm_work": True,
        "llm": {"default_provider": "deepseek", "deepseek": {}},
    }
    isolated_client.put(
        "/api/config",
        json={**base, "llm": {**base["llm"], "deepseek": {"api_key": "sk-x", "reasoning_effort": "max"}}},
    )
    from openbiliclaw.config import load_config

    assert load_config(tmp_path / "config.toml").llm.deepseek.reasoning_effort == "max"

    cleared = isolated_client.put(
        "/api/config",
        json={**base, "llm": {**base["llm"], "deepseek": {"reasoning_effort": ""}}},
    )
    assert cleared.status_code == 200, cleared.json().get("message")
    assert load_config(tmp_path / "config.toml").llm.deepseek.reasoning_effort == ""


def test_wizard_payload_does_not_use_upstream_routing_schema(
    isolated_client: TestClient,
    tmp_path: Path,
) -> None:
    """上游 routing v2 payload 在本 fork 无效 —— 保留此例说明为何不能用它。"""
    response = isolated_client.put(
        "/api/config",
        json={
            "suppress_background_llm_work": True,
            "llm": {
                "routing_version": 2,
                "instances": {
                    "deepseek": {
                        "provider_type": "deepseek",
                        "enabled": True,
                        "api_key": "sk-should-not-land",
                        "model": "deepseek-chat",
                    }
                },
                "default_chain": ["deepseek"],
                "routes": {},
            },
        },
    )

    assert response.status_code == 400
    assert "sk-should-not-land" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


# ── 向导页静态对齐 ────────────────────────────────────────────


def test_wizard_page_drops_dead_upstream_branches() -> None:
    html = _setup_html()
    for banned in (
        "/api/config/apply-status",  # 后端从无 config-apply 状态机
        "/api/embedding/repair",  # 后端从无 ollama pull 子系统
        "routing_version",
        "default_chain",
        "orcarouter",  # 后端无该 provider 配置段
    ):
        assert banned not in html, f"向导页仍引用本 fork 不支持的 {banned}"


def test_wizard_page_keeps_model_discovery_wired() -> None:
    html = _setup_html()
    assert "/api/config/discover-models" in html
    assert "buildSetupLlmUpdate" in html
    # 保存路径必须落到后端认识的 provider-name 形状
    assert "llm: { default_provider: provider, [provider]: block }" in html
