# recommend-feed 独立系统 · 依赖盘点与 providers 协议草案

> 日期：2026-09-07
> 目标：把 OpenBiliClaw 的推荐流（前端 6 个 tab + 推荐引擎 + 候选池）抽离为独立系统 `recommend-feed`。
> 本文档是第 1 步：精确盘点 `src/openbiliclaw/recommendation/` 对宿主的全部依赖，并起草独立系统的 `providers/` 协议。
> 依据：对 `recommendation/` 8 个文件、`api/app.py`、`storage/database.py`、`soul/`、`llm/`、`discovery/` 源码的逐一核查（2026-09-07）。

---

## 1. 结论摘要

- `recommendation/`（6393 行）对外部宿主的依赖分四类：**画像（soul）**、**LLM / Embedding（llm）**、**内容与画像工具（discovery）**、**存储（database）**，外加一个可选回调（task_registry / xhs_self_info_provider）。
- 其中 **6 个符号是纯函数/纯常量，可直接随包复制**，不需要接口化；**4 类需要定义为 Provider 协议** 由宿主适配。
- 数据契约 `DiscoveredContent` 与三张表（`content_cache` / `recommendations` / `user_feedback`）是抽离的锚点，独立系统直接继承。

---

## 2. 宿主依赖清单（精确到符号）

### 2.1 画像层（soul）— 需 ProfileProvider

| 符号 | 来源 | 在 recommendation 中的用途 |
|---|---|---|
| `SoulProfile` | `soul/profile.py` | 全引擎的入参类型（`serve` / `generate_recommendations` / `precompute_*` 共 20+ 处），读取 `preferences.interests / preferences.style / preferences.exploration_openness / disliked_topics / personality_portrait / core_traits / cognitive_style / motivational_drivers / current_phase / values / life_stage` 等字段 |
| `InterestTag` | `soul/profile.py` | `_interests_by_weight()` 排序对象（name / category / weight / first_seen / last_seen / source） |
| `ToneProfile` | `soul/tone.py` | 推荐表达的语气配置（density / warmth / playfulness / directness 四维 TypedDict） |
| `build_tone_profile()` | `soul/tone.py` | `_expression_tone_profile()` 中由画像推断语气；依赖 `profile.preferences.style`、`personality_portrait`、`core_traits` |
| `build_profile_summary()` | `discovery/strategies/_utils.py` | `_recommendation_profile_summary()` 委托它生成喂给 LLM 的结构化画像摘要（发现与推荐共用同一份输入） |

### 2.2 LLM 层（llm）— 需 LLMProvider / EmbeddingProvider

| 符号 | 来源 | 用途 |
|---|---|---|
| `SupportsCoreMemoryTask.complete_structured_task()` | `engine.py` 自建 Protocol | 引擎批量表达 / 分类 / delight 评分的主 LLM 入口 |
| `LLMService.complete_with_core_memory()` | `llm/service.py` | `quality_scorer.py` 的质量打分入口（注意：与 complete_structured_task 是**两个不同方法**） |
| `generate_structured()` | `llm/generation.py` | delight / reranker / chat_recommender 的通用结构化生成（纯函数，依赖传入的 service） |
| `LLMResponse` | `llm/base.py` | 统一响应（content / model / provider / usage / raw / tool_calls） |
| `is_llm_rate_limit_error()` | `llm/service.py` | 批量任务限流退避判定（纯函数） |
| `extract_llm_json_list()` / `extract_llm_json_object()` | `llm/json_utils.py` | JSON 容错解析（纯函数） |
| `build_delight_score_batch_prompt()` | `llm/prompts.py` | delight 批量评分 prompt 构造（纯函数） |
| `SupportsEmbeddingService` | `llm/embedding.py` | MMR 多样化需要 `embed()` + `lookup_cached()` + `similarity_threshold`（已是协议形态） |
| `mmr_cache_text(title, description)` | `llm/embedding.py` | MMR embedding 缓存 key 的唯一事实源（纯函数） |

### 2.3 内容与画像工具（discovery）— 直迁 / 复制

| 符号 | 来源 | 用途 |
|---|---|---|
| `DiscoveredContent` | `discovery/engine.py` | **核心数据契约**：40+ 字段 dataclass（bvid/content_id/title/topic_key/topic_group/style_key/franchise_key/pool_expression/…），curator / engine / llm_reranker 全部基于它 |
| `VALID_STYLE_KEYS` / `normalize_style_key()` | `discovery/style_keys.py` | 13 种观看模式（deep_focus…）常量化与归一（纯常量 + 纯函数） |

### 2.4 存储层（database）— 需拆 PoolStore / RecommendationStore / FeedbackStore

engine 依赖的 Database 方法（18 个）：

