#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="${repo_root}/native/macos/Baleobala.xcodeproj"
base_xcconfig="${repo_root}/native/macos/Config/Base.xcconfig"
app_xcconfig="${repo_root}/native/macos/Config/App.xcconfig"
packet_xcconfig="${repo_root}/native/macos/Config/PacketTunnel.xcconfig"
app_entitlements="${repo_root}/native/macos/BaleobalaApp/BaleobalaApp.entitlements"
tunnel_entitlements="${repo_root}/native/macos/BaleobalaPacketTunnel/BaleobalaPacketTunnel.entitlements"
app_group_swift="${repo_root}/native/macos/Shared/BaleAppGroup.swift"
provider_swift="${repo_root}/native/macos/BaleobalaPacketTunnel/PacketTunnelProvider.swift"
export_options_plist="${EXPORT_OPTIONS_PLIST:-${repo_root}/native/macos/ExportOptions.direct.plist}"
mode="${1:-repo}"

if [[ ! -d "${project}" ]]; then
  echo "missing Xcode project: ${project}" >&2
  exit 2
fi

if ! command -v xcodebuild >/dev/null 2>&1; then
  echo "xcodebuild is required for macOS acceptance checks" >&2
  exit 2
fi

require_pattern() {
  local pattern="$1"
  local path="$2"
  local message="$3"
  if ! grep -q "${pattern}" "${path}"; then
    echo "${message}" >&2
    exit 3
  fi
}

check_release_inputs() {
  local missing=()
  [[ -n "${MACOS_DEVELOPMENT_TEAM:-}" ]] || missing+=("MACOS_DEVELOPMENT_TEAM")
  [[ -n "${MACOS_CODE_SIGN_IDENTITY:-}" ]] || missing+=("MACOS_CODE_SIGN_IDENTITY")
  [[ -n "${MACOS_APP_PROFILE_SPECIFIER:-}${MACOS_APP_PROFILE_UUID:-}" ]] || missing+=("MACOS_APP_PROFILE_SPECIFIER or MACOS_APP_PROFILE_UUID")
  [[ -n "${MACOS_PACKET_TUNNEL_PROFILE_SPECIFIER:-}${MACOS_PACKET_TUNNEL_PROFILE_UUID:-}" ]] || missing+=("MACOS_PACKET_TUNNEL_PROFILE_SPECIFIER or MACOS_PACKET_TUNNEL_PROFILE_UUID")
  [[ -f "${export_options_plist}" ]] || missing+=("EXPORT_OPTIONS_PLIST (${export_options_plist})")

  if ((${#missing[@]} > 0)); then
    printf 'missing required macOS release input(s):\n' >&2
    printf '  - %s\n' "${missing[@]}" >&2
    exit 4
  fi
}

app_group="$(python3 - <<'PY' "${base_xcconfig}"
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^BALEOBALA_APP_GROUP_IDENTIFIER\s*=\s*(\S+)\s*$', text, re.M)
if not match:
    raise SystemExit(1)
print(match.group(1))
PY
)"

provider_bundle="$(python3 - <<'PY' "${base_xcconfig}"
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
values = dict(re.findall(r'^(BALEOBALA_[A-Z_]+)\s*=\s*(\S+)\s*$', text, re.M))
provider = values.get("BALEOBALA_PACKET_TUNNEL_BUNDLE_ID", "")
app = values.get("BALEOBALA_APP_BUNDLE_ID", "")
if not provider:
    raise SystemExit(1)
print(provider.replace("$(BALEOBALA_APP_BUNDLE_ID)", app))
PY
)"

effective_team="${MACOS_DEVELOPMENT_TEAM:-$(python3 - <<'PY' "${base_xcconfig}"
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^DEVELOPMENT_TEAM\s*=\s*(\S*)\s*$', text, re.M)
print(match.group(1) if match else "")
PY
)}"

for path in "${app_entitlements}" "${tunnel_entitlements}"; do
  entitlements_xml="$(plutil -convert xml1 -o - "${path}")"
  if ! grep -Fq "${app_group}" <<<"${entitlements_xml}" && ! grep -Fq '$(BALEOBALA_APP_GROUP_IDENTIFIER)' <<<"${entitlements_xml}"; then
    echo "app group ${app_group} missing from ${path}" >&2
    exit 3
  fi
done

require_pattern "BALEOBALA_PACKET_TUNNEL_BUNDLE_ID" "${packet_xcconfig}" "provider bundle setting missing from PacketTunnel.xcconfig"
require_pattern "com.apple.developer.networking.networkextension" "${tunnel_entitlements}" "packet-tunnel entitlement missing network extension capability"
require_pattern "packet-tunnel-provider" "${tunnel_entitlements}" "packet-tunnel entitlement missing packet-tunnel-provider value"
require_pattern "com.apple.security.application-groups" "${app_entitlements}" "app entitlements missing application-groups capability"
require_pattern "com.apple.security.application-groups" "${tunnel_entitlements}" "packet-tunnel entitlements missing application-groups capability"
require_pattern "handleControlMessage" "${provider_swift}" "packet tunnel provider is missing control-message handling"
require_pattern '"type": "status"' "${provider_swift}" "packet tunnel provider is missing status response payloads"

xcodebuild \
  -project "${project}" \
  -scheme Baleobala \
  -configuration Debug \
  -destination "platform=macOS" \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  build >/dev/null

if [[ "${mode}" == "release" ]]; then
  check_release_inputs
  if [[ -z "${effective_team}" ]]; then
    echo "effective DEVELOPMENT_TEAM is empty; macOS packaging is still in scaffold mode" >&2
    exit 4
  fi
  if ! plutil -extract method raw -o - "${export_options_plist}" | grep -q '^developer-id$'; then
    echo "export options plist must use developer-id for direct distribution" >&2
    exit 4
  fi
  if ! plutil -extract signingStyle raw -o - "${export_options_plist}" | grep -q '^manual$'; then
    echo "export options plist must use manual signing for direct distribution" >&2
    exit 4
  fi
  echo "macOS release acceptance checks passed"
  exit 0
fi

echo "macOS native repo acceptance checks passed"
