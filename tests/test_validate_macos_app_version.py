import importlib.util
import plistlib
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/validate-macos-app-version.py"


def load_module():
    spec = importlib.util.spec_from_file_location("validate_macos_app_version", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ValidateMacosAppVersionTest(unittest.TestCase):
    def test_accepts_expected_bundle_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            plist = Path(directory) / "RustDesk Yan.app" / "Contents" / "Info.plist"
            plist.parent.mkdir(parents=True)
            with plist.open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleShortVersionString": "1.5.2",
                        "CFBundleVersion": "2026100302",
                    },
                    stream,
                )
            load_module().validate_bundle(plist, "1.5.2", "2026100302")

    def test_rejects_stale_bundle_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            plist = Path(directory) / "Info.plist"
            with plist.open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleShortVersionString": "1.5.0",
                        "CFBundleVersion": "2026093001",
                    },
                    stream,
                )
            with self.assertRaises(ValueError):
                load_module().validate_bundle(plist, "1.5.2", "2026100302")


if __name__ == "__main__":
    unittest.main()