| 类别 | 方法 |
|---|---|
| 池子读 | `get_pool_candidates` / `count_pool_candidates` / `get_pool_candidates_needing_copy` / `get_pool_candidates_needing_delight_score` / `get_pool_candidates_needing_evaluation` / `get_topic_group_samples` |
| 用户信号读 | `get_recent_viewed_bvids` / `get_clicked_bvids` |
| 池子写 | `cache_content` / `mark_pool_items_shown` / `update_pool_copy` / `update_delight_score` |
| 推荐历史写 | `batch_insert_recommendations` / `mark_recommendations_presented` / `update_recommendation_content` / `update_recommendation_feedback` / `get_recommendation_by_id` |

curator 依赖（5 个）：`count_pool_candidates` / `get_bandit_impressions` / `get_feedback_signals` / `get_recent_recommendation_signals` / `get_recent_recommendation_signals_since`

### 2.5 Runtime 回调 — 保持注入形态

| 符号 | 用途 |
|---|---|
| `BackgroundTaskRegistry`（可选） | 热重载时 cancel 引擎派生的后台任务；独立系统自带等价物 |
| `xhs_self_info_provider: Callable`（可选） | 排除用户自发布的小红书内容；已是回调注入 |

---

## 3. 判定矩阵：直迁 / 复制 / 接口化

| 处置 | 清单 |
|---|---|
| **整包直迁**（不改代码） | `recommendation/engine.py`、`curator.py`、`agents.py`、`bandit.py`、`delight.py`、`llm_reranker.py`、`quality_scorer.py`、`chat_recommender.py`（import 改到 providers 后） |
| **复制为纯工具**（带来源注释） | `extract_llm_json_list/object`、`generate_structured`、`mmr_cache_text`、`is_llm_rate_limit_error`、`VALID_STYLE_KEYS`、`normalize_style_key`、`build_tone_profile`、`build_profile_summary`、`build_delight_score_batch_prompt` |
| **定义为协议，宿主适配** | `ProfileProvider`（画像）、`LLMProvider`（LLM 两入口统一）、`EmbeddingProvider`、`PoolStore` / `RecommendationStore` / `FeedbackStore`、`EventSink`（反馈/展示事件） |
| **宿主侧保留** | `soul/`、`llm/`、`discovery/` 原实现、`sources/*` 抓取、`runtime/refresh.py` 调度、`api/app.py` 其余端点 |

---

## 4. providers 协议草案

> 以下为独立系统 `recommend-feed/providers/` 的 Python 协议。独立系统只 import 本包，宿主实现本包定义的协议后注入。

### 4.1 `providers/profile.py` — 画像

```python
"""Profile contract — the host (OpenBiliClaw's soul layer) implements this."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol


@dataclass
class InterestTag:
    """Weighted interest tag (mirrors soul/profile.py::InterestTag)."""

    name: str
    category: str = ""
    weight: float = 1.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    source: str = ""


@dataclass
class StylePreference:
    """Content style snapshot (mirrors soul/profile.py::StylePreference)."""

    preferred_duration: str = ""          # "short" | "medium" | "long"
    depth_preference: float = 0.5
    humor_preference: float = 0.5


@dataclass
class ProfileSnapshot:
    """The minimal profile view the recommendation engine reads.

    Extract-on-write: the host adapter copies from its own SoulProfile;
    the independent system never imports soul.*.
    """

    interests: list[InterestTag] = field(default_factory=list)
    style: StylePreference = field(default_factory=StylePreference)
    exploration_openness: float = 0.5
    disliked_topics: list[str] = field(default_factory=list)
    personality_portrait: str = ""
    core_traits: list[str] = field(default_factory=list)
    cognitive_style: list[str] = field(default_factory=list)
    motivational_drivers: list[str] = field(default_factory=list)
    current_phase: str = ""
    values: list[str] = field(default_factory=list)
    life_stage: str = ""
    # opaque extras the host wants to pass through (raw dict)
    extra: dict[str, Any] = field(default_factory=dict)


class ProfileProvider(Protocol):
    """Host-side profile access. Called on every serve() / precompute pass."""

    async def get_profile(self) -> ProfileSnapshot: ...

    def get_recent_feedback(self, *, limit: int = 50) -> list[dict[str, Any]]: ...
```

### 4.2 `providers/llm.py` — LLM 与 Embedding

```python
"""LLM + Embedding contract — the host's llm/service.py implements this.

Unifies the two entry points the engine currently uses:
- RecommendationEngine / delight / reranker: ``complete_structured_task``
- QualityScorer: ``complete_with_core_memory``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResponse:
    """Standardized LLM response (mirrors llm/base.py::LLMResponse)."""

    content: str = ""
    model: str = ""
    provider: str = ""
    usage: dict[str, int] | None = None
    raw: Any = None
    tool_calls: list[dict[str, Any]] | None = None


class LLMProvider(Protocol):
    """Single LLM entry point for the whole recommendation system."""

    async def complete_structured_task(
        self,
        *,
        system_instruction: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        caller: str = "",
        reasoning_effort: str | None = None,
        inject_core_memory: bool = True,
    ) -> LLMResponse: ...

    def is_rate_limit_error(self, exc: BaseException) -> bool:
        """True when an exception chain represents provider backoff."""
        ...


class EmbeddingProvider(Protocol):
    """Embedding access for MMR diversification.

    ``lookup_cached`` must be pure-cache (never calls the provider API),
    so the serve() hot path stays API-free; ``embed`` is the fallback.
    """

    similarity_threshold: float

    async def embed(self, text: str) -> list[float]: ...

    def lookup_cached(self, text: str) -> list[float]: ...
```

