#!/bin/sh
set -eu

app=${RUSTDESK_APP:-"/Applications/RustDesk Yan.app"}
bundle_id=${RUSTDESK_BUNDLE_ID:-com.carriez.rustdesk}
sleep_seconds=${RUSTDESK_PERMISSION_SLEEP_SECONDS:-1}
log_file=${RUSTDESK_UNINSTALL_LOG_FILE:-"${TMPDIR:-/tmp}/rustdesk-yan-uninstall-$(date +%Y%m%d-%H%M%S).log"}
dry_run=0
purge_data=0

usage() {
    cat <<'EOF'
用法：macos-screen-permission-uninstall.sh [选项]

默认操作：退出 RustDesk Yan、清除 TCC 权限记录、删除 RustDesk Yan.app。
默认保留 RustDesk 配置、设备 ID、日志和用户数据。

选项：
  -y, --yes       兼容旧命令，当前版本默认直接执行
  --dry-run       只显示将执行的操作，不修改系统
  --purge-data    额外删除当前用户的 RustDesk 配置、缓存和日志
  -h, --help      显示帮助

环境变量：
  RUSTDESK_APP                 应用路径
  RUSTDESK_BUNDLE_ID           Bundle ID
  RUSTDESK_UNINSTALL_LOG_FILE  日志文件路径
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -y|--yes) : ;;
        --dry-run) dry_run=1 ;;
        --purge-data) purge_data=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf '错误：未知选项：%s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$log_file" in
    /dev/null|none) log_file="" ;;
    *)
        log_directory=$(dirname "$log_file")
        mkdir -p "$log_directory" 2>/dev/null || log_file=""
        if [ -n "$log_file" ]; then
            : >"$log_file" || log_file=""
        fi
        ;;
esac

say() {
    message=$*
    printf '%s\n' "$message"
    if [ -n "$log_file" ]; then
        printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$message" >>"$log_file"
    fi
}

fail() {
    say "错误：$*" >&2
    exit 1
}

command_line() {
    command_name=$1
    shift
    printf '%s' "$command_name"
    for command_arg in "$@"; do
        printf ' %s' "$(printf '%s' "$command_arg" | sed 's/[[:space:]]/\\\\&/g')"
    done
}

run_logged() {
    output_file=$(mktemp "${TMPDIR:-/tmp}/rustdesk-uninstall-command.XXXXXX") || return 1
    say "+ $(command_line "$@")"
    if "$@" >"$output_file" 2>&1; then
        command_status=0
    else
        command_status=$?
    fi
    if [ -s "$output_file" ]; then
        while IFS= read -r output_line || [ -n "$output_line" ]; do
            say "  $output_line"
        done <"$output_file"
    fi
    rm -f "$output_file"
    return "$command_status"
}

stop_app() {
    say "- 正在退出 RustDesk Yan..."
    run_logged pkill -x "RustDesk Yan" || true
    run_logged pkill -f "$app/Contents/MacOS/" || true
    sleep "$sleep_seconds"

    # RustDesk 可能拦截 AppleScript quit 并返回 -128；只有进程仍在时才尝试它。
    if pgrep -x "RustDesk Yan" >/dev/null 2>&1; then
        run_logged osascript -e 'tell application "RustDesk Yan" to quit' || true
        sleep "$sleep_seconds"
        run_logged pkill -x "RustDesk Yan" || true
        run_logged pkill -f "$app/Contents/MacOS/" || true
    fi

    elapsed=0
    while pgrep -x "RustDesk Yan" >/dev/null 2>&1; do
        if [ "$elapsed" -ge 10 ]; then
            fail "RustDesk Yan 进程仍未退出，请手动结束后重试。"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
}

reset_tcc() {
    say "- 清除 RustDesk Yan 的 macOS 权限记录..."
    for service in ScreenCapture Accessibility ListenEvent; do
        if run_logged tccutil reset "$service" "$bundle_id"; then
            :
        else
            say "  警告：无法重置 $service，继续执行卸载。"
        fi
    done
}

remove_path() {
    target=$1
    [ -n "$target" ] || return 0
    case "$target" in
        /|"${HOME:-}"|/Applications|/Applications/)
            fail "拒绝删除不安全路径：$target"
            ;;
    esac
    [ -e "$target" ] || [ -L "$target" ] || {
        say "  不存在，跳过：$target"
        return 0
    }
    if run_logged rm -rf "$target"; then
        return 0
    fi
    say "  当前用户无法删除，申请一次管理员密码..."
    run_logged sudo rm -rf "$target" || fail "无法删除：$target"
}

[ "$(uname -s)" = "Darwin" ] || fail "此脚本只能在 macOS 上运行。"
case "$app" in
    *.app) ;;
    *) fail "应用路径必须以 .app 结尾：$app" ;;
esac

say "RustDesk Yan macOS 卸载"
say "应用：$app"
say "Bundle ID：$bundle_id"
[ -n "$log_file" ] && say "日志文件：$log_file"
[ "$purge_data" -eq 1 ] && say "模式：同时删除当前用户配置、缓存和日志"
[ "$dry_run" -eq 1 ] && say "模式：dry-run，不修改系统"

if [ "$dry_run" -eq 1 ]; then
    say "- 计划：退出进程"
    say "- 计划：重置 ScreenCapture、Accessibility、ListenEvent"
    say "- 计划：删除应用 $app"
    if [ "$purge_data" -eq 1 ]; then
        say "- 计划：删除 ~/Library/Preferences/com.carriez.RustDesk"
        say "- 计划：删除 ~/Library/Preferences/com.carriez.rustdesk"
        say "- 计划：删除 ~/Library/Application Support/RustDesk"
        say "- 计划：删除 ~/Library/Caches/com.carriez.rustdesk"
        say "- 计划：删除 ~/Library/Logs/RustDesk"
    fi
    exit 0
fi

stop_app
reset_tcc
remove_path "$app"

if [ "$purge_data" -eq 1 ]; then
    say "- 删除当前用户的 RustDesk 配置、缓存和日志..."
    home_directory=${HOME:-}
    [ -n "$home_directory" ] || fail "无法确定 HOME，拒绝执行 --purge-data。"
    remove_path "$home_directory/Library/Preferences/com.carriez.RustDesk"
    remove_path "$home_directory/Library/Preferences/com.carriez.rustdesk"
    remove_path "$home_directory/Library/Application Support/RustDesk"
    remove_path "$home_directory/Library/Caches/com.carriez.rustdesk"
    remove_path "$home_directory/Library/Logs/RustDesk"
fi

run_logged osascript -e 'tell application "System Settings" to quit' || true
run_logged osascript -e 'tell application "System Preferences" to quit' || true
say "卸载完成。"
