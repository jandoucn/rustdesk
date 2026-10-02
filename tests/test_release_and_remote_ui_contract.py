import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAndRemoteUiContractTest(unittest.TestCase):
    def test_macos_retina_input_mapping_keeps_the_upstream_display_index_path(self):
        source = (ROOT / "src/server/connection.rs").read_text()
        quartz = (ROOT / "libs/scrap/src/common/quartz.rs").read_text()
        retina = source[
            source.index("struct Retina") : source.index(
                "/// Get control permission state", source.index("struct Retina")
            )
        ]

        self.assertNotIn("selected_display_name", retina)
        self.assertNotIn("select_display", retina)
        self.assertNotIn("display_matching", retina)
        self.assertIn("let Some(d) = self.displays.get(current) else", retina)
        self.assertIn("fn set_displays(&mut self, displays: &Vec<DisplayInfo>)", retina)
        self.assertIn("conn.retina.set_displays(&_pi.displays);", source)
        self.assertIn("self.retina.set_displays(&displays);", source)
        self.assertIn("Ok(quartz::Display::online()", quartz)
        self.assertNotIn("fn active_displays", quartz)
        self.assertNotIn("display.is_active()", quartz)

    def test_macos_mouse_buttons_keep_the_upstream_cursor_location_path(self):
        source = (ROOT / "libs/enigo/src/macos/macos_impl.rs").read_text()
        mouse_down = source[source.index("fn mouse_down"):source.index("fn mouse_up")]
        mouse_up = source[source.index("fn mouse_up"):source.index("fn mouse_click")]

        self.assertIn("Self::mouse_location()", mouse_down)
        self.assertIn("Self::mouse_location()", mouse_up)
        self.assertNotIn("self.current_mouse_location()", mouse_down)
        self.assertNotIn("self.current_mouse_location()", mouse_up)

    def test_macos_relative_mouse_keeps_the_upstream_edge_reset_path(self):
        source = (ROOT / "libs/enigo/src/macos/macos_impl.rs").read_text()
        relative_move = source[
            source.index("fn mouse_move_relative") : source.index(
                "fn mouse_down", source.index("fn mouse_move_relative")
            )
        ]

        self.assertNotIn("fn relative_mouse_target", source)
        self.assertIn("Self::main_display_size()", relative_move)
        self.assertIn("Self::mouse_location_raw_coords()", relative_move)
        self.assertIn("let near_edge =", relative_move)

    def test_flutter_display_topology_keeps_the_upstream_switch_path(self):
        source = (ROOT / "flutter/lib/models/model.dart").read_text()
        peer_info = source[
            source.index("  handlePeerInfo(Map<String, dynamic>") : source.index(
                "handleSyncPeerInfo(",
                source.index("  handlePeerInfo(Map<String, dynamic>"),
            )
        ]
        defaults = (
            ROOT / "flutter/lib/utils/session_option_defaults.dart"
        ).read_text()

        self.assertNotIn("normalizedDisplayIndexAfterTopologyChange", source)
        self.assertNotIn("localIsAndroid", defaults)
        self.assertIn("localIsWindows && peerIsMacOS", defaults)
        self.assertIn("if (_pi.currentDisplay < _pi.displays.length)", peer_info)
        self.assertIn("updateCurDisplay(sessionId);", peer_info)

    def test_release_snapshot_requires_standard_and_sos_editions(self):
        caller = (ROOT / ".github/workflows/flutter-tag.yml").read_text()
        self.assertIn("validate-release-selection.py", caller)
        validator = (ROOT / ".github/scripts/validate-release-selection.py").read_text()
        self.assertIn("完整发布快照必须同时构建 standard 和 SOS", validator)

    def test_oss_publish_probes_the_requested_release_and_fails_closed(self):
        workflow = (ROOT / ".github/workflows/publish-oss.yml").read_text()
        agents = (ROOT / "AGENTS.md").read_text()

        self.assertIn('RELEASE_TAG: ${{ inputs.tag }}', workflow)
        self.assertIn('gh release download "$RELEASE_TAG"', workflow)
        self.assertIn('--tag "$RELEASE_TAG"', workflow)
        self.assertIn("group: oss-stable-release", workflow)
        self.assertIn("path: release-source", workflow)
        self.assertIn("ref: ${{ inputs.tag }}", workflow)
        self.assertIn("python3 .github/scripts/publish-release-to-oss.py", workflow)
        self.assertIn("--source-dir release-source", workflow)
        self.assertIn("UPDATE_SIGNING_KEY", workflow)
        self.assertIn("UPDATE_PUBLISH_TOKEN", workflow)
        publisher = (ROOT / ".github/scripts/publish-release-to-oss.py").read_text()
        self.assertIn("oss2.resumable_upload(", publisher)
        self.assertIn("ThreadPoolExecutor(max_workers=4)", publisher)
        self.assertIn("num_threads=1", publisher)
        self.assertIn("validate_release_identity(metadata, args.tag)", publisher)
        self.assertIn('"build_seq": metadata["build_seq"]', publisher)
        self.assertIn("publish_and_verify_manifest(", publisher)
        self.assertIn("cleanup_complete_releases(", publisher)

        caller = (ROOT / ".github/workflows/flutter-tag.yml").read_text()
        self.assertIn("needs: [release-tag, publish-release]", caller)
        self.assertIn("uses: ./.github/workflows/publish-oss.yml", caller)
        self.assertIn("secrets: inherit", caller)
        self.assertIn('CUSTOM_TAG: ${{ inputs.tag }}', caller)
        self.assertNotIn('if [ "${{ inputs.tag }}"', caller)
        self.assertIn('tag_base="v${version}-build-${formatted_date}"', caller)
        self.assertIn('build_seq=$(python3 -c', caller)
        self.assertIn("release-metadata.py resolve", caller)
        self.assertIn("android_version_code", caller)
        self.assertIn('sequence_value=$((10#$build_seq % 100))', caller)
        self.assertIn('printf -v sequence \'%02d\' "$sequence_value"', caller)
        self.assertIn('git ls-remote --exit-code --tags origin "refs/tags/${tag}"', caller)
        self.assertNotIn('max_sequence=0', caller)
        self.assertIn('release_name="$tag"', caller)
        self.assertNotIn('yan-v${version}', caller)
        self.assertIn("fail-fast: true", caller)
        self.assertIn("group: flutter-tag-release", caller)
        self.assertIn("cancel-in-progress: false", caller)
        self.assertIn("if: inputs.standard && inputs.sos", caller)
        self.assertIn("校验 Android 发布签名", caller)
        self.assertIn("Android 发布必须配置长期签名证书", caller)
        self.assertIn("Android 发布必须始终使用固定长期签名证书", agents)
        self.assertIn(
            "71:11:D7:30:EC:A2:C7:98:47:41:53:04:59:DD:38:97:20:16:2F:7B:FE:B7:39:FA:96:8F:2E:D7:4E:6F:0B:D8",
            agents,
        )
        self.assertIn("禁止把 debug 或 unsigned APK 上传到 Release 或 OSS", agents)
        self.assertNotIn("inputs.platforms == 'all'", caller[caller.index("publish-oss:") :])
        self.assertIn("publish-release:", caller)
        self.assertIn("preflight:", caller)
        self.assertIn("flutter analyze", caller)
        self.assertIn("flutter test", caller)
        self.assertIn("python3 -m unittest discover -s tests", caller)
        self.assertIn("needs: [release-tag, preflight]", caller)
        self.assertIn("needs: [release-tag, run-flutter-tag-build]", caller)
        self.assertIn("pattern: release-assets-*", caller)
        self.assertIn("merge-multiple: true", caller)
        self.assertIn('gh release create "$RELEASE_TAG"', caller)
        self.assertIn('--target "$GITHUB_SHA"', caller)
        self.assertIn('--notes ""', caller)
        self.assertIn('gh release upload "$RELEASE_TAG"', caller)
        self.assertIn("validate-release-assets.py", caller)
        self.assertIn("sha256sum -c SHA256SUMS", caller)
        self.assertIn("cleanup-incomplete-release:", caller)
        self.assertIn("needs.publish-release.result == 'failure'", caller)
        self.assertIn("needs.publish-oss.result == 'failure'", caller)
        self.assertIn(
            'gh release delete "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" --cleanup-tag --yes',
            caller,
        )
        self.assertIn('expected_tag="${tag_base}-${sequence}"', caller)
        self.assertIn('if [ "$CUSTOM_TAG" != "$expected_tag" ]; then', caller)

        build = (ROOT / ".github/workflows/flutter-build.yml").read_text()
        self.assertIn("publish_release_assets:", build)
        self.assertIn("default: false", build[build.index("publish_release_assets:") : build.index("# NOTE:")])
        self.assertIn("if: inputs.publish_release_assets && inputs.upload-artifact", build)
        self.assertIn("publish_release_assets: false", caller)
        self.assertIn("release-assets-${{ env.EDITION }}-windows-${{ matrix.job.arch }}", build)
        self.assertIn("release-assets-${{ env.EDITION }}-macos-${{ matrix.job.arch }}", build)
        self.assertIn("release-assets-${{ env.EDITION }}-android-${{ matrix.job.arch }}", build)
        self.assertIn('VERSION: "${{ inputs.version }}"', build)
        self.assertIn('BUILD_NUMBER: "${{ inputs.build_number }}"', build)
        self.assertIn('BUILD_SEQ: "${{ inputs.build_seq }}"', build)
        self.assertIn('ANDROID_VERSION_CODE: "${{ inputs.android_version_code }}"', build)
        self.assertIn('--build-name "${VERSION}"', build)
        self.assertIn('--build-number "${ANDROID_VERSION_CODE}"', build)
        self.assertIn("validate-android-apk.py", build)
        self.assertEqual(build.count("validate-android-apk.py"), 2)
        self.assertIn("assemble-release-snapshot.py", caller)
        self.assertIn("release-snapshot.json", caller)
        self.assertIn("Smoke test built Windows client", build)
        self.assertIn("Get-Process -Name rustdesk", build)
        self.assertIn("Stop-Process -Force", build)
        self.assertIn("RUSTDESK_EDITION: ${{ inputs.edition }}", build)
        self.assertIn(
            "name: rustdesk-${{ env.VERSION }}-${{ env.EDITION }}-android-universal.apk",
            build,
        )
        self.assertNotIn("if: False", build)
        self.assertIn("if: ${{ false }}", build)
        version_generator = (ROOT / "libs/hbb_common/src/lib.rs").read_text()
        self.assertIn('std::env::var("RUSTDESK_EDITION")', version_generator)
        release_name = "name: ${{ inputs.release-name != '' && inputs.release-name || env.TAG_NAME }}"
        self.assertEqual(build.count(release_name), 17)

    def test_custom_clients_use_the_online_update_entry_and_status_events(self):
        common = (ROOT / "flutter/lib/common.dart").read_text()
        self.assertIn("kSoftwareUpdateEvent", common)
        check_update = common[common.index("void checkUpdate()") : common.index("// https://github.com/flutter", common.index("void checkUpdate()"))]
        self.assertNotIn("!bind.isCustomClient()", check_update)

        rust_common = (ROOT / "src/common.rs").read_text()
        rust_updater = (ROOT / "src/updater.rs").read_text()
        self.assertIn('"software_update_event"', rust_updater)
        self.assertIn("update_check_should_stop(resp.update_available, &resp.mode, request_origin)", rust_common)
        self.assertIn('mode == "disabled" && !request_origin.starts_with("command")', rust_common)
        self.assertIn(
            "update_client_identity(\n        &hbb_common::config::Config::get_id(),",
            rust_common,
        )
        self.assertIn(
            "update_client_identity(\n        &hbb_common::config::Config::get_id(),",
            rust_updater,
        )
        self.assertNotIn(
            "update_client_identity(&hbb_common::fingerprint::get_fingerprint",
            rust_updater,
        )
        mobile = (ROOT / "flutter/lib/mobile/pages/connection_page.dart").read_text()
        self.assertNotIn("!bind.isCustomClient() && !isIOS", mobile)
        self.assertIn("launchUrl(Uri.parse(updateUrl))", mobile)
        desktop = (ROOT / "flutter/lib/desktop/pages/desktop_home_page.dart").read_text()
        update_card = desktop[desktop.index("Widget buildHelpCards") : desktop.index("if (systemError", desktop.index("Widget buildHelpCards"))]
        self.assertNotIn("mainUriPrefixSync().contains('rustdesk')", update_card)

    def test_home_page_uses_version_only_update_card(self):
        desktop = (ROOT / "flutter/lib/desktop/pages/desktop_home_page.dart").read_text()
        update_card = desktop[
            desktop.index("Widget buildHelpCards") : desktop.index(
                "if (systemError", desktop.index("Widget buildHelpCards")
            )
        ]
        self.assertIn("desktopUpdateCardVisible.value", update_card)
        self.assertIn("version: updateUiState.targetVersion.value", update_card)
        self.assertIn("handleUpdate(updateUiState.updateUrl.value)", update_card)
        self.assertIn("onClose: () => desktopUpdateCardVisible.value = false", update_card)
        self.assertNotIn("Changelog", update_card)
        self.assertNotIn("launchUrl", update_card)

        main = (ROOT / "flutter/lib/main.dart").read_text()
        prompt = main[main.index("Future<void> _showStartupUpdatePrompt") : main.index("void _handleDesktopUpdateResult")]
        self.assertIn("if (isDesktop)", prompt)
        self.assertIn("desktopUpdateCardVisible.value = true", prompt)

    def test_update_commands_report_command_id_for_download_and_install_states(self):
        updater = (ROOT / "src/updater.rs").read_text()
        self.assertIn("fn report_command_update_event(", updater)
        for status in ("downloaded", "installing"):
            self.assertIn(f'report_command_update_event(command, "{status}", "")', updater)
        self.assertIn("fn report_pending_update_terminal_with_error(", updater)
        self.assertIn("persist_and_report_update_command_terminal(", updater)

    def test_installed_updates_relaunch_windows_and_macos_clients(self):
        windows = (ROOT / "src/updater.rs").read_text()
        macos = (ROOT / "src/platform/macos.rs").read_text()
        self.assertIn('launch_privileged_process(', windows)
        self.assertIn('"{} --update {}"', windows)
        self.assertIn('launchctl asuser <uid> open -n -a /Applications/RustDesk.app/', macos)
        self.assertIn('write_result installed', macos)

    def test_verified_update_command_supports_android_but_not_ios(self):
        source = (ROOT / "src/flutter_ffi.rs").read_text()
        command = source[source.index('if _key == "install-verified-update"') - 100 :]
        command = command[: command.index("return;", command.index("manually_check_update"))]

        self.assertIn('#[cfg(not(target_os = "ios"))]', command)
        self.assertNotIn('target_os = "android"', command)

    def test_android_update_checks_send_exact_apk_identity_and_device_auth(self):
        source = (ROOT / "src/common.rs").read_text()
        auth = source[source.index("let auth = crate::updater::update_device_auth_headers") - 100 :]
        auth = auth[: auth.index("let proxy_conf")]

        self.assertIn('#[cfg(not(target_os = "ios"))]', auth)
        self.assertNotIn('target_os = "android"', auth)
        request = source[source.index("let (target_key, package_kind)") : source.index("let identity")]
        self.assertIn('#[cfg(not(target_os = "ios"))]', request)
        self.assertNotIn('target_os = "android"', request)

        updater = (ROOT / "src/updater.rs").read_text()
        self.assertIn('#[cfg(target_os = "android")]\nfn update_target_kind()', updater)
        self.assertIn('Ok("apk")', updater)

    def test_android_starts_realtime_update_policy_and_opens_verified_apk(self):
        crate_root = (ROOT / "src/lib.rs").read_text()
        self.assertIn('#[cfg(not(target_os = "ios"))]\nmod updater;', crate_root)

        ffi = (ROOT / "src/flutter_ffi.rs").read_text()
        initializer = ffi[ffi.index("fn initialize(") : ffi.index("pub fn set_cur_session_id")]
        self.assertIn('#[cfg(not(target_os = "ios"))]\n    crate::updater::start_auto_update();', initializer)

        updater = (ROOT / "src/updater.rs").read_text()
        self.assertIn('"android_update_ready"', updater)
        android = (ROOT / "flutter/android/app/src/main/kotlin/com/carriez/flutter_hbb/MainActivity.kt").read_text()
        self.assertIn('"install_verified_apk"', android)
        self.assertIn("FileProvider.getUriForFile", android)
        self.assertIn("USER_ACTION_REQUIRED", android)
        self.assertNotIn("USER_ACTION_NOT_REQUIRED", android)
        self.assertIn("longVersionCode", android)
        self.assertIn("update_apk_version_not_newer", android)

        common = (ROOT / "flutter/lib/common.dart").read_text()
        installer = common[
            common.index("Future<void> startAndroidVerifiedUpdate(") : common.index(
                "Future<void> consumePendingAndroidUpdateReady()"
            )
        ]
        self.assertIn(
            "gFFI.invokeMethodWithResult<Map<dynamic, dynamic>>(\n"
            "      'install_verified_apk',",
            installer,
        )
        self.assertIn("kAndroidUpdateInstallResult", common)
        self.assertIn("requestOrigin == 'system'", common)
        app = (ROOT / "flutter/lib/main.dart").read_text()
        self.assertIn("if (isAndroid ||", app)
        self.assertIn("WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed", app)
        self.assertIn("void didChangeAppLifecycleState(AppLifecycleState state)", app)
        mobile = (ROOT / "flutter/lib/mobile/pages/server_page.dart").read_text()
        self.assertIn('case "on_android_update_install_status":', mobile)
        self.assertIn("isAndroidUpdateInstallTerminal(status)", mobile)
        self.assertIn("clear_update_install_status", mobile)

    def test_android_cold_start_polls_durable_install_state_before_clearing(self):
        app = (ROOT / "flutter/lib/main.dart").read_text()
        mobile = (ROOT / "flutter/lib/mobile/pages/server_page.dart").read_text()
        common = (ROOT / "flutter/lib/common.dart").read_text()
        ffi = (ROOT / "src/flutter_ffi.rs").read_text()
        updater = (ROOT / "src/updater.rs").read_text()

        self.assertLess(app.index("androidChannelInit();"), app.index("checkUpdate();", app.index("void runMobileApp")))
        self.assertIn("get_update_install_status", mobile)
        self.assertIn("consumeAndroidUpdateInstallStatus", mobile)
        self.assertIn("pending-android-update-ready", common)
        self.assertIn("pending-android-update-state", mobile)
        self.assertIn("pending_android_update_ready", ffi)
        self.assertIn("PendingAndroidUpdate", updater)
        self.assertIn("android_terminal_report_should_clear", updater)

    def test_android_about_owns_all_update_controls(self):
        mobile = (ROOT / "flutter/lib/mobile/pages/settings_page.dart").read_text()
        about = mobile[mobile.index('title: Text(translate("About"))') : mobile.index("return settings;")]
        self.assertIn("StandardAboutUpdateControls(", about)
        self.assertIn("kOptionEnableCheckUpdate", about)
        self.assertIn("kOptionAllowAutoUpdate", about)
        self.assertIn("kOptionEnableScheduledUpdate", about)
        self.assertIn("mainStartSoftwareUpdateCheck", mobile)

        enhancements = mobile[mobile.index("enhancementsTiles.add") : mobile.index("defaultDisplaySection()")]
        self.assertNotIn("kOptionEnableCheckUpdate", enhancements)

    def test_background_update_scheduler_has_one_retry_path_and_policy_gate(self):
        updater = (ROOT / "src/updater.rs").read_text()

        self.assertNotIn("fn schedule_update_check_retry", updater)
        self.assertIn("if background_update_enabled()", updater)
        self.assertIn("if scheduled_update_interval().is_none()", updater)
        self.assertIn("wait_for_mac_schedule_change(&schedule_rx, next_delay)", updater)

    def test_android_remote_monitor_menu_uses_official_per_display_switching(self):
        source = (ROOT / "flutter/lib/mobile/pages/remote_page.dart").read_text()
        monitor_menu = source[source.index("void showOptions(") : source.index("List<TRadioMenu<String>> viewStyleRadios")]

        self.assertNotIn("translate('All displays')", monitor_menu)
        self.assertNotIn("openMonitorInTheSameTab(kAllDisplayValue", monitor_menu)
        self.assertIn("pi.currentDisplay != kAllDisplayValue", monitor_menu)
        self.assertIn("openMonitorInTheSameTab(i, gFFI, pi)", monitor_menu)

    def test_macos_detached_update_preserves_command_id(self):
        source = (ROOT / "src/platform/macos.rs").read_text()
        script = source[source.index("write_result() {{") : source.index("bootstrap_agent() {{")]

        self.assertIn("printf 'command_id=%s\\n' '{command_id}'", script)
        self.assertIn("command_id = event.command_id.as_deref().unwrap_or_default()", source)

    def test_removed_remote_actions_do_not_reappear(self):
        desktop = (ROOT / "flutter/lib/desktop/widgets/remote_toolbar.dart").read_text()
        mobile = (ROOT / "flutter/lib/mobile/pages/remote_page.dart").read_text()
        camera = (ROOT / "flutter/lib/mobile/pages/view_camera_page.dart").read_text()
        common_toolbar = (ROOT / "flutter/lib/common/widgets/toolbar.dart").read_text()

        self.assertIn("toolbarItems.add(_ChatMenu", desktop)
        self.assertNotIn("class _VoiceCallMenu", desktop)
        self.assertNotIn("class _RecordMenu", desktop)
        self.assertNotIn("showChatOptions", mobile)
        self.assertIn("!isWeb && !isAndroid", mobile)
        self.assertIn("onPressedTextChat", mobile)
        self.assertNotIn("showChatOptions", camera)
        self.assertIn("!isWeb && !isAndroid", camera)
        self.assertIn("onPressedTextChat", camera)
        self.assertNotIn("sessionRequestVoiceCall", mobile)
        self.assertNotIn("sessionRequestVoiceCall", camera)
        self.assertNotIn("recordingModel.toggle()", common_toolbar)

    def test_address_book_web_console_entry_is_removed(self):
        address_book = (ROOT / "flutter/lib/common/widgets/address_book.dart").read_text()

        self.assertNotIn('translate("ab_web_console_tip")', address_book)
        self.assertNotIn("launchUrlString", address_book)

    def test_view_only_defaults_preserve_existing_sessions_and_reach_web(self):
        desktop = (ROOT / "flutter/lib/desktop/pages/remote_page.dart").read_text()
        common = (ROOT / "flutter/lib/common.dart").read_text()

        self.assertIn("hasTabWindowId: widget.tabWindowId != null", desktop)
        web_route = common[common.index("if (isWeb) {", common.index("connect(BuildContext")) :]
        self.assertIn("isViewOnly: isViewOnly", web_route)


if __name__ == "__main__":
    unittest.main()
