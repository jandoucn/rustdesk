import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/assemble-release-snapshot.py"


def load_assembler():
    spec = importlib.util.spec_from_file_location("assemble_release_snapshot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AssembleReleaseSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.assembler = load_assembler()

    @staticmethod
    def metadata(tag, build_seq):
        return {
            "version": "1.5.0",
            "build_number": str(build_seq),
            "build_seq": build_seq,
            "product": "rustdesk-yan",
            "channel": "stable",
            "source_commit": ("a" if tag == "new" else "b") * 40,
            "source_tag": tag,
        }

    def previous_manifest(self):
        metadata = self.metadata("old", 2026100107)
        inherited = b"old"
        targets = {}
        for edition in ("standard", "sos"):
            for key, name in {
                f"windows-x86_64-exe-{edition}": f"rustdesk-1.5.0-{edition}-windows-x86_64.exe",
                f"windows-x86_64-msi-{edition}": f"rustdesk-1.5.0-{edition}-windows-x86_64.msi",
                f"macos-aarch64-dmg-{edition}": f"rustdesk-1.5.0-{edition}-macos-aarch64.dmg",
                f"android-aarch64-apk-{edition}": f"rustdesk-1.5.0-{edition}-android-aarch64-signed.apk",
            }.items():
                targets[key] = {
                    **metadata,
                    "primary": f"https://download.yan.life/rustdesk/stable/old/{name}",
                    "mirrors": [f"https://github.test/releases/download/old/{name}"],
                    "size": len(inherited),
                    "sha256": hashlib.sha256(inherited).hexdigest(),
                    "signature": "sig",
                    "signature_key_id": "key",
                }
        return {**metadata, "schema": 2, "targets": targets}

    @staticmethod
    def write_current_platforms(directory, platforms, version="1.5.1", build_seq=2026100202):
        suffixes = {
            "windows": ("windows-x86_64.exe", "windows-x86_64.msi"),
            "macos": ("macos-aarch64.dmg",),
            "android": ("android-aarch64.apk",),
        }
        for edition in ("standard", "sos"):
            for platform in platforms:
                for suffix in suffixes[platform]:
                    name = f"rustdesk-{version}-{build_seq}-{edition}-{suffix}"
                    (directory / name).write_bytes(name.encode())

    def test_android_only_inherits_six_targets_and_keeps_origin_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            output = root / "output"
            current.mkdir()
            for edition in ("standard", "sos"):
                name = f"rustdesk-1.5.0-{edition}-android-aarch64-signed.apk"
                (current / name).write_bytes(name.encode())
            previous = self.previous_manifest()
            fetched = []

            snapshot = self.assembler.assemble_snapshot(
                current,
                output,
                self.metadata("new", 2026100201),
                "new",
                previous,
                lambda target, path: (
                    fetched.append(target["source_tag"]),
                    path.write_bytes(b"old"),
                    target["primary"],
                )[2],
            )

            self.assertEqual(len(snapshot["targets"]), 8)
            self.assertEqual(len(list(output.glob("rustdesk-*"))), 8)
            self.assertEqual(len(fetched), 6)
            self.assertEqual(
                snapshot["targets"]["windows-x86_64-exe-standard"]["build_seq"],
                2026100107,
            )
            self.assertEqual(
                snapshot["targets"]["android-aarch64-apk-standard"]["build_seq"],
                2026100201,
            )
            self.assertEqual(
                snapshot["targets"]["android-aarch64-apk-standard"]["name"],
                "rustdesk-1.5.0-2026100201-standard-android-aarch64.apk",
            )
            self.assertEqual(
                snapshot["targets"]["windows-x86_64-exe-standard"]["name"],
                "rustdesk-1.5.0-2026100107-standard-windows-x86_64.exe",
            )
            self.assertEqual(
                snapshot["targets"]["windows-x86_64-exe-standard"]["source_key"],
                "rustdesk/stable/old/rustdesk-1.5.0-standard-windows-x86_64.exe",
            )
            self.assertTrue(
                snapshot["targets"]["windows-x86_64-exe-standard"]["copy_source_verified"]
            )

    def test_selected_platform_must_not_silently_inherit_missing_current_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            current.mkdir()
            (current / "rustdesk-1.5.0-standard-android-aarch64-signed.apk").write_bytes(b"one")

            with self.assertRaisesRegex(self.assembler.SnapshotError, "selected target"):
                self.assembler.assemble_snapshot(
                    current,
                    root / "output",
                    self.metadata("new", 2026100201),
                    "new",
                    self.previous_manifest(),
                    lambda target, path: path.write_bytes(b"old"),
                )

    def test_inherited_target_hash_and_size_must_match_previous_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            current.mkdir()
            for edition in ("standard", "sos"):
                name = f"rustdesk-1.5.1-2026100201-{edition}-android-aarch64.apk"
                (current / name).write_bytes(b"new")

            with self.assertRaisesRegex(
                self.assembler.SnapshotError, "inherited target (size|SHA-256) mismatch"
            ):
                self.assembler.assemble_snapshot(
                    current,
                    root / "output",
                    {
                        **self.metadata("new", 2026100201),
                        "version": "1.5.1",
                    },
                    "new",
                    self.previous_manifest(),
                    lambda target, path: path.write_bytes(b"corrupt"),
                )

    def test_schema_two_inherited_target_requires_build_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            current.mkdir()
            self.write_current_platforms(current, ("android",))
            previous = self.previous_manifest()
            previous["targets"]["windows-x86_64-exe-standard"]["build_number"] = ""

            with self.assertRaisesRegex(
                self.assembler.SnapshotError, "release identity is incomplete"
            ):
                self.assembler.assemble_snapshot(
                    current,
                    root / "output",
                    {**self.metadata("new", 2026100202), "version": "1.5.1"},
                    "new",
                    previous,
                    lambda target, path: (
                        path.write_bytes(b"old"),
                        target["primary"],
                    )[1],
                )

    def test_mirror_fallback_disables_server_side_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            current.mkdir()
            self.write_current_platforms(current, ("android",))

            snapshot = self.assembler.assemble_snapshot(
                current,
                root / "output",
                {**self.metadata("new", 2026100202), "version": "1.5.1"},
                "new",
                self.previous_manifest(),
                lambda target, path: (
                    path.write_bytes(b"old"),
                    target["mirrors"][0],
                )[1],
            )

            self.assertFalse(
                snapshot["targets"]["windows-x86_64-exe-standard"]["copy_source_verified"]
            )

    def test_windows_and_macos_build_inherit_only_android_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            current.mkdir()
            self.write_current_platforms(current, ("windows", "macos"))

            snapshot = self.assembler.assemble_snapshot(
                current,
                root / "output",
                {**self.metadata("new", 2026100202), "version": "1.5.1"},
                "new",
                self.previous_manifest(),
                lambda target, path: (
                    path.write_bytes(b"old"),
                    target["primary"],
                )[1],
            )

            current_targets = {
                key for key, target in snapshot["targets"].items() if target["current"]
            }
            inherited_targets = set(snapshot["targets"]) - current_targets
            self.assertEqual(len(current_targets), 6)
            self.assertEqual(
                inherited_targets,
                {
                    "android-aarch64-apk-standard",
                    "android-aarch64-apk-sos",
                },
            )

    def test_all_platform_build_uses_eight_current_targets_without_previous_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            current.mkdir()
            self.write_current_platforms(current, ("windows", "macos", "android"))

            snapshot = self.assembler.assemble_snapshot(
                current,
                root / "output",
                {**self.metadata("new", 2026100202), "version": "1.5.1"},
                "new",
                {},
                lambda target, path: self.fail("all build must not fetch inherited assets"),
            )

            self.assertEqual(len(snapshot["targets"]), 8)
            self.assertTrue(all(target["current"] for target in snapshot["targets"].values()))


if __name__ == "__main__":
    unittest.main()
