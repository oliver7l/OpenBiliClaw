# 中性契约层（core）

> 分层图最底层：只放**跨包共享的值对象与异常**，无 IO、无配置、运行时不 import 项目内任何其它包。
> 存在的唯一理由是「解环」——把两处会导致循环/反向依赖的类型下沉到这里。

## 概述

`core/` 只有两个文件、204 行，但它是**上游 13 个 sources adapter + obc-discovery 引擎 + 推荐引擎**的公共类型来源。
它不做任何事，只定义「大家都能认的对象」。

| 文件 | 内容 | 解的是哪个环 | 行数 |
|------|------|--------------|-----:|
| `contracts.py` | `DiscoveredContent`（值对象）、`DiscoveryStrategy`（策略 ABC） | **K6b**：sources 侧 adapter 引用 `obc_discovery.engine` 的同名类型 → sources 与 discovery 同层耦合 | 165 |
| `x_errors.py` | `XClientError` + 4 个叶子异常 | **K5**：`storage.x_health` 要捕获 X 异常，而异常定义在 `sources.x_client` → storage 反向依赖 sources | 34 |

两个环的共同点：**都不会报错**，只在特定加载顺序下才炸，且会让分层图彻底失去意义。下沉是纯搬家，行为零变化。

## 组件：core.contracts

### DiscoveredContent —— 全平台内容的统一值对象（39 个字段）

字段按用途分 8 组（顺序即代码顺序）：

| 组 | 字段 | 说明 |
|---|---|---|
| B 站遗留 | `bvid` `title` `up_name` `up_mid` `cover_url` `duration` | 新代码优先用多来源组 |
| 社媒指标 | `view_count` `like_count` `favorite_count` `collect_count` `comment_count` `share_count` `danmaku_count` `reply_count` `retweet_count` `bookmark_count` | 各平台含义不同但共用字段名 |
| 语义标签 | `tags` `topic_key` `topic_group` `style_key` `franchise_key` | `franchise_key` = LLM 判定的 IP/系列（原神、ChatGPT…），用于同 IP 降权与「一条 dislike 冷却整个 IP」 |
| 相关性 | `relevance_score` `relevance_reason` `score_threshold` | `score_threshold` 是策略内部准入线，**不是内容属性** |
| 池内预生成 | `pool_expression` `pool_topic_label` `candidate_tier` | 文案由 curator 单独写入 |
| 多来源 | `content_id` `content_url` `source_platform` `author_name` `body_text` `content_type` `content_text` | `content_text` 只服务阅读库 |
| 溯源 | `source_strategy` `source_keyword_id` `discovered_at` `last_scored_at` | `source_keyword_id` = 统一关键词规划器的产出词 id，供 yield 归因 |

### `__post_init__` 的四条派生规则（与显式值的关系：显式>派生）

| 条件 | 派生结果 |
|---|---|
| `content_id` 为空且有 `bvid` | `content_id = bvid` |
| `source_platform` 为空且有 `bvid` | `source_platform = "bilibili"` |
| `author_name` 为空且有 `up_name` | `author_name = up_name` |
| `content_url` 为空且有 `bvid` | `https://www.bilibili.com/video/{bvid}` |
| `content_url` 为空 且 `source_platform == "xiaohongshu"` 且 `content_id` | `https://www.xiaohongshu.com/explore/{content_id}` |

> 值对象**不猜平台**：没有 `bvid` 也不会默认填 `bilibili`；落库阶段的兜底在
> `Database.cache_content()`（`source_platform` 默认 `bilibili`）。

### `to_cache_kwargs()` —— 落库映射的**唯一**入口

两个生产调用方：`obc_discovery/engine.py:2636`（发现结果落库）、`recommendation/engine.py:1247`（池积压分类落库）。

⚠️ **这是全项目最容易静默丢数据的接缝**：`cache_content(bvid, **kwargs)` 内部是
`kwargs.get("<列名>", 默认值)` —— **多传的键被静默忽略，漏传的键静默落默认值**。
所以「新加一个字段却忘了加进映射」不报错、不告警，那份数据只是永远进不了库。

