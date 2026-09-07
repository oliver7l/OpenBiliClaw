# 克隆系统

> 管理和浏览克隆的网站，支持已有站点导入、新站点克隆、分类标签管理与本地预览。

## 概述

`clone/` 包实现了完整的克隆站点管理系统，对标日记模块的独立子系统架构。提供从站点导入、数据管理、克隆引擎到前端可视化的一站式能力。

| 组件 | 职责 | 核心文件 |
|------|------|----------|
| 数据模型 | 克隆站点、标签、统计信息的 Pydantic 模型 | `models.py` |
| 存储层 | SQLite 表管理、CRUD、检索、统计 | `store.py` |
| 业务层 | 站点管理、批量导入、克隆新站点 | `service.py` |
| 克隆引擎 | wget/httrack/playwright 三种克隆方式 | `cloner.py` |
| API 层 | RESTful 接口（在 `api/app.py` 中） | `api/app.py` |
| 前端页面 | 桌面端克隆站点浏览与管理界面 | `web/desktop/index.html` |
| 站点存储 | 克隆站点的文件存放目录 | `web/clone/sites/` |

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 站点 CRUD | ✅ | 创建、读取、更新、删除克隆站点记录 |
| 多条件筛选 | ✅ | 按分类、状态、标签、关键词筛选 |
| 统计概览 | ✅ | 总数、总大小、总文件数、分类分布 |
| 标签管理 | ✅ | 自动同步标签计数，支持按标签筛选 |
| 批量导入 | ✅ | 扫描 `web/clone/sites/` 目录，自动导入已有站点 |
| 站点预览 | ✅ | 点击卡片直接在新标签页打开克隆站点 |
| 克隆新站点 | ✅ | 支持 wget/httrack/playwright 三种克隆引擎 |
| 前端页面 | ✅ | 卡片式布局，显示名称、大小、文件数、分类、标签 |

## 数据模型

### CloneSite（克隆站点）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 唯一 ID |
| `name` | str | 站点名称 |
| `slug` | str | URL 标识（唯一） |
| `source_url` | str | 原始来源 URL |
| `local_path` | str | 本地相对路径 |
| `description` | str | 站点描述 |
| `category` | str | 分类：website / single-page / tool / game / art / travel / other |
| `status` | CloneStatus | 状态：cloned / cloning / failed / moved / archived |
| `size_bytes` | int | 文件总大小 |
| `file_count` | int | 文件总数 |
| `tags` | list[str] | 标签列表 |
| `created_at` / `updated_at` | datetime | 创建/更新时间 |

### CloneStatus（站点状态）

```python
class CloneStatus(str, Enum):
    CLONED = "cloned"       # 已克隆完成
    CLONING = "cloning"     # 正在克隆中
    FAILED = "failed"       # 克隆失败
    MOVED = "moved"         # 源站已迁移
    ARCHIVED = "archived"   # 已归档
```

## 公开 API

### Python API

```python
from openbiliclaw.clone import CloneService, CloneStore, CloneSiteCreate, CloneStatus

# 初始化服务
from openbiliclaw.storage.database import Database
db = Database("data/openbiliclaw.db")
db.initialize()
store = CloneStore(database=db)
service = CloneService(store=store, sites_dir="src/openbiliclaw/web/clone/sites")

# 创建站点
site = service.create_site(CloneSiteCreate(
    name="My Site",
    slug="my-site",
    source_url="https://example.com",
    local_path="my-site",
    category="website",
    tags=["demo", "test"],
))

# 列出站点
sites, total = service.list_sites(category="website", search="关键词")

# 批量导入已有站点
imported = service.import_existing_sites("src/openbiliclaw/web/clone/sites")

# 克隆新站点
from openbiliclaw.clone import CloneRequest
site = service.clone_new_site(CloneRequest(
    url="https://example.com",
    name="Example Site",
    depth=1,
))
```

### REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/clone/sites` | 列出站点（支持 limit/offset/category/status/tag/search） |
| GET | `/api/clone/sites/{id}` | 获取站点详情 |
| POST | `/api/clone/sites` | 创建站点 |
| PUT | `/api/clone/sites/{id}` | 更新站点 |
| DELETE | `/api/clone/sites/{id}` | 删除站点 |
| GET | `/api/clone/stats` | 获取统计信息 |
| GET | `/api/clone/tags` | 列出所有标签 |
| POST | `/api/clone/import` | 扫描导入已有站点 |
| POST | `/api/clone/clone` | 克隆新网站 |

### 站点预览

克隆站点通过 `/clone/sites/{slug}` 路径直接访问，由 FastAPI 静态文件服务托管。

## 数据库表

### clone_sites

```sql
CREATE TABLE clone_sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    source_url TEXT DEFAULT '',
    local_path TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'other',
    status TEXT DEFAULT 'cloned',
    size_bytes INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_clone_sites_category ON clone_sites(category);
CREATE INDEX idx_clone_sites_status ON clone_sites(status);
CREATE INDEX idx_clone_sites_slug ON clone_sites(slug);
```

### clone_tags

```sql
CREATE TABLE clone_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_clone_tags_count ON clone_tags(count DESC);
```

## 克隆引擎

`cloner.py` 提供三种克隆方式，按优先级自动选择：

1. **wget**（首选）：支持递归克隆、链接转换，`--mirror --convert-links --adjust-extension --page-requisites`
2. **httrack**（备选）：功能完整的网站克隆工具，支持深度控制
3. **playwright**（终极备选）：无头浏览器保存页面，仅支持单页克隆

克隆深度支持 1-3 级，默认 1 级。

## 设计决策

### 1. 独立存储层

与日记模块相同，`CloneStore` 独立管理自己的数据表，而非将所有方法塞入主 `Database` 类。好处：
- 模块边界清晰，克隆相关的 SQL 集中在一个文件
- 可以独立初始化和测试
- 复用主数据库连接，数据存储在 `openbiliclaw.db` 中

### 2. 站点文件与数据库分离

站点文件存储在 `web/clone/sites/` 目录下，数据库只记录元数据。这样：
- 站点文件可以直接通过静态文件服务访问
- 数据库记录轻量，包含筛选和搜索所需的所有字段
- 删除站点记录后，文件可以保留或单独清理

### 3. 懒加载服务

API 层采用懒加载模式，`_get_clone_service()` 在首次调用时初始化，降低启动开销，与日记模块保持一致。

### 4. 批量导入的幂等性

`import_existing_sites()` 按 slug 去重，已导入的站点不会重复导入。支持增量更新。

## 与日记模块的对比

| 维度 | 日记模块 | 克隆系统 |
|------|----------|----------|
| 数据模型 | DiaryEntry + DiaryAnalysis + 5 个关联表 | CloneSite + CloneTags 2 个表 |
| 存储层 | DiaryStore（独立 schema） | CloneStore（独立 schema） |
| 业务层 | DiaryService（CRUD + LLM 分析） | CloneService（CRUD + 导入 + 克隆） |
| 外部依赖 | LLM/Embedding 服务 | wget/httrack/playwright |
| 前端页面 | 丰富的分析视图 | 卡片式站点导航 |
| 复杂度 | 高（多表关联 + AI 分析） | 中（单表 + 文件管理） |

## 后续规划

- [ ] 克隆站点缩略图/截图预览
- [ ] 站点编辑功能（名称、描述、标签、分类在线编辑）
- [ ] 克隆进度实时推送（WebSocket）
- [ ] 批量克隆/定时同步
- [ ] 站点健康检查（检查源站是否可访问）
- [ ] 移动端适配
- [ ] 克隆站点搜索（全文搜索站点名称和描述）
- [ ] 导出站点清单（JSON/CSV）