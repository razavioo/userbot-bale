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

case "${mode}" in
  build)
    xcodebuild "${common_args[@]}" build
    ;;
  debug-local)
    xcodebuild \
      "${common_args[@]}" \
      -configuration Debug \
      CODE_SIGNING_ALLOWED=NO \
      CODE_SIGNING_REQUIRED=NO \
      build
    ;;
  archive)
    xcodebuild \
      "${common_args[@]}" \
      -archivePath "${archive_path}" \
      archive
    ;;
  export)
    if [[ ! -d "${archive_path}" ]]; then
      echo "missing archive at ${archive_path}" >&2
      echo "run: ${0} archive" >&2
      exit 2
    fi
    if [[ -z "${EXPORT_OPTIONS_PLIST:-}" ]]; then
      echo "set EXPORT_OPTIONS_PLIST to an export options plist before running export" >&2
      exit 2
    fi
    xcodebuild \
      -exportArchive \
      -archivePath "${archive_path}" \
      -exportPath "${export_path}" \
      -exportOptionsPlist "${EXPORT_OPTIONS_PLIST}"
    ;;
  *)
    echo "usage: ${0} [build|debug-local|archive|export]" >&2
    exit 2
    ;;
esac
