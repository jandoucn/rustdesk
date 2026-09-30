import ast
import re
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


class MacosReleaseAppPathTest(unittest.TestCase):
    def test_release_scripts_use_configured_product_name(self) -> None:
        product_name = macos_product_name()
        self.assertIn(" ", product_name)
        app_name = f"{product_name}.app"

        build_script = (REPO_ROOT / "build.py").read_text(encoding="utf-8")
        workflow = (
            REPO_ROOT / ".github/workflows/flutter-build.yml"
        ).read_text(encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
