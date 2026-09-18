# 乐仔成长相册模块（lezai）

> **真值源铁律**：照片数据（原图、识别模型、分类目录）的真值源在外部
> 源库（项目内 `08_乐仔相册/乐仔相片库`，2026-09-18 自 030-夸克网盘迁入），本模块只是**发布视图**。不要在本项目里
> 改照片/分类——要改就改源库，再同步过来。

---

## 1. 模块职责

把「乐仔相片库」（InsightFace 人脸识别 + 幼儿园/家庭双通道判据 + 成长分析
平台）集成进 OpenBiliClaw 对外提供：

- `GET /lezai/` —— 成长分析平台页（自包含 HTML，数据内嵌，无 API 依赖）
- `GET /lezai/thumbs/<ID>.jpg` —— 缩略图（QZ*=QQ群相册源，KU*=夸克源）
- `GET /lezai/分析报告.md` 等静态资产
- **桌面端 tab**：`/web/lezai`（顶栏「🍼 乐仔」按钮）——SPA 页
  `lezaiPage` 用 iframe 懒加载内嵌 `/lezai/`；路由白名单
  `_desktop_page_names` 含 `lezai`，接线全在 `app.js`
  （`DESKTOP_PAGE_ROUTES.lezai` + `openLezaiPage`），无新 JS 文件。

当前规模：**2576 张**（幼儿园 525 / 家庭 2041 / 待确认 10），thumbs 66MB。

平台两个分析 section（2026-09-17）：
- **同伴网络**：数据由源库侧 `042-QQ相册备份/companion_network_v2.py` 产出
  （21,098 脸 kNN+Chinese Whispers 聚类 → 排除乐仔簇 → 数同框），
  `companions.json` + `同伴接触表-top15.jpg` 随 sync 分发；人工命名写
  `companion_names.json`（042/companion_out/）后重跑 build_site.py 即显真名；
- **活动细分类**：`activity_classify.py` 产出 `activities.json`
  （相册专辑真值优先 + Vision 标签弱信号）。

## 2. 目录与路径锚点

| 内容 | 位置 | 说明 |
|---|---|---|
| 模块代码 | `src/openbiliclaw/lezai/` | `paths.py`（锚点）+ `sync.py`（同步）+ `__main__.py`（CLI） |
| 页面资产 | `src/openbiliclaw/web/lezai/` | index.html 等 7 个轻量文件，**随包入 git** |
| 缩略图 | `src/openbiliclaw/web/lezai/thumbs/` | 个人照片数据，**.gitignore 排除**（同 `web/clone/sites/` 先例） |
| 外部源库 | `/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/08_乐仔相册/乐仔相片库/` | 真值源；可用 `OBC_LEZAI_SOURCE_DIR` 环境变量覆盖 |

## 3. 同步流程（源库更新后）

```bash
cd <项目根> && .venv/bin/python -m openbiliclaw.lezai          # dry-run 看计划
.venv/bin/python -m openbiliclaw.lezai --apply                  # 执行同步
.venv/bin/python -m openbiliclaw.lezai --apply --prune          # 兼清源库已删的孤儿缩略图
```

- 幂等：按「相对路径 + 文件大小」比对，无变化不重拷；
- 只同步 7 个轻量文件 + thumbs/；**原图目录（01_幼儿园 / 02_家庭生活 /
  03_待确认）不同步**——体积大，且源库才是唯一真值源；
- 同步后改的是包内静态文件，**必须 `pm2 restart 84`** 才对外生效
  （静态挂载无版本号缓存机制，浏览器侧硬刷新即可）。

## 4. 路由挂载（`api/_web_ui_routes.py`）

```python
app.mount("/lezai/thumbs", StaticFiles(...))   # ⚠️ 必须注册在 /lezai 之前
app.mount("/lezai", StaticFiles(..., html=True))
```

⚠️ 顺序铁律：`/lezai` 前缀会吞掉 `/lezai/thumbs`， thumbs 挂载必须在前面。
auth：路径不以 `/api` 开头 → `_is_public` 恒放行（与 `/web`、`/knowledge-graph`
同待遇）。

## 5. 已知边界 / 待续

- 源库持续增量（夸克网盘目录还在涨），重跑识别人脸管线仍在
  `042-QQ相册备份/` 与源库脚本，本项目不负责识别，只负责发布；
- `03_待确认/` 10 张归位后：先在源库重跑分类，再同步；
- 页面数据内嵌在 index.html（`const DATA`），更新数据依赖源库
  `build_site.py` 重新生成后再同步。
