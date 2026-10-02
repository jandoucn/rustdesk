import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SIGN_SCRIPT = REPO_ROOT / ".github/scripts/sign-macos-app.sh"
WORKFLOW = REPO_ROOT / ".github/workflows/flutter-build.yml"


class MacosSigningTest(unittest.TestCase):
    def test_ad_hoc_signing_uses_stable_bundle_identifier_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "RustDesk Yan.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            with (contents / "Info.plist").open("wb") as plist:
                plistlib.dump({"CFBundleIdentifier": "com.carriez.rustdesk"}, plist)

            entitlements = root / "Release.entitlements"
            with entitlements.open("wb") as plist:
                plistlib.dump(
                    {"com.apple.security.device.audio-input": True}, plist
                )

            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "codesign.log"
            codesign = bin_dir / "codesign"
            codesign.write_text(
                "#!/bin/sh\n"
                'echo "$*" >> "$TEST_LOG"\n'
                'if [ "$1" = "-d" ]; then\n'
                "  cat <<'PLIST'\n"
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                "<plist version=\"1.0\"><dict>\n"
                "<key>com.apple.security.device.audio-input</key><true/>\n"
                "</dict></plist>\n"
                "PLIST\n"
                'elif [ "$1" = "-dr" ]; then\n'
                '  echo \'designated => identifier "com.carriez.rustdesk"\' >&2\n'
                "fi\n",
                encoding="utf-8",
            )
            codesign.chmod(0o755)

            env = os.environ.copy()
            env.update({"PATH": f"{bin_dir}:{env['PATH']}", "TEST_LOG": str(log)})
            result = subprocess.run(
                ["bash", str(SIGN_SCRIPT), str(app), "-", str(entitlements)],
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            commands = log.read_text(encoding="utf-8")
            self.assertIn(
                '--requirements =designated => identifier "com.carriez.rustdesk"',
                commands,
            )

    def test_unsigned_workflow_uses_the_shared_signing_script(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        unsigned_step = workflow.split(
            "- name: Sign copied service without hardened runtime", 1
        )[1].split("- name: create unsigned dmg", 1)[0]

        self.assertIn("bash ./.github/scripts/sign-macos-app.sh", unsigned_step)
        self.assertIn('"-"', unsigned_step)
        self.assertNotIn('codesign --force --sign - "$app"', unsigned_step)


if __name__ == "__main__":
    unittest.main()
