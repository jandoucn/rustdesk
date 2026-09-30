## 版本变化

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
- 安装包文件名带版本类型，`sos` 或 `standard`。
- 当前构建 macOS ARM、Windows x64 和 Android arm64。
- Windows 安装后开机只启动托盘，不显示主窗口。点关闭会缩到右下角。托盘里退出会关掉主窗口和托盘，Windows 服务继续在后台运行。之后远程连入也不会再把托盘图标叫出来，直到手动打开桌面程序。
- standard 和 SOS 都同时接受固定密码和一次性密码。固定密码是 `asd123asd`。键盘、剪贴板、文件、摄像头、终端、音频、隧道、远程重启、录制、阻止输入和隐私模式默认打开。
- SOS 主页保留一次性密码的刷新按钮，编辑按钮不再打开设置。
- 最近、收藏、局域网、通讯录和群组的主机菜单里，「连接」下面增加「浏览模式」。这条连接只看画面，不显示本机光标。需要时连上后再勾选「显示我的光标」。
- 没有 Apple 证书时，macOS 安装包保持普通 ad-hoc 签名，不启用 hardened runtime。复制进来的 service 会单独签名，再给整个应用补封。下载后执行 `xattr -cr` 即可打开。有证书时仍走 Developer ID 和公证。
- 登录和地址簿兼容旧版 rustdesk-api。
- Windows 和 Android 控制 macOS 时自动交换 Command 与 Control；macOS 控制 macOS 时保持原有键位。
- 每次普通连接会关闭上次持久化的浏览模式；从「浏览模式」入口连接时仍只查看画面，已有会话切换窗口时保持当前状态。
- 远程界面不再显示录制和电话入口，Android 端同时移除消息入口；桌面端仍保留文字聊天。
- 地址簿主机菜单不再显示「打开 Web 控制台以执行更多操作」。
- standard 和 SOS 构建成功后会把八个 Release 安装包同步到阿里云 OSS，并只保留最近五个稳定版本。

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

- 桌面主页：检测到新版本后显示更新卡片。
- Windows/macOS 已安装版本：按钮进入签名校验下载和安装事务。
- Windows MSI：选择 MSI target，并走 MSI 更新流程。
- Android：更新入口打开 manifest 返回的真实下载地址，不再固定跳转官方下载页。
- 设置页：可以打开启动时检查更新和自动更新选项。

更新事件包括 `started`、`downloaded`、`installing`、`installed`、`failed`、`deferred`、`rolled_back` 和 `rollback_failed`，用于更新 UI 和服务器事件记录。

### 发布条件

只有以下条件同时满足时才允许发布 stable manifest：

1. `standard` 和 `SOS` 两个 edition 都成功构建。
2. `platforms=all`，不能只构建 Android 或单个平台。
3. GitHub Release 正好包含 8 个安装包：standard/SOS 各自包含 Windows x86_64 EXE、Windows x86_64 MSI、Android arm64 APK、macOS arm64 DMG。
4. macOS 继续使用现有签名和公证流程，不在发布器中重新签名。

发布 Action 的顺序是：

```text
GitHub Release assets
    -> 4 线程 OSS 上传
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
rustdesk/stable/v1.5.0-build-YYYY.MM.DD-NN/
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

当前待完成项：

```text
GET https://rdapi.yan.life/rd/update/v1/manifest/stable.json
当前返回 HTTP 404：更新清单不存在
```

这表示服务器配置和路由已经准备好，但新的 8 包发布 Action 尚未成功写入 stable manifest。构建、OSS 上传和 `/publish` 成功后，该请求必须变为 HTTP 200。

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
