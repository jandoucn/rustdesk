## 版本变化

本次发布版本为 `1.5.1`，构建号为 `20261002.2`，构建序号为 `2026100202`。

- 修复 macOS 下载更新并输入管理员密码后安装失败的问题；更新器现在能正确识别 `RustDesk Yan.app`，并安全处理应用名中的空格。
- Android 真机控制 Windows 或 macOS 时，鼠标模式和触屏模式默认显示虚拟鼠标；用户明确关闭后仍保留关闭状态。
- Android 真机首页只保留“最近会话”和“通讯录”，已有配置和长按菜单都不能重新显示其他分组。
- Windows 和 macOS 标准版隐藏左侧内容后，ID 区域下方的空白背景与上方保持一致，不再显示灰色空块。
- macOS 权限向导默认保留已有权限，不再重复运行时清空勾选；权限检测改为通过 LaunchServices 使用 RustDesk Yan 的应用身份，选择“以后”后也能继续检测并完成重启。
- 通讯录保留官方彩色客户端卡片，只将第二行改为客户端 ID；备注存在时显示备注和原始 ID，无备注时显示官方格式化 ID。
- standard 通讯录标签栏缩窄到约 78px，左侧“你的桌面”支持折叠并记忆状态。
- 更新策略支持启动检查、服务端检查/强制安装、网络恢复补查、manifest 事件通知，以及默认关闭、默认 5 小时的定时检查；服务端可实时下发策略。
- 客户端使用 `client_id=83077683`，设备真实 UUID 保持不变。
- 更新请求改为使用每台设备实时 ID；服务端检查命令只在主窗口可见时在应用内提示，隐藏到托盘时延迟到恢复窗口后提示。
- 主界面更新卡片已隐藏；服务端「立即更新」静默执行签名校验、下载和安装，并回报带 `command_id` 的完整状态。
- Android 控制 Windows 或 macOS 多显示器时，鼠标坐标按当前远端显示器映射；主屏关闭或显示器数量变化后按实时屏幕拓扑重新判断。
- 发布构建支持单平台、多平台或全部平台构建，并始终组装 standard/SOS 共 8 个目标的完整发布快照。

本包基于 RustDesk 1.5.0。下面是相对官方客户端的变化。

### 被控端

- 主控连上被控端后，被控端右上角的连接管理窗口不再显示。
- 远程桌面、传输文件、聊天和语音来电都不会再把这个窗口叫出来。
- 这个窗口上的「接受」按钮不会出现。连入需要密码。

### SOS

- 同时接受一次性密码和固定密码。
- 启动时显示主界面，本机 ID 和密码可以直接看到。
- 主页标题显示为 RustDesk。

### 构建

- 修正工作流里一处无效条件。以前每次推代码都会多出一条失败的 Action。
- vcpkg 工具下载失败时会自动重试，避免 GitHub 短暂返回 500 时整次构建失败。
- 修掉 macOS、Android、Windows 安装包编译时几处我们自己的警告。其中 Windows 写注册表时原先把数值当成了指针。

### 构建与发布

