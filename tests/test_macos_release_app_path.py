import ast
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def macos_product_name() -> str:
    config = (
        REPO_ROOT / "flutter/macos/Runner/Configs/AppInfo.xcconfig"
    ).read_text(encoding="utf-8")
    match = re.search(r"^PRODUCT_NAME\s*=\s*(.+?)\s*$", config, re.MULTILINE)
    if match is None:
        raise AssertionError("PRODUCT_NAME is missing from AppInfo.xcconfig")
    return match.group(1)


def macos_rename_script() -> str:
    workflow = (
        REPO_ROOT / ".github/workflows/flutter-build.yml"
    ).read_text(encoding="utf-8")
    match = re.search(
        r"      - name: Rename rustdesk\n"
        r".*?        run: \|\n"
        r"(?P<script>.*?)"
        r"(?=\n      - name:)",
        workflow,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("macOS Rename rustdesk step is missing")
    script = "\n".join(
        line[10:] if line.startswith(" " * 10) else line
        for line in match.group("script").splitlines()
    )
    return (
        script.replace("${{ env.VERSION }}", "1.5.0")
        .replace("${{ env.BUILD_SEQ }}", "2026100202")
        .replace("${{ env.EDITION }}", "custom")
        .replace("${{ matrix.job.arch }}", "aarch64")
    )


class MacosReleaseAppPathTest(unittest.TestCase):
    def test_branded_bundle_ipc_authorization_uses_the_current_executable(self) -> None:
        source = (REPO_ROOT / "src/ipc/auth.rs").read_text(encoding="utf-8")
        helper = source[
            source.index("fn macos_gui_service_bundle_siblings") :
            source.index("#[cfg(target_os = \"windows\")]", source.index("fn macos_gui_service_bundle_siblings"))
        ]
        function = source[
            source.index("pub(crate) fn authorize_user_server_process") :
            source.index("#[cfg(windows)]", source.index("pub(crate) fn authorize_user_server_process"))
        ]

        self.assertIn('OsStr::new("MacOS")', helper)
        self.assertIn('OsStr::new("Contents")', helper)
        self.assertIn('OsStr::new("service")', helper)
        self.assertIn("bundle_dir.file_stem()", helper)
        self.assertIn("ensure_macos_user_server_peer_executable", function)
        self.assertNotIn('PathBuf::from(format!("/Applications/', function)

    def test_root_updater_supports_managed_and_standalone_install_topologies(self) -> None:
        source = (REPO_ROOT / "src/platform/macos.rs").read_text(encoding="utf-8")

        self.assertIn("enum MacInstallTopology", source)
        self.assertIn("MacInstallTopology::Managed", source)
        self.assertIn("MacInstallTopology::Standalone", source)
        self.assertIn("inconsistent launchd plist installation", source)
        self.assertIn('MacInstallTopology::Managed => "managed"', source)
        self.assertIn('MacInstallTopology::Standalone => "standalone"', source)
        self.assertIn('[ "$install_topology" = "managed" ]', source)
        self.assertIn("standalone bundle verification failed", source)
        self.assertIn("ensure_standalone_update_state_clean", source)
        residue_check = source[
            source.index("fn ensure_standalone_update_state_clean") :
            source.index("fn backup_update_plist", source.index("fn ensure_standalone_update_state_clean"))
        ]
        self.assertIn("launchctl_job_loaded", residue_check)
        self.assertIn('format!("system/{}", daemon_label)', residue_check)
        self.assertIn('format!("gui/{}/{}", uid, agent_label)', residue_check)
        self.assertIn('format!("user/{}/{}", uid, agent_label)', residue_check)
        self.assertIn('format!("login/{}/{}", loginwindow_asid, agent_label)', residue_check)
        self.assertIn("root_managed_process_running", residue_check)
        self.assertIn("service_ipc_path", residue_check)
        self.assertIn("clear_stale_service_ipc_state", residue_check)
        updater = (REPO_ROOT / "src/updater.rs").read_text(encoding="utf-8")
        start = updater.index("fn start_auto_update_check_")
        end = updater.index("\n}\n", start)
        self.assertIn("consume_mac_update_result()", updater[start:end])

    def test_root_update_shell_template_has_valid_syntax(self) -> None:
        source = (REPO_ROOT / "src/platform/macos.rs").read_text(encoding="utf-8")
        match = re.search(
            r'let script = format!\(\s*r#"(?P<script>.*?)"#,\s*app_name =',
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        script = match.group("script")
        script = script.replace("{{", "__OPEN_BRACE__").replace("}}", "__CLOSE_BRACE__")
        script = re.sub(r"\{[a-z_]+\}", "fixture", script)
        script = script.replace("__OPEN_BRACE__", "{").replace("__CLOSE_BRACE__", "}")
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    def test_standalone_update_result_is_delivered_to_the_requesting_user(self) -> None:
        source = (REPO_ROOT / "src/platform/macos.rs").read_text(encoding="utf-8")

        self.assertIn("USER_UPDATE_RESULT_ROOT", source)
        self.assertIn("ensure_user_update_result_directory", source)
        self.assertNotIn('/Users/Shared/.rustdeskupdate_result-', source)
        self.assertIn("update_result_paths_for_uid", source)
        self.assertIn("requesting_uid", source)
        self.assertIn('chown "$result_uid" "$result_tmp"', source)
        self.assertIn("consume_update_result", source)
        self.assertNotIn("pub fn consume_root_update_result", source)

    def test_standalone_update_verifies_relaunched_gui_before_commit(self) -> None:
        source = (REPO_ROOT / "src/platform/macos.rs").read_text(encoding="utf-8")
        standalone_verify = source.index("standalone bundle verification failed")
        installed_result = source.index(
            'write_result_file installed "$installed_result_stage"', standalone_verify
        )
        transaction = source[standalone_verify:installed_result]

        self.assertIn("relaunch_gui", transaction)
        self.assertIn("gui_snapshot_stable", source)
        self.assertIn("standalone GUI failed readiness check", transaction)

    def test_installed_result_publish_is_the_transaction_commit_point(self) -> None:
        source = (REPO_ROOT / "src/platform/macos.rs").read_text(encoding="utf-8")
        stage = source.index('write_result_file installed "$installed_result_stage"')
        publish = source.index("if ! publish_installed_result", stage)
        rollback_disabled = source.index("rollback_done=1", publish)
        bundle_committed = source.index("bundle_swapped=0", rollback_disabled)
        signal_blocked = source.index("trap '' HUP INT TERM", stage)
        signal_restored = source.index("trap - HUP INT TERM", bundle_committed)

        self.assertLess(stage, publish)
        self.assertLess(signal_blocked, publish)
        self.assertLess(publish, rollback_disabled)
        self.assertLess(rollback_disabled, bundle_committed)
        self.assertLess(bundle_committed, signal_restored)
        self.assertNotIn("write_result installed", source[stage:publish])

    def test_root_updater_binds_the_staged_bundle_to_the_target_build(self) -> None:
        source = (REPO_ROOT / "src/platform/macos.rs").read_text(encoding="utf-8")

        self.assertIn("Print :CFBundleShortVersionString", source)
        self.assertIn("Print :CFBundleVersion", source)
        self.assertIn("staged bundle build mismatch", source)

    def test_root_updater_supports_the_configured_product_name(self) -> None:
        product_name = macos_product_name()
        source = (REPO_ROOT / "src/platform/macos.rs").read_text(encoding="utf-8")

        self.assertIn("validate_update_app_name(&app_name)?", source)
        self.assertIn("matches!(byte, b' ' | b'-' | b'_' | b'.')", source)
        self.assertIn('pgrep -u "$agent_uid" -x "{app_name}"', source)
        self.assertIn('ditto "{src_app}" "$staged_bundle"', source)
        self.assertIn('mv "{app_bundle}" "{app_bundle}.bak"', source)
        self.assertIn('--write-plists "{app_name}"', source)
        self.assertIn("write_plists_for_bundle", source)
        self.assertIn(product_name, (REPO_ROOT / "flutter/macos/Runner/Configs/AppInfo.xcconfig").read_text())

    def test_release_scripts_use_configured_product_name(self) -> None:
        product_name = macos_product_name()
        self.assertIn(" ", product_name)
        app_name = f"{product_name}.app"

        build_script = (REPO_ROOT / "build.py").read_text(encoding="utf-8")
        workflow = (
            REPO_ROOT / ".github/workflows/flutter-build.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('EDITION: "${{ inputs.edition }}"', workflow)
        self.assertNotIn('EDITION: "custom"', workflow)
        self.assertNotIn("body_path: release-notes.zh.md", workflow)
        self.assertIn(
            "rustdesk-${{ env.VERSION }}-${{ env.BUILD_SEQ }}-${{ env.EDITION }}-windows-",
            workflow,
        )
        self.assertIn(
            "rustdesk-${{ env.VERSION }}-${{ env.BUILD_SEQ }}-${{ env.EDITION }}-android-",
            workflow,
        )
        self.assertIn(
            "rustdesk-${{ env.VERSION }}-${{ env.BUILD_SEQ }}-${{ env.EDITION }}-macos",
            workflow,
        )
        self.assertIn(
            "env.UPLOAD_ARTIFACT == 'true' && (env.MACOS_P12_BASE64 == null || env.MACOS_P12_BASE64 == '')",
            workflow,
        )
        self.assertIn(app_name, build_script)
        self.assertIn(app_name, workflow)
        tree = ast.parse(build_script)
        flutter_dmg = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "build_flutter_dmg"
        )
        system_commands = [
            call.args[0].value
            for call in ast.walk(flutter_dmg)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "system2"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ]
        self.assertTrue(any(app_name in command for command in system_commands))
        self.assertFalse(
            any(
                "./build/macos/Build/Products/Release/RustDesk.app" in command
                for command in system_commands
            )
        )
        self.assertNotIn(
            "./flutter/build/macos/Build/Products/Release/RustDesk.app",
            workflow,
        )
        self.assertIn("--build-name", build_script)
        self.assertIn("--build-number", build_script)
        self.assertIn("validate-macos-app-version.py", workflow)

    def test_unsigned_dmg_with_arch_suffix_does_not_require_rename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dmg = Path(temp_dir) / "rustdesk-1.5.0-2026100202-custom-aarch64.dmg"
            dmg.touch()

            subprocess.run(
                ["bash", "-e", "-c", macos_rename_script()],
                cwd=temp_dir,
                check=True,
            )

            self.assertTrue(dmg.exists())

    def test_signed_dmg_is_renamed_with_arch_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "rustdesk-1.5.0-2026100202-custom-macos.dmg"
            target = Path(temp_dir) / "rustdesk-1.5.0-2026100202-custom-macos-aarch64.dmg"
            source.touch()

            subprocess.run(
                ["bash", "-e", "-c", macos_rename_script()],
                cwd=temp_dir,
                check=True,
            )

            self.assertFalse(source.exists())
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