39 个字段 → 32 个落库键，差额全部有明确理由（下表冻结在
`tests/core/test_contracts.py::_NOT_MAPPED_BY_DESIGN`）：

| 字段 | 为什么不进映射 |
|---|---|
| `bvid` | 以 `content_id` 落库，列名 `bvid` 由调用方单独传 |
| `source_strategy` | 落库列名叫 `source`（**唯一的别名键**） |
| `content_text` | 正文只进 `articles.content_text`，刻意不污染推荐池缓存 |
| `score_threshold` | 策略内部阈值，不是内容属性 |
| `pool_expression` `pool_topic_label` | 由 curator 单独写；`cache_content` 用 `COALESCE` 保留既有值 |
| `discovered_at` `last_scored_at` | 列由 SQL `CURRENT_TIMESTAMP` 生成 |

## 组件：core.x_errors

```
RuntimeError
└── XClientError                       # 基类；未知 X 错误按「限流」处理
    ├── XMissingCookieError  → missing_cookie  （auth_token / ct0 缺失，惰性抛出）
    ├── XAuthError           → expired_cookie  （401）
    ├── XBlockedError        → blocked         （403）
    └── XRateLimitError      → rate_limited    （429）
```

四个叶子**互为兄弟**（不是链）：否则 `storage.x_health` 里的 `isinstance` 顺序检查会把 401/403 混为一谈。

再导出锚点：`sources/x_client.py` 从 core import 并写进自己的 `__all__`，**类型身份不变**
（既有 `except XAuthError` 与 `from ...x_client import XAuthError` 全部照旧）。

## 再导出与依赖方向（谁从哪拿类型）

| 位置 | 取用方式 | 说明 |
|---|---|---|
| `sources/**`（13 个文件） | `TYPE_CHECKING` 下 `from openbiliclaw.core.contracts import DiscoveredContent` | 运行时不加载，只为注解 |
| `packages/obc-discovery/obc_discovery/engine.py` | 运行时 `from openbiliclaw.core.contracts import ...`（re-export） | 引擎与全部策略零改动 |
| `storage/x_health.py` | 运行时 `from openbiliclaw.core.x_errors import ...` | K5 的收益点 |
| `sources/x_client.py` | 运行时 import core 后纳入 `__all__` | 保持旧 import 路径 |

方向规则：**领域包 → core 合法；core → 任何项目内包都非法**。

## 回归网

| 文件 | 用例 | 守什么 |
|---|---:|---|
| `tests/core/test_contracts.py` | 23 | 映射完整性（字段 ↔ 落库键，含白名单防腐）、`content_text` 不落池、跨平台 URL 派生、`DiscoveryStrategy` 抽象契约（`name` 必须是 property）、`obc_discovery.engine` 再导出的**类型身份**（`is` 判定） |
| `tests/core/test_x_errors.py` | 24 | 异常层级（叶子互为兄弟）、两条 import 路径**同一个类对象**、`storage.x_health` 的分类映射端到端有效 |
| `tests/test_layering_contracts.py` | 12 | K5（storage 不得依赖 sources）／K6b（sources 加载期不得引 discovery、soul）／core 运行时不得依赖项目内包；**AST 判据**区分「模块级 import / `if TYPE_CHECKING` / 函数内延迟 import」，附带阳性-阴性自检 |

分层闸门用 AST 而不是正则，原因：正则分不清 `if TYPE_CHECKING:` 里的 import、函数体内的
延迟 import 与模块级 import，而这三者的运行时含义完全不同。

## 已知边界与待办

- 分层闸门只管 `storage` / `sources` / `core` 三个方向。`runtime`、`recommendation`、`api` 之间的
  依赖方向**尚未纳入**（无环不代表分层清晰）。
- `core/contracts.py` 里 `DiscoveredContent` 的 39 个字段全部扁平堆放，跨平台语义靠注释约束
  （例如 `view_count` 在小红书侧其实是「点赞数」）。要真正区分需要拆子结构，属大改，未做。
- `core/` 的 `__init__.py` 只写了分层规则说明，**未汇总 re-export**：调用方必须
  `from openbiliclaw.core.contracts import ...` 写出具体子模块。
