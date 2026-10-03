#!/usr/bin/env python3

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


SIGNATURE_KEY_ID = "yan-release-2026"
OSS_CONNECT_TIMEOUT_SECONDS = 60
RELEASE_TAG_PATTERN = re.compile(
    r"^v(?P<version>\d+\.\d+\.\d+)-build-(?P<date>\d{4}\.\d{2}\.\d{2})-(?P<sequence>\d{2})$"
)
COMPATIBLE_TAG_PATTERNS = (
    RELEASE_TAG_PATTERN,
    re.compile(
        r"^yan-v(?P<version>\d+\.\d+\.\d+)-build(?P<compact_date>\d{8})\.(?P<sequence>\d+)$"
    ),
    re.compile(r"^(?P<compact_date>\d{8})-(?P<sequence>\d+)$"),
)
TARGET_SUFFIXES = {
    "windows-x86_64-exe": ("windows-x86_64.exe",),
    "windows-x86_64-msi": ("windows-x86_64.msi",),
    "macos-aarch64-dmg": ("macos-aarch64.dmg", "aarch64.dmg"),
    "android-aarch64-apk": ("android-aarch64-signed.apk", "android-aarch64.apk"),
}
TARGET_EDITIONS = {
    "windows-x86_64-exe": ("standard", "sos"),
    "windows-x86_64-msi": ("standard", "sos"),
    "macos-aarch64-dmg": ("standard", "sos"),
    "android-aarch64-apk": ("standard",),
}
EXPECTED_TARGETS = {
    f"{base_key}-{edition}"
    for base_key, editions in TARGET_EDITIONS.items()
    for edition in editions
}


class PublishError(RuntimeError):
    pass


def compatible_tag_build_seq(tag):
    for pattern in COMPATIBLE_TAG_PATTERNS:
        match = pattern.fullmatch(tag)
        if match is None:
            continue
        values = match.groupdict()
        compact_date = values.get("compact_date")
        if compact_date is None:
            try:
                compact_date = datetime.strptime(values["date"], "%Y.%m.%d").strftime("%Y%m%d")
            except ValueError:
                return None
        return int(compact_date) * 100 + int(values["sequence"])
    return None


def validate_release_identity(metadata, tag):
    match = RELEASE_TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise PublishError("Release tag must match vX.Y.Z-build-YYYY.MM.DD-NN")
    if metadata.get("channel") != "stable":
        raise PublishError("OSS stable publishing requires stable channel metadata")
    if match.group("version") != metadata.get("version"):
        raise PublishError("Release tag version does not match version.json")
    build_seq = compatible_tag_build_seq(tag)
    if build_seq is None or build_seq != metadata.get("build_seq"):
        raise PublishError("Release tag date/sequence does not match version.json build_seq")


def resolve_release_assets(directory, version, snapshot=None):
    directory = Path(directory)
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name not in ("SHA256SUMS", "release-snapshot.json")
    )
    if snapshot and snapshot.get("targets"):
        resolved = {}
        for target_key, target in snapshot["targets"].items():
            path = directory / str(target.get("name") or "")
            if not path.is_file():
                raise PublishError(f"Missing release asset for {target_key}")
            resolved[target_key] = path
        if set(resolved) != EXPECTED_TARGETS:
            raise PublishError(
                f"Release snapshot must contain exactly {len(EXPECTED_TARGETS)} target keys"
            )
        if set(files) != set(resolved.values()):
            raise PublishError("Release snapshot files do not match target metadata")
        return resolved
    resolved = {}
    for base_key, suffixes in TARGET_SUFFIXES.items():
        for edition in TARGET_EDITIONS[base_key]:
            candidates = [directory / f"rustdesk-{version}-{edition}-{suffix}" for suffix in suffixes]
            matches = [path for path in candidates if path.is_file()]
            if not matches:
                raise PublishError(f"Missing release asset for {base_key}-{edition}")
            if len(matches) != 1:
                raise PublishError(f"Ambiguous release assets for {base_key}-{edition}: {matches}")
            resolved[f"{base_key}-{edition}"] = matches[0]
    if len(files) != len(EXPECTED_TARGETS) or set(files) != set(resolved.values()):
        all_files = sorted(path.name for path in directory.iterdir() if path.is_file())
        raise PublishError(
            f"Expected exactly {len(EXPECTED_TARGETS)} release assets, found {all_files}"
        )
    return resolved


