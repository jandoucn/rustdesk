#!/usr/bin/env python3
"""Validate the version embedded in the built macOS app bundle."""

import argparse
import plistlib
from pathlib import Path


def validate_bundle(plist_path: Path, expected_version: str, expected_build: str) -> None:
    with plist_path.open("rb") as stream:
        plist = plistlib.load(stream)
    actual_version = str(plist.get("CFBundleShortVersionString", ""))
    actual_build = str(plist.get("CFBundleVersion", ""))
    if actual_version != expected_version or actual_build != expected_build:
        raise ValueError(
            f"macOS app version mismatch: expected {expected_version}/{expected_build}, "
            f"found {actual_version}/{actual_build}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("app")
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", required=True)
    args = parser.parse_args()
    plist_path = Path(args.app) / "Contents" / "Info.plist"
    try:
        validate_bundle(plist_path, args.version, args.build)
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
