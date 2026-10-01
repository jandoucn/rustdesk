import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAndRemoteUiContractTest(unittest.TestCase):
    def test_oss_publish_probes_the_requested_release_and_fails_closed(self):
        workflow = (ROOT / ".github/workflows/publish-oss.yml").read_text()

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
        self.assertIn('sequence_value=$((10#$build_seq % 100))', caller)
        self.assertIn('printf -v sequence \'%02d\' "$sequence_value"', caller)
        self.assertIn('git ls-remote --exit-code --tags origin "refs/tags/${tag}"', caller)
        self.assertNotIn('max_sequence=0', caller)
        self.assertIn('release_name="$tag"', caller)
        self.assertNotIn('yan-v${version}', caller)
        self.assertIn("fail-fast: true", caller)
        self.assertIn("group: flutter-tag-release", caller)
        self.assertIn("cancel-in-progress: false", caller)
        self.assertIn("if: inputs.standard && inputs.sos && inputs.platforms == 'all'", caller)
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
        self.assertIn('mode == "disabled" && request_origin != "command"', rust_common)
        self.assertIn(
            "update_client_identity(&crate::encode64(hbb_common::get_uuid()))",
            rust_common,
        )
        self.assertIn(
            "update_client_identity(&crate::encode64(hbb_common::get_uuid()))",
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

    def test_verified_update_command_is_desktop_only(self):
        source = (ROOT / "src/flutter_ffi.rs").read_text()
        command = source[source.index('if _key == "install-verified-update"') - 100 :]
        command = command[: command.index("return;", command.index("manually_check_update"))]

        self.assertIn(
            '#[cfg(not(any(target_os = "android", target_os = "ios")))]',
            command,
        )

    def test_update_device_auth_is_desktop_only(self):
        source = (ROOT / "src/common.rs").read_text()
        auth = source[source.index("let auth = crate::updater::update_device_auth_headers") - 100 :]
        auth = auth[: auth.index("let proxy_conf")]

        self.assertIn(
            '#[cfg(not(any(target_os = "android", target_os = "ios")))]',
            auth,
        )
        self.assertIn(
            '#[cfg(not(any(target_os = "android", target_os = "ios")))]',
            source[source.index("let build_request") : source.index("let latest_release_response")],
        )

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
