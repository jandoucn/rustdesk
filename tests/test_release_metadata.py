import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/release-metadata.py"


def load_module():
    spec = importlib.util.spec_from_file_location("release_metadata", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseMetadataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.metadata = load_module()

    def test_new_semantic_version_resets_visible_sequence(self):
        tags = [
            f"v1.5.1-build-2026.10.02-{sequence:02d}"
            for sequence in range(1, 7)
        ]

        resolved = self.metadata.resolve_release_metadata(
            version="1.5.2",
            build_date="20261002",
            tags=tags,
            previous_android_version_code=2026100206,
        )

        self.assertEqual(resolved["build_number"], "20261002.1")
        self.assertEqual(resolved["build_seq"], 2026100201)
        self.assertEqual(resolved["android_version_code"], 2026100207)

    def test_existing_semantic_version_sequence_is_incremented(self):
        tags = [
            "v1.5.2-build-2026.10.02-01",
            "v1.5.2-build-2026.10.02-02",
        ]

        resolved = self.metadata.resolve_release_metadata(
            version="1.5.2",
            build_date="20261002",
            tags=tags,
            previous_android_version_code=2026100202,
        )

        self.assertEqual(resolved["build_number"], "20261002.3")
        self.assertEqual(resolved["build_seq"], 2026100203)
        self.assertEqual(resolved["android_version_code"], 2026100203)

    def test_next_day_resets_both_sequences_without_downgrading_android(self):
        resolved = self.metadata.resolve_release_metadata(
            version="1.5.2",
            build_date="20261003",
            tags=["v1.5.2-build-2026.10.02-06"],
            previous_android_version_code=2026100206,
        )

        self.assertEqual(resolved["build_number"], "20261003.1")
        self.assertEqual(resolved["build_seq"], 2026100301)
        self.assertEqual(resolved["android_version_code"], 2026100301)

    def test_android_sequence_handles_tag_gaps_and_semver_resets(self):
        tags = [
            "v1.5.1-build-2026.10.02-01",
            "v1.5.1-build-2026.10.02-06",
            "v1.5.2-build-2026.10.02-01",
        ]

        previous = self.metadata.infer_previous_android_version_code(
            build_date="20261002",
            tags=tags,
        )
        resolved = self.metadata.resolve_release_metadata(
            version="1.5.2",
            build_date="20261002",
            tags=tags,
            previous_android_version_code=previous,
        )

        self.assertEqual(previous, 2026100207)
        self.assertEqual(resolved["build_number"], "20261002.2")
        self.assertEqual(resolved["android_version_code"], 2026100208)


if __name__ == "__main__":
    unittest.main()
