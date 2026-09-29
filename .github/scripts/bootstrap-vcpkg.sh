#!/usr/bin/env bash
set -euo pipefail

root="${VCPKG_ROOT:?}"
commit="${VCPKG_COMMIT_ID:?}"

rm -rf "$root"
mkdir -p "$root"
git clone --no-checkout https://github.com/microsoft/vcpkg.git "$root"
git -C "$root" fetch --depth 1 origin "$commit"
git -C "$root" checkout --force "$commit"
chmod +x "$root/bootstrap-vcpkg.sh"

for attempt in 1 2 3 4 5; do
  if "$root/bootstrap-vcpkg.sh" -disableMetrics; then
    echo "VCPKG_ROOT=$root" >> "$GITHUB_ENV"
    echo "$root" >> "$GITHUB_PATH"
    exit 0
  fi
  echo "vcpkg bootstrap failed, attempt ${attempt}"
  sleep $((attempt * 10))
done

exit 1
