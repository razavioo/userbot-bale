#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="${repo_root}/native/macos/Baleobala.xcodeproj"
app_entitlements="${repo_root}/native/macos/BaleobalaApp/BaleobalaApp.entitlements"
tunnel_entitlements="${repo_root}/native/macos/BaleobalaPacketTunnel/BaleobalaPacketTunnel.entitlements"
app_group_swift="${repo_root}/native/macos/Shared/BaleAppGroup.swift"
provider_swift="${repo_root}/native/macos/BaleobalaPacketTunnel/PacketTunnelProvider.swift"

if [[ ! -d "${project}" ]]; then
  echo "missing Xcode project: ${project}" >&2
  exit 2
fi

if ! command -v xcodebuild >/dev/null 2>&1; then
  echo "xcodebuild is required for macOS acceptance checks" >&2
  exit 2
fi

app_group="$(python3 - <<'PY' "${app_group_swift}"
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'static let identifier = "([^"]+)"', text)
if not match:
    raise SystemExit(1)
print(match.group(1))
PY
)"

provider_bundle="$(python3 - <<'PY' "${app_group_swift}"
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'static let providerBundleIdentifier = "([^"]+)"', text)
if not match:
    raise SystemExit(1)
print(match.group(1))
PY
)"

for path in "${app_entitlements}" "${tunnel_entitlements}"; do
  if ! plutil -convert xml1 -o - "${path}" | grep -q "${app_group}"; then
    echo "app group ${app_group} missing from ${path}" >&2
    exit 3
  fi
done

if ! grep -q "${provider_bundle}" "${repo_root}/native/macos/Config/PacketTunnel.xcconfig"; then
  echo "provider bundle ${provider_bundle} missing from PacketTunnel.xcconfig" >&2
  exit 3
fi

if ! grep -q 'handleControlMessage' "${provider_swift}"; then
  echo "packet tunnel provider is missing control-message handling" >&2
  exit 3
fi

if ! grep -q '"type": "status"' "${provider_swift}"; then
  echo "packet tunnel provider is missing status response payloads" >&2
  exit 3
fi

xcodebuild \
  -project "${project}" \
  -scheme Baleobala \
  -configuration Debug \
  -destination "platform=macOS" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  build >/dev/null

echo "macOS native acceptance checks passed"
