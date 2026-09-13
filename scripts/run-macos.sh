#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="${repo_root}/native/macos/UserbotBale.xcodeproj"
derived_data="${repo_root}/build/macos"
app_path="${derived_data}/Build/Products/Debug/UserbotBale.app"

if [[ -d /Applications/Xcode.app ]]; then
  export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
fi

xcodebuild \
  -project "${project}" \
  -scheme UserbotBale \
  -configuration Debug \
  -derivedDataPath "${derived_data}" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  build

open "${app_path}"
