# OpenBiliClaw 模块独立化提取方案

> 编写日期：2026-09-07
> 目标：将 4 个核心模块从主项目提取为独立 Python 包，支持独立开发、测试和复用

---

## 1. 总体架构

### 当前依赖关系（简化）

```
config.py (TOML → dataclass)
  ├── storage/database.py (SQLite 封装)
  ├── memory/manager.py (JSON + DB 双层记忆)
  ├── llm/ ← 最底层，几乎无内部依赖
  ├── soul/ → 依赖 llm/, memory/, storage/, sources/
  ├── discovery/ → 依赖 llm/, soul/, storage/, runtime/image_cache
  └── runtime/ → 依赖 config, llm/, soul/, discovery/, storage/

api/app.py (FastAPI 入口)
  └── 组装所有模块 → RuntimeContext → 路由注册
```

### 提取目标

每个模块成为独立的 pip 包，主项目通过 `pip install -e` 引用：

```
# 最终目录结构
040-OpenBiliClaw/
├── src/openbiliclaw/  ← 主项目（保留核心业务逻辑）
├── packages/
│   ├── obc-llm/        ← 提取包 1: LLM 提供商系统
│   ├── obc-soul/       ← 提取包 2: 灵魂/画像系统
│   ├── obc-discovery/  ← 提取包 3: 发现引擎
│   └── obc-runtime/    ← 提取包 4: 运行时/生产者系统
├── pyproject.toml
└── ...
```

---

## 2. 模块 1：LLM 提供商系统（`obc-llm`）

### 2.1 优先级

**最高** — 完全独立于业务逻辑，是最纯粹的"基础设施"模块，也是其他所有模块的共同依赖。

### 2.2 模块边界

| 包含（进包） | 不包含（留主项目） |
|---|---|
| `llm/base.py` — 抽象接口、错误类型 | `api/models.py` 中的 LLM 相关模型 |
| `llm/openai_provider.py` | `config.py` 中的 LLM 配置段 |
| `llm/claude_provider.py` | `api/app.py` 中的 LLM 注册逻辑 |
| `llm/gemini_provider.py` | 所有 LLM 使用场景（soul/discovery 等） |
| `llm/ollama_provider.py` | |
| `llm/openrouter_provider.py` | |
| `llm/service.py` — 服务层（需解耦 soul 依赖） | |
| `llm/registry.py` — 注册构建器（需解耦 config 依赖） | |
| `llm/prompts.py`（需解耦 discovery/style_keys 和 soul/tone 依赖） | |
| `llm/pricing.py` | |
| `llm/usage_recorder.py` | |
| `llm/generation.py` | |
| `llm/embedding.py` | |
| `llm/json_utils.py` | |
| `llm/codex_auth.py` | |

### 2.3 需解耦的内部依赖

| 文件 | 依赖 | 解耦方案 |
|---|---|---|
| `service.py` | `openbiliclaw.soul.profile` → `SoulProfile`, `preference_layer_from_dict` | 将 `SoulProfile` 类型定义为 Protocol，提取到包内；或让主项目传入 `profile_renderer` 回调 |
| `service.py` | `openbiliclaw.soul.tone` → `ToneProfile`, `build_tone_profile` | 同上，定义为 Protocol |
| `service.py` | `openbiliclaw.memory.manager` → `MemoryManager`（TYPE_CHECKING 仅） | 定义为 Protocol，提取到包内 |
| `registry.py` | `openbiliclaw.config` → `Config` | 定义为 `LLMConfig` 协议类，让主项目传入配置 dict |
| `prompts.py` | `openbiliclaw.discovery.style_keys` | 将 `STYLE_KEY_PROMPT_TEXT` 等常量内联或通过回调传入 |
| `prompts.py` | `openbiliclaw.soul.tone` → `ToneProfile` | 定义为 Protocol |
| `prompts.py` | `openbiliclaw.soul.taxonomy` → `CATEGORY_VOCAB` | 将词汇表作为配置参数传入 |

### 2.4 接口设计

```python
# obc_llm/__init__.py 的公开 API

# 提供者
from .openai_provider import OpenAIProvider, DeepSeekProvider
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openrouter_provider import OpenRouterProvider

# 基础类型
from .base import (
    LLMProvider, LLMResponse, HealthCheckResult,
    LLMProviderError, LLMRateLimitError, LLMTimeoutError,
    LLMResponseError, LLMFallbackError,
)

# 服务层
from .service import LLMService, LLMServiceError

# 注册构建
from .registry import build_llm_registry, RegistrySummary

# 工具
from .json_utils import parse_llm_json_tolerant, extract_llm_json_object
from .pricing import estimate_cost
from .embedding import EmbeddingService, EmbeddingCache
```

