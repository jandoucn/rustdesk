#!/bin/sh
set -eu

app=${RUSTDESK_APP:-"/Applications/RustDesk Yan.app"}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bundle_id=${RUSTDESK_BUNDLE_ID:-}
executable=${RUSTDESK_EXECUTABLE:-}
status_command=${RUSTDESK_PERMISSION_STATUS_COMMAND:-}
open_command=${RUSTDESK_OPEN_COMMAND:-open}
reset_permissions=${RUSTDESK_RESET_PERMISSIONS:-0}
sleep_seconds=${RUSTDESK_PERMISSION_SLEEP_SECONDS:-1}
timeout_seconds=${RUSTDESK_PERMISSION_TIMEOUT_SECONDS:-600}
log_file=${RUSTDESK_PERMISSION_LOG_FILE:-"${TMPDIR:-/tmp}/rustdesk-yan-permission-$(date +%Y%m%d-%H%M%S).log"}
require_input_monitoring=${RUSTDESK_REQUIRE_INPUT_MONITORING:-0}
permission_probe_status=1
tcc_db=${RUSTDESK_TCC_DB:-"/Library/Application Support/com.apple.TCC/TCC.db"}

case "$tcc_db" in
    /dev/null|none) tcc_db="" ;;
esac

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
    say "- 正在彻底退出 RustDesk Yan..."
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

start_app() {
    say "- 正在启动 RustDesk Yan..."
    # 不传 --open-window：该参数会绕过 RustDesk 的正常初始化和服务启动分支。
    run_logged "$open_command" -n "$app" || fail "无法启动 RustDesk Yan。"
    sleep "$sleep_seconds"
}

probe_permissions() {
    permission_output_file=$(mktemp "${TMPDIR:-/tmp}/rustdesk-permissions.XXXXXX") || return 1
    permission_stdout_file=$(mktemp "${TMPDIR:-/tmp}/rustdesk-permissions-stdout.XXXXXX") || {
        rm -f "$permission_output_file"
        return 1
    }
    permission_stderr_file=$(mktemp "${TMPDIR:-/tmp}/rustdesk-permissions-stderr.XXXXXX") || {
        rm -f "$permission_output_file" "$permission_stdout_file"
        return 1
    }

    say "- 运行 RustDesk 权限探针（直接执行应用，避免 LaunchServices 实例混淆）..."
    permission_probe_status=1
    if "$executable" --check-macos-permissions \
        "--macos-permission-output=$permission_output_file" \
        >"$permission_stdout_file" 2>"$permission_stderr_file"; then
        permission_probe_status=0
    else
        permission_probe_status=$?
    fi
    permission_output=$(cat "$permission_output_file" 2>/dev/null || true)
    if [ -z "$permission_output" ]; then
        permission_output=$(cat "$permission_stdout_file" 2>/dev/null || true)
    fi
    permission_error=$(cat "$permission_stderr_file" 2>/dev/null || true)
    if [ -n "$permission_output" ]; then
        say "  探针原始输出：$permission_output"
    fi
    if [ -n "$permission_error" ]; then
        say "  探针 stderr：$permission_error"
    fi

    # 兼容旧版本应用或无法直接执行的安装包，再回退到 LaunchServices。
    if [ -z "$permission_output" ]; then
        say "  直接探针无输出（退出码：${permission_probe_status}），回退到 LaunchServices..."
        "$open_command" -n -W "$app" --args \
            --check-macos-permissions \
            "--macos-permission-output=$permission_output_file" \
            >"$permission_stdout_file" 2>"$permission_stderr_file" || true
        permission_output=$(cat "$permission_output_file" 2>/dev/null || true)
        if [ -z "$permission_output" ]; then
            permission_output=$(cat "$permission_stdout_file" 2>/dev/null || true)
        fi
        permission_error=$(cat "$permission_stderr_file" 2>/dev/null || true)
        [ -n "$permission_output" ] && say "  回退探针原始输出：$permission_output"
        [ -n "$permission_error" ] && say "  回退探针 stderr：$permission_error"
    fi
    rm -f "$permission_output_file" "$permission_stdout_file" "$permission_stderr_file"
    [ -n "$permission_output" ]
}

has_permission() {
    service=$1
    if [ -n "$status_command" ]; then
        "$status_command" "$service"
        return $?
    fi

    if tcc_permission_state "$service"; then
        return 0
    else
        tcc_state=$?
    fi

    # TCC 数据库是 macOS 27 的真实授权状态；数据库不可读时才使用应用探针回退。
    if [ "$tcc_state" -eq 2 ] && probe_permissions; then
        case "$service" in
            ScreenCapture) printf '%s\n' "$permission_output" | grep -q 'screen_recording=true' ;;
            Accessibility) printf '%s\n' "$permission_output" | grep -Eq '(^|[[:space:]])(accessibility|device_control)=true([[:space:]]|$)' ;;
            ListenEvent) printf '%s\n' "$permission_output" | grep -q 'input_monitoring=true' ;;
            *) return 1 ;;
        esac
    else
        return 1
    fi
}

