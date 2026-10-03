import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "macos-screen-permission.sh"


class MacosScreenPermissionTest(unittest.TestCase):
    def test_startup_fix_clears_quarantine_and_does_not_touch_privacy_permissions(self) -> None:
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

            self._write_command(bin_dir, "uname", 'echo "Darwin"')
            self._write_command(bin_dir, "xattr", 'echo "xattr $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "codesign", 'echo "codesign $*" >> "$TEST_LOG"; exit 0')
            self._write_command(bin_dir, "open", 'echo "open $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "osascript", 'echo "osascript $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "pkill", 'echo "pkill $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "pgrep", "exit 1")
            self._write_command(bin_dir, "sleep", ":")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "RUSTDESK_APP": str(app),
                    "RUSTDESK_BUNDLE_ID": "com.example.rustdesk",
                    "RUSTDESK_EXECUTABLE": str(executable),
                    "RUSTDESK_OPEN_COMMAND": str(bin_dir / "open"),
                    "RUSTDESK_PERMISSION_LOG_FILE": "/dev/null",
                    "RUSTDESK_PERMISSION_SLEEP_SECONDS": "0",
                    "TEST_LOG": str(log),
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
            self.assertIn("清除隔离属性", result.stdout)
            self.assertIn("应用签名有效", result.stdout)
            self.assertIn("启动修复完成", result.stdout)
            self.assertIn("请在系统设置中手动开启", result.stdout)
            commands = log.read_text(encoding="utf-8")
            self.assertIn(f"xattr -cr {app}", commands)
            self.assertIn(f"open -n {app}", commands)
            self.assertNotIn("tccutil", commands)
            self.assertNotIn("System Settings", commands)
            self.assertNotIn("--check-macos-permissions", commands)

    def test_invalid_signature_repairs_service_before_app_without_resetting_tcc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "RustDesk Yan.app"
            executable = app / "Contents/MacOS/RustDesk Yan"
            service = app / "Contents/MacOS/service"
            executable.parent.mkdir(parents=True)
            executable.touch()
            service.touch()
            executable.chmod(0o755)
            service.chmod(0o755)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "commands.log"
            signed = root / "signed"

            self._write_command(bin_dir, "uname", 'echo "Darwin"')
            self._write_command(bin_dir, "xattr", ":")
            self._write_command(bin_dir, "open", 'echo "open $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "osascript", ":")
            self._write_command(bin_dir, "pkill", ":")
            self._write_command(bin_dir, "pgrep", "exit 1")
            self._write_command(bin_dir, "sleep", ":")
            self._write_command(
                bin_dir,
                "codesign",
                'echo "codesign $*" >> "$TEST_LOG"\n'
                'case "$*" in\n'
                '  *--verify*) test -f "$SIGNED" ;;\n'
                '  *--sign*) touch "$SIGNED" ;;\n'
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
                    "RUSTDESK_PERMISSION_LOG_FILE": "/dev/null",
                    "RUSTDESK_PERMISSION_SLEEP_SECONDS": "0",
                    "TEST_LOG": str(log),
                    "SIGNED": str(signed),
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
            self.assertIn("临时签名修复完成", result.stdout)
            commands = log.read_text(encoding="utf-8")
            service_command = f"codesign --force --sign - {service}"
            app_command = f"codesign --force --sign - {app}"
            command_lines = commands.splitlines()
            service_line = next(i for i, line in enumerate(command_lines) if service_command in line)
            app_line = next(
                i
                for i, line in enumerate(command_lines)
                if app_command in line and line.strip() != service_command
            )
            self.assertLess(service_line, app_line)
            self.assertNotIn("tccutil", commands)

    @staticmethod
    def _write_command(bin_dir: Path, name: str, body: str) -> None:
        command = bin_dir / name
        command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        command.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
