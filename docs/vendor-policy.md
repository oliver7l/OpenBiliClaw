# 二创 / GitHub 下载项目 · 统一管理机制（vendor-policy）

> **一句话**：每个二创项目 = 一个**独立 git 仓库**，主仓只用 `.gitignore` 排除它、不在主仓管理历史；另用一份 `vendor-registry.json` 做全景登记，用 `scripts/vendor_manage.py` 统一扫描/登记/体检。

---

## 1. 为什么必须这样管

仓库里嵌了大量「从 GitHub / B站 下载、你改过」的衍生项目（`二创/*`、`tools/*`、
`03_万声科技/GitHub仓库存档/*`、`12_开源项目研究/references/*`、`18/19_*` 等，已识别 70+ 个）。
直接 `git add` 它们会踩三个坑：

- **gitlink 污染**：目录里带 `.git`，主仓把它当子模块指针（内容进不来、还污染历史）。
- **改没改 / remote 在哪理不清**：几十个项目散落各处，谁改过、上游是谁全靠记。
- **大文件/密钥混入**：照片 116G、模型 767M、`.env` 密钥——一旦入库撑爆仓库或泄密。

## 2. 核心三原则

1. **独立 `.git`（独立仓库）**——二创项目的改动提交进它**自己的仓库**，主仓恒干净。
2. **主仓 `.gitignore` 排除其整目录**——防 gitlink。若其中有少量**代码脚本**想入库，用
   `git add -f <具体 .py>` 精准收编（数据/模型/依赖仍忽略）。
3. **`vendor-registry.json` 登记**——`path / upstream / remote / note / updated` 作「单一事实来源」。

## 3. 工具：`scripts/vendor_manage.py`

```bash
python3 scripts/vendor_manage.py scan          # 全仓扫描：哪些二创、改没改(*N)、remote
python3 scripts/vendor_manage.py bulk          # 全仓批量登记到注册表
python3 scripts/vendor_manage.py tag           # 有本地改动的仓库打「已本地修改」标
python3 scripts/vendor_manage.py register <path> --upstream <URL> --note "用途"
python3 scripts/vendor_manage.py usage <path> applied|research|archive   # 标注用途
python3 scripts/vendor_manage.py list          # 看注册表（叠加 scan 状态）
python3 scripts/vendor_manage.py status <path> # 单仓体检（json）
```

> `usage` 二选三：**applied**（已应用/移植到本项目）· **research**（仅调研参考）· **archive**（归档）。
> `register --usage` 可一并标注；`bulk` 会按目录自动把 `references/`、`GitHub仓库存档/` 标为 research。

`scan` 输出含义：`*N` = 该子仓有 N 项未提交本地改动；`-` = 干净；空白 = 未登记。

> **按用途归类（不搬文件）**：`scan --usage applied|research` / `list --usage applied|research`
> 一处看同类；另建了机内软链入口 `_应用/`（applied 9 个）与 `_调研/`（research 61 个）
> 指向各项目（不动原目录/服务/仓库，已 gitignore 不入库）。

## 4. 分类规则

| 类型 | 处置 | 例子 |
|---|---|---|
| 常用 / 常改 / 要写历史 | 独立 `.git` + 登记 + remote | `二创/*`、`tools/*` |
| 有本地改动 | `tag` 打标，改动提交进子仓 | 7 个（ChatLab/xinli-test/EHR-django…）|
| **已应用/移植到本项目** | `usage applied` | 运行中的 tools 服务、你自己的 fork |
| **仅调研参考** | `usage research`（bulk 自动标）| `12_开源项目研究/references/*`、`03_万声科技/GitHub仓库存档/*` |
| 纯他人仓库存档（只看不改）| 整目录忽略，可不登记 | 同上 |
| 含密钥 / 超大（照片/模型）| 整目录忽略，绝不入库 | `tools/*/.env`、`19_统一相册库`、`照片人物判定` |
| 少量作者代码在数据目录里 | `git add -f` 收编代码，数据忽略 | `18_照片分组/*.py`、`19_统一相册库/tools/*.py` |

## 5. 新增一个二创的标准流程

```bash
# 1) 克隆到 二创/<name>/（自带独立 .git）
git clone <upstream> "二创/<name>"
# 2) 主仓 .gitignore 加一行：/<该目录>/（防 gitlink）
# 3) 登记
python3 scripts/vendor_manage.py register "二创/<name>" --upstream <URL> --note "用途"
```

之后你在它里面的改动，一律 `git -C 二创/<name> commit` 提交进它自己的仓库；主仓不看它。

## 6. 与主仓数据/代码分流的衔接

同一原则向下延伸到「大文件/纯数据」：`data/`、`logs/`、照片/模型目录靠 `.gitignore` 排除；
作者脚本按「代码要提交」用 `-f` 收编进主仓。这样主仓只含**本仓库自己的代码 + 文档 + 数据索引**，
二创项目与大数据一律在外且可追溯。

## 7. 相关文件

- 工具：`scripts/vendor_manage.py`
- 登记表：`vendor-registry.json`（当前 70 项，7 项已打「本地修改」标）
- 本机制：`docs/vendor-policy.md`