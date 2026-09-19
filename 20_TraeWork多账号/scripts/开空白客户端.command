#!/bin/zsh
# 开一个「空白第二实例」，专门用来登录新账号 —— 主客户端完全不动
#
# 为什么需要它：两份 /Applications/TRAE SOLO CN*.app 的 package.json 里 name 都是
# "TRAE SOLO CN"，数据目录同名，直接双击副本会抢同一个目录。多开必须显式给
# --user-data-dir。
#
# 用法：双击 → 在弹出的窗口里用新账号（手机号+验证码）登录 → 登录完成后
#       双击同目录的「捕获新账号.command」收尾即可。
#       主客户端 `TRAE SOLO CN` 当前登录的账号不受任何影响。
cd "$(dirname "$0")" || exit 1
APP="/Applications/TRAE SOLO CN.app/Contents/MacOS/Electron"
RES="/Applications/TRAE SOLO CN.app/Contents/Resources/app"
DATA="$HOME/Library/Application Support/TRAE SOLO CN 3"

if [[ ! -x "$APP" ]]; then
  echo "✗ 找不到 $APP"; printf "按回车关闭…"; read -r _; exit 1
fi

# 已经在跑就别重复开。注意必须限定 Electron 主进程——crashpad 等残留辅助进程的
# 命令行里也带 --user-data-dir，用宽泛 pgrep -f 会误判成"已在运行"（2026-09-19 实测踩坑）
if pgrep -f -- "TRAE SOLO CN.app/Contents/MacOS/Electron.*--user-data-dir=$DATA" >/dev/null; then
  echo "已有一个指向该数据目录的实例在运行，直接切到它。"
  open -a "TRAE SOLO CN"
  printf "按回车关闭…"; read -r _; exit 0
fi

mkdir -p "$DATA"
echo "启动空白实例："
echo "  数据目录 = $DATA"
echo "  主客户端 TRAE SOLO CN 的数据目录不受影响"
echo
# 注意：直接跑 Electron 二进制必须把 Resources/app 作为第一个参数传进去，
# 否则它会报 "bad option: --user-data-dir=..." 秒退（2026-09-19 实测踩坑）。
# 另外 ELECTRON_RUN_AS_NODE=1 会让客户端以纯 Node 模式启动并报
# "does not provide an export named 'BrowserWindow'"，必须显式去掉。
unset ELECTRON_RUN_AS_NODE
"$APP" "$RES" "--user-data-dir=$DATA" --no-sandbox >/dev/null 2>&1 &

echo "已在后台启动，等它开出窗口后用新账号登录即可。"
echo "登录完成后：双击同目录的「捕获新账号.command」。"
printf "按回车关闭本窗口…"; read -r _
