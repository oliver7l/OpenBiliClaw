# tests/_attic — 测试归档区

非破坏性归档（git 追踪，可随时恢复）。pytest 通过 conftest.py 的 collect_ignore_glob 不收集本目录。

| 归档项 | 原因 |
|---|---|
| runtime/（12 文件） | browser/account 集成测试，240s 跑不完，从未进过门禁 |
| web/test_web_guided_init_e2e.py | Playwright UI 测试，7 例 30s 超时，依赖真实浏览器 |
| desktop/test_desktop_web_multimodal_settings.py | 断言旧版前端 JS 字符串，前端已重构 |
| openclaw/test_openclaw_proactive_e2e.py | e2e 异步条件超时，环境敏感 |
| agent/、js/ | 空壳目录（无可收集测试） |
