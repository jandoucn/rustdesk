import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "macos-screen-permission-uninstall.sh"


class MacosScreenPermissionUninstallTest(unittest.TestCase):
    def test_dry_run_never_executes_destructive_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "RustDesk Yan.app"
            app.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            command_log = root / "commands.log"
            self._write_command(bin_dir, "uname", 'echo "Darwin"')
            self._write_command(bin_dir, "rm", 'echo "rm $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "tccutil", 'echo "tccutil $*" >> "$TEST_LOG"')
            env = self._environment(bin_dir, app, command_log)

            result = subprocess.run(
                ["/bin/sh", str(SCRIPT), "--dry-run", "--yes"],
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("dry-run", result.stdout)
            self.assertIn("计划：删除应用", result.stdout)
            self.assertFalse(command_log.exists())

    def test_uninstall_resets_permissions_and_removes_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "RustDesk Yan.app"
            app.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            command_log = root / "commands.log"
            self._write_command(bin_dir, "uname", 'echo "Darwin"')
            self._write_command(bin_dir, "sleep", ":")
            self._write_command(bin_dir, "osascript", 'echo "osascript $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "pkill", 'echo "pkill $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "pgrep", "exit 1")
            self._write_command(bin_dir, "tccutil", 'echo "tccutil $*" >> "$TEST_LOG"')
            self._write_command(bin_dir, "rm", 'echo "rm $*" >> "$TEST_LOG"')
            env = self._environment(bin_dir, app, command_log)

            result = subprocess.run(
                ["/bin/sh", str(SCRIPT)],
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            commands = command_log.read_text(encoding="utf-8")
            self.assertIn("tccutil reset ScreenCapture com.carriez.rustdesk", commands)
            self.assertIn("tccutil reset Accessibility com.carriez.rustdesk", commands)
            self.assertIn("tccutil reset ListenEvent com.carriez.rustdesk", commands)
            self.assertIn(f"rm -rf {app}", commands)
            self.assertIn("卸载完成", result.stdout)

            purge_log = root / "purge-commands.log"
            purge_env = self._environment(bin_dir, app, purge_log)
            purge_env["HOME"] = str(root / "home")
            (root / "home/Library/Preferences/com.carriez.RustDesk").mkdir(parents=True)
            purge_result = subprocess.run(
                ["/bin/sh", str(SCRIPT), "--purge-data"],
                text=True,
                capture_output=True,
                env=purge_env,
                timeout=10,
            )

            self.assertEqual(purge_result.returncode, 0, purge_result.stdout + purge_result.stderr)
            purge_commands = purge_log.read_text(encoding="utf-8")
            self.assertIn(
                f"rm -rf {root / 'home/Library/Preferences/com.carriez.RustDesk'}",
                purge_commands,
            )

    @staticmethod
    def _environment(bin_dir: Path, app: Path, command_log: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "RUSTDESK_APP": str(app),
                "RUSTDESK_UNINSTALL_LOG_FILE": "/dev/null",
                "RUSTDESK_PERMISSION_SLEEP_SECONDS": "0",
                "TEST_LOG": str(command_log),
            }
        )
        return env

    @staticmethod
    def _write_command(bin_dir: Path, name: str, body: str) -> None:
        command = bin_dir / name
        command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        command.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
