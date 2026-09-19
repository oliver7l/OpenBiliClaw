#!/bin/zsh
# 全自动收尾：不用登录、不用打开助手，一条命令把该做的都做完
#
# 干什么：
#   ① synth  —— 给 accounts.json 里「只有浏览器 token、没有客户端登录态」的账号合成档案
#              （切换靠档案，有了它菜单里就能选到这些账号）
#   ② verify —— 自检每份档案：blob 能否解密、uid 与档案目录是否一致
#   ③ checkin—— 逐账号签到（status → claim → status）+ 刷新余额快照
#
# 全程只读写 ~/Library/Application Support/cn.traework.assistant/，
# 改 accounts.json 前自动备份；合成的档案带 synthesized 标记，可用
#   python twa_synth.py rollback --uid <uid>
# 精确撤销，不会碰真正捕获的档案。
cd "$(dirname "$0")" || exit 1
PY=/Users/imac/.workbuddy/binaries/python/envs/default/bin/python

echo "=============================================="
echo " 全自动收尾：合成档案 → 自检 → 签到"
echo "=============================================="
echo

echo "① 合成缺失档案"
env -u PYTHONHOME -u PYTHONPATH "$PY" twa_synth.py synth
echo

echo "② 档案自检"
env -u PYTHONHOME -u PYTHONPATH "$PY" twa_synth.py verify
echo

echo "③ 签到 + 余额刷新"
env -u PYTHONHOME -u PYTHONPATH "$PY" twa_checkin.py all
echo

echo "----------------------------------------------"
echo "签到结果里若出现「活动不可用 / enable=false」，说明该账号在服务端"
echo "还没激活签到活动（通常是没在客户端里登录过）。切到这个账号、让客户端"
echo "真正登录一次后再跑一遍本脚本即可。"
echo
printf "按回车关闭这个窗口…"
read -r _
