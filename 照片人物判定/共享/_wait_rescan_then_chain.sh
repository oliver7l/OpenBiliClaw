#!/bin/zsh
# 等 SCRFD 重扫收尾，然后自动跑一遍全链路（重扫期间跑会读不到 18 归档、且会抢写锁）。
#
# 判定收尾：scan_faces.py 正常结束会打印以「完成」开头的一行；
# ps 在沙箱不可用，所以只能靠日志尾行判断。
# 启动前先确认库没被别的 apply/train 占着（链路自身会写库）。
set -u
ROOT="/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw"
LOG="$ROOT/19_统一相册库/_rescan_scrfd_20260919.log"
SH="$ROOT/照片人物判定/共享"
PY="/Users/imac/.workbuddy/binaries/python/envs/default/bin/python"
OUT="$SH/_auto_chain.log"

echo "=== 守护启动 $(date '+%F %T') ===" > "$OUT"
for i in $(seq 1 90); do
  if tail -3 "$LOG" 2>/dev/null | grep -q "^完成"; then
    echo "[$(date '+%T')] 检测到重扫完成标记" >> "$OUT"
    break
  fi
  if [ "$i" = "90" ]; then
    echo "[$(date '+%T')] 等待超时（90 分钟），放弃" >> "$OUT"
    exit 1
  fi
  sleep 60
done

sleep 20   # 让 sqlite WAL 落盘
echo "[$(date '+%T')] 开始跑 run_chain.py（apply 真写库；妈妈 会被锚点护栏跳过）" >> "$OUT"
cd "$SH"
"$PY" -u run_chain.py >> "$OUT" 2>&1
echo "[$(date '+%T')] run_chain 退出码 $?" >> "$OUT"
