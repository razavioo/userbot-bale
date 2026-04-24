#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project="${repo_root}/native/macos/Baleobala.xcodeproj"
scheme="${SCHEME:-Baleobala}"
configuration="${CONFIGURATION:-Release}"
derived_data="${DERIVED_DATA_PATH:-${repo_root}/build/macos}"
archive_path="${ARCHIVE_PATH:-${derived_data}/archive/${scheme}.xcarchive}"
export_path="${EXPORT_PATH:-${derived_data}/export}"
destination="${DESTINATION:-platform=macOS}"
export_options_plist="${EXPORT_OPTIONS_PLIST:-${repo_root}/native/macos/ExportOptions.direct.plist}"
generated_export_options_plist="${GENERATED_EXPORT_OPTIONS_PLIST:-${derived_data}/export-options.generated.plist}"
notary_log_path="${NOTARY_LOG_PATH:-${derived_data}/notary/submission.json}"
release_metadata_path="${RELEASE_METADATA_PATH:-${derived_data}/release-metadata.txt}"
mode="${1:-build}"

if [[ -d /Applications/Xcode.app ]]; then
  export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
fi

common_args=(
  -project "${project}"
  -scheme "${scheme}"
  -configuration "${configuration}"
  -derivedDataPath "${derived_data}"
  -destination "${destination}"
)

release_settings=()

append_setting_if_present() {
  local key="$1"
  local value="${2:-}"
  if [[ -n "${value}" ]]; then
    release_settings+=("${key}=${value}")
  fi
}

append_setting_if_present "DEVELOPMENT_TEAM" "${MACOS_DEVELOPMENT_TEAM:-}"
append_setting_if_present "CODE_SIGN_IDENTITY" "${MACOS_CODE_SIGN_IDENTITY:-}"
append_setting_if_present "OTHER_CODE_SIGN_FLAGS" "${MACOS_OTHER_CODE_SIGN_FLAGS:-}"
append_setting_if_present "BALEOBALA_APP_BUNDLE_ID" "${MACOS_APP_BUNDLE_ID:-}"
append_setting_if_present "BALEOBALA_PACKET_TUNNEL_BUNDLE_ID" "${MACOS_PACKET_TUNNEL_BUNDLE_ID:-}"
append_setting_if_present "BALEOBALA_APP_GROUP_IDENTIFIER" "${MACOS_APP_GROUP_IDENTIFIER:-}"