- 手动构建的 Release 标签由版本、UTC 构建日期和两位序号组成，例如 `v1.5.0-build-2026.09.30-01`。同一天再次构建会变成 `v1.5.0-build-2026.09.30-02`。
- 安装包文件名同时带真实版本号、构建序号和版本类型，例如 `rustdesk-1.5.1-2026100202-standard-windows-x86_64.exe`。
- 当前构建 macOS ARM、Windows x64 和 Android arm64。
- Windows 安装后开机只启动托盘，不显示主窗口。点关闭会缩到右下角。托盘里退出会关掉主窗口和托盘，Windows 服务继续在后台运行。之后远程连入也不会再把托盘图标叫出来，直到手动打开桌面程序。
- standard 和 SOS 都同时接受固定密码和一次性密码。固定密码是 `asd123asd`。键盘、剪贴板、文件、摄像头、终端、音频、隧道、远程重启、录制、阻止输入和隐私模式默认打开。
- SOS 主页保留一次性密码的刷新按钮，编辑按钮不再打开设置。
- 最近、收藏、局域网、通讯录和群组的主机菜单里，「连接」下面增加「浏览模式」。这条连接只看画面，不显示本机光标。需要时连上后再勾选「显示我的光标」。
- 没有 Apple 证书时，macOS 安装包保持普通 ad-hoc 签名，不启用 hardened runtime。复制进来的 service 会单独签名，再给整个应用补封。下载后执行 `xattr -cr` 即可打开。有证书时仍走 Developer ID 和公证。
- 登录和地址簿兼容旧版 rustdesk-api。
- Windows 和 Android 控制 macOS 时自动交换 Command 与 Control；macOS 控制 macOS 时保持原有键位。
- 每次普通连接会关闭上次持久化的浏览模式；从「浏览模式」入口连接时仍只查看画面，已有会话切换窗口时保持当前状态。
- 远程连接顶部工具栏不再显示文字聊天、语音通话和开始录屏入口。
- 地址簿主机菜单不再显示「打开 Web 控制台以执行更多操作」。
- standard 版隐藏设置里的录屏选项、首页底部的自建服务器提示和通讯录的「我的地址簿」栏目；标签入口移动到搜索、刷新和多选操作区，默认使用一行多项的紧凑视图。
- 通讯录设备卡片只显示备注名和客户端 ID，不再显示用户名、主机名或大写字母头像。
- 终端应用默认允许复制到剪贴板。
- standard 和 SOS 构建成功后会把八个 Release 安装包同步到阿里云 OSS，并只保留最近五个稳定版本。GitHub Actions 上传使用上海 OSS 传输加速 endpoint `oss-accelerate.aliyuncs.com`；客户端仍通过 `https://download.yan.life` 下载，CNAME 保持指向上海普通 OSS endpoint `rustdesk-release.oss-cn-shanghai.aliyuncs.com`。
- 同一 GitHub runner 的 32 MiB multipart 实测：上海传输加速约 8.40-8.48 MiB/s，香港 OSS 约 8.48 MiB/s，差异约 1%；因此保留上海 Bucket，不迁移香港。

## 在线版本检测与升级

### 链路概览

客户端启动后会向 `https://rdapi.yan.life` 发送版本检测请求，请求中包含产品、edition、平台、架构、安装方式、版本号、build sequence 和精确的 `target_key`。服务器返回更新模式和 manifest 后，客户端按当前安装包类型选择对应资产。

桌面端的 target 选择如下：

| 平台 | 安装类型 | target key 后缀 |
| --- | --- | --- |
| Windows x86_64 | EXE | `windows-x86_64-exe` |
| Windows x86_64 | MSI | `windows-x86_64-msi` |
| macOS arm64 | DMG | `macos-aarch64-dmg` |

Android arm64 使用 `android-aarch64-apk`。每个 target 还会带 `standard` 或 `sos` edition 后缀。下载前会校验 HTTPS 地址、文件大小、SHA-256、签名和 manifest 身份；主地址失败时回退到 GitHub Release mirror。

### 客户端入口

- 桌面主页不显示更新卡片；更新提示由关于页、启动检查或服务端实时命令进入。
- Windows/macOS 已安装版本：按钮进入签名校验下载和安装事务。
- Windows MSI：选择 MSI target，并走 MSI 更新流程。
- Android：更新入口打开 manifest 返回的真实下载地址，不再固定跳转官方下载页。
- 设置「关于」页：显示服务端最新版本，提供手动检查更新、启动时检查软件更新和自动更新入口；后两个开关默认关闭。
- 服务端可通过实时策略修改启动检查和自动更新开关，客户端无需重启即可在数秒内应用最新下发值。
- rustdesk-api 设备列表提供「立即检查」和「立即更新」按钮；一次性命令通过设备签名认证的 SSE 通道投递，客户端校验锁定的版本、build、大小、SHA-256 和签名后执行并回报状态。

更新事件包括 `started`、`downloaded`、`installing`、`installed`、`failed`、`deferred`、`rolled_back` 和 `rollback_failed`，用于更新 UI 和服务器事件记录。

