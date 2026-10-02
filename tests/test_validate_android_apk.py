import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/validate-android-apk.py"


def load_module():
    spec = importlib.util.spec_from_file_location("validate_android_apk", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ValidateAndroidApkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_module()

    def test_accepts_release_identity_and_flutter_split_abi_version_codes(self):
        base_version_code = 2026100301
        for offset in (0, 1_000, 2_000, 3_000, 4_000):
            with self.subTest(offset=offset):
                badging = (
                    "package: name='com.carriez.flutter_hbb' "
                    f"versionCode='{base_version_code + offset}' "
                    "versionName='1.5.2' compileSdkVersion='36'\n"
                )

                self.validator.validate_badging(
                    badging,
                    expected_package="com.carriez.flutter_hbb",
                    expected_version="1.5.2",
                    base_version_code=base_version_code,
                )

    def test_rejects_non_flutter_split_abi_version_code(self):
        badging = (
            "package: name='com.carriez.flutter_hbb' versionCode='2026110301' "
            "versionName='1.5.2' compileSdkVersion='36'\n"
        )

        with self.assertRaisesRegex(ValueError, "versionCode"):
            self.validator.validate_badging(
                badging,
                expected_package="com.carriez.flutter_hbb",
                expected_version="1.5.2",
                base_version_code=2026100301,
            )

    def test_rejects_stale_pubspec_identity(self):
        badging = (
            "package: name='com.carriez.flutter_hbb' versionCode='2026095001' "
            "versionName='1.5.0' compileSdkVersion='36'\n"
        )

        with self.assertRaisesRegex(ValueError, "versionName"):
            self.validator.validate_badging(
                badging,
                expected_package="com.carriez.flutter_hbb",
                expected_version="1.5.2",
                base_version_code=2026100301,
            )


if __name__ == "__main__":
    unittest.main()
