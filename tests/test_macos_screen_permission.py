import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "macos-screen-permission.sh"


class MacosScreenPermissionTest(unittest.TestCase):
    def test_wizard_clears_quarantine_and_verifies_each_permission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "RustDesk Yan.app"
            executable = app / "Contents/MacOS/RustDesk Yan"
            executable.parent.mkdir(parents=True)
            executable.touch()
            executable.chmod(0o755)

            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "commands.log"
            state = root / "state"
            state.mkdir()

            self._write_command(bin_dir, "xattr", 'echo "xattr $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "tccutil", 'echo "tccutil $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "osascript", 'echo "osascript $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "pkill", 'echo "pkill $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "pgrep", "exit 1")
            self._write_command(bin_dir, "codesign", 'echo "codesign $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "sleep", ":")
            self._write_command(bin_dir, "uname", 'echo "Darwin"')
            self._write_command(
                bin_dir,
                "open",
                'echo "open $*" >> "$TEST_LOG"\n'
                'case "$*" in\n'
                '  *--check-macos-permissions*)\n'
                '    for arg in "$@"; do\n'
                '      case "$arg" in\n'
                '        --macos-permission-output=*) output=${arg#*=} ;;\n'
                '      esac\n'
                '    done\n'
                '    screen_recording=false\n'
                '    accessibility=false\n'
                '    input_monitoring=false\n'
                '    test -f "$TEST_STATE/ScreenCapture" && screen_recording=true\n'
                '    test -f "$TEST_STATE/Accessibility" && accessibility=true\n'
                '    test -f "$TEST_STATE/ListenEvent" && input_monitoring=true\n'
                '    echo "screen_recording=$screen_recording accessibility=$accessibility input_monitoring=$input_monitoring" > "$output"\n'
                '    ;;\n'
                '  *"--args --open-window"*) : ;;\n'
                '  *Privacy_ScreenCapture*) touch "$TEST_STATE/ScreenCapture" ;;\n'
                '  *Privacy_Accessibility*) touch "$TEST_STATE/Accessibility" ;;\n'
                '  *Privacy_ListenEvent*) touch "$TEST_STATE/ListenEvent" ;;\n'
                'esac',
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "RUSTDESK_APP": str(app),
                    "RUSTDESK_BUNDLE_ID": "com.example.rustdesk",
                    "RUSTDESK_EXECUTABLE": str(executable),
                    "RUSTDESK_OPEN_COMMAND": str(bin_dir / "open"),
                    "RUSTDESK_PERMISSION_NO_COLOR": "1",
                    "RUSTDESK_PERMISSION_SLEEP_SECONDS": "1",
                    "RUSTDESK_PERMISSION_TIMEOUT_SECONDS": "5",
                    "TEST_LOG": str(log),
                    "TEST_STATE": str(state),
                }
            )

            result = subprocess.run(
                ["/bin/sh", str(SCRIPT)],
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = result.stdout
            self.assertIn("清除隔离属性", output)
            self.assertIn("屏幕录制权限：已通过", output)
            self.assertIn("辅助功能权限：已通过", output)
            self.assertIn("输入监控权限：已通过", output)
            self.assertIn("权限配置完成", output)

            commands = log.read_text(encoding="utf-8")
            self.assertIn(f"xattr -cr {app}", commands)
            self.assertNotIn("tccutil reset", commands)
            self.assertIn(f"open -n {app} --args --open-window", commands)
            self.assertIn(
                f"open -n -W {app} --args --check-macos-permissions",
                commands,
            )
            self.assertGreaterEqual(commands.count("osascript "), 2)
            self.assertEqual(commands.count("pkill -x RustDesk Yan"), 2)
            self.assertEqual(commands.count(f"open -n {app} --args --open-window"), 2)

            reset_env = env.copy()
            reset_env["RUSTDESK_RESET_PERMISSIONS"] = "1"
            reset_result = subprocess.run(
                ["/bin/sh", str(SCRIPT)],
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=reset_env,
                timeout=10,
            )
            self.assertEqual(
                reset_result.returncode,
                0,
                reset_result.stdout + reset_result.stderr,
            )
            reset_commands = log.read_text(encoding="utf-8")
            self.assertIn("tccutil reset ScreenCapture com.example.rustdesk", reset_commands)
            self.assertIn("tccutil reset Accessibility com.example.rustdesk", reset_commands)
            self.assertIn("tccutil reset ListenEvent com.example.rustdesk", reset_commands)

    @staticmethod
    def _write_command(bin_dir: Path, name: str, body: str) -> None:
        command = bin_dir / name
        command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        command.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