def load_release_snapshot(directory, metadata, tag):
    path = Path(directory) / "release-snapshot.json"
    if not path.is_file():
        return {
            "schema": 1,
            "tag": tag,
            "catalog_revision": metadata["build_seq"],
            "targets": {},
        }
    snapshot = json.loads(path.read_text())
    if snapshot.get("schema") != 1 or snapshot.get("tag") != tag:
        raise PublishError("Release snapshot identity is invalid")
    if snapshot.get("catalog_revision") != metadata["build_seq"]:
        raise PublishError("Release snapshot catalog revision is invalid")
    if not isinstance(snapshot.get("targets"), dict):
        raise PublishError("Release snapshot targets are invalid")
    return snapshot


def load_release_metadata(source_dir):
    source_dir = Path(source_dir)
    version_info = json.loads((source_dir / "version.json").read_text())
    source_commit = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(source_commit) != 40:
        raise PublishError(f"Invalid release source commit: {source_commit}")
    return {
        "version": version_info["version"],
        "build_number": version_info["build_number"],
        "build_seq": version_info["build_seq"],
        "product": version_info["product"],
        "channel": version_info["channel"],
        "source_commit": source_commit,
    }


def signature_payload(metadata, target_key, size, sha256):
    fields = {
        "product": metadata["product"],
        "edition": "multi",
        "channel": metadata["channel"],
        "version": metadata["version"],
        "source_commit": metadata["source_commit"],
        "target_key": target_key,
        "sha256": sha256.lower(),
    }
    for name, value in fields.items():
        if "\r" in value or "\n" in value:
            raise PublishError(f"Signature field {name} contains a line break")
    return (
        "rustdesk-update-v1\n"
        f"product={fields['product']}\n"
        f"edition={fields['edition']}\n"
        f"channel={fields['channel']}\n"
        f"version={fields['version']}\n"
        f"build_seq={metadata['build_seq']}\n"
        f"source_commit={fields['source_commit']}\n"
        f"target_key={fields['target_key']}\n"
        f"size={size}\n"
        f"sha256={fields['sha256']}\n"
    ).encode()


def transfer_release_assets(current, inherited, upload, copy, destination_prefix):
    for target_key, path in current.items():
        upload(destination_prefix + path.name, path)
    for target_key, target in inherited.items():
        if target.get("copy_source_verified"):
            copy(target["source_key"], destination_prefix + target["name"])
        else:
            upload(destination_prefix + target["name"], target["path"])


def cleanup_release_prefix(bucket, list_keys, release_prefix):
    for key in list(list_keys(release_prefix)):
        bucket.delete_object(key)


def reuse_complete_release_assets(catalog, resolved, expected_releases, verify_signature):
    if catalog.get("schema") != 2 or not isinstance(catalog.get("assets"), list):
        raise PublishError("Existing complete release catalog cannot be safely reused")
    uploaded = {}
    for asset in catalog["assets"]:
        target_key = asset.get("target_key") if isinstance(asset, dict) else None
        if not isinstance(target_key, str) or target_key in uploaded:
            raise PublishError("Existing complete release target identity is invalid")
        uploaded[target_key] = asset
    if set(uploaded) != set(resolved):
        raise PublishError("Existing complete release target set differs from local assets")
    for target_key, path in resolved.items():
        asset = uploaded[target_key]
        size = path.stat().st_size
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        release = asset.get("release")
        if (
            asset.get("name") != path.name
            or asset.get("size") != size
            or str(asset.get("sha256") or "").lower() != sha256
            or not isinstance(asset.get("signature"), str)
            or release != expected_releases.get(target_key)
        ):
            raise PublishError(f"Existing complete release differs from local asset: {target_key}")
        try:
            verify_signature(release, target_key, size, sha256, asset["signature"])
        except Exception as exc:
            raise PublishError(
                f"Existing complete release signature is invalid: {target_key}"
            ) from exc
    return uploaded


def prepare_uploaded_assets(
    existing_catalog,
    resolved,
    publish_asset,
    expected_releases,
    verify_signature,
):
    if existing_catalog is not None:
        return reuse_complete_release_assets(
            existing_catalog,
            resolved,
            expected_releases,
            verify_signature,
        )
    with ThreadPoolExecutor(max_workers=4) as executor:
        return dict(executor.map(publish_asset, resolved.items()))


