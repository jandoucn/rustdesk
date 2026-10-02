#!/usr/bin/env python3

import argparse


PLATFORMS = ("windows", "macos", "android")


def parse_platforms(value):
    if value == "all":
        return PLATFORMS
    selected = tuple(value.split(","))
    if (
        not selected
        or any(not item for item in selected)
        or "all" in selected
        or len(selected) != len(set(selected))
        or not set(selected) <= set(PLATFORMS)
    ):
        raise ValueError("platforms 必须是 all 或 windows,macos,android 的无重复逗号组合")
    return selected


def validate_selection(platforms, standard, sos):
    if not standard or not sos:
        raise ValueError("完整发布快照必须同时构建 standard 和 SOS")
    return parse_platforms(platforms)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("platforms")
    parser.add_argument("standard", choices=("true", "false"))
    parser.add_argument("sos", choices=("true", "false"))
    args = parser.parse_args()
    try:
        validate_selection(
            args.platforms,
            args.standard == "true",
            args.sos == "true",
        )
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