### 2.5 依赖注入设计

`LLMService` 需要 `SoulProfile` 和 `ToneProfile` 信息来组装 system prompt。解决方案：

```python
# 包内定义 Protocol
class ProfileRenderer(Protocol):
    """主项目实现的画像渲染接口"""
    def render_profile(self, style: str = "compact") -> str: ...
    def preference_layer(self) -> dict: ...

# 包内定义 ToneProfile 值对象
@dataclass
class ToneProfile:
    style: str
    traits: list[str]
    instructions: list[str]

# LLMService 注入方式
class LLMService:
    def __init__(
        self,
        registry: LLMRegistry,
        profile_renderer: ProfileRenderer | None = None,
        tone_provider: Callable[[], ToneProfile] | None = None,
        memory_summarizer: Callable[[], str] | None = None,
    ):
        ...
```

### 2.6 迁移步骤

1. 新建 `packages/obc-llm/` 目录，创建 `pyproject.toml`
2. 将所有 `llm/*.py` 复制到包内，包名改为 `obc_llm`
3. 提取依赖接口到 `obc_llm/_protocols.py`
4. 修改 `prompts.py` 中对外部常量的引用改为参数注入
5. 主项目安装 `obc-llm` 为本地包
6. 主项目实现 `ProfileRenderer` 等适配器，传入 `LLMService`
7. 逐步替换 `from openbiliclaw.llm` → `from obc_llm`
8. 运行测试验证

---

## 3. 模块 2：灵魂/画像系统（`obc-soul`）

### 3.1 优先级

**第二** — 依赖 `obc-llm`，是整个项目的用户建模核心。

### 3.2 模块边界

| 包含（进包） | 不包含（留主项目） |
|---|---|
| `soul/profile.py` — 数据模型 | 主项目中的 `SoulConfig` 配置 |
| `soul/profile_builder.py` | `runtime_context.py` 中的 SoulEngine 构建逻辑 |
| `soul/pipeline.py` | 与 `memory/manager.py` 的紧密耦合 |
| `soul/engine.py` — 核心编排器 | 与 `storage/database.py` 的直接调用 |
| `soul/speculator.py` | |
| `soul/avoidance_speculator.py` | |
| `soul/awareness_analyzer.py` | |
| `soul/insight_analyzer.py` | |
| `soul/dialogue_insight_analyzer.py` | |
| `soul/layer_updaters.py` | |
| `soul/consolidator.py` | |
| `soul/preference_analyzer.py` | |
| `soul/overrides.py` | |
| `soul/interest_writeback.py` | |
| `soul/dislike_writeback.py` | |
| `soul/event_filters.py` | |
| `soul/taxonomy.py` | |
| `soul/tone.py` | |
| `soul/cognition_cycle.py` | |
| `soul/dialogue.py` | |
| `soul/pool_purge.py` | |
| `soul/profile_renderer.py` | |
| `soul/category_migration.py` | |
| `soul/exploration_buffer.py` | |
| `soul/negative_exemplars.py` | |

### 3.3 需解耦的内部依赖

soul 模块依赖链：

```
soul/
  ├── llm/    → 提取为 obc-llm 后，改依赖 obc-llm
  ├── memory/ → 需抽象为接口
  ├── storage/→ 仅 pool_purge.py 依赖，需抽象
  └── sources/→ 仅 profile_builder.py 依赖，需抽象
```

| 文件 | 依赖 | 解耦方案 |
|---|---|---|
| 多个文件 | `openbiliclaw.llm.*` | 改为依赖 `obc_llm` |
| `cognition_cycle.py`, `engine.py` 等 | `openbiliclaw.memory.manager.MemoryManager` | 定义 `MemoryStore` Protocol |
| `pool_purge.py` | `openbiliclaw.storage.database.Database` | 定义 `PoolStore` Protocol |
| `profile_builder.py` | `openbiliclaw.sources.event_format` | 定义 `Event` 数据类，设为值对象 |

### 3.4 接口设计

