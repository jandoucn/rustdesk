#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path


ALLOWED_SUFFIXES = {".apk", ".appimage", ".deb", ".dmg", ".exe", ".msi", ".rpm"}


def validate(root: Path, required_editions: set[str] | None = None) -> list[Path]:
    if not root.is_dir():
        raise ValueError(f"release asset directory does not exist: {root}")

    assets = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
    )
    if not assets:
        raise ValueError("no release assets found")

    names: set[str] = set()
    editions: set[str] = set()
    for asset in assets:
        name = asset.name
        if asset.stat().st_size == 0:
            raise ValueError(f"empty release asset: {name}")
        if name in names:
            raise ValueError(f"duplicate release asset name: {name}")
        names.add(name)
        if "-standard-" in name:
            editions.add("standard")
        if "-sos-" in name:
            editions.add("sos")

    for edition in sorted(required_editions or {"standard", "sos"}):
        if edition not in editions:
            raise ValueError(f"missing {edition} release asset")
    return assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--editions", default="standard,sos")
    args = parser.parse_args()
    try:
        editions = {value.strip() for value in args.editions.split(",") if value.strip()}
        if not editions or not editions <= {"standard", "sos"}:
            raise ValueError("editions must contain standard and/or sos")
        assets = validate(args.directory, editions)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    for asset in assets:
        print(asset)
    print(f"validated {len(assets)} release assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
