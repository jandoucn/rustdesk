import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PartialReleaseE2ETest(unittest.TestCase):
    def test_android_only_produces_complete_release_and_two_uploads_six_copies(self):
        assembler = load_script(
            "assemble_release_snapshot_e2e",
            ROOT / ".github/scripts/assemble-release-snapshot.py",
        )
        publisher = load_script(
            "publish_release_to_oss_e2e",
            ROOT / ".github/scripts/publish-release-to-oss.py",
        )
        old_body = b"old-package"
        old_sha = hashlib.sha256(old_body).hexdigest()
        old_release = {
            "version": "1.5.0",
            "build_number": "20261001.7",
            "build_seq": 2026100107,
            "source_commit": "b" * 40,
            "source_tag": "v1.5.0-build-2026.10.01-07",
        }
        previous_targets = {}
        suffixes = {
            "windows-x86_64-exe": "windows-x86_64.exe",
            "windows-x86_64-msi": "windows-x86_64.msi",
            "macos-aarch64-dmg": "macos-aarch64.dmg",
            "android-aarch64-apk": "android-aarch64.apk",
        }
        for edition in ("standard", "sos"):
            for base_key, suffix in suffixes.items():
                name = f"rustdesk-1.5.0-{edition}-{suffix}"
                previous_targets[f"{base_key}-{edition}"] = {
                    **old_release,
                    "primary": f"https://download.yan.life/rustdesk/stable/old/{name}",
                    "mirrors": [],
                    "size": len(old_body),
                    "sha256": old_sha,
                    "signature": "old-signature",
                    "signature_key_id": "yan-release-2026",
                }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            output = root / "release"
            current.mkdir()
            for edition in ("standard", "sos"):
                (current / f"rustdesk-1.5.1-2026100201-{edition}-android-aarch64-signed.apk").write_bytes(
                    f"new-{edition}".encode()
                )
            metadata = {
                "version": "1.5.1",
                "build_number": "20261002.1",
                "build_seq": 2026100201,
                "product": "rustdesk-yan",
                "channel": "stable",
                "source_commit": "a" * 40,
            }
            snapshot = assembler.assemble_snapshot(
                current,
                output,
                metadata,
                "v1.5.1-build-2026.10.02-01",
                {"schema": 2, "targets": previous_targets},
                lambda target, path: (
                    path.write_bytes(old_body),
                    target["primary"],
                )[1],
            )

            operations = []
            current_assets = {
                key: output / value["name"]
                for key, value in snapshot["targets"].items()
                if value["current"]
            }
            inherited_assets = {
                key: {**value, "path": output / value["name"]}
                for key, value in snapshot["targets"].items()
                if not value["current"]
            }
            publisher.transfer_release_assets(
                current_assets,
                inherited_assets,
                lambda key, path: operations.append(("upload", key)),
                lambda source, key: operations.append(("copy", source, key)),
                "rustdesk/stable/new/",
            )

            self.assertEqual(len(snapshot["targets"]), 8)
            self.assertEqual(len(list(output.glob("rustdesk-*"))), 8)
            self.assertEqual(sum(op[0] == "upload" for op in operations), 2)
            self.assertEqual(sum(op[0] == "copy" for op in operations), 6)
            self.assertEqual(
                snapshot["targets"]["android-aarch64-apk-standard"]["build_seq"],
                2026100201,
            )
            self.assertEqual(
                snapshot["targets"]["macos-aarch64-dmg-standard"]["build_seq"],
                2026100107,
            )


if __name__ == "__main__":
    unittest.main()