def failed_release_cleanup_allowed(
    http,
    stable_manifest_url,
    download_base,
    release_prefix,
    prefix_preexisted_complete=False,
):
    if prefix_preexisted_complete:
        return False
    try:
        active_manifest = http.get_json(stable_manifest_url)
        targets = active_manifest.get("targets")
        if not isinstance(targets, dict) or not targets:
            raise PublishError("Active manifest targets are missing")
        active_prefix = f'{download_base.rstrip("/")}/{release_prefix.lstrip("/")}'
        for target in targets.values():
            if not isinstance(target, dict) or not isinstance(target.get("primary"), str):
                raise PublishError("Active manifest target primary is invalid")
            if target["primary"].startswith(active_prefix):
                return False
        return True
    except Exception as status_error:
        print(
            f"Skipped OSS cleanup because active manifest status is unknown: {status_error}",
            file=sys.stderr,
        )
        return False


def build_manifest(metadata, uploaded, repository, tag, download_base):
    targets = {}
    for target_key, asset in uploaded.items():
        name = asset["name"]
        release = asset.get("release") or {
            **metadata,
            "source_tag": tag,
        }
        targets[target_key] = {
            "version": release["version"],
            "build_number": release["build_number"],
            "build_seq": release["build_seq"],
            "source_commit": release["source_commit"],
            "source_tag": release["source_tag"],
            "primary": f'{download_base.rstrip("/")}/{asset["key"]}',
            "mirrors": [f"https://github.com/{repository}/releases/download/{tag}/{name}"],
            "size": asset["size"],
            "sha256": asset["sha256"],
            "signature": asset["signature"],
            "signature_key_id": SIGNATURE_KEY_ID,
        }
    return {
        "schema": 2,
        "catalog_revision": metadata["build_seq"],
        "version": metadata["version"],
        "build_number": metadata["build_number"],
        "build_seq": metadata["build_seq"],
        "product": metadata["product"],
        "edition": "multi",
        "channel": metadata["channel"],
        "source_commit": metadata["source_commit"],
        "targets": targets,
    }


class UrlHttpClient:
    def probe(self, url):
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 400:
                raise PublishError(f"Asset probe failed: HTTP {response.status}: {url}")

    def post_json(self, url, payload, headers):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 201:
                raise PublishError(f"Manifest publish failed: HTTP {response.status}")

    def get_json(self, url):
        with urllib.request.urlopen(url, timeout=30) as response:
            if response.status != 200:
                raise PublishError(f"Manifest fetch failed: HTTP {response.status}")
            return json.load(response)


def probe_with_retry(http, url, attempts, retry_delay):
    error = None
    for attempt in range(1, attempts + 1):
        try:
            http.probe(url)
            return
        except Exception as exc:
            error = exc
            if attempt < attempts:
                time.sleep(retry_delay)
    raise PublishError(f"Asset was not reachable after {attempts} attempts: {url}: {error}")


def verify_published_manifest(expected, actual):
    fields = (
        "schema",
        "catalog_revision",
        "version",
        "build_number",
        "build_seq",
        "product",
        "edition",
        "channel",
        "source_commit",
        "targets",
    )
    mismatched = [field for field in fields if actual.get(field) != expected.get(field)]
    if mismatched:
        raise PublishError(f"Published manifest did not match fields: {', '.join(mismatched)}")


