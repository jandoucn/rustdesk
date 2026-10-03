#!/bin/sh
set -eu

app=${RUSTDESK_APP:-"/Applications/RustDesk Yan.app"}
open_command=${RUSTDESK_OPEN_COMMAND:-open}
bundle_id=${RUSTDESK_BUNDLE_ID:-}
executable=${RUSTDESK_EXECUTABLE:-}
sleep_seconds=${RUSTDESK_PERMISSION_SLEEP_SECONDS:-1}
log_file=${RUSTDESK_PERMISSION_LOG_FILE:-"${HOME:-/tmp}/Library/Logs/RustDesk/rustdesk-yan-startup.log"}
repair_invalid_signature=${RUSTDESK_REPAIR_INVALID_SIGNATURE:-1}

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
    output_file=$(mktemp "${TMPDIR:-/tmp}/rustdesk-permission-command.XXXXXX") || return 1
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

read_plist() {
    /usr/libexec/PlistBuddy -c "Print :$1" "$app/Contents/Info.plist" 2>/dev/null
}

stop_app() {
    say "- 正在退出 RustDesk Yan..."
    run_logged osascript -e 'tell application "RustDesk Yan" to quit' || true
    sleep "$sleep_seconds"
    run_logged pkill -x "RustDesk Yan" || true
    run_logged pkill -f "$app/Contents/MacOS/" || true

    elapsed=0
    while pgrep -x "RustDesk Yan" >/dev/null 2>&1; do
        if [ "$elapsed" -ge 10 ]; then
            fail "RustDesk Yan 进程仍未退出，请手动结束后重试。"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
}

clear_quarantine() {
    say "- 清除隔离属性，避免出现“仍要打开”限制..."
    if run_logged xattr -cr "$app"; then
        return 0
    fi
    say "  当前用户无法修改应用属性，申请一次管理员密码..."
    run_logged sudo xattr -cr "$app" || fail "无法清除应用隔离属性。"
}

repair_signature_if_needed() {
    say "- 校验应用签名..."
    if run_logged codesign --verify --deep --strict --verbose=2 "$app"; then
        say "  应用签名有效，保留现有签名和权限身份。"
        return 0
    fi

    [ "$repair_invalid_signature" = "1" ] || \
        fail "应用签名无效；设置 RUSTDESK_REPAIR_INVALID_SIGNATURE=1 可执行临时签名修复。"

    say "  签名无效，使用 macOS 临时签名修复启动限制..."
    service="$app/Contents/MacOS/service"
    if [ -f "$service" ]; then
        if ! run_logged codesign --force --sign - "$service"; then
            run_logged sudo codesign --force --sign - "$service" || \
                fail "无法为 RustDesk service 重新签名。"
        fi
    fi
    if ! run_logged codesign --force --sign - "$app"; then
        run_logged sudo codesign --force --sign - "$app" || \
            fail "无法为 RustDesk Yan.app 重新签名。"
    fi
    run_logged codesign --verify --deep --strict --verbose=2 "$app" || \
        fail "临时签名完成后校验仍失败。"
    say "  临时签名修复完成；不会重置 ScreenCapture、Accessibility 或 ListenEvent。"
}

[ "$(uname -s)" = "Darwin" ] || fail "此脚本只能在 macOS 上运行。"
[ -d "$app" ] || fail "找不到应用：$app"
[ -f "$app/Contents/Info.plist" ] || [ -n "$bundle_id" ] || fail "应用缺少 Info.plist。"

if [ -z "$bundle_id" ]; then
    bundle_id=$(read_plist CFBundleIdentifier) || fail "无法读取 Bundle ID。"
fi
if [ -z "$executable" ] && [ -f "$app/Contents/Info.plist" ]; then
    executable_name=$(read_plist CFBundleExecutable) || fail "无法读取可执行文件名。"
    executable="$app/Contents/MacOS/$executable_name"
fi
[ -z "$executable" ] || [ -x "$executable" ] || fail "应用主程序不存在或不可执行：$executable"

say "RustDesk Yan macOS 启动修复"
say "应用：$app"
say "Bundle ID：$bundle_id"
[ -n "$log_file" ] && say "日志文件：$log_file"
say "- 本脚本只处理隔离属性和应用签名，不修改 macOS 隐私权限。"

stop_app
clear_quarantine
repair_signature_if_needed

say "- 正在启动 RustDesk Yan..."
run_logged "$open_command" -n "$app" || fail "无法启动 RustDesk Yan。"
sleep "$sleep_seconds"

say ""
say "启动修复完成。"
say "请在系统设置中手动开启：录屏与系统录音、设备控制和数据访问。"