tcc_permission_state() {
    service=$1
    [ -n "$tcc_db" ] || return 2
    [ -r "$tcc_db" ] || return 2
    command -v sqlite3 >/dev/null 2>&1 || return 2

    case "$service" in
        ScreenCapture) tcc_service="kTCCServiceScreenCapture" ;;
        Accessibility) tcc_service="kTCCServiceAccessibility" ;;
        ListenEvent) tcc_service="kTCCServiceListenEvent" ;;
        *) return 2 ;;
    esac

    tcc_value=$(sqlite3 -noheader -batch "$tcc_db" \
        "select auth_value from access where service='$tcc_service' and client='$bundle_id' order by last_modified desc limit 1;" \
        2>/dev/null) || return 2
    [ "$tcc_value" = "2" ]
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
    say "macOS 27 中“辅助功能”已改名为“设备控制和数据访问”；如果列表中没有 RustDesk Yan，请点击左下角“+”，选择：${app}，然后打开右侧开关。"
    say "脚本正在自动检测，无需回到终端按键。"
    run_logged "$open_command" "$settings_url" || \
        say "  无法自动打开系统设置，请手动打开：$settings_url"

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
    say "- 优先直接执行 RustDesk Yan 权限探针；旧包无输出时才回退到 LaunchServices。"
fi

say "RustDesk Yan macOS 权限修复"
say "应用：$app"
say "Bundle ID：$bundle_id"
[ -n "$log_file" ] && say "日志文件：$log_file"
if [ -n "$tcc_db" ] && [ -r "$tcc_db" ] && command -v sqlite3 >/dev/null 2>&1; then
    say "- 使用 TCC 数据库复核真实授权状态：$tcc_db"
else
    say "- TCC 数据库不可读，将使用 RustDesk 应用权限探针回退检测。"
fi

stop_app

say "- 清除隔离属性（xattr -cr）..."
if ! run_logged xattr -cr "$app"; then
    say "  当前用户没有修改应用属性的权限，申请一次管理员密码..."
    run_logged sudo xattr -cr "$app" || fail "无法清除应用隔离属性。"
fi

say "- 校验应用签名..."
run_logged codesign --verify --deep --strict "$app" || \
    fail "应用签名校验失败，请重新安装完整的 RustDesk Yan.app。"

code_requirement=$(codesign -dr - "$app" 2>&1 || true)
say "  当前代码签名要求：${code_requirement:-未返回}"
case "$code_requirement" in
    *"designated => identifier \"${bundle_id}\""*) ;;
    *'cdhash H"'*)
        say "- 当前安装包使用构建级 CDHash 身份，正在迁移为稳定签名身份..."
        entitlements_file=$(mktemp "${TMPDIR:-/tmp}/rustdesk-entitlements.XXXXXX") || \
            fail "无法创建签名权限临时文件。"
        entitlements="$entitlements_file"
        codesign -d --entitlements :- "$app" >"$entitlements_file" 2>/dev/null || true
        if ! plutil -lint "$entitlements_file" >/dev/null 2>&1; then
            entitlements="$script_dir/flutter/macos/Runner/Release.entitlements"
            if [ ! -f "$entitlements" ]; then
                rm -f "$entitlements_file"
                fail "当前安装包没有可读取的签名权限，且找不到：$entitlements"
            fi
            say "  当前安装包缺少签名权限，使用仓库内 Release.entitlements 恢复。"
        fi
        if ! run_logged codesign --force --sign - \
            --requirements "=designated => identifier \"${bundle_id}\"" \
            --generate-entitlement-der \
            --entitlements "$entitlements" \
            "$app"; then
            rm -f "$entitlements_file"
            fail "无法把 RustDesk Yan 迁移为稳定签名身份。"
        fi
        rm -f "$entitlements_file"
        run_logged codesign --verify --deep --strict "$app" || \
            fail "稳定签名完成后校验失败。"
        code_requirement=$(codesign -dr - "$app" 2>&1 || true)
        say "  稳定签名复检要求：${code_requirement:-未返回}"
        case "$code_requirement" in
            *"designated => identifier \"${bundle_id}\""*) ;;
            *) fail "稳定签名身份复检失败：${code_requirement:-未返回 designated requirement}" ;;
        esac
        reset_permissions=1
        say "  已迁移为稳定签名身份；本次将清理旧 CDHash 对应的失效权限记录。"
        ;;
    *)
        fail "无法确认 RustDesk Yan 的稳定签名身份：${code_requirement:-未返回 designated requirement}"
        ;;
esac

if [ "$reset_permissions" = "1" ]; then
    say "- 按要求清除旧的权限记录..."
    for service in ScreenCapture Accessibility; do
        run_logged tccutil reset "$service" "$bundle_id" || \
            fail "无法重置 $service 权限。"
    done
    if [ "$require_input_monitoring" = "1" ]; then
        run_logged tccutil reset ListenEvent "$bundle_id" || \
            fail "无法重置 ListenEvent 权限。"
    fi
else
    say "- 保留已有 macOS 权限记录，不执行 tccutil reset。"
fi

# RustDesk 必须先启动并调用相关系统 API，才会出现在隐私设置列表中。
start_app

wait_for_permission \
    ScreenCapture \
    "屏幕录制权限" \
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
wait_for_permission \
    Accessibility \
    "设备控制和数据访问（辅助功能）" \
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
if [ "$require_input_monitoring" = "1" ]; then
    wait_for_permission \
        ListenEvent \
        "输入监控权限（可选）" \
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
else
    say "- 输入监控：仅记录状态，不作为 RustDesk 控制功能的必需权限。"
fi

say ""
run_logged osascript -e 'tell application "System Settings" to quit' || true
run_logged osascript -e 'tell application "System Preferences" to quit' || true
say "- 权限已写入，正在彻底退出旧进程并重新启动..."
stop_app
start_app

failed=""
for service_and_label in \
    "ScreenCapture:屏幕录制权限" \
    "Accessibility:设备控制和数据访问（辅助功能）"
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

if has_permission ListenEvent; then
    say "- 输入监控状态（不作为必需项）：已通过"
else
    say "- 输入监控状态（不作为必需项）：未开启"
fi

say ""
say "权限配置完成，RustDesk Yan 已使用新进程启动。"