```python
# obc_soul/__init__.py 的公开 API

# 核心引擎
from .engine import SoulEngine

# 数据模型
from .profile import SoulProfile, OnionProfile, InterestTag

# 对话系统
from .dialogue import SocraticDialogue

# 配置类型
from ._config import SoulConfig, OnionLayerConfig

# Protocol 接口（由主项目实现）
from ._protocols import (
    MemoryStore,        # 记忆存储接口
    PoolStore,          # 池存储接口
    EventNormalizer,    # 事件标准化接口
)
```

### 3.5 依赖注入设计

```python
class SoulEngine:
    def __init__(
        self,
        llm_service: obc_llm.LLMService,
        memory_store: MemoryStore,
        pool_store: PoolStore | None = None,
        event_normalizer: EventNormalizer | None = None,
        config: SoulConfig | None = None,
    ):
        ...
```

### 3.6 迁移步骤

1. 依赖 `obc-llm` 先提取完成
2. 新建 `packages/obc-soul/`，创建 `pyproject.toml`
3. 复制 `soul/` 文件，修改导入为 `obc_llm`
4. 提取 `MemoryStore`, `PoolStore`, `EventNormalizer` 协议
5. 主项目实现协议接口并注入
6. 逐步替换导入路径

---

## 4. 模块 3：发现引擎（`obc-discovery`）

### 4.1 优先级

**第三** — 依赖 `obc-llm` 和 `obc-soul`。

### 4.2 模块边界

| 包含（进包） | 不包含（留主项目） |
|---|---|
| `discovery/engine.py` | `runtime/image_cache.py` |
| `discovery/candidate_pipeline.py` | `runtime/keyword_*` |
| `discovery/candidate_pool.py` | 各 platform producer 的具体实现 |
| `discovery/douyin.py` | |
| `discovery/keyword_digest.py` | |
| `discovery/multimodal.py` | |
| `discovery/pool_snapshot.py` | |
| `discovery/style_keys.py` | |
| `discovery/style_rules.py` | |
| `discovery/x_normalize.py` | |
| `discovery/strategies/`（全部策略） | |

### 4.3 需解耦的内部依赖

| 文件 | 依赖 | 解耦方案 |
|---|---|---|
| 多个文件 | `openbiliclaw.llm.*` | 改为依赖 `obc_llm` |
| 多个文件 | `openbiliclaw.soul.profile` | 改为依赖 `obc_soul` |
| 多个文件 | `openbiliclaw.storage.database` | 定义 `CandidateStore` Protocol |
| `engine.py` | `openbiliclaw.runtime.image_cache` | 定义 `ImageCache` Protocol |
| `multimodal.py` | `openbiliclaw.runtime.image_cache` | 同上 |

### 4.4 接口设计

```python
# obc_discovery/__init__.py

from .engine import ContentDiscoveryEngine, DiscoveryStrategy
from .candidate_pipeline import DiscoveryCandidatePipeline
from .candidate_pool import DiscoveryCandidateWrite
from .style_keys import STYLE_KEY_DEFINITIONS, normalize_style_key

from ._protocols import (
    CandidateStore,     # 候选存储接口
    ImageCache,         # 图片缓存接口
    SoulProfileReader,  # 画像读取接口
)
```

### 4.5 迁移步骤

1. 依赖 `obc-llm` 和 `obc-soul` 先提取完成
2. 新建 `packages/obc-discovery/`
3. 复制文件，修改导入
4. 提取 `CandidateStore` 等协议
5. 主项目实现协议接口

---

## 5. 模块 4：运行时/生产者系统（`obc-runtime`）

### 5.1 优先级

**第四** — 依赖前三者，且与主项目耦合最深。

### 5.2 模块边界

| 包含（进包） | 不包含（留主项目） |
|---|---|
| `runtime/refresh.py` — 刷新控制器 | `config.py` 中的 `SchedulerConfig` |
| `runtime/events.py` — 事件总线 | `api/app.py` 中的启动/关闭逻辑 |
| `runtime/keyword_fetch.py` | `runtime/autostart/`（平台相关） |
| `runtime/keyword_planner.py` | `runtime/init_coordinator.py` |
| `runtime/source_policy.py` | `runtime/init_prereqs.py` |
| `runtime/feedback_scheduler.py` | `runtime/updater.py` |
| `runtime/presence.py` | `runtime/ollama_supervisor.py` |
| `runtime/image_cache.py` | |
| `runtime/activity_feed.py` | |
| `runtime/task_registry.py` | |
| `runtime/*_producer.py`（13个 producer） | |

