#!/usr/bin/env bash
#
# TraeWork CN 多开管理脚本
# 原理：使用不同的 --user-data-dir 启动多个实例，数据完全隔离
# 使用方法: chmod +x traework-multi-open.sh && ./traework-multi-open.sh
#

set -euo pipefail

APP_PATH="/Applications/TRAE SOLO CN.app"
ELECTRON="$APP_PATH/Contents/MacOS/Electron"
APP_RES="$APP_PATH/Contents/Resources/app"
BASE_DATA_DIR="$HOME/Library/Application Support/TRAE SOLO CN"

# ==================== 颜色输出 ====================
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
title() { echo -e "${CYAN}${BOLD}$1${NC}"; }

# 检查原版应用
[ ! -d "$APP_PATH" ] && error "未找到 TraeWork: $APP_PATH"

# 检测运行中的实例数量
detect_instances() {
    local count=0
    while IFS= read -r line; do
        if echo "$line" | grep -q "MacOS/Electron" && ! echo "$line" | grep -q "Helper"; then
            count=$((count + 1))
        fi
    done < <(ps aux | grep "$APP_PATH" | grep -v grep || true)
    echo "$count"
}

# 获取所有实例的 PID 和数据目录
list_instances() {
    while IFS= read -r line; do
        if echo "$line" | grep -q "MacOS/Electron" && ! echo "$line" | grep -q "Helper"; then
            pid=$(echo "$line" | awk '{print $2}')
            full_args=$(echo "$line" | awk '{for(i=11;i<=NF;i++) printf "%s ", $i}')
            data_dir="$BASE_DATA_DIR"
            if echo "$full_args" | grep -q -- "--user-data-dir="; then
                data_dir=$(echo "$full_args" | grep -o -- '--user-data-dir=[^ ]*' | head -1 | cut -d= -f2)
            fi
            echo "$pid|$data_dir"
        fi
    done < <(ps aux | grep "$APP_PATH" | grep -v grep || true)
}

# 启动新实例
launch_instance() {
    local num=$1
    local data_dir="${BASE_DATA_DIR} ${num}"

    info "启动实例 #${num}"
    info "数据目录: ${data_dir}"

    nohup "$ELECTRON" "$APP_RES" "--user-data-dir=${data_dir}" --no-sandbox > /dev/null 2>&1 &
    # 注意: data_dir 包含空格，用双引号包裹确保作为一个参数传递
    local pid=$!
    sleep 3

    if kill -0 $pid 2>/dev/null; then
        info "实例 #${num} 已启动 (PID: $pid)"
    else
        warn "实例 #${num} 可能启动异常，请检查"
    fi
}

# 显示状态
show_status() {
    clear
    title "=========================================="
    title "      TraeWork CN 多开管理工具"
    title "=========================================="
    echo ""
    info "应用路径: $APP_PATH"

    local count=$(detect_instances)
    if [ "$count" -gt 0 ]; then
        info "当前运行 ${count} 个实例"
        echo ""
        list_instances | while IFS='|' read -r pid dir; do
            if [ "$dir" = "$BASE_DATA_DIR" ]; then
                echo "  PID ${pid}  ← 默认实例"
            else
                echo "  PID ${pid}  → ${dir}"
            fi
        done
    else
        warn "当前没有运行中的实例"
    fi
    echo ""
}

show_menu() {
    echo ""
    echo -e "${BOLD}请选择操作:${NC}"
    echo "  1) 查看当前状态"
    echo "  2) 启动新实例"
    echo "  3) 启动多个实例（批量）"
    echo "  4) 停止所有额外实例（保留第一个）"
    echo "  5) 停止所有实例"
    echo "  6) 退出"
    echo ""
}

# ==================== 主函数 ====================
main() {
    while true; do
        show_status
        show_menu

        read -p "$(echo -e ${CYAN}请输入选项 [1-6]: ${NC})" choice

        case "$choice" in
            1)
                read -p "$(echo -e ${CYAN}按回车继续...${NC})"
                ;;
            2)
                echo ""
                local next=2
                while true; do
                    local check_dir="${BASE_DATA_DIR} ${next}"
                    if list_instances | grep -q "$check_dir"; then
                        next=$((next + 1))
                    else
                        break
                    fi
                done
                launch_instance "$next"
                read -p "$(echo -e ${CYAN}按回车继续...${NC})"
                ;;
            3)
                echo ""
                read -p "$(echo -e ${CYAN}要启动几个实例（含原版）？[2-10]: ${NC})" count
                if [[ "$count" =~ ^[0-9]+$ ]] && [ "$count" -ge 2 ] && [ "$count" -le 10 ]; then
                    local current=$(detect_instances)
                    local to_launch=$((count - current))
                    if [ "$to_launch" -le 0 ]; then
                        warn "当前已有 ${current} 个实例，无需创建"
                    else
                        info "将启动 ${to_launch} 个新实例..."
                        local start_num=$((current + 1))
                        for ((i=0; i<to_launch; i++)); do
                            launch_instance $((start_num + i))
                            sleep 1
                        done
                        info "全部启动完成！"
                    fi
                else
                    warn "请输入 2-10 之间的数字"
                fi
                read -p "$(echo -e ${CYAN}按回车继续...${NC})"
                ;;
            4)
                echo ""
                warn "将停止所有额外实例（保留第一个默认实例）..."
                read -p "$(echo -e ${RED}${BOLD}确认？[y/N]: ${NC})" confirm
                if [[ "$confirm" =~ ^[Yy]$ ]]; then
                    local stopped=0
                    while IFS='|' read -r pid dir; do
                        if [ "$dir" != "$BASE_DATA_DIR" ]; then
                            kill "$pid" 2>/dev/null && stopped=$((stopped + 1)) && info "已停止 PID $pid"
                        fi
                    done < <(list_instances)
                    sleep 1
                    info "已停止 ${stopped} 个额外实例"
                fi
                read -p "$(echo -e ${CYAN}按回车继续...${NC})"
                ;;
            5)
                echo ""
                warn "将停止所有 TraeWork 实例！"
                read -p "$(echo -e ${RED}${BOLD}确认？[y/N]: ${NC})" confirm
                if [[ "$confirm" =~ ^[Yy]$ ]]; then
                    while IFS='|' read -r pid dir; do
                        kill "$pid" 2>/dev/null && info "已停止 PID $pid ($dir)"
                    done < <(list_instances)
                    sleep 1
                    info "所有实例已停止"
                fi
                read -p "$(echo -e ${CYAN}按回车继续...${NC})"
                ;;
            6)
                echo ""
                info "再见！"
                exit 0
                ;;
            *)
                warn "无效的选项"
                sleep 1
                ;;
        esac
    done
}

main "$@"