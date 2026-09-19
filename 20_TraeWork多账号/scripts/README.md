# scripts/ — 使用速查

> 机制原理、踩坑记录、FAQ 全在上级目录的 [README.md](../README.md)，以那份为准，本文件不重复维护。

## 日常三件事（双击即可）

| 想做什么 | 双击 |
|---|---|
| 切账号 | `切换Trae账号.command` |
| 新账号收尾（合成档案→自检→签到 一条龙） | `一键自动收尾.command` |
| 添加账号全自动入库（捕获+合成，15s 生效） | `安装守护开机自启.command`（装一次，登录自启）|

## 其他入口

- `捕获新账号.command` — 客户端登录后手动收尾
- `开空白客户端.command` — 开第二个客户端实例（不动主客户端）
- `自动捕获守护.command` — 守护的手动启停（前台跑，关终端即停）
- `traework-multi-open.sh` — 多实例批量管理

## 命令行

```bash
PY=/Users/imac/.workbuddy/binaries/python/envs/default/bin/python
env -u PYTHONHOME -u PYTHONPATH $PY twa_checkin.py all          # 全员签到+刷余额
env -u PYTHONHOME -u PYTHONPATH $PY twa_switch_account.py status # 档案/登录状态
env -u PYTHONHOME -u PYTHONPATH $PY twa_synth.py verify          # 档案自检
env -u PYTHONHOME -u PYTHONPATH $PY twa_watch.py --status        # 守护状态
```

⚠️ `accounts.json` / `storage.json` 里是**明文 token**，不要入库、不要外传。
