import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/flutter-build.yml"
SIGN_SCRIPT = REPO_ROOT / ".github/scripts/sign-macos-app.sh"


class MacosSigningTest(unittest.TestCase):
    def test_unsigned_workflow_preserves_successful_signing_sequence(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        unsigned_step = workflow.split(
            "- name: Sign copied service without hardened runtime", 1
        )[1].split("- name: create unsigned dmg", 1)[0]

        service = 'codesign --force --sign - "$app/Contents/MacOS/service"'
        app = 'codesign --force --sign - "$app"'
        verify = 'codesign --verify --deep --strict --verbose=2 "$app"'
        self.assertLess(unsigned_step.index(service), unsigned_step.index(app))
        self.assertLess(unsigned_step.index(app), unsigned_step.index(verify))
        self.assertNotIn("sign-macos-app.sh", unsigned_step)

    def test_certificate_signing_script_keeps_successful_build_flags(self) -> None:
        script = SIGN_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('sign_args+=(--options runtime --timestamp)', script)
        self.assertIn('codesign "${sign_args[@]}" --generate-entitlement-der', script)
        self.assertNotIn("--requirements", script)


if __name__ == "__main__":
    unittest.main()