### 发布条件

只有以下条件同时满足时才允许发布 stable manifest：

1. 每次必须同时生成 `standard` 和 `SOS`，可选择单平台、多平台或 `all`。
2. 本轮选中的平台从最新 `master` 构建；未参与本轮构建的平台沿用上一份 stable manifest 中的安装包与 target 元数据。
3. 继承包保留其真实版本号和构建序号，不得改名伪装成本轮新包。
4. GitHub Release 的安装包快照固定为 8 个：standard/SOS 各自包含 Windows x86_64 EXE、Windows x86_64 MSI、Android arm64 APK、macOS arm64 DMG。
5. 本轮新包上传 OSS；继承包通过同 Bucket 的 `copy_object` 汇总到新 tag 目录，最终目录同样固定包含 8 个安装包和一个 `catalog.json`。
6. macOS 继续使用现有签名和公证流程，不在发布器中重新签名。

发布 Action 的顺序是：

```text
选中平台构建并组装 8 包 Release 快照
    -> 新包 4 线程上传、继承包 OSS 服务端复制
    -> 每个资产计算 SHA-256 并签名
    -> 生成 stable manifest
    -> POST /rd/update/v1/publish
    -> GET /rd/update/v1/manifest/stable.json 验证
```

任一构建 job 失败时不应进入 OSS 发布；发布器对资产数量、文件名、版本元数据、签名和远端尺寸做失败关闭校验。

### 签名与环境变量

客户端内置的更新公钥为：

```text
key_id: yan-release-2026
public_key: YNphn2SGjnetwp0bb/uEGpzfQi8OavMWTHCmqPbMuxg=
```

生产 API 容器需要配置 `RUSTDESK_UPDATE_BASE_URL`、`RUSTDESK_UPDATE_DOWNLOAD_PREFIX`、`RUSTDESK_UPDATE_KEYS_JSON` 和 `RUSTDESK_UPDATE_PUBLISH_TOKEN`。`RUSTDESK_UPDATE_KEYS_JSON` 必须包含上面的 key id 和公钥。`UPDATE_SIGNING_KEY` 只存在于 GitHub 构建仓库的 Actions Secret，不能放进客户端或 API 服务器。

以下三个 token 必须是同一个值，但 GitHub 页面只能查看 Secret 名称，不能读取 Secret 原文：

```text
jandoucn/rustdesk.UPDATE_PUBLISH_TOKEN
jandoucn/rustdesk-api.UPDATE_PUBLISH_TOKEN
生产容器 RUSTDESK_UPDATE_PUBLISH_TOKEN
```

### OSS 目录迁移

历史 OSS 目录已由迁移 Action 统一到当前 tag 目录格式：

```text
rustdesk/stable/vX.Y.Z-build-YYYY.MM.DD-NN/
```

迁移 Action `36726092411` 已成功迁移 5 个历史版本。每个版本包含 8 个资产和一个 `catalog.json`，旧目录已删除。当前发布器兼容历史 tag 的排序和清理逻辑，并只保留最近五个完整 stable 版本。

### NTServer-SH 核对结果

通过 Termark 检查的目标是 `NTServer-SH`，地址为 `47.100.7.221`，API 容器为 `rustdesk-api`。

已确认：

- `rustdesk-api` 容器运行正常；
- 更新环境变量已注入容器；
- `RUSTDESK_UPDATE_KEYS_JSON` 是有效 JSON；
- `yan-release-2026` 公钥与客户端一致；
- `/rd/update/v1/publish` 路由存在，未携带凭据时返回 `401`；
- `/rd/update/v1/events` 路由存在，参数错误时返回 `422`。

本次发布结果：

```text
GET https://rdapi.yan.life/rd/update/v1/manifest/stable.json
HTTP 200
version: 1.5.0
build_seq: 2026093006
source_commit: 9f8b6d19d0171d1542cd30e4a76848137d5f23b5
targets: 8
```