### 5.3 需解耦的内部依赖

| 文件 | 依赖 | 解耦方案 |
|---|---|---|
| 多个文件 | `openbiliclaw.config` | 定义 `RuntimeConfig` Protocol |
| 多个文件 | `openbiliclaw.llm.*` | 改为依赖 `obc_llm` |
| 多个文件 | `openbiliclaw.soul.*` | 改为依赖 `obc_soul` |
| 多个文件 | `openbiliclaw.discovery.*` | 改为依赖 `obc_discovery` |
| 多个文件 | `openbiliclaw.sources.*` | 定义 `SourceAdapter` Protocol |

### 5.4 接口设计

```python
# obc_runtime/__init__.py

from .refresh import RefreshController
from .events import RuntimeEventHub, RuntimeEvent
from .keyword_fetch import KeywordFetchCoordinator
from .keyword_planner import KeywordPlanner
from .image_cache import ImageCacheService
from .task_registry import BackgroundTaskRegistry

from ._protocols import (
    RuntimeConfig,          # 运行时配置
    SourceAdapter,          # 平台源适配器
    DiscoveryEngine,        # 发现引擎接口
    Recommender,            # 推荐引擎接口
)
```

### 5.5 迁移步骤

1. 依赖前三者先提取完成
2. 新建 `packages/obc-runtime/`
3. 复制文件，修改导入
4. 提取 `RuntimeConfig` 等协议
5. 主项目实现协议接口

---

## 6. 依赖注入汇总

### 提取后依赖关系

```
obc-llm     ← 零内部依赖，仅依赖标准库 + openai/anthropic 等 SDK
obc-soul    ← 依赖 obc-llm
obc-discovery ← 依赖 obc-llm + obc-soul
obc-runtime   ← 依赖 obc-llm + obc-soul + obc-discovery

主项目 (openbiliclaw)
  ├── 依赖全部 4 个包
  └── 实现所有 Protocol 接口，注入到各模块
```

### 主项目需要实现的接口清单

| 接口 | 用途 | 实现方 |
|---|---|---|
| `ProfileRenderer` | LLM 组装 prompt 时需要画像信息 | `memory/manager.py` |
| `MemoryStore` | Soul 引擎读写记忆 | `memory/manager.py` |
| `PoolStore` | 推荐池存储 | `storage/database.py` |
| `CandidateStore` | 发现引擎候选存储 | `storage/database.py` |
| `ImageCache` | 图片缓存 | `runtime/image_cache.py` |
| `RuntimeConfig` | 运行时配置读取 | `config.py` |
| `SourceAdapter` | 平台源适配 | `sources/adapter.py` |
| `EventNormalizer` | 事件标准化 | `sources/event_format.py` |

---

## 7. 实施路线图

### 阶段 1：LLM 提取（预估 1-2 天）

```
Day 1:
  ├── 创建 packages/obc-llm/ 和 pyproject.toml
  ├── 复制 llm/ 文件，重命名包为 obc_llm
  ├── 提取 _protocols.py（ProfileRenderer, ToneProvider 等）
  ├── 修改 prompts.py 解耦外部常量引用
  └── 主项目安装 obc-llm，编写适配器

Day 2:
  ├── 逐步替换主项目中的 from openbiliclaw.llm → from obc_llm
  ├── 运行测试，修复问题
  └── 验证 LLM 服务正常启动和调用
```

### 阶段 2：Soul 提取（预估 1-2 天）

```
Day 1:
  ├── 创建 packages/obc-soul/ 和 pyproject.toml
  ├── 复制 soul/ 文件，修改导入为 obc_llm
  ├── 提取 MemoryStore, PoolStore, EventNormalizer 协议
  └── 主项目实现协议接口

Day 2:
  ├── 替换主项目导入路径
  ├── 运行测试
  └── 验证画像系统正常
```

### 阶段 3：Discovery 提取（预估 1 天）

```
  ├── 创建 packages/obc-discovery/
  ├── 复制文件，修改导入
  ├── 提取 CandidateStore 等协议
  ├── 主项目实现协议
  └── 测试验证
```

### 阶段 4：Runtime 提取（预估 2 天）

```
  ├── 创建 packages/obc-runtime/
  ├── 复制文件，修改导入
  ├── 提取 RuntimeConfig 等协议
  ├── 主项目实现协议
  └── 全面测试验证
```

