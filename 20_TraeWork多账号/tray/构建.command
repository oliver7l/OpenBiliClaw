#!/bin/zsh
# TraeBar 构建脚本：改完 TraeBar.swift 后双击本文件重新编译打包。
set -e
cd "$(dirname "$0")"

echo "编译 TraeBar.swift …"
mkdir -p TraeBar.app/Contents/MacOS TraeBar.app/Contents/Resources
swiftc -O TraeBar.swift -o TraeBar.app/Contents/MacOS/TraeBar
codesign --force -s - TraeBar.app

# 已在运行就先停掉再重启，让新版本生效
pkill -f "TraeBar.app/Contents/MacOS/TraeBar" 2>/dev/null && sleep 1 || true
open TraeBar.app
echo "✅ 构建完成，TraeBar 已重启"
printf "按回车关闭…"; read -r _
