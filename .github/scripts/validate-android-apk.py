#!/usr/bin/env python3

import argparse
import re
import shutil
import subprocess
from pathlib import Path


PACKAGE_PATTERN = re.compile(
    r"^package:\s+name='(?P<package>[^']+)'\s+"
    r"versionCode='(?P<version_code>\d+)'\s+"
    r"versionName='(?P<version_name>[^']+)'",
    re.MULTILINE,
)
SPLIT_ABI_VERSION_CODE_OFFSETS = {0, 1_000, 2_000, 3_000, 4_000}


def validate_badging(
    badging,
    expected_package,
    expected_version,
    base_version_code,
):
    match = PACKAGE_PATTERN.search(badging)
    if match is None:
        raise ValueError("APK package identity is missing from aapt output")
    values = match.groupdict()
    if values["package"] != expected_package:
        raise ValueError(
            f"package name mismatch: {values['package']} != {expected_package}"
        )
    if values["version_name"] != expected_version:
        raise ValueError(
            f"versionName mismatch: {values['version_name']} != {expected_version}"
        )
    actual_code = int(values["version_code"])
    offset = actual_code - int(base_version_code)
    if offset not in SPLIT_ABI_VERSION_CODE_OFFSETS:
        raise ValueError(
            f"versionCode mismatch: {actual_code} is not based on {base_version_code}"
        )
    return {
        "package": values["package"],
        "version_name": values["version_name"],
        "version_code": actual_code,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument("--expected-package", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--base-version-code", type=int, required=True)
    parser.add_argument("--aapt", default="aapt")
    args = parser.parse_args()

    aapt = shutil.which(args.aapt) or args.aapt
    output = subprocess.run(
        [aapt, "dump", "badging", str(args.apk)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    identity = validate_badging(
        output,
        expected_package=args.expected_package,
        expected_version=args.expected_version,
        base_version_code=args.base_version_code,
    )
    print(
        f"validated {args.apk}: {identity['package']} "
        f"versionName={identity['version_name']} "
        f"versionCode={identity['version_code']}"
    )


if __name__ == "__main__":
    main()
