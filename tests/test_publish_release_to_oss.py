import importlib.util
import inspect
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/publish-release-to-oss.py"


def load_publisher():
    spec = importlib.util.spec_from_file_location("publish_release_to_oss", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBody:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class FakeHead:
    def __init__(self, content_length):
        self.content_length = content_length


class FakeBucket:
    def __init__(self, catalogs, keys, sizes):
        self.catalogs = catalogs
        self.keys = list(keys)
        self.sizes = sizes
        self.deleted = []

    def get_object(self, key):
        return FakeBody(self.catalogs[key])

    def head_object(self, key):
        return FakeHead(self.sizes[key])

    def delete_object(self, key):
        self.deleted.append(key)


class FakeObject:
    def __init__(self, key):
        self.key = key


class FakeHttp:
    def __init__(self, published_manifest):
        self.published_manifest = published_manifest
        self.calls = []

    def probe(self, url):
        self.calls.append(("probe", url))

    def post_json(self, url, payload, headers):
        self.calls.append(("post", url, payload, headers))

    def get_json(self, url):
        self.calls.append(("get", url))
        return self.published_manifest


class FailingHttp:
    def get_json(self, url):
        raise OSError("manifest unavailable")


class PublishReleaseToOssTest(unittest.TestCase):
    def setUp(self):
        self.publisher = load_publisher()

    def make_assets(self, directory, signed):
        names = []
        for edition in ("standard", "sos"):
            suffixes = [
                "windows-x86_64.exe",
                "windows-x86_64.msi",
                "macos-aarch64.dmg" if signed else "aarch64.dmg",
            ]
            if edition == "standard":
                suffixes.append(
                    "android-aarch64-signed.apk" if signed else "android-aarch64.apk"
                )
            for suffix in suffixes:
                name = f"rustdesk-1.5.0-{edition}-{suffix}"
                (directory / name).write_bytes(name.encode())
                names.append(name)
        return names

    def test_signed_and_unsigned_assets_resolve_to_actual_release_names(self):
        for signed in (False, True):
            with self.subTest(signed=signed), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                names = self.make_assets(directory, signed)

                resolved = self.publisher.resolve_release_assets(directory, "1.5.0")

                self.assertEqual(len(resolved), 7)
                self.assertEqual({path.name for path in resolved.values()}, set(names))
                self.assertEqual(
                    resolved["macos-aarch64-dmg-standard"].name,
                    "rustdesk-1.5.0-standard-macos-aarch64.dmg"
                    if signed
                    else "rustdesk-1.5.0-standard-aarch64.dmg",
                )
                self.assertEqual(
                    resolved["android-aarch64-apk-standard"].name,
                    "rustdesk-1.5.0-standard-android-aarch64-signed.apk"
                    if signed
                    else "rustdesk-1.5.0-standard-android-aarch64.apk",
                )
                self.assertNotIn("android-aarch64-apk-sos", resolved)

    def test_signature_payload_matches_client_envelope(self):
        metadata = {
            "version": "1.5.0",
            "build_seq": 2026093005,
            "product": "rustdesk-yan",
            "channel": "stable",
            "source_commit": "0123456789abcdef0123456789abcdef01234567",
        }

        payload = self.publisher.signature_payload(
            metadata, "windows-x86_64-exe-standard", 42, "AB" * 32
        )

        self.assertEqual(
            payload.decode(),
            "rustdesk-update-v1\n"
            "product=rustdesk-yan\n"
            "edition=multi\n"
            "channel=stable\n"
            "version=1.5.0\n"
            "build_seq=2026093005\n"
            "source_commit=0123456789abcdef0123456789abcdef01234567\n"
            "target_key=windows-x86_64-exe-standard\n"
            "size=42\n"
            f"sha256={'ab' * 32}\n",
        )

    def test_signature_payload_rejects_line_break_injection(self):
        metadata = {
            "version": "1.5.0",
            "build_seq": 2026093005,
            "product": "rustdesk-yan\nchannel=beta",
            "channel": "stable",
            "source_commit": "0" * 40,
        }
        with self.assertRaisesRegex(self.publisher.PublishError, "line break"):
            self.publisher.signature_payload(metadata, "target", 1, "a" * 64)

    def test_missing_or_extra_release_asset_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            names = self.make_assets(directory, signed=True)
            (directory / names[0]).unlink()
            with self.assertRaisesRegex(self.publisher.PublishError, "Missing release asset"):
                self.publisher.resolve_release_assets(directory, "1.5.0")

            (directory / names[0]).write_bytes(b"restored")
            (directory / "unexpected.txt").write_text("unexpected")
            with self.assertRaisesRegex(self.publisher.PublishError, "exactly 7"):
                self.publisher.resolve_release_assets(directory, "1.5.0")

    def test_sha256_manifest_is_metadata_not_an_install_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.make_assets(directory, signed=True)
            (directory / "SHA256SUMS").write_text("checksum manifest")

            resolved = self.publisher.resolve_release_assets(directory, "1.5.0")

            self.assertEqual(len(resolved), 7)

    def test_upload_concurrency_is_capped_at_four_total_transfers(self):
        source = inspect.getsource(self.publisher)

        self.assertIn("ThreadPoolExecutor(max_workers=4)", source)
        self.assertIn("num_threads=1", source)

    def test_failed_release_cleanup_deletes_only_the_current_prefix(self):
        bucket = FakeBucket({}, [], {})
        objects = [
            FakeObject("rustdesk/stable/current/a.exe"),
            FakeObject("rustdesk/stable/current/catalog.json"),
            FakeObject("rustdesk/stable/previous/a.exe"),
        ]

        self.publisher.cleanup_release_prefix(
            bucket,
            lambda prefix: (obj.key for obj in objects if obj.key.startswith(prefix)),
            "rustdesk/stable/current/",
        )

        self.assertEqual(
            bucket.deleted,
            [
                "rustdesk/stable/current/a.exe",
                "rustdesk/stable/current/catalog.json",
            ],
        )

    def test_publish_failure_checks_active_manifest_before_cleanup(self):
        source = inspect.getsource(self.publisher.main)

        self.assertLess(
            source.index("failed_release_cleanup_allowed("),
            source.index("cleanup_release_prefix("),
        )

    def test_failed_publish_does_not_delete_active_release_prefix(self):
        active_manifest = {
            "targets": {
                "windows-x86_64-exe-standard": {
                    "primary": "https://download.yan.life/rustdesk/stable/current/app.exe"
                }
            }
        }

        allowed = self.publisher.failed_release_cleanup_allowed(
            FakeHttp(active_manifest),
            "https://rdapi.yan.life/rd/update/v1/manifest/stable.json",
            "https://download.yan.life",
            "rustdesk/stable/current/",
        )

        self.assertFalse(allowed)

    def test_failed_publish_deletes_prefix_only_when_active_release_differs(self):
        active_manifest = {
            "targets": {
                "windows-x86_64-exe-standard": {
                    "primary": "https://download.yan.life/rustdesk/stable/previous/app.exe"
                }
            }
        }

        allowed = self.publisher.failed_release_cleanup_allowed(
            FakeHttp(active_manifest),
            "https://rdapi.yan.life/rd/update/v1/manifest/stable.json",
            "https://download.yan.life",
            "rustdesk/stable/current/",
        )

        self.assertTrue(allowed)

    def test_failed_publish_keeps_prefix_when_active_manifest_is_unknown(self):
        allowed = self.publisher.failed_release_cleanup_allowed(
            FailingHttp(),
            "https://rdapi.yan.life/rd/update/v1/manifest/stable.json",
            "https://download.yan.life",
            "rustdesk/stable/current/",
        )

        self.assertFalse(allowed)

    def test_failed_rerun_preserves_preexisting_complete_historical_release(self):
        prefix = "rustdesk/stable"
        tag = "v1.5.0-build-2026.09.30-05"
        release_prefix = f"{prefix}/{tag}/"
        catalog_key = f"{release_prefix}catalog.json"
        assets = [
            {
                "name": f"asset-{index}",
                "key": f"{release_prefix}asset-{index}",
                "size": index + 1,
                "sha256": f"{index:064x}",
                "signature": "signature",
            }
            for index in range(len(self.publisher.EXPECTED_TARGETS))
        ]
        body = json.dumps(
            {
                "schema": 2,
                "tag": tag,
                "build_seq": 2026093005,
                "published_at": 1,
                "assets": assets,
            }
        ).encode()
        keys = [catalog_key, *(asset["key"] for asset in assets)]
        sizes = {
            catalog_key: len(body),
            **{asset["key"]: asset["size"] for asset in assets},
        }
        bucket = FakeBucket({catalog_key: body}, keys, sizes)
        active_manifest = {
            "targets": {
                "windows-x86_64-exe-standard": {
                    "primary": "https://download.yan.life/rustdesk/stable/newer/app.exe"
                }
            }
        }

        preexisted_complete = self.publisher.release_prefix_is_complete(
            bucket,
            lambda object_prefix: (key for key in keys if key.startswith(object_prefix)),
            prefix,
            tag,
        )
        allowed = self.publisher.failed_release_cleanup_allowed(
            FakeHttp(active_manifest),
            "https://rdapi.yan.life/rd/update/v1/manifest/stable.json",
            "https://download.yan.life",
            release_prefix,
            preexisted_complete,
        )
        if allowed:
            self.publisher.cleanup_release_prefix(
                bucket,
                lambda object_prefix: (key for key in keys if key.startswith(object_prefix)),
                release_prefix,
            )

        self.assertTrue(preexisted_complete)
        self.assertFalse(allowed)
        self.assertEqual(bucket.deleted, [])

    def test_complete_release_hash_mismatch_fails_before_any_upload(self):
        target_keys = sorted(self.publisher.EXPECTED_TARGETS)
        upload_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            resolved = {}
            assets = []
            for index, target_key in enumerate(target_keys):
                path = directory / f"asset-{index}"
                path.write_bytes(f"local-{index}".encode())
                resolved[target_key] = path
                catalog_bytes = path.read_bytes() if index else b"different"
                assets.append(
                    {
                        "target_key": target_key,
                        "name": path.name,
                        "key": f"rustdesk/stable/tag/{path.name}",
                        "size": len(catalog_bytes),
                        "sha256": hashlib.sha256(catalog_bytes).hexdigest(),
                        "signature": "signature",
                        "release": {
                            "version": "1.5.0",
                            "build_number": "20260930.5",
                            "build_seq": 2026093005,
                            "source_commit": "a" * 40,
                            "source_tag": "v1.5.0-build-2026.09.30-05",
                        },
                    }
                )

            with self.assertRaisesRegex(self.publisher.PublishError, "differs"):
                self.publisher.prepare_uploaded_assets(
                    {"schema": 2, "assets": assets},
                    resolved,
                    lambda item: upload_calls.append(item),
                    {
                        target_key: {
                            "version": "1.5.0",
                            "build_number": "20260930.5",
                            "build_seq": 2026093005,
                            "source_commit": "a" * 40,
                            "source_tag": "v1.5.0-build-2026.09.30-05",
                        }
                        for target_key in target_keys
                    },
                    lambda release, target_key, size, sha256, signature: None,
                )

        self.assertEqual(upload_calls, [])

    def test_matching_complete_release_is_reused_without_upload(self):
        target_keys = sorted(self.publisher.EXPECTED_TARGETS)
        upload_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            resolved = {}
            assets = []
            for index, target_key in enumerate(target_keys):
                path = directory / f"asset-{index}"
                path.write_bytes(f"local-{index}".encode())
                resolved[target_key] = path
                assets.append(
                    {
                        "target_key": target_key,
                        "name": path.name,
                        "key": f"rustdesk/stable/tag/{path.name}",
                        "size": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "signature": "signature",
                        "release": {
                            "version": "1.5.0",
                            "build_number": "20260930.5",
                            "build_seq": 2026093005,
                            "source_commit": "a" * 40,
                            "source_tag": "v1.5.0-build-2026.09.30-05",
                        },
                    }
                )

            uploaded = self.publisher.prepare_uploaded_assets(
                {"schema": 2, "assets": assets},
                resolved,
                lambda item: upload_calls.append(item),
                {
                    target_key: {
                        "version": "1.5.0",
                        "build_number": "20260930.5",
                        "build_seq": 2026093005,
                        "source_commit": "a" * 40,
                        "source_tag": "v1.5.0-build-2026.09.30-05",
                    }
                    for target_key in target_keys
                },
                lambda release, target_key, size, sha256, signature: None,
            )

        self.assertEqual(set(uploaded), set(target_keys))
        self.assertEqual(upload_calls, [])

    def test_complete_release_identity_or_signature_mismatch_fails_before_upload(self):
        target_keys = sorted(self.publisher.EXPECTED_TARGETS)
        expected_release = {
            "version": "1.5.0",
            "build_number": "20260930.5",
            "build_seq": 2026093005,
            "source_commit": "a" * 40,
            "source_tag": "v1.5.0-build-2026.09.30-05",
        }
        for changed_field in ("source_commit", "signature"):
            with self.subTest(changed_field=changed_field), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                resolved = {}
                assets = []
                for index, target_key in enumerate(target_keys):
                    path = directory / f"asset-{index}"
                    path.write_bytes(f"local-{index}".encode())
                    resolved[target_key] = path
                    release = dict(expected_release)
                    signature = "valid"
                    if index == 0 and changed_field == "source_commit":
                        release["source_commit"] = "b" * 40
                    if index == 0 and changed_field == "signature":
                        signature = "invalid"
                    assets.append(
                        {
                            "target_key": target_key,
                            "name": path.name,
                            "key": f"rustdesk/stable/tag/{path.name}",
                            "size": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "signature": signature,
                            "release": release,
                        }
                    )
                upload_calls = []

                with self.assertRaises(self.publisher.PublishError):
                    self.publisher.prepare_uploaded_assets(
                        {"schema": 2, "assets": assets},
                        resolved,
                        lambda item: upload_calls.append(item),
                        {target_key: expected_release for target_key in target_keys},
                        lambda release, target_key, size, sha256, signature: (
                            None
                            if signature == "valid"
                            else (_ for _ in ()).throw(self.publisher.PublishError("bad signature"))
                        ),
                    )

                self.assertEqual(upload_calls, [])

    def test_new_catalog_records_build_sequence(self):
        source = inspect.getsource(self.publisher)

        self.assertIn('"build_seq": metadata["build_seq"]', source)

    def test_manifest_uses_release_checkout_metadata_and_actual_asset_names(self):
        metadata = {
            "version": "1.5.0",
            "build_number": "20260930.5",
            "build_seq": 2026093005,
            "product": "rustdesk-yan",
            "channel": "stable",
            "source_commit": "a" * 40,
        }
        uploaded = {
            "macos-aarch64-dmg-standard": {
                "name": "rustdesk-1.5.0-standard-macos-aarch64.dmg",
                "key": "rustdesk/stable/tag/rustdesk-1.5.0-standard-macos-aarch64.dmg",
                "size": 10,
                "sha256": "b" * 64,
                "signature": "sig",
            }
        }

        manifest = self.publisher.build_manifest(
            metadata, uploaded, "owner/repo", "tag", "https://download.yan.life"
        )

        self.assertEqual(manifest["source_commit"], "a" * 40)
        target = manifest["targets"]["macos-aarch64-dmg-standard"]
        self.assertTrue(target["primary"].endswith("/rustdesk-1.5.0-standard-macos-aarch64.dmg"))
        self.assertTrue(target["mirrors"][0].endswith("/tag/rustdesk-1.5.0-standard-macos-aarch64.dmg"))

    def test_schema_two_manifest_preserves_each_target_release_identity(self):
        metadata = {
            "version": "1.5.0",
            "build_number": "20261002.1",
            "build_seq": 2026100201,
            "product": "rustdesk-yan",
            "channel": "stable",
            "source_commit": "a" * 40,
        }
        uploaded = {
            "android-aarch64-apk-standard": {
                "name": "rustdesk-1.5.0-standard-android-aarch64-signed.apk",
                "key": "rustdesk/stable/new/android.apk",
                "size": 10,
                "sha256": "b" * 64,
                "signature": "new",
                "release": {**metadata, "source_tag": "new"},
            },
            "windows-x86_64-exe-standard": {
                "name": "rustdesk-1.5.0-standard-windows-x86_64.exe",
                "key": "rustdesk/stable/new/windows.exe",
                "size": 20,
                "sha256": "c" * 64,
                "signature": "inherited",
                "release": {
                    "version": "1.5.0",
                    "build_number": "20261001.7",
                    "build_seq": 2026100107,
                    "source_commit": "d" * 40,
                    "source_tag": "old",
                },
            },
        }

        manifest = self.publisher.build_manifest(
            metadata, uploaded, "owner/repo", "new", "https://download.yan.life"
        )

        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["catalog_revision"], 2026100201)
        self.assertEqual(
            manifest["targets"]["windows-x86_64-exe-standard"]["build_seq"],
            2026100107,
        )
        self.assertEqual(
            manifest["targets"]["android-aarch64-apk-standard"]["build_seq"],
            2026100201,
        )

    def test_inherited_assets_are_server_side_copied_into_new_release_directory(self):
        operations = []
        current = {"android-aarch64-apk-standard": Path("android.apk")}
        inherited = {
            "windows-x86_64-exe-standard": {
                "source_key": "rustdesk/stable/old/windows.exe",
                "name": "windows.exe",
                "copy_source_verified": True,
            }
        }

        self.publisher.transfer_release_assets(
            current,
            inherited,
            upload=lambda key, path: operations.append(("upload", key, str(path))),
            copy=lambda source, key: operations.append(("copy", source, key)),
            destination_prefix="rustdesk/stable/new/",
        )

        self.assertEqual(
            operations,
            [
                ("upload", "rustdesk/stable/new/android.apk", "android.apk"),
                (
                    "copy",
                    "rustdesk/stable/old/windows.exe",
                    "rustdesk/stable/new/windows.exe",
                ),
            ],
        )

    def test_unverified_inherited_copy_source_falls_back_to_upload(self):
        operations = []
        inherited = {
            "windows-x86_64-exe-standard": {
                "source_key": "rustdesk/stable/old/windows.exe",
                "name": "windows.exe",
                "copy_source_verified": False,
                "path": Path("verified-windows.exe"),
            }
        }

        self.publisher.transfer_release_assets(
            {},
            inherited,
            upload=lambda key, path: operations.append(("upload", key, str(path))),
            copy=lambda source, key: operations.append(("copy", source, key)),
            destination_prefix="rustdesk/stable/new/",
        )

        self.assertEqual(
            operations,
            [("upload", "rustdesk/stable/new/windows.exe", "verified-windows.exe")],
        )

    def test_release_metadata_comes_from_release_checkout_and_its_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "version.json").write_text(
                json.dumps(
                    {
                        "version": "1.5.0",
                        "build_number": "20260930.5",
                        "build_seq": 2026093005,
                        "product": "rustdesk-yan",
                        "channel": "stable",
                    }
                )
            )
            subprocess.run(["git", "init", "-q", source], check=True)
            subprocess.run(["git", "-C", source, "add", "version.json"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    source,
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", source, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            metadata = self.publisher.load_release_metadata(source)

            self.assertEqual(metadata["build_seq"], 2026093005)
            self.assertEqual(metadata["source_commit"], commit)

    def test_release_identity_requires_new_tag_format_stable_channel_and_matching_build(self):
        metadata = {
            "version": "1.5.0",
            "build_seq": 2026093005,
            "channel": "stable",
        }

        self.publisher.validate_release_identity(metadata, "v1.5.0-build-2026.09.30-05")

        for tag in (
            "yan-v1.5.0-build-2026.09.30-05",
            "v1.5.0-build-20260930-05",
            "v1.5.0-build-2026.09.30-5",
            "v1.5.1-build-2026.09.30-05",
            "v1.5.0-build-2026.09.30-04",
        ):
            with self.subTest(tag=tag), self.assertRaises(self.publisher.PublishError):
                self.publisher.validate_release_identity(metadata, tag)

        with self.assertRaisesRegex(self.publisher.PublishError, "stable"):
            self.publisher.validate_release_identity(dict(metadata, channel="beta"), "v1.5.0-build-2026.09.30-05")

    def test_targets_are_probed_before_publish_and_verified_after_publish(self):
        manifest = {
            "version": "1.5.0",
            "build_seq": 2026093005,
            "source_commit": "c" * 40,
            "targets": {
                "windows-x86_64-exe-standard": {
                    "primary": "https://download.example/file.exe",
                    "mirrors": ["https://github.example/file.exe"],
                }
            },
        }
        http = FakeHttp(manifest)

        self.publisher.publish_and_verify_manifest(
            http,
            manifest,
            "token",
            "https://api.example/publish",
            "https://api.example/stable.json",
            attempts=1,
            retry_delay=0,
        )

        self.assertEqual([call[0] for call in http.calls], ["probe", "probe", "post", "get"])

    def test_manifest_verification_rejects_stale_identity_or_targets(self):
        expected = {
            "version": "1.5.0",
            "build_number": "20260930.5",
            "build_seq": 2026093005,
            "product": "rustdesk-yan",
            "edition": "multi",
            "channel": "stable",
            "source_commit": "d" * 40,
            "targets": {"target": {"primary": "https://example/file", "mirrors": []}},
        }
        for field, value in (
            ("version", "1.5.1"),
            ("build_number", "20260930.4"),
            ("build_seq", 2026093004),
            ("product", "other"),
            ("edition", "standard"),
            ("channel", "beta"),
            ("source_commit", "e" * 40),
            ("targets", {}),
        ):
            with self.subTest(field=field):
                http = FakeHttp(dict(expected, **{field: value}))
                with self.assertRaisesRegex(self.publisher.PublishError, field):
                    self.publisher.publish_and_verify_manifest(
                        http,
                        expected,
                        "token",
                        "https://api.example/publish",
                        "https://api.example/stable.json",
                        attempts=1,
                        retry_delay=0,
                    )

    def test_failed_asset_probe_prevents_manifest_publish(self):
        manifest = {
            "version": "1.5.0",
            "build_seq": 2026093005,
            "source_commit": "e" * 40,
            "targets": {"target": {"primary": "https://unreachable.example/file", "mirrors": []}},
        }

        class FailingHttp(FakeHttp):
            def probe(self, url):
                self.calls.append(("probe", url))
                raise OSError("unreachable")

        http = FailingHttp(manifest)
        with self.assertRaisesRegex(self.publisher.PublishError, "not reachable"):
            self.publisher.publish_and_verify_manifest(
                http,
                manifest,
                "token",
                "https://api.example/publish",
                "https://api.example/stable.json",
                attempts=1,
                retry_delay=0,
            )
        self.assertEqual([call[0] for call in http.calls], ["probe"])

    def test_cleanup_counts_only_complete_valid_catalogs(self):
        prefix = "rustdesk/stable"
        catalogs = {}
        keys = []
        sizes = {}
        for number in range(1, 8):
            tag = f"v1.5.0-build-2026.09.{number + 20:02d}-01"
            catalog_key = f"{prefix}/{tag}/catalog.json"
            assets = [
                {
                    "name": f"asset-{index}",
                    "key": f"{prefix}/{tag}/asset-{index}",
                    "size": index + 1,
                    "sha256": f"{index:064x}",
                    "signature": "signature",
                }
                for index in range(len(self.publisher.EXPECTED_TARGETS))
            ]
            catalogs[catalog_key] = json.dumps({
                "schema": 1,
                "tag": tag,
                "build_seq": int(f"202609{number + 20:02d}01"),
                "published_at": 100 - number,
                "assets": assets,
            }).encode()
            keys.append(catalog_key)
            keys.extend(asset["key"] for asset in assets)
            sizes[catalog_key] = len(catalogs[catalog_key])
            sizes.update({asset["key"]: asset["size"] for asset in assets})

        invalid_tag = "partial-newest"
        invalid_catalog_key = f"{prefix}/{invalid_tag}/catalog.json"
        catalogs[invalid_catalog_key] = json.dumps(
            {"schema": 1, "tag": invalid_tag, "published_at": 999, "assets": []}
        ).encode()
        keys.extend([invalid_catalog_key, f"{prefix}/{invalid_tag}/partial.bin"])
        keys.append(f"{prefix}/no-catalog/partial.bin")
        sizes[invalid_catalog_key] = len(catalogs[invalid_catalog_key])
        sizes[f"{prefix}/{invalid_tag}/partial.bin"] = 1
        sizes[f"{prefix}/no-catalog/partial.bin"] = 1
        bucket = FakeBucket(catalogs, keys, sizes)

        retained = self.publisher.cleanup_complete_releases(
            bucket, lambda object_prefix: (key for key in keys if key.startswith(object_prefix)), prefix, 5
        )

        self.assertEqual(retained, [
            "v1.5.0-build-2026.09.27-01",
            "v1.5.0-build-2026.09.26-01",
            "v1.5.0-build-2026.09.25-01",
            "v1.5.0-build-2026.09.24-01",
            "v1.5.0-build-2026.09.23-01",
        ])
        expected_deleted = {
            f"{prefix}/v1.5.0-build-2026.09.{number + 20:02d}-01/{name}"
            for number in (1, 2)
            for name in (
                "catalog.json",
                *(f"asset-{index}" for index in range(len(self.publisher.EXPECTED_TARGETS))),
            )
        }
        self.assertEqual(set(bucket.deleted), expected_deleted)
        self.assertNotIn(f"{prefix}/{invalid_tag}/partial.bin", bucket.deleted)

    def test_complete_catalog_rejects_legacy_ten_package_snapshot(self):
        prefix = "rustdesk/stable"
        tag = "v1.5.0-build-2026.09.30-05"
        catalog_key = f"{prefix}/{tag}/catalog.json"
        assets = [
            {
                "name": f"asset-{index}",
                "key": f"{prefix}/{tag}/asset-{index}",
                "size": index + 1,
                "sha256": f"{index:064x}",
                "signature": "signature",
            }
            for index in range(10)
        ]
        body = json.dumps(
            {"schema": 1, "tag": tag, "published_at": 1, "assets": assets}
        ).encode()
        keys = [catalog_key, *(asset["key"] for asset in assets)]
        sizes = {
            catalog_key: len(body),
            **{asset["key"]: asset["size"] for asset in assets},
        }
        bucket = FakeBucket({catalog_key: body}, keys, sizes)

        catalog = self.publisher.valid_complete_catalog(
            bucket, catalog_key, set(keys), prefix, tag
        )

        self.assertIsNone(catalog)

    def test_cleanup_rejects_catalog_build_sequence_that_disagrees_with_tag(self):
        prefix = "rustdesk/stable"
        tag = "v1.5.0-build-2026.09.30-05"
        catalog_key = f"{prefix}/{tag}/catalog.json"
        assets = [
            {
                "name": f"asset-{index}",
                "key": f"{prefix}/{tag}/asset-{index}",
                "size": index + 1,
                "sha256": f"{index:064x}",
                "signature": "signature",
            }
            for index in range(len(self.publisher.EXPECTED_TARGETS))
        ]
        body = json.dumps({
            "schema": 1,
            "tag": tag,
            "build_seq": 2026093006,
            "published_at": 1,
            "assets": assets,
        }).encode()
        keys = [catalog_key, *(asset["key"] for asset in assets)]
        sizes = {
            catalog_key: len(body),
            **{asset["key"]: asset["size"] for asset in assets},
        }
        bucket = FakeBucket({catalog_key: body}, keys, sizes)

        retained = self.publisher.cleanup_complete_releases(
            bucket,
            lambda object_prefix: (key for key in keys if key.startswith(object_prefix)),
            prefix,
            5,
        )

        self.assertEqual(retained, [])
        self.assertEqual(bucket.deleted, [])

    def test_complete_catalog_rejects_catalog_or_asset_head_size_mismatch(self):
        prefix = "rustdesk/stable"
        tag = "yan-v1.5.0-build20260930.2"
        catalog_key = f"{prefix}/{tag}/catalog.json"
        assets = [
            {
                "name": f"asset-{index}",
                "key": f"{prefix}/{tag}/asset-{index}",
                "size": index + 10,
                "sha256": f"{index:064x}",
                "signature": "signature",
            }
            for index in range(len(self.publisher.EXPECTED_TARGETS))
        ]
        body = json.dumps({"schema": 1, "tag": tag, "published_at": 1, "assets": assets}).encode()
        keys = [catalog_key, *(asset["key"] for asset in assets)]
        sizes = {catalog_key: len(body), **{asset["key"]: asset["size"] for asset in assets}}

        bucket = FakeBucket({catalog_key: body}, keys, sizes)
        self.assertIsNotNone(self.publisher.valid_complete_catalog(bucket, catalog_key, set(keys), prefix, tag))

        bucket.sizes[catalog_key] += 1
        self.assertIsNone(self.publisher.valid_complete_catalog(bucket, catalog_key, set(keys), prefix, tag))
        bucket.sizes[catalog_key] -= 1
        bucket.sizes[assets[0]["key"]] += 1
        self.assertIsNone(self.publisher.valid_complete_catalog(bucket, catalog_key, set(keys), prefix, tag))

    def test_cleanup_falls_back_to_compatible_historical_tag_order(self):
        prefix = "rustdesk/stable"
        tags = [
            "yan-v1.5.0-build20260929.9",
            "20260930-1",
            "v1.5.0-build-2026.09.30-02",
        ]
        catalogs = {}
        keys = []
        sizes = {}
        for published_at, tag in enumerate(tags, start=100):
            catalog_key = f"{prefix}/{tag}/catalog.json"
            assets = [
                {
                    "name": f"asset-{index}",
                    "key": f"{prefix}/{tag}/asset-{index}",
                    "size": index + 1,
                    "sha256": f"{index:064x}",
                    "signature": "signature",
                }
                for index in range(len(self.publisher.EXPECTED_TARGETS))
            ]
            body = json.dumps(
                {"schema": 1, "tag": tag, "published_at": published_at, "assets": assets}
            ).encode()
            catalogs[catalog_key] = body
            keys.extend([catalog_key, *(asset["key"] for asset in assets)])
            sizes[catalog_key] = len(body)
            sizes.update({asset["key"]: asset["size"] for asset in assets})

        bucket = FakeBucket(catalogs, keys, sizes)
        retained = self.publisher.cleanup_complete_releases(
            bucket, lambda object_prefix: (key for key in keys if key.startswith(object_prefix)), prefix, 2
        )

        self.assertEqual(
            retained,
            ["v1.5.0-build-2026.09.30-02", "20260930-1"],
        )
        self.assertTrue(all(f"/{tags[0]}/" in key for key in bucket.deleted))


if __name__ == "__main__":
    unittest.main()
