import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/validate-release-assets.py"


class ValidateReleaseAssetsTest(unittest.TestCase):
    def run_validator(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            return subprocess.run(
                ["python3", str(SCRIPT), str(root)],
                text=True,
                capture_output=True,
            )

    def test_accepts_nonempty_standard_and_sos_packages(self):
        result = self.run_validator(
            {
                "rustdesk-1.5.0-standard-x86_64.exe": b"standard",
                "rustdesk-1.5.0-sos-x86_64.exe": b"sos",
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validated 2 release assets", result.stdout)

    def test_rejects_missing_edition(self):
        result = self.run_validator(
            {"rustdesk-1.5.0-standard-x86_64.exe": b"standard"}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing sos release asset", result.stderr)

    def test_can_validate_single_requested_edition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rustdesk-1.5.0-standard-x86_64.exe").write_bytes(b"standard")
            result = subprocess.run(
                ["python3", str(SCRIPT), str(root), "--editions", "standard"],
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_empty_or_duplicate_assets(self):
        empty = self.run_validator(
            {
                "rustdesk-1.5.0-standard-x86_64.exe": b"",
                "rustdesk-1.5.0-sos-x86_64.exe": b"sos",
            }
        )
        duplicate = self.run_validator(
            {
                "a/rustdesk-1.5.0-standard-x86_64.exe": b"one",
                "b/rustdesk-1.5.0-standard-x86_64.exe": b"two",
                "rustdesk-1.5.0-sos-x86_64.exe": b"sos",
            }
        )

        self.assertNotEqual(empty.returncode, 0)
        self.assertIn("empty release asset", empty.stderr)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("duplicate release asset name", duplicate.stderr)


if __name__ == "__main__":
    unittest.main()
