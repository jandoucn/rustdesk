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
