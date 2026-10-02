#!/usr/bin/env bash

set -euo pipefail

app_path=$1
identity=$2
entitlements=$3
plist_buddy=${PLIST_BUDDY:-/usr/libexec/PlistBuddy}

sign_args=(--force --sign "$identity")
if [[ "$identity" != "-" ]]; then
  sign_args+=(--options runtime --timestamp)
fi

app_sign_args=("${sign_args[@]}")
if [[ "$identity" == "-" ]]; then
  if [[ -x "$plist_buddy" ]]; then
    bundle_id=$("$plist_buddy" -c 'Print :CFBundleIdentifier' \
      "$app_path/Contents/Info.plist")
  else
    bundle_id=$(python3 - "$app_path/Contents/Info.plist" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as stream:
    print(plistlib.load(stream).get("CFBundleIdentifier", ""))
PY
)
  fi
  [[ -n "$bundle_id" ]] || {
    echo "Missing CFBundleIdentifier in $app_path" >&2
    exit 1
  }
  app_sign_args+=(--requirements "=designated => identifier \"$bundle_id\"")
fi

frameworks_path="$app_path/Contents/Frameworks"
if [[ -d "$frameworks_path" ]]; then
  while IFS= read -r -d '' code; do
    if file -b "$code" | grep -q 'Mach-O'; then
      codesign "${sign_args[@]}" "$code"
    fi
  done < <(find "$frameworks_path" -type f -print0)

  while IFS= read -r -d '' framework; do
    codesign "${sign_args[@]}" "$framework"
  done < <(find "$frameworks_path" -depth -type d -name '*.framework' -print0)
fi

service_path="$app_path/Contents/MacOS/service"
if [[ -f "$service_path" ]]; then
  codesign "${sign_args[@]}" "$service_path"
fi

codesign "${app_sign_args[@]}" --generate-entitlement-der \
  --entitlements "$entitlements" "$app_path"
codesign --verify --deep --strict --verbose=2 "$app_path"

if [[ "$identity" == "-" ]]; then
  actual_requirement=$(codesign -dr - "$app_path" 2>&1)
  expected_requirement="designated => identifier \"$bundle_id\""
  if [[ "$actual_requirement" != *"$expected_requirement"* \
    || "$actual_requirement" == *'cdhash H"'* ]]; then
    echo "Unstable ad-hoc designated requirement: $actual_requirement" >&2
    exit 1
  fi
fi

actual_entitlements=$(codesign -d --entitlements :- "$app_path" 2>/dev/null)
audio_input=$(plutil -extract 'com\.apple\.security\.device\.audio-input' raw - \
  <<<"$actual_entitlements")
if [[ "$audio_input" != "true" ]]; then
  echo "Missing com.apple.security.device.audio-input entitlement" >&2
  exit 1
fi
