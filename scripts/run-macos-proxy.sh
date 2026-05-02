#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DERIVED="$(pwd)/build/macos-proxy"
mkdir -p "$DERIVED"

xcodebuild \
  -project native/macos/BaleobalaProxy.xcodeproj \
  -scheme BaleobalaProxy \
  -configuration Debug \
  -derivedDataPath "$DERIVED" \
  CODE_SIGN_IDENTITY="-" \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGNING_ALLOWED=NO \
  build

APP="$DERIVED/Build/Products/Debug/BaleobalaProxy.app"
if [[ -d "$APP" ]]; then
  echo "Built: $APP"
  open "$APP"
else
  echo "Build did not produce app bundle." >&2
  exit 1
fi
