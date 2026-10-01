#!/bin/sh
set -eu

app=${RUSTDESK_APP:-"/Applications/RustDesk Yan.app"}
bundle_id=${RUSTDESK_BUNDLE_ID:-}
executable=${RUSTDESK_EXECUTABLE:-}
status_command=${RUSTDESK_PERMISSION_STATUS_COMMAND:-}
sleep_seconds=${RUSTDESK_PERMISSION_SLEEP_SECONDS:-1}
timeout_seconds=${RUSTDESK_PERMISSION_TIMEOUT_SECONDS:-600}

say() {
    printf '%s\n' "$*"
}

fail() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

read_plist() {
    /usr/libexec/PlistBuddy -c "Print :$1" "$app/Contents/Info.plist" 2>/dev/null
}

stop_app() {
    say "- 正在彻底退出 RustDesk Yan..."
    osascript -e 'tell application "RustDesk Yan" to quit' >/dev/null 2>&1 || true
    sleep "$sleep_seconds"
    pkill -x "RustDesk Yan" >/dev/null 2>&1 || true
    pkill -f "$app/Contents/MacOS/" >/dev/null 2>&1 || true

    elapsed=0
    while pgrep -x "RustDesk Yan" >/dev/null 2>&1; do
        if [ "$elapsed" -ge 10 ]; then
            fail "RustDesk Yan 进程仍未退出，请手动结束后重试。"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
}

start_app() {
    say "- 正在启动 RustDesk Yan..."
    open -n "$app" --args --open-window
    sleep "$sleep_seconds"
}

has_permission() {
    service=$1
    if [ -n "$status_command" ]; then
        "$status_command" "$service"
    elif [ -x "$executable" ]; then
        permission_output=$("$executable" --check-macos-permissions 2>/dev/null || true)
        case "$service" in
            ScreenCapture) printf '%s\n' "$permission_output" | grep -q 'screen_recording=true' ;;
            Accessibility) printf '%s\n' "$permission_output" | grep -q 'accessibility=true' ;;
            ListenEvent) printf '%s\n' "$permission_output" | grep -q 'input_monitoring=true' ;;
            *) return 1 ;;
        esac
    else
        return 1
    fi
}

wait_for_permission() {
    service=${1-}
    label=${2-}
    settings_url=${3-}
    [ -n "$service" ] && [ -n "$label" ] && [ -n "$settings_url" ] || fail "权限检查参数不完整。"

    if has_permission "$service"; then
        say "  ${label}：已通过"
        return
    fi

    say ""
    say "请在打开的系统设置中启用 RustDesk Yan 的“${label}”。"
    say "如果列表中没有 RustDesk Yan，请点击左下角“+”，选择：${app}，然后打开右侧开关。"
    say "脚本正在自动检测，无需回到终端按键。"
    open "$settings_url"

    elapsed=0
    while ! has_permission "$service"; do
        if [ "$elapsed" -ge "$timeout_seconds" ]; then
            fail "${label}在 ${timeout_seconds} 秒内未通过。重新运行脚本可继续配置。"
        fi
        sleep "$sleep_seconds"
        elapsed=$((elapsed + sleep_seconds))
        if [ $((elapsed % 5)) -eq 0 ]; then
            say "  仍在检测：${label}（应用权限探针未通过，已等待 ${elapsed} 秒）"
        fi
    done
    say "  ${label}：已通过"
}

[ "$(uname -s)" = "Darwin" ] || fail "此脚本只能在 macOS 上运行。"
[ -d "$app" ] || fail "找不到应用：$app"
[ -f "$app/Contents/Info.plist" ] || [ -n "$bundle_id" ] || fail "应用缺少 Info.plist。"

if [ -z "$bundle_id" ]; then
    bundle_id=$(read_plist CFBundleIdentifier) || fail "无法读取 Bundle ID。"
fi
if [ -z "$executable" ]; then
    executable_name=$(read_plist CFBundleExecutable) || fail "无法读取可执行文件名。"
    executable="$app/Contents/MacOS/$executable_name"
fi
[ -x "$executable" ] || fail "应用主程序不存在或不可执行：$executable"

if [ -z "$status_command" ]; then
    say "- 使用应用内 macOS 权限探针读取权限状态..."
fi

say "RustDesk Yan macOS 权限修复"
say "应用：$app"
say "Bundle ID：$bundle_id"

stop_app

say "- 清除隔离属性（xattr -cr）..."
if ! xattr -cr "$app"; then
    say "  当前用户没有修改应用属性的权限，申请一次管理员密码..."
    sudo xattr -cr "$app" || fail "无法清除应用隔离属性。"
fi

say "- 校验应用签名..."
codesign --verify --deep --strict "$app" >/dev/null 2>&1 || \
    fail "应用签名校验失败，请重新安装完整的 RustDesk Yan.app。"

say "- 清除旧的权限记录..."
for service in ScreenCapture Accessibility ListenEvent; do
    tccutil reset "$service" "$bundle_id" >/dev/null || \
        fail "无法重置 $service 权限。"
done

# RustDesk 必须先启动并调用相关系统 API，才会出现在隐私设置列表中。
start_app

wait_for_permission \
    ScreenCapture \
    "屏幕录制权限" \
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
wait_for_permission \
    Accessibility \
    "辅助功能权限" \
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
wait_for_permission \
    ListenEvent \
    "输入监控权限" \
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"

say ""
osascript -e 'tell application "System Settings" to quit' >/dev/null 2>&1 || true
osascript -e 'tell application "System Preferences" to quit' >/dev/null 2>&1 || true
say "- 权限已写入，正在彻底退出旧进程并重新启动..."
stop_app
start_app

failed=""
for service_and_label in \
    "ScreenCapture:屏幕录制权限" \
    "Accessibility:辅助功能权限" \
    "ListenEvent:输入监控权限"
do
    service=${service_and_label%%:*}
    label=${service_and_label#*:}
    if has_permission "$service"; then
        say "  ${label}：最终复检通过"
    else
        failed="$failed $label"
    fi
done

[ -z "$failed" ] || fail "重启后复检未通过：$failed"

say ""
say "权限配置完成，RustDesk Yan 已使用新进程启动。"