validate_release_env() {
  local missing=()
  [[ -n "${MACOS_DEVELOPMENT_TEAM:-}" ]] || missing+=("MACOS_DEVELOPMENT_TEAM")
  [[ -n "${MACOS_CODE_SIGN_IDENTITY:-}" ]] || missing+=("MACOS_CODE_SIGN_IDENTITY")
  [[ -n "${MACOS_APP_PROFILE_SPECIFIER:-}${MACOS_APP_PROFILE_UUID:-}" ]] || missing+=("MACOS_APP_PROFILE_SPECIFIER or MACOS_APP_PROFILE_UUID")
  [[ -n "${MACOS_PACKET_TUNNEL_PROFILE_SPECIFIER:-}${MACOS_PACKET_TUNNEL_PROFILE_UUID:-}" ]] || missing+=("MACOS_PACKET_TUNNEL_PROFILE_SPECIFIER or MACOS_PACKET_TUNNEL_PROFILE_UUID")

  if [[ ! -f "${export_options_plist}" ]]; then
    missing+=("EXPORT_OPTIONS_PLIST (${export_options_plist})")
  fi

  if ((${#missing[@]} > 0)); then
    printf 'missing required macOS release input(s):\n' >&2
    printf '  - %s\n' "${missing[@]}" >&2
    exit 2
  fi
}

validate_notary_env() {
  local missing=()
  [[ -n "${MACOS_NOTARY_PROFILE:-}" ]] || missing+=("MACOS_NOTARY_PROFILE")
  if ((${#missing[@]} > 0)); then
    printf 'missing required notarization input(s):\n' >&2
    printf '  - %s\n' "${missing[@]}" >&2
    exit 2
  fi
}

run_xcodebuild_signed() {
  xcodebuild "${common_args[@]}" "$@"
}

prepare_export_options_plist() {
  if [[ "${export_options_plist}" != "${repo_root}/native/macos/ExportOptions.direct.plist" ]]; then
    printf '%s\n' "${export_options_plist}"
    return
  fi

  mkdir -p "$(dirname "${generated_export_options_plist}")"
  app_bundle_id="${MACOS_APP_BUNDLE_ID:-com.baleobala.app}"
  packet_tunnel_bundle_id="${MACOS_PACKET_TUNNEL_BUNDLE_ID:-${app_bundle_id}.packet-tunnel}"
  cat > "${generated_export_options_plist}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>destination</key>
    <string>export</string>
    <key>method</key>
    <string>developer-id</string>
    <key>provisioningProfiles</key>
    <dict>
        <key>${app_bundle_id}</key>
        <string>${MACOS_APP_PROFILE_SPECIFIER:-${MACOS_APP_PROFILE_UUID:-}}</string>
        <key>${packet_tunnel_bundle_id}</key>
        <string>${MACOS_PACKET_TUNNEL_PROFILE_SPECIFIER:-${MACOS_PACKET_TUNNEL_PROFILE_UUID:-}}</string>
    </dict>
    <key>signingStyle</key>
    <string>manual</string>
    <key>stripSwiftSymbols</key>
    <true/>
    <key>teamID</key>
    <string>${MACOS_DEVELOPMENT_TEAM}</string>
</dict>
</plist>
EOF
  printf '%s\n' "${generated_export_options_plist}"
}

write_release_metadata() {
  mkdir -p "$(dirname "${release_metadata_path}")"
  cat > "${release_metadata_path}" <<EOF
scheme=${scheme}
configuration=${configuration}
archive_path=${archive_path}
export_path=${export_path}
export_options_plist=${export_options_plist}
generated_export_options_plist=${generated_export_options_plist}
notary_log_path=${notary_log_path}
EOF
}

case "${mode}" in
  build)
    validate_release_env
    run_xcodebuild_signed "${release_settings[@]}" build
    ;;
  debug-local)
    xcodebuild \
      -project "${project}" \
      -scheme "${scheme}" \
      -configuration Debug \
      -derivedDataPath "${derived_data}" \
      -destination "${destination}" \
      CODE_SIGNING_ALLOWED=NO \
      CODE_SIGNING_REQUIRED=NO \
      build
    ;;
  archive)
    validate_release_env
    run_xcodebuild_signed \
      -archivePath "${archive_path}" \
      "${release_settings[@]}" \
      archive
    ;;
  export)
    validate_release_env
    if [[ ! -d "${archive_path}" ]]; then
      echo "missing archive at ${archive_path}" >&2
      echo "run: ${0} archive" >&2
      exit 2
    fi
    resolved_export_options_plist="$(prepare_export_options_plist)"
    xcodebuild \
      -exportArchive \
      -archivePath "${archive_path}" \
      -exportPath "${export_path}" \
      -exportOptionsPlist "${resolved_export_options_plist}"
    write_release_metadata
    ;;
  notarize)
    validate_release_env
    validate_notary_env
    if [[ ! -d "${export_path}" ]]; then
      echo "missing export at ${export_path}" >&2
      echo "run: ${0} export" >&2
      exit 2
    fi
    mkdir -p "$(dirname "${notary_log_path}")"
    xcrun notarytool submit "${export_path}/Baleobala.app" \
      --keychain-profile "${MACOS_NOTARY_PROFILE}" \
      --wait \
      --output-format json > "${notary_log_path}"
    write_release_metadata
    ;;
  staple)
    if [[ ! -d "${export_path}/Baleobala.app" ]]; then
      echo "missing exported app at ${export_path}/Baleobala.app" >&2
      echo "run: ${0} export" >&2
      exit 2
    fi
    xcrun stapler staple "${export_path}/Baleobala.app"
    write_release_metadata
    ;;
  release)
    "${BASH_SOURCE[0]}" archive
    "${BASH_SOURCE[0]}" export
    "${BASH_SOURCE[0]}" notarize
    "${BASH_SOURCE[0]}" staple
    ;;
  validate-release-env)
    validate_release_env
    printf 'macOS release inputs look complete\n'
    ;;
  *)
    echo "usage: ${0} [build|debug-local|archive|export|notarize|staple|release|validate-release-env]" >&2
    exit 2
    ;;
esac
