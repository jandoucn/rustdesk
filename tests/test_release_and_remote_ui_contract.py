import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAndRemoteUiContractTest(unittest.TestCase):
    def test_oss_publish_probes_the_requested_release_and_fails_closed(self):
        workflow = (ROOT / ".github/workflows/publish-oss.yml").read_text()

        self.assertIn('RELEASE_TAG: ${{ inputs.tag }}', workflow)
        self.assertIn('gh release download "$RELEASE_TAG"', workflow)
        self.assertIn("tag = os.environ['RELEASE_TAG']", workflow)
        self.assertNotIn('tag = "${{ inputs.tag }}"', workflow)
        self.assertIn('${OSS_PREFIX}/${RELEASE_TAG}/catalog.json', workflow)
        self.assertIn('test "$status" = 200', workflow)
        self.assertNotIn("test \"$status\" = 200 || echo", workflow)
        self.assertIn("oss2.resumable_upload(", workflow)
        self.assertIn("ThreadPoolExecutor(max_workers=4)", workflow)
        self.assertIn("num_threads=2", workflow)
        self.assertIn("bucket.head_object(object_key)", workflow)
        self.assertIn("sorted(tags, key=sort_key, reverse=True)[5:]", workflow)
        self.assertIn("if len(names) != 8 or missing", workflow)
        self.assertIn("stable_tag.fullmatch(candidate)", workflow)
        self.assertIn("legacy_tag.fullmatch(candidate)", workflow)
        self.assertIn("oss:PutObject/oss:GetObject/oss:DeleteObject", workflow)
        self.assertIn("and oss:ListObjects on", workflow)
        self.assertIn("request-id={request_id}", workflow)

        caller = (ROOT / ".github/workflows/flutter-tag.yml").read_text()
        self.assertIn("needs: [release-tag, run-flutter-tag-build]", caller)
        self.assertIn("uses: ./.github/workflows/publish-oss.yml", caller)
        self.assertIn("secrets: inherit", caller)
        self.assertIn('CUSTOM_TAG: ${{ inputs.tag }}', caller)
        self.assertNotIn('if [ "${{ inputs.tag }}"', caller)
        self.assertIn('tag="${base}-build${build_number}"', caller)
        self.assertNotIn('git/ref/tags/${base}', caller)
        self.assertIn("fail-fast: true", caller)

    def test_removed_remote_actions_do_not_reappear(self):
        desktop = (ROOT / "flutter/lib/desktop/widgets/remote_toolbar.dart").read_text()
        mobile = (ROOT / "flutter/lib/mobile/pages/remote_page.dart").read_text()
        camera = (ROOT / "flutter/lib/mobile/pages/view_camera_page.dart").read_text()
        common_toolbar = (ROOT / "flutter/lib/common/widgets/toolbar.dart").read_text()

        self.assertIn("toolbarItems.add(_ChatMenu", desktop)
        self.assertNotIn("class _VoiceCallMenu", desktop)
        self.assertNotIn("class _RecordMenu", desktop)
        self.assertNotIn("showChatOptions", mobile)
        self.assertIn("!isWeb && !isAndroid", mobile)
        self.assertIn("onPressedTextChat", mobile)
        self.assertNotIn("showChatOptions", camera)
        self.assertIn("!isWeb && !isAndroid", camera)
        self.assertIn("onPressedTextChat", camera)
        self.assertNotIn("sessionRequestVoiceCall", mobile)
        self.assertNotIn("sessionRequestVoiceCall", camera)
        self.assertNotIn("recordingModel.toggle()", common_toolbar)

    def test_address_book_web_console_entry_is_removed(self):
        address_book = (ROOT / "flutter/lib/common/widgets/address_book.dart").read_text()

        self.assertNotIn('translate("ab_web_console_tip")', address_book)
        self.assertNotIn("launchUrlString", address_book)

    def test_view_only_defaults_preserve_existing_sessions_and_reach_web(self):
        desktop = (ROOT / "flutter/lib/desktop/pages/remote_page.dart").read_text()
        common = (ROOT / "flutter/lib/common.dart").read_text()

        self.assertIn("hasTabWindowId: widget.tabWindowId != null", desktop)
        web_route = common[common.index("if (isWeb) {", common.index("connect(BuildContext")) :]
        self.assertIn("isViewOnly: isViewOnly", web_route)


if __name__ == "__main__":
    unittest.main()