def publish_and_verify_manifest(
    http,
    manifest,
    token,
    publish_url,
    stable_manifest_url,
    attempts=10,
    retry_delay=6,
):
    for target in manifest["targets"].values():
        probe_with_retry(http, target["primary"], attempts, retry_delay)
        for mirror in target["mirrors"]:
            probe_with_retry(http, mirror, attempts, retry_delay)
    http.post_json(
        publish_url,
        manifest,
        {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    actual = None
    error = None
    for attempt in range(1, attempts + 1):
        try:
            actual = http.get_json(stable_manifest_url)
            verify_published_manifest(manifest, actual)
            return
        except Exception as exc:
            error = exc
            if attempt < attempts:
                time.sleep(retry_delay)
    raise PublishError(f"Published manifest verification failed after {attempts} attempts: {error}")


def valid_complete_catalog(bucket, catalog_key, object_keys, prefix, tag):
    try:
        catalog_body = bucket.get_object(catalog_key).read()
        if bucket.head_object(catalog_key).content_length != len(catalog_body):
            return None
        catalog = json.loads(catalog_body)
    except Exception:
        return None
    assets = catalog.get("assets")
    release_prefix = f"{prefix}/{tag}/"
    if (
        catalog.get("schema") not in (1, 2)
        or catalog.get("tag") != tag
        or not isinstance(catalog.get("published_at"), int)
        or not isinstance(assets, list)
        or len(assets) != len(EXPECTED_TARGETS)
    ):
        return None
    asset_keys = set()
    for asset in assets:
        key = asset.get("key")
        if (
            not isinstance(key, str)
            or not key.startswith(release_prefix)
            or not asset.get("name")
            or not isinstance(asset.get("size"), int)
            or asset["size"] <= 0
            or len(asset.get("sha256", "")) != 64
            or not all(character in "0123456789abcdefABCDEF" for character in asset["sha256"])
        ):
            return None
        try:
            if bucket.head_object(key).content_length != asset["size"]:
                return None
        except Exception:
            return None
        asset_keys.add(key)
    if len(asset_keys) != len(assets) or not asset_keys.issubset(object_keys):
        return None
    return catalog


def complete_release_catalog(bucket, list_keys, prefix, tag):
    release_prefix = f"{prefix}/{tag}/"
    object_keys = set(list_keys(release_prefix))
    catalog_key = release_prefix + "catalog.json"
    if catalog_key not in object_keys:
        return None
    return valid_complete_catalog(bucket, catalog_key, object_keys, prefix, tag)


def release_prefix_is_complete(bucket, list_keys, prefix, tag):
    return complete_release_catalog(bucket, list_keys, prefix, tag) is not None


def cleanup_complete_releases(bucket, list_keys, prefix, retain):
    object_keys = set(list_keys(f"{prefix}/"))
    releases = {}
    for object_key in object_keys:
        relative_key = object_key[len(prefix) + 1 :]
        tag, separator, _ = relative_key.partition("/")
        if not separator:
            continue
        tag_build_seq = compatible_tag_build_seq(tag)
        if tag_build_seq is None:
            continue
        releases[tag] = tag_build_seq

    ordered = sorted(
        ((build_seq, tag) for tag, build_seq in releases.items()),
        reverse=True,
    )
    retained = [tag for _, tag in ordered[:retain]]
    for _, tag in ordered[retain:]:
        old_prefix = f"{prefix}/{tag}/"
        for key in list(list_keys(old_prefix)):
            bucket.delete_object(key)
    return retained


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--download-base", required=True)
    parser.add_argument("--publish-url", required=True)
    parser.add_argument("--stable-manifest-url", required=True)
    return parser.parse_args()


def required_env(name):
    value = os.environ.get(name, "")
    if not value:
        raise PublishError(f"Missing required environment variable: {name}")
    return value


def main():
    import oss2
    from nacl.signing import SigningKey
    from oss2.exceptions import AccessDenied

    args = parse_args()
    key_id = required_env("ALIYUN_ACCESS_KEY_ID")
    key_secret = required_env("ALIYUN_ACCESS_KEY_SECRET")
    publish_token = required_env("UPDATE_PUBLISH_TOKEN")
    signing_key = SigningKey(base64.b64decode(required_env("UPDATE_SIGNING_KEY"), validate=True))
    metadata = load_release_metadata(args.source_dir)
    validate_release_identity(metadata, args.tag)
    snapshot = load_release_snapshot(args.assets_dir, metadata, args.tag)
    resolved = resolve_release_assets(args.assets_dir, metadata["version"], snapshot)
    auth = oss2.Auth(key_id, key_secret)
    bucket = oss2.Bucket(
        auth,
        f"https://{args.endpoint}",
        args.bucket,
        connect_timeout=OSS_CONNECT_TIMEOUT_SECONDS,
    )
    release_prefix = f"{args.prefix}/{args.tag}/"
    expected_releases = {}
    for target_key in resolved:
        snapshot_target = snapshot["targets"].get(target_key, {})
        expected_releases[target_key] = {
            field: snapshot_target.get(field, metadata[field])
            for field in ("version", "build_number", "build_seq", "source_commit")
        }
        expected_releases[target_key]["source_tag"] = snapshot_target.get(
            "source_tag", args.tag
        )

    def verify_existing_signature(release, target_key, size, sha256, signature):
        signature_bytes = base64.b64decode(signature, validate=True)
        payload = signature_payload(
            release | {"product": metadata["product"], "channel": metadata["channel"]},
            target_key,
            size,
            sha256,
        )
        signing_key.verify_key.verify(payload, signature_bytes)

    def list_keys(object_prefix):
        return (obj.key for obj in oss2.ObjectIterator(bucket, prefix=object_prefix))

    def publish_asset(item):
        target_key, path = item
        data = path.read_bytes()
        size = path.stat().st_size
        sha256 = hashlib.sha256(data).hexdigest()
        object_key = release_prefix + path.name
        snapshot_target = snapshot["targets"].get(target_key, {})
        release = expected_releases[target_key]
        upload_bucket = oss2.Bucket(
            auth,
            f"https://{args.endpoint}",
            args.bucket,
            connect_timeout=OSS_CONNECT_TIMEOUT_SECONDS,
        )
        if snapshot_target.get("current", True) or not snapshot_target.get(
            "copy_source_verified", False
        ):
            oss2.resumable_upload(
                upload_bucket,
                object_key,
                str(path),
                multipart_threshold=10 * 1024 * 1024,
                part_size=10 * 1024 * 1024,
                num_threads=1,
            )
        else:
            source_key = snapshot_target.get("source_key", "")
            if not source_key:
                raise PublishError(f"Inherited OSS source key is missing for {target_key}")
            upload_bucket.copy_object(args.bucket, source_key, object_key)
        uploaded = upload_bucket.head_object(object_key)
        if uploaded.content_length != path.stat().st_size:
            raise PublishError(f"OSS size mismatch for {object_key}")
        return target_key, {
            "name": path.name,
            "key": object_key,
            "size": size,
            "sha256": sha256,
            "signature": base64.b64encode(
                signing_key.sign(signature_payload(release | {
                    "product": metadata["product"],
                    "channel": metadata["channel"],
                }, target_key, size, sha256)).signature
            ).decode(),
            "release": release,
        }

    prefix_preexisted_complete = None
    try:
        existing_catalog = complete_release_catalog(
            bucket,
            list_keys,
            args.prefix,
            args.tag,
        )
        prefix_preexisted_complete = existing_catalog is not None
        uploaded = prepare_uploaded_assets(
            existing_catalog,
            resolved,
            publish_asset,
            expected_releases,
            verify_existing_signature,
        )
        catalog = {
            "schema": 2,
            "tag": args.tag,
            "build_seq": metadata["build_seq"],
            "published_at": int(time.time()),
            "download_base": args.download_base.rstrip("/") + "/",
            "assets": [dict(uploaded[key], target_key=key) for key in sorted(uploaded)],
        }
        if existing_catalog is None:
            catalog_key = release_prefix + "catalog.json"
            catalog_body = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).encode()
            bucket.put_object(catalog_key, catalog_body)
            if bucket.head_object(catalog_key).content_length != len(catalog_body):
                raise PublishError(f"OSS size mismatch for {catalog_key}")
        manifest = build_manifest(metadata, uploaded, args.repository, args.tag, args.download_base)
        publish_and_verify_manifest(
            UrlHttpClient(),
            manifest,
            publish_token,
            args.publish_url,
            args.stable_manifest_url,
        )

        retained = cleanup_complete_releases(bucket, list_keys, args.prefix, 5)
        print(json.dumps({"tag": args.tag, "uploaded": len(uploaded), "retained": retained}))
    except Exception as exc:
        cleanup_allowed = prefix_preexisted_complete is False and failed_release_cleanup_allowed(
            UrlHttpClient(),
            args.stable_manifest_url,
            args.download_base,
            release_prefix,
            prefix_preexisted_complete,
        )
        if cleanup_allowed:
            try:
                cleanup_release_prefix(
                    bucket,
                    lambda object_prefix: (
                        obj.key for obj in oss2.ObjectIterator(bucket, prefix=object_prefix)
                    ),
                    release_prefix,
                )
            except Exception as cleanup_error:
                print(f"Failed to clean incomplete OSS release: {cleanup_error}", file=sys.stderr)
        if isinstance(exc, AccessDenied):
            details = getattr(exc, "details", {})
            request_id = getattr(exc, "request_id", None) or details.get("RequestId", "unknown")
            raise PublishError(
                "OSS access denied. Grant the configured RAM identity "
                f"oss:PutObject/oss:GetObject/oss:DeleteObject on "
                f"acs:oss:*:*:{args.bucket}/{args.prefix}/* and oss:ListObjects on "
                f"acs:oss:*:*:{args.bucket}; request-id={request_id}"
            ) from exc
        raise


if __name__ == "__main__":
    main()
