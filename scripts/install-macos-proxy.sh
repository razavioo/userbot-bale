#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DERIVED="$(pwd)/build/macos-proxy"
APP_NAME="BaleobalaProxy.app"
SRC_APP="$DERIVED/Build/Products/Debug/$APP_NAME"
DEST="/Applications/$APP_NAME"

echo "==> Quitting any running instance..."
osascript -e 'tell application "BaleobalaProxy" to quit' 2>/dev/null || true
pkill -f "BaleobalaProxy.app/Contents/MacOS/BaleobalaProxy" 2>/dev/null || true
sleep 1

echo "==> Building..."
xcodebuild \
  -project native/macos/BaleobalaProxy.xcodeproj \
  -scheme BaleobalaProxy \
  -configuration Debug \
  -derivedDataPath "$DERIVED" \
  CODE_SIGN_IDENTITY="-" \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGNING_ALLOWED=NO \
  build > /tmp/baleobala-proxy-build.log 2>&1

if [[ ! -d "$SRC_APP" ]]; then
  echo "Build failed. See /tmp/baleobala-proxy-build.log" >&2
  tail -30 /tmp/baleobala-proxy-build.log >&2
  exit 1
fi

echo "==> Installing to $DEST..."
rm -rf "$DEST"
cp -R "$SRC_APP" "$DEST"

echo "==> Cleaning build copy..."
rm -rf "$DERIVED"

LSREG="/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister"
if [[ -x "$LSREG" ]]; then
  "$LSREG" -f "$DEST" >/dev/null 2>&1 || true
fi

echo "==> Launching..."
open "$DEST"

echo
echo "Installed: $DEST"
echo "Build log: /tmp/baleobala-proxy-build.log"
