#!/usr/bin/env python3

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


TAG_PATTERN = re.compile(
    r"^v(?P<version>\d+\.\d+\.\d+)-build-"
    r"(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})-"
    r"(?P<sequence>\d{2})$"
)
MAX_ANDROID_VERSION_CODE = 2_100_000_000


def _normalized_build_date(value):
    compact = str(value).replace(".", "").replace("-", "")
    datetime.strptime(compact, "%Y%m%d")
    return compact


def _parsed_tags(tags):
    parsed = []
    for tag in tags:
        match = TAG_PATTERN.fullmatch(str(tag).strip())
        if match is None:
            continue
        values = match.groupdict()
        build_date = f"{values['year']}{values['month']}{values['day']}"
        parsed.append((values["version"], build_date, int(values["sequence"])))
    return parsed


def infer_previous_android_version_code(build_date, tags):
    compact_date = _normalized_build_date(build_date)
    max_sequence_by_version = {}
    for version, tag_date, sequence in _parsed_tags(tags):
        if tag_date != compact_date:
            continue
        max_sequence_by_version[version] = max(
            sequence,
            max_sequence_by_version.get(version, 0),
        )
    return int(compact_date) * 100 + sum(max_sequence_by_version.values())


def resolve_release_metadata(
    version,
    build_date,
    tags,
    previous_android_version_code=0,
):
    if re.fullmatch(r"\d+\.\d+\.\d+", str(version)) is None:
        raise ValueError("version must use X.Y.Z format")
    compact_date = _normalized_build_date(build_date)
    parsed = _parsed_tags(tags)
    sequences = [
        sequence
        for tag_version, tag_date, sequence in parsed
        if tag_version == version and tag_date == compact_date
    ]
    visible_sequence = max(sequences, default=0) + 1
    if visible_sequence > 99:
        raise ValueError("visible build sequence exceeds two digits")

    build_seq = int(compact_date) * 100 + visible_sequence
    android_version_code = max(
        int(compact_date) * 100 + 1,
        int(previous_android_version_code) + 1,
    )
    if android_version_code > MAX_ANDROID_VERSION_CODE:
        raise ValueError("android versionCode exceeds the supported release range")
    return {
        "version": version,
        "build_number": f"{compact_date}.{visible_sequence}",
        "build_seq": build_seq,
        "android_version_code": android_version_code,
    }


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _resolve(args):
    metadata = json.loads(args.metadata.read_text())
    build_date = args.build_date or str(metadata["build_number"]).split(".", 1)[0]
    tags = args.tags_file.read_text().splitlines()
    compact_date = _normalized_build_date(build_date)
    inferred_previous_code = infer_previous_android_version_code(compact_date, tags)
    minimum_code = int(metadata.get("android_version_code") or 0)
    resolved = resolve_release_metadata(
        version=str(metadata["version"]),
        build_date=compact_date,
        tags=tags,
        previous_android_version_code=max(
            inferred_previous_code,
            minimum_code - 1,
        ),
    )
    output = {**metadata, **resolved}
    _write_json(args.output, output)
    print(json.dumps(output, ensure_ascii=False))


def _apply(args):
    metadata = json.loads(args.metadata.read_text())
    metadata.update(
        version=args.version,
        build_number=args.build_number,
        build_seq=int(args.build_seq),
        android_version_code=int(args.android_version_code),
    )
    _write_json(args.metadata, metadata)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--metadata", type=Path, required=True)
    resolve.add_argument("--tags-file", type=Path, required=True)
    resolve.add_argument("--output", type=Path, required=True)
    resolve.add_argument("--build-date")
    resolve.set_defaults(handler=_resolve)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--metadata", type=Path, required=True)
    apply.add_argument("--version", required=True)
    apply.add_argument("--build-number", required=True)
    apply.add_argument("--build-seq", required=True)
    apply.add_argument("--android-version-code", required=True)
    apply.set_defaults(handler=_apply)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