### 4.3 `providers/storage.py` — 池子 / 历史 / 反馈

```python
"""Storage contracts — split from the host's 8400-line Database class."""

from __future__ import annotations

from typing import Any, Protocol

from recommend_feed.core.model import FeedItem  # 见 4.5：由 DiscoveredContent 直迁


class PoolStore(Protocol):
    """content_cache table access."""

    def get_pool_candidates(
        self, *, limit: int, platform: str | None = None
    ) -> list[FeedItem]: ...

    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int: ...

    def get_pool_candidates_needing_copy(self, *, limit: int) -> list[FeedItem]: ...

    def get_pool_candidates_needing_delight_score(
        self, *, limit: int, threshold: float
    ) -> list[FeedItem]: ...

    def get_pool_candidates_needing_evaluation(self, *, limit: int) -> list[FeedItem]: ...

    def get_topic_group_samples(self, *, limit: int) -> list[dict[str, Any]]: ...

    def cache_content(self, item: FeedItem) -> None: ...

    def mark_pool_items_shown(self, bvids: list[str]) -> None: ...

    def update_pool_copy(
        self, *, bvid: str, expression: str, topic_label: str
    ) -> None: ...

    def update_delight_score(
        self, *, bvid: str, score: float, reason: str, hook: str
    ) -> None: ...


class RecommendationStore(Protocol):
    """recommendations table access."""

    def batch_insert_recommendations(self, rows: list[dict[str, Any]]) -> None: ...

    def mark_recommendations_presented(self, ids: list[int]) -> None: ...

    def update_recommendation_feedback(
        self, *, recommendation_id: int, feedback_type: str, note: str = ""
    ) -> None: ...

    def update_recommendation_content(
        self, *, recommendation_id: int, **fields: Any
    ) -> None: ...

    def get_recommendation_by_id(
        self, recommendation_id: int
    ) -> dict[str, Any] | None: ...

    def get_recent_recommendation_signals(
        self, *, limit: int
    ) -> list[dict[str, Any]]: ...


class FeedbackStore(Protocol):
    """User signals — feedback, dwell, bandit impressions."""

    def get_recent_viewed_bvids(self, *, days: int) -> list[str]: ...

    def get_clicked_bvids(self, *, days: int) -> list[str]: ...

    def get_bandit_impressions(self, *, days: int) -> list[dict[str, Any]]: ...

    def get_feedback_signals(self, *, limit: int) -> list[dict[str, Any]]: ...

    def record_feedback(
        self, *, bvid: str, action: str, source_platform: str, note: str = ""
    ) -> None: ...
```

### 4.4 `providers/events.py` — 对外事件

```python
"""Outbound event sink — recommendations shown / feedbacked / delighted."""

from __future__ import annotations

from typing import Any, Protocol


class EventSink(Protocol):
    """Host subscribes to recommendation lifecycle events."""

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...
```

### 4.5 核心数据契约 `core/model.py`

`DiscoveredContent`（`discovery/engine.py` L413 起）原样直迁为 `FeedItem`，字段不变（bvid / content_id / title / up_name / author_name / topic_key / topic_group / style_key / franchise_key / pool_expression / pool_topic_label / candidate_tier / discovered_at / last_scored_at / content_type / body_text / source / …），作为独立系统的唯一内容形态。宿主写入 `content_cache` 时按该契约落库。

---

## 5. 迁移顺序与验证

| 步 | 内容 | 验证 |
|---|---|---|
| 1a | 建独立包骨架 + `core/model.py`（直迁 DiscoveredContent） | `pytest tests/test_recommendation_*.py` 全绿 |
| 1b | 复制纯工具（§3 清单）进 `core/utils/`，import 改指向 | ruff + mypy + 全量测试 |
| 1c | 定义 providers 协议 + 宿主适配器（`SoulProfileProvider` / `LLMServiceProvider` / `DatabaseStore`） | 引擎测试注入适配器运行 |
| 1d | `recommendation/` 8 文件 import 全部改走 providers | `pytest` + `mypy src/` + `ruff check` |
| 2 | 拆 `database.py` 推荐 DAO 为独立 `storage/`（协议实现） | 数据库相关测试 |
| 3 | `api/app.py` 推荐端点拆 Router | 端点 e2e 测试 |
| 4 | 前端 6 tab 收敛 FeedView | 手动验证 + popup/desktop e2e |
| 5 | 独立仓库 / 独立进程部署 | 宿主抓取写库 → 独立系统服务 |

> 第 1 步（本文档）不改变任何宿主行为，是纯增量：新包 + 适配器 + import 重指向，全程可回滚。
