import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataPolicyTest(unittest.TestCase):
    def test_new_semver_starts_at_build_one_with_independent_android_code(self):
        metadata = json.loads((ROOT / "version.json").read_text())

        self.assertEqual(metadata["version"], "1.5.2")
        self.assertEqual(metadata["build_number"], "20261003.6")
        self.assertEqual(metadata["build_seq"], 2026100306)
        self.assertEqual(metadata["android_version_code"], 2026100306)

        pubspec = (ROOT / "flutter/pubspec.yaml").read_text()
        self.assertIn(
            f'version: {metadata["version"]}+{metadata["android_version_code"]}',
            pubspec,
        )

    def test_release_metadata_stays_empty_and_uses_the_canonical_tag(self):
        agents = (ROOT / "AGENTS.md").read_text()
        caller = (ROOT / ".github/workflows/flutter-tag.yml").read_text()
        build = (ROOT / ".github/workflows/flutter-build.yml").read_text()
        release_notes = (ROOT / "release-notes.zh.md").read_text()

        self.assertIn("Release 正文和 tag 说明必须保持为空", agents)
        self.assertNotIn("打 tag 时原文放进 Release", agents)
        self.assertIn('tag_base="v${version}-build-${formatted_date}"', caller)
        self.assertIn('release_name="$tag"', caller)
        self.assertIn("publish_release_assets:", build)
        self.assertIn("if: inputs.publish_release_assets && inputs.upload-artifact", build)
        self.assertIn("publish_release_assets: false", caller)
        self.assertIn("publish-release:", caller)
        self.assertIn('gh release create "$RELEASE_TAG"', caller)
        self.assertIn('--notes ""', caller)
        self.assertNotIn("publish-release-notes:", build)
        self.assertNotIn("body_path:", build)
        self.assertNotIn("generate_release_notes:", build)
        self.assertIn("cleanup-incomplete-release:", caller)
        self.assertIn(
            'gh release delete "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" --cleanup-tag --yes',
            caller,
        )
        self.assertIn("`v1.5.0-build-2026.09.30-01`", release_notes)
        self.assertNotIn("`20260929-01`", release_notes)

    def test_release_notes_document_partial_snapshot_inheritance(self):
        release_notes = (ROOT / "release-notes.zh.md").read_text()

        self.assertIn("支持单平台、多平台或全部平台构建", release_notes)
        self.assertIn("未参与本轮构建的平台沿用上一份 stable manifest", release_notes)
        self.assertIn("继承包保留其真实版本号和构建序号", release_notes)


if __name__ == "__main__":
    unittest.main()
