#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="${repo_root}/native/macos/Baleobala.xcodeproj"

if [[ -d /Applications/Xcode.app ]]; then
  export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
fi

xcodebuild \
  -project "${project}" \
  -scheme Baleobala \
  -configuration Release \
  -destination "platform=macOS" \
  build
