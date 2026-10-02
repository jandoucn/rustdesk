import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/validate-release-selection.py"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_release_selection", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleasePlatformSelectionTest(unittest.TestCase):
    def test_accepts_single_multiple_and_all_platforms(self):
        validator = load_validator()
        self.assertEqual(validator.parse_platforms("windows"), ("windows",))
        self.assertEqual(
            validator.parse_platforms("windows,android"),
            ("windows", "android"),
        )
        self.assertEqual(
            validator.parse_platforms("all"),
            ("windows", "macos", "android"),
        )

    def test_rejects_empty_duplicate_or_unknown_platforms(self):
        validator = load_validator()
        for value in ("", "windows,", "windows,windows", "linux", "all,android"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validator.parse_platforms(value)

    def test_complete_snapshot_requires_both_editions(self):
        validator = load_validator()
        validator.validate_selection("android", True, True)
        with self.assertRaisesRegex(ValueError, "standard.*SOS"):
            validator.validate_selection("android", True, False)
        with self.assertRaisesRegex(ValueError, "standard.*SOS"):
            validator.validate_selection("android", False, True)


if __name__ == "__main__":
    unittest.main()