### 阶段 5：收尾（预估 1 天）

```
  ├── 清理主项目中已迁移的源文件
  ├── 更新 pyproject.toml 依赖声明
  ├── 更新文档
  └── 端到端验证
```

---

## 8. 风险与注意事项

### 风险 1：循环依赖
- `discovery/` 中的 `multimodal.py` 依赖 `runtime/image_cache`，但 `runtime/` 也会依赖 `discovery/`
- **解决方案**：将 `ImageCache` 提取为独立的 Protocol，runtime 和 discovery 都依赖协议而非实现

### 风险 2：prompts.py 过大
- `prompts.py` 约 110KB，包含大量业务相关的 prompt 模板
- **解决方案**：将纯 LLM 调度相关的 prompt 留在 `obc-llm`，业务特定的 prompt 移到使用方模块

### 风险 3：配置耦合
- 当前 `registry.py` 直接引用 `Config` 类读取配置
- **解决方案**：定义轻量级 `LLMConfig` 协议类，只传递需要的字段

### 风险 4：渐进式替换
- 不能一次性替换所有导入，需要确保每一步都可用
- **解决方案**：提取过程中，主项目同时保留 `from openbiliclaw.llm` 和 `from obc_llm` 两种导入，逐步迁移

---

## 9. 附录：Protocol 接口定义

### 9.1 `obc_llm/_protocols.py`

```python
@runtime_checkable
class ProfileRenderer(Protocol):
    def render_profile_section(self, style: str = "compact") -> str: ...
    def get_preference_layer(self) -> dict[str, Any]: ...

@runtime_checkable
class ToneProvider(Protocol):
    def get_tone_profile(self) -> "ToneProfile": ...

@runtime_checkable
class MemorySummarizer(Protocol):
    def summarize(self, max_tokens: int = 512) -> str: ...
```

### 9.2 `obc_soul/_protocols.py`

```python
@runtime_checkable
class MemoryStore(Protocol):
    async def read_layer(self, layer: str) -> dict: ...
    async def write_layer(self, layer: str, data: dict) -> None: ...
    async def append_events(self, events: list[dict]) -> None: ...
    def get_profile_change_callback(self) -> Callable | None: ...

@runtime_checkable
class PoolStore(Protocol):
    async def purge_by_embedding(self, content_ids: list[str]) -> int: ...
    async def get_pool_stats(self) -> dict: ...

@runtime_checkable
class EventNormalizer(Protocol):
    def normalize(self, raw_event: dict) -> dict: ...
```

### 9.3 `obc_discovery/_protocols.py`

```python
@runtime_checkable
class CandidateStore(Protocol):
    async def write_candidates(self, candidates: list[dict]) -> int: ...
    async def get_pool_snapshot(self) -> dict: ...

@runtime_checkable
class ImageCache(Protocol):
    async def get_or_fetch(self, url: str) -> bytes | None: ...
    async def exists(self, url: str) -> bool: ...
```

### 9.4 `obc_runtime/_protocols.py`

```python
@runtime_checkable
class RuntimeConfig(Protocol):
    @property
    def scheduler(self) -> SchedulerConfig: ...
    @property
    def sources(self) -> SourcesConfig: ...

@runtime_checkable
class SourceAdapter(Protocol):
    @property
    def platform(self) -> str: ...
    async def fetch(self, keywords: list[str], limit: int) -> list[dict]: ...
```

---

## 10. 关键文件引用

### 提取包的 pyproject.toml 模板

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "obc-llm"
version = "0.1.0"
description = "OpenBiliClaw LLM Provider System"
requires-python = ">=3.11"
dependencies = [
    "openai>=1.0.0",
    "anthropic>=0.30.0",
]

[project.optional-dependencies]
gemini = ["google-genai"]
```

### 主项目 monorepo 配置

```toml
# 040-OpenBiliClaw/pyproject.toml
[project]
name = "openbiliclaw"
...

[tool.hatch.envs.default]
features = ["dev"]

[tool.hatch.envs.default.scripts]
sync = "pip install -e packages/obc-llm -e packages/obc-soul -e packages/obc-discovery -e packages/obc-runtime"
```

---

> **下一步行动**：确认本方案后，从阶段 1（LLM 提取）开始实施。每个阶段完成后验证可用性，再进入下一阶段。