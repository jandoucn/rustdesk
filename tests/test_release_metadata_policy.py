import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataPolicyTest(unittest.TestCase):
    def test_release_metadata_stays_empty_and_uses_the_canonical_tag(self):
        agents = (ROOT / "AGENTS.md").read_text()
        caller = (ROOT / ".github/workflows/flutter-tag.yml").read_text()
        build = (ROOT / ".github/workflows/flutter-build.yml").read_text()
        release_notes = (ROOT / "release-notes.zh.md").read_text()

        self.assertIn("Release 正文和 tag 说明必须保持为空", agents)
        self.assertNotIn("打 tag 时原文放进 Release", agents)
        self.assertIn('tag_base="v${version}-build-${formatted_date}"', caller)
        self.assertIn('release_name="$tag"', caller)
        self.assertIn("publish-release:", build)
        self.assertNotIn("publish-release-notes:", build)
        self.assertNotIn("body_path:", build)
        self.assertNotIn("generate_release_notes:", build)
        self.assertIn("name: ${{ inputs.release-name != '' && inputs.release-name || env.TAG_NAME }}", build)
        self.assertIn("`v1.5.0-build-2026.09.30-01`", release_notes)
        self.assertNotIn("`20260929-01`", release_notes)


if __name__ == "__main__":
    unittest.main()
