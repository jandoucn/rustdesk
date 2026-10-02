#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path


TARGET_SUFFIXES = {
    "windows-x86_64-exe": ("windows-x86_64.exe",),
    "windows-x86_64-msi": ("windows-x86_64.msi",),
    "macos-aarch64-dmg": ("macos-aarch64.dmg", "aarch64.dmg"),
    "android-aarch64-apk": ("android-aarch64-signed.apk", "android-aarch64.apk"),
}
EXPECTED_TARGETS = {
    f"{base_key}-{edition}"
    for edition in ("standard", "sos")
    for base_key in TARGET_SUFFIXES
}
CANONICAL_SUFFIXES = {
    "windows-x86_64-exe": "windows-x86_64.exe",
    "windows-x86_64-msi": "windows-x86_64.msi",
    "macos-aarch64-dmg": "macos-aarch64.dmg",
    "android-aarch64-apk": "android-aarch64.apk",
}


class SnapshotError(RuntimeError):
    pass


def target_key_for_name(name):
    for edition in ("standard", "sos"):
        marker = f"-{edition}-"
        if marker not in name:
            continue
        for base_key, suffixes in TARGET_SUFFIXES.items():
            if any(name.endswith(f"-{suffix}") for suffix in suffixes):
                return f"{base_key}-{edition}"
    return None


def source_tag(target, manifest):
    value = str(target.get("source_tag") or "")
    if value:
        return value
    for url in target.get("mirrors") or []:
        match = re.search(r"/releases/download/([^/]+)/", str(url))
        if match:
            return urllib.parse.unquote(match.group(1))
    return str(manifest.get("source_tag") or "")


def target_release(target, manifest):
    schema = int(manifest.get("schema") or 1)
    source = target if schema >= 2 else manifest
    release = {
        "version": str(source.get("version") or ""),
        "build_number": str(source.get("build_number") or ""),
        "build_seq": int(source.get("build_seq") or 0),
        "source_commit": str(source.get("source_commit") or ""),
        "source_tag": source_tag(target, manifest),
    }
    if (
        not release["version"]
        or not release["build_number"]
        or release["build_seq"] < 1
        or len(release["source_commit"]) != 40
    ):
        raise SnapshotError("previous target release identity is incomplete")
    if not release["source_tag"]:
        raise SnapshotError("previous target source tag is missing")
    return release


def asset_name_from_target(target):
    urls = [*(target.get("mirrors") or []), target.get("primary") or ""]
    for url in urls:
        name = Path(urllib.parse.urlparse(str(url)).path).name
        if name:
            return name
    raise SnapshotError("previous target asset name is missing")


def canonical_asset_name(target_key, release):
    for edition in ("standard", "sos"):
        suffix = f"-{edition}"
        if not target_key.endswith(suffix):
            continue
        base_key = target_key[: -len(suffix)]
        package_suffix = CANONICAL_SUFFIXES.get(base_key)
        if package_suffix:
            return (
                f"rustdesk-{release['version']}-{release['build_seq']}-"
                f"{edition}-{package_suffix}"
            )
    raise SnapshotError(f"unsupported target key: {target_key}")


def verify_inherited_asset(path, target, target_key):
    expected_size = target.get("size")
    expected_sha256 = str(target.get("sha256") or "").lower()
    if not isinstance(expected_size, int) or expected_size < 1:
        raise SnapshotError(f"inherited target size is missing: {target_key}")
    if path.stat().st_size != expected_size:
        raise SnapshotError(f"inherited target size mismatch: {target_key}")
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(expected_sha256) != 64 or actual_sha256 != expected_sha256:
        raise SnapshotError(f"inherited target SHA-256 mismatch: {target_key}")


def assemble_snapshot(current_dir, output_dir, metadata, tag, previous_manifest, fetch):
    current_dir = Path(current_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    current = {}
    for path in sorted(current_dir.rglob("*")):
        if not path.is_file():
            continue
        key = target_key_for_name(path.name)
        if key is None:
            continue
        if key in current:
            raise SnapshotError(f"duplicate selected target: {key}")
        current[key] = path
    if not current:
        raise SnapshotError("no selected targets were built")
    selected_platforms = {key.split("-", 1)[0] for key in current}
    for platform in selected_platforms:
        required = {key for key in EXPECTED_TARGETS if key.startswith(platform + "-")}
        missing = required - current.keys()
        if missing:
            raise SnapshotError(f"selected target set is incomplete: {sorted(missing)}")

    previous_targets = previous_manifest.get("targets") if isinstance(previous_manifest, dict) else None
    if not isinstance(previous_targets, dict):
        previous_targets = {}
    snapshot = {
        "schema": 1,
        "tag": tag,
        "catalog_revision": int(metadata["build_seq"]),
        "targets": {},
    }
    for key in sorted(EXPECTED_TARGETS):
        if key in current:
            path = current[key]
            release = {
                field: metadata[field]
                for field in ("version", "build_number", "build_seq", "source_commit")
            }
            release["source_tag"] = tag
            destination = output_dir / canonical_asset_name(key, release)
            shutil.copy2(path, destination)
            snapshot["targets"][key] = {
                **release,
                "name": destination.name,
                "current": True,
            }
            continue
        target = previous_targets.get(key)
        if not isinstance(target, dict):
            raise SnapshotError(f"missing inherited target: {key}")
        source_name = asset_name_from_target(target)
        release = target_release(target, previous_manifest)
        name = canonical_asset_name(key, release)
        destination = output_dir / name
        fetched_url = fetch(target, destination)
        if not destination.is_file() or destination.stat().st_size < 1:
            raise SnapshotError(f"inherited target download failed: {key}")
        verify_inherited_asset(destination, target, key)
        primary_path = urllib.parse.urlparse(str(target.get("primary") or "")).path.lstrip("/")
        if not primary_path:
            raise SnapshotError(f"inherited target OSS key is missing: {key}")
        snapshot["targets"][key] = {
            **release,
            "name": name,
            "current": False,
            "source_key": primary_path,
            "source_name": source_name,
            "copy_source_verified": fetched_url == target.get("primary"),
        }
    (output_dir / "release-snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    )
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--previous-manifest-url", required=True)
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text())
    metadata["source_commit"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    metadata["source_tag"] = args.tag
    current_keys = {
        target_key_for_name(path.name)
        for path in args.current_assets.rglob("*")
        if path.is_file()
    }
    if EXPECTED_TARGETS <= current_keys:
        previous = {}
    else:
        with urllib.request.urlopen(args.previous_manifest_url, timeout=30) as response:
            previous = json.load(response)

    def fetch(target, destination):
        urls = [target.get("primary") or "", *(target.get("mirrors") or [])]
        error = None
        for url in urls:
            if not url:
                continue
            try:
                urllib.request.urlretrieve(url, destination)
                return url
            except Exception as exc:
                error = exc
        raise SnapshotError(f"failed to download inherited asset: {error}")

    assemble_snapshot(args.current_assets, args.output, metadata, args.tag, previous, fetch)


if __name__ == "__main__":
    main()