GitHub Actions run `36782267617` 已完成 standard/SOS 的 Windows x86_64、Android arm64、macOS arm64 构建。OSS 发布 job `110137268795` 成功上传 8 个资产、发布 stable manifest，并完成发布后读取校验。

8 个 OSS 主地址和 GitHub mirror 均已实测返回 HTTP 200；实际下载大小、SHA-256 和 `yan-release-2026` Ed25519 签名全部通过校验。对上一 build `2026093005` 调用 `/rd/update/v1/check` 时，8 个 target 均返回可更新到 `2026093006`；当前 build 返回无更新。

服务器当前默认策略为 `mode=notify`、`auto_install=false`。因此启动检测和用户点击更新后的下载安装路径可用，但勾选客户端「Auto update」不会使默认设备在后台自动安装。需要后台静默自动安装时，必须给对应设备配置 `auto_install` 策略，并另做真机升级验收。

### 发布后验收

1. GitHub Release 检查 8 个资产和文件名。
2. OSS 检查对应 tag 的 `catalog.json` 和 8 个对象均可访问。
3. API 检查 stable manifest 返回 HTTP 200。
4. 确认 manifest 中的版本、build sequence、source commit、target key、尺寸、SHA-256 和签名均正确。
5. Windows EXE、Windows MSI、Android arm64、macOS arm64 分别下载新包测试。
6. 测试主下载地址失败时是否回退 GitHub mirror。
7. 测试签名错误、SHA-256 错误、下载中断、安装失败和回滚，确认旧版本仍可启动。
8. 检查 `/rd/update/v1/events` 是否收到成功、失败、延期和回滚事件。

以下异常路径必须在构建机或真机上补测，不能只用单元测试代替：Windows EXE 提权启动失败、custom staging 目录准备失败、Windows 服务会话不存在、macOS 管理员授权失败，以及安装事务回滚失败。验收时要确认这些路径既清理临时下载文件，也能留下明确的失败或回滚事件。

### 当前验证边界

已完成的本地验证：

```text
发布器和远程 UI 契约测试：21 passed
base 更新模块测试：12 passed
updater 测试：7 passed
workflow YAML 校验：通过
git diff --check：通过
```

本机没有 Flutter/Dart SDK，因此 Flutter analyze、widget 测试和真实桌面启动冒烟需要在构建机或安装了 Flutter 的验收机执行。没有完成这些平台验收前，不能把在线升级链路标记为最终闭环。

本文档的发布矩阵只覆盖本项目当前要求的 Windows x86_64、Android arm64 和 macOS arm64。Linux AppImage、macOS x86_64 和 iOS 不在本次 8 包 stable manifest 中，不能把它们当作已发布的自动安装 target。

Android arm64 当前是检测更新后打开服务端返回的 APK 下载地址，不是应用内静默安装。Windows EXE/MSI 和 macOS DMG 已具备下载、校验、提权安装及回滚代码路径，但 run `36782267617` 只证明构建和发布成功；生产事件表尚无 build `2026093006` 的真实 `installed` 或回滚终态记录。需要用旧 build 客户端升级到 `2026093006`，或发布下一 build 后用 `2026093006` 升级，才能完成安装端到端验收。

Windows standard MSI 真机 `83077683` 已上报为 `1.5.0 / 2026093006`、`x86_64`、`installed`，但此前最近设备报告仍为 `last_update_status=not_checked`，且没有升级事件。本版本已在设置的关于页增加「检查更新」按钮，并保留默认关闭的「启动时检查软件更新」和「自动更新」开关；服务端可通过实时策略修改两个开关。

本版本把更新检查、策略流和事件上报的 `client_id` 统一修正为 `83077683`，UUID 继续使用设备真实 UUID。服务端继续为未升级客户端保留 `RustDesk Yan + 唯一 UUID` 的策略兼容映射；一次性检查和安装命令只向通过设备 Ed25519 签名认证的新客户端投递。后台强制安装会锁定已发布的目标版本和 build，客户端完成下载、大小、SHA-256、签名和安装校验后才上报终态；真机从旧 build 升级到更高 build 的完整安装与重启验收仍须在下一次发布后执行。
