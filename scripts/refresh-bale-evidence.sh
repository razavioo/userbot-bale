#!/usr/bin/env bash
# refresh-bale-evidence.sh — fetch latest Bale APK, decompile, diff, summarise.
#
# Usage:
#   ./scripts/refresh-bale-evidence.sh [--apk <path-or-url>] [--jadx <path>]
#
# Options:
#   --apk   Path or HTTPS URL to the Bale APK. Defaults to $BALE_APK_URL env
#           var, then the existing bale.apk in the repo root.
#   --jadx  Path to the jadx binary. Defaults to `jadx` on $PATH.
#
# Outputs:
#   re/jadx-out-<version>/   Fresh decompile (version = APK versionName).
#   docs/BALE_RE_NOTES.md    A date-stamped section appended with the diff
#                            summary (changed protos + RPC names).
#   bale.apk                 Updated if a new APK was fetched.
#
# Dependencies: jadx, aapt (or aapt2), curl, diff, python3 (for proto scan).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK_SRC=""
JADX_BIN="jadx"

# ---- arg parse ---------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apk)   APK_SRC="$2"; shift 2 ;;
        --jadx)  JADX_BIN="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | head -20 | sed 's/^# //'
            exit 0 ;;
        *)
            echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---- resolve APK source ------------------------------------------------------
if [[ -z "$APK_SRC" ]]; then
    APK_SRC="${BALE_APK_URL:-}"
fi
if [[ -z "$APK_SRC" ]]; then
    APK_SRC="${REPO_ROOT}/bale.apk"
fi

echo "[refresh] APK source: $APK_SRC"

APK_TMP="${REPO_ROOT}/bale.apk.new"
if [[ "$APK_SRC" == http* ]]; then
    echo "[refresh] Downloading APK…"
    curl -fsSL "$APK_SRC" -o "$APK_TMP"
else
    cp "$APK_SRC" "$APK_TMP"
fi

# ---- extract version ---------------------------------------------------------
VERSION=""
if command -v aapt &>/dev/null; then
    VERSION=$(aapt dump badging "$APK_TMP" 2>/dev/null | grep versionName | sed "s/.*versionName='\([^']*\)'.*/\1/")
elif command -v aapt2 &>/dev/null; then
    VERSION=$(aapt2 dump badging "$APK_TMP" 2>/dev/null | grep versionName | sed "s/.*versionName='\([^']*\)'.*/\1/")
fi
if [[ -z "$VERSION" ]]; then
    VERSION="$(date +%Y%m%d)"
    echo "[refresh] WARNING: could not extract versionName; using date: $VERSION"
fi
echo "[refresh] APK version: $VERSION"

# ---- check if this is actually newer -----------------------------------------
PREV_APK="${REPO_ROOT}/bale.apk"
if [[ -f "$PREV_APK" ]] && cmp -s "$APK_TMP" "$PREV_APK"; then
    echo "[refresh] APK unchanged — nothing to do."
    rm "$APK_TMP"
    exit 0
fi

# ---- run jadx ----------------------------------------------------------------
OUT_DIR="${REPO_ROOT}/re/jadx-out-${VERSION}"
if [[ -d "$OUT_DIR" ]]; then
    echo "[refresh] $OUT_DIR already exists — skipping decompile."
else
    echo "[refresh] Decompiling with jadx into $OUT_DIR …"
    "$JADX_BIN" --deobf --export-gradle -d "$OUT_DIR" "$APK_TMP"
fi

# ---- diff against the previous jadx-out ------------------------------------
PREV_OUT="${REPO_ROOT}/re/jadx-out"
DIFF_SUMMARY=""

if [[ -d "$PREV_OUT" ]]; then
    echo "[refresh] Diffing against previous decompile…"
    # Focus on protobuf descriptors and service definitions.
    PROTO_CHANGED=$(diff -rq \
        --include="*.proto" --include="*.json" \
        "$PREV_OUT/sources" "$OUT_DIR/sources" 2>/dev/null \
        | grep -E "(proto|Grpc|Service|Request|Response)" \
        | head -40 || true)
    JAVA_CHANGED=$(diff -rq \
        "$PREV_OUT/sources/ai/bale/proto" \
        "$OUT_DIR/sources/ai/bale/proto" 2>/dev/null \
        | head -60 || true)
    if [[ -z "$PROTO_CHANGED" && -z "$JAVA_CHANGED" ]]; then
        DIFF_SUMMARY="No changes in proto / RPC surface."
    else
        DIFF_SUMMARY="$(echo "$PROTO_CHANGED"; echo "$JAVA_CHANGED")"
    fi
else
    DIFF_SUMMARY="No previous decompile to diff against (first run)."
fi

# ---- scan for interesting new RPC / proto names ------------------------------
NEW_RPCS=""
if command -v python3 &>/dev/null && [[ -d "$OUT_DIR/sources/ai/bale/proto" ]]; then
    NEW_RPCS=$(python3 - "$OUT_DIR/sources" <<'PYEOF'
import sys, os, re

root = sys.argv[1]
rpcs = set()
for dirpath, _, filenames in os.walk(root):
    for fn in filenames:
        if not fn.endswith(".java"):
            continue
        try:
            text = open(os.path.join(dirpath, fn)).read()
        except Exception:
            continue
        # Look for gRPC method descriptors and service class names.
        rpcs.update(re.findall(r'"([A-Z][a-zA-Z]+(?:Request|Response|Service|Rpc))"', text))
for r in sorted(rpcs)[:80]:
    print(" ", r)
PYEOF
)
fi

# ---- append to BALE_RE_NOTES.md ---------------------------------------------
NOTES="${REPO_ROOT}/docs/BALE_RE_NOTES.md"
DATE="$(date +%Y-%m-%d)"

cat >> "$NOTES" <<MDEOF


---

## Refresh ${DATE} — APK ${VERSION}

**APK version:** ${VERSION}
**Decompile output:** \`re/jadx-out-${VERSION}/\`
**Previous decompile:** \`re/jadx-out/\`

### Proto / RPC diff summary

\`\`\`
${DIFF_SUMMARY:-"(no diff computed)"}
\`\`\`

### RPC / message names detected in this version

\`\`\`
${NEW_RPCS:-"(scan not run)"}
\`\`\`

_Appended automatically by scripts/refresh-bale-evidence.sh_
MDEOF

echo "[refresh] Appended section to $NOTES"

# ---- update canonical bale.apk and jadx-out symlink -------------------------
mv "$APK_TMP" "$PREV_APK"
echo "[refresh] Updated bale.apk (was $(du -sh "$PREV_APK" | cut -f1))"

# Update the canonical jadx-out symlink / directory name.
if [[ -L "$PREV_OUT" ]]; then
    ln -sfn "jadx-out-${VERSION}" "$PREV_OUT"
    echo "[refresh] Symlink re/jadx-out → jadx-out-${VERSION}"
elif [[ -d "$PREV_OUT" && ! -L "$PREV_OUT" ]]; then
    echo "[refresh] re/jadx-out is a real directory — not replacing with symlink."
    echo "[refresh] New decompile is at: $OUT_DIR"
fi

echo "[refresh] Done. Remember to commit: bale.apk, re/jadx-out-${VERSION}/, docs/BALE_RE_NOTES.md"
