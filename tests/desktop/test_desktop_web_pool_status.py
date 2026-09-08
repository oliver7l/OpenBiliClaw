"""Desktop web 前端防回归测试（精简版）。

本文件只保留**有明确防回归价值**的断言：
- 初始推荐列表不能内置 demo 数据（防止开发调试数据泄漏到生产）
- 反馈逻辑不能回退（喜欢保留、不喜欢/忽略隐藏）

其余前端 JS 静态文案/函数名断言（pool_available_count 文案、hydrateFromBackend
内部实现、delight cover eager loading 等）已移除：
- 这类断言把实现细节当契约，前端每次重构/改文案都全挂
- Python 静态读取 JS 文件无法验证前端真实行为
- 前端 UI 契约应由前端自身测试 / 代码审查覆盖，后端 CI 不断言前端文案

如果未来需要前端行为测试，应使用浏览器自动化（Playwright/Puppeteer），
而不是 grep JS 源码。
"""

import re
from pathlib import Path

_APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
_PROFILE_JS = Path("src/openbiliclaw/web/desktop/assets/js/profile.js")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_web_starts_with_empty_recommendation_list() -> None:
    """Desktop web must not ship built-in demo cards as real recommendations."""
    app_js = _read(_APP_JS)
    profile_js = _read(_PROFILE_JS) if _PROFILE_JS.exists() else ""

    # 初始 state.videos 必须为空数组
    match = re.search(
        r"\n\s+videos:\s*(?P<value>\[[\s\S]*?\])\s*,\n\s+messages:",
        app_js,
    )
    if match is not None:
        assert match.group("value").strip() == "[]"

    # 不能内置具体的 demo 视频标题
    assert "为什么说回县城你也躺不平" not in app_js
    assert "为什么说回县城你也躺不平" not in profile_js
    assert "Concrete, light and silence" not in app_js
    assert "Concrete, light and silence" not in profile_js


def test_desktop_positive_feedback_keeps_recommendation_card_visible() -> None:
    """Like feedback must keep the card; only dislike/dismiss hides it."""
    app_js = _read(_APP_JS)
    profile_js = _read(_PROFILE_JS) if _PROFILE_JS.exists() else ""
    combined = app_js + "\n" + profile_js

    # shouldRemoveRecommendationAfterFeedback 必须只对 dislike/dismiss 返回 true
    decision = re.search(
        r"function shouldRemoveRecommendationAfterFeedback\(feedbackType\) \{(?P<body>.*?)\n    \}",
        combined,
        flags=re.S,
    )
    assert decision is not None, "shouldRemoveRecommendationAfterFeedback not found"
    body = decision.group("body")
    assert "dislike" in body
    assert "dismiss" in body
    # like 不应该在移除条件里
    assert '"like"' not in body
    assert "'like'" not in body


def test_desktop_recommendation_hydration_filters_only_negative_feedback() -> None:
    """Hydration must not hide liked recommendations returned by another client."""
    app_js = _read(_APP_JS)
    profile_js = _read(_PROFILE_JS) if _PROFILE_JS.exists() else ""
    combined = app_js + "\n" + profile_js

    feedbacked = re.search(
        r"function isFeedbackedRecommendation\(item\) \{(?P<body>.*?)\n    \}",
        combined,
        flags=re.S,
    )
    assert feedbacked is not None, "isFeedbackedRecommendation not found"
    body = feedbacked.group("body")
    assert "shouldRemoveRecommendationAfterFeedback(feedback)" in body
