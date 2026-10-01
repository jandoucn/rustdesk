#!/usr/bin/env python3

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


SIGNATURE_KEY_ID = "yan-release-2026"
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


def resolve_release_assets(directory, version):
    directory = Path(directory)
    files = sorted(
        path for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS"
    )
    resolved = {}
    for edition in ("standard", "sos"):
        for base_key, suffixes in TARGET_SUFFIXES.items():
            candidates = [directory / f"rustdesk-{version}-{edition}-{suffix}" for suffix in suffixes]
            matches = [path for path in candidates if path.is_file()]
            if not matches:
                raise PublishError(f"Missing release asset for {base_key}-{edition}")
            if len(matches) != 1:
                raise PublishError(f"Ambiguous release assets for {base_key}-{edition}: {matches}")
            resolved[f"{base_key}-{edition}"] = matches[0]
    if len(files) != 8 or set(files) != set(resolved.values()):
        all_files = sorted(path.name for path in directory.iterdir() if path.is_file())
        raise PublishError(f"Expected exactly 8 release assets, found {all_files}")
    return resolved


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


def build_manifest(metadata, uploaded, repository, tag, download_base):
    targets = {}
    for target_key, asset in uploaded.items():
        name = asset["name"]
        targets[target_key] = {
            "primary": f'{download_base.rstrip("/")}/{asset["key"]}',
            "mirrors": [f"https://github.com/{repository}/releases/download/{tag}/{name}"],
            "size": asset["size"],
            "sha256": asset["sha256"],
            "signature": asset["signature"],
            "signature_key_id": SIGNATURE_KEY_ID,
        }
    return {
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
        catalog.get("schema") != 1
        or catalog.get("tag") != tag
        or not isinstance(catalog.get("published_at"), int)
        or not isinstance(assets, list)
        or len(assets) not in (8, 10)
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


def cleanup_complete_releases(bucket, list_keys, prefix, retain):
    object_keys = set(list_keys(f"{prefix}/"))
    catalog_suffix = "/catalog.json"
    complete = []
    for catalog_key in object_keys:
        if not catalog_key.endswith(catalog_suffix):
            continue
        tag = catalog_key[len(prefix) + 1 : -len(catalog_suffix)]
        if not tag or "/" in tag:
            continue
        catalog = valid_complete_catalog(bucket, catalog_key, object_keys, prefix, tag)
        if catalog is not None:
            tag_build_seq = compatible_tag_build_seq(tag)
            catalog_build_seq = catalog.get("build_seq")
            if tag_build_seq is None:
                continue
            if catalog_build_seq is not None and catalog_build_seq != tag_build_seq:
                continue
            complete.append((tag_build_seq, tag))
    complete.sort(reverse=True)
    retained = [tag for _, tag in complete[:retain]]
    for _, tag in complete[retain:]:
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
    resolved = resolve_release_assets(args.assets_dir, metadata["version"])
    auth = oss2.Auth(key_id, key_secret)
    bucket = oss2.Bucket(auth, f"https://{args.endpoint}", args.bucket)
    release_prefix = f"{args.prefix}/{args.tag}/"

    def upload_asset(item):
        target_key, path = item
        data = path.read_bytes()
        size = path.stat().st_size
        sha256 = hashlib.sha256(data).hexdigest()
        object_key = release_prefix + path.name
        upload_bucket = oss2.Bucket(auth, f"https://{args.endpoint}", args.bucket)
        oss2.resumable_upload(
            upload_bucket,
            object_key,
            str(path),
            multipart_threshold=10 * 1024 * 1024,
            part_size=10 * 1024 * 1024,
            num_threads=1,
        )
        uploaded = upload_bucket.head_object(object_key)
        if uploaded.content_length != path.stat().st_size:
            raise PublishError(f"OSS size mismatch for {object_key}")
        return target_key, {
            "name": path.name,
            "key": object_key,
            "size": size,
            "sha256": sha256,
            "signature": base64.b64encode(
                signing_key.sign(signature_payload(metadata, target_key, size, sha256)).signature
            ).decode(),
        }

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            uploaded = dict(executor.map(upload_asset, resolved.items()))
        catalog = {
            "schema": 1,
            "tag": args.tag,
            "build_seq": metadata["build_seq"],
            "published_at": int(time.time()),
            "download_base": args.download_base.rstrip("/") + "/",
            "assets": [uploaded[key] for key in sorted(uploaded)],
        }
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

        def list_keys(object_prefix):
            return (obj.key for obj in oss2.ObjectIterator(bucket, prefix=object_prefix))

        retained = cleanup_complete_releases(bucket, list_keys, args.prefix, 5)
        print(json.dumps({"tag": args.tag, "uploaded": len(uploaded), "retained": retained}))
    except AccessDenied as exc:
        details = getattr(exc, "details", {})
        request_id = getattr(exc, "request_id", None) or details.get("RequestId", "unknown")
        raise PublishError(
            "OSS access denied. Grant the configured RAM identity "
            f"oss:PutObject/oss:GetObject/oss:DeleteObject on "
            f"acs:oss:*:*:{args.bucket}/{args.prefix}/* and oss:ListObjects on "
            f"acs:oss:*:*:{args.bucket}; request-id={request_id}"
        ) from exc


if __name__ == "__main__":
    main()
