# 二创 / GitHub 下载项目统一管理约定（vendor-policy）

> 场景：本仓库内嵌了一批「从 GitHub / B站 下载、你做过本地修改」的二创项目（`二创/*`、
> `tools/*`、`03_万声科技/GitHub仓库存档/*`、`12_开源项目研究` 等）。这类项目最常见的坑：
> 嵌套 `.git` 会被主仓当成 gitlink（污染、内容进不来）、改没改/上游在哪理不清、大文件混进主仓。

## 核心约定（三件事）

1. **各二创项目保留独立 `.git`（独立仓库）**，你在它里面的修改提交到**它自己的仓库**，不改主仓。
   - 未自带的下载项目可 `git init` / `git clone` 保持独立。
2. **主仓 `.gitignore` 排除二创项目整目录**（避免 gitlink 污染）——例如 `tools/`、`19_统一相册库/`。
   - 若其中有少量**代码脚本**想入库，用 `git add -f <具体 .py>` 精准收编（数据/模型/依赖仍忽略）。
3. **在 `vendor-registry.json` 登记**每个二创：`{path, upstream, remote, note}`，作为「单一事实来源」。

## 工具

`scripts/vendor_manage.py`：

```bash
python3 scripts/vendor_manage.py scan            # 全仓扫描嵌套 git 仓库并体检（改动数/提交数/remote）
python3 scripts/vendor_manage.py register 二创/tvbox-web --upstream https://github.com/x/tvbox-web --note "改动说明"
python3 scripts/vendor_manage.py list            # 展示注册表（叠加 scan 状态）
python3 scripts/vendor_manage.py status <path>   # 单仓体检
```

`scan` 输出的 `R` 标记表示该路径已登记；`*N` 表示该二创有 N 项未提交的本地改动（提醒你回各自仓库提交）。

## 归类建议

- **需要写历史/长期沿用/常改** → 保留独立 `.git` + 登记在注册表（`二创/`、`tools/`）。
- **纯资料/他人仓库存档（只看不改）** → 整目录忽略即可，不登记（如 `03_万声科技/GitHub仓库存档`）。
- **有保密性（.env / 密钥）或超大** → 整目录忽略、绝不入库（如 `tools/*/.env`、`19_统一相册库`）。

## 新增一个二创的标准流程

```bash
# 1) 下载或克隆到二创/<name>/
git clone <upstream> "二创/<name>"          # 自带独立 .git
# 2) 确保主仓不把它当 gitlink：主仓 .gitignore 加 <该目录>/
# 3) 登记
python3 scripts/vendor_manage.py register "二创/<name>" --upstream <URL> --note "用途/改动"
```