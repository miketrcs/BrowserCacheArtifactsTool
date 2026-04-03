#!/usr/bin/env bash
# =============================================================================
# build_usb.sh — BrowserCacheArtifactsTool USB preparation script
#
# Run this ONCE on your own Mac to build the USB key.
# Requires internet access. The resulting USB requires none.
#
# Usage:
#   chmod +x build_usb.sh
#   ./build_usb.sh /Volumes/YourUSBName
#
# USB filesystem requirement: APFS or HFS+ (NOT exFAT / FAT32)
# To reformat a USB as APFS in Disk Utility:
#   Erase → Format: APFS → Scheme: GUID Partition Map
# =============================================================================
set -euo pipefail

# -----------------------------------------------------------------------------
# Config — update these when new python-build-standalone releases come out
# -----------------------------------------------------------------------------
PY_TAG="20260325"
PY_VER="3.13.12"
PY_BASE="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}"
PY_ARM64_URL="${PY_BASE}/cpython-${PY_VER}%2B${PY_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PY_X86_URL="${PY_BASE}/cpython-${PY_VER}%2B${PY_TAG}-x86_64-apple-darwin-install_only.tar.gz"

# Short Python version for pip (e.g. 313)
PY_SHORT="313"
# -----------------------------------------------------------------------------

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[+]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
fatal()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

USB="${1:-}"
[ -z "$USB" ] && fatal "Usage: $0 /Volumes/YourUSBName"
[ -d "$USB" ] || fatal "Mount point not found: $USB"

# Check filesystem type (must support symlinks)
FS=$(diskutil info "$USB" 2>/dev/null | awk '/File System Personality/ {print $NF}')
info "USB filesystem: $FS"
if [[ "$FS" == *"FAT"* ]] || [[ "$FS" == *"ExFAT"* ]]; then
    fatal "USB is formatted as $FS — symlinks not supported.\nReformat as APFS or HFS+ (Mac OS Extended) in Disk Utility."
fi

# Check available space (need ~800MB)
AVAIL=$(df -m "$USB" | awk 'NR==2 {print $4}')
[ "$AVAIL" -lt 800 ] && fatal "Not enough space on USB (need ~800MB, have ${AVAIL}MB)"

info "Preparing USB at: $USB"

# -----------------------------------------------------------------------------
# Directory structure
# -----------------------------------------------------------------------------
APP_DIR="$USB/BrowserCacheArtifacts"
mkdir -p \
    "$APP_DIR/python-arm64" \
    "$APP_DIR/python-x86_64" \
    "$APP_DIR/wheels/arm64" \
    "$APP_DIR/wheels/x86_64" \
    "$APP_DIR/chrome_artifacts"

# -----------------------------------------------------------------------------
# Copy app files
# -----------------------------------------------------------------------------
info "Copying app files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/app.py"               "$APP_DIR/"
cp "$SCRIPT_DIR/main.py"              "$APP_DIR/"
cp "$SCRIPT_DIR/requirements.txt"     "$APP_DIR/"
cp "$SCRIPT_DIR/requirements_full.txt" "$APP_DIR/"
cp "$SCRIPT_DIR/chrome_artifacts/"*.py "$APP_DIR/chrome_artifacts/"
touch "$APP_DIR/chrome_artifacts/__init__.py"

# -----------------------------------------------------------------------------
# Download Python (arm64)
# -----------------------------------------------------------------------------
info "Downloading Python ${PY_VER} for arm64..."
TMPDIR_PBS=$(mktemp -d)
trap 'rm -rf "$TMPDIR_PBS"' EXIT

ARM64_TGZ="$TMPDIR_PBS/python-arm64.tar.gz"
if ! curl -fL --progress-bar -o "$ARM64_TGZ" "$PY_ARM64_URL"; then
    fatal "Failed to download arm64 Python"
fi
info "Extracting arm64 Python..."
tar -xzf "$ARM64_TGZ" -C "$APP_DIR/python-arm64" --strip-components=1

# -----------------------------------------------------------------------------
# Download Python (x86_64)
# -----------------------------------------------------------------------------
info "Downloading Python ${PY_VER} for x86_64..."
X86_TGZ="$TMPDIR_PBS/python-x86_64.tar.gz"
if ! curl -fL --progress-bar -o "$X86_TGZ" "$PY_X86_URL"; then
    fatal "Failed to download x86_64 Python"
fi
info "Extracting x86_64 Python..."
tar -xzf "$X86_TGZ" -C "$APP_DIR/python-x86_64" --strip-components=1

# -----------------------------------------------------------------------------
# Download wheels for arm64
# -----------------------------------------------------------------------------
info "Downloading wheels for arm64..."
"$APP_DIR/python-arm64/bin/python3" -m pip download \
    --platform "macosx_11_0_arm64" \
    --python-version "$PY_SHORT" \
    --implementation cp \
    --abi "cp${PY_SHORT}" \
    --only-binary :all: \
    --dest "$APP_DIR/wheels/arm64/" \
    -r "$APP_DIR/requirements_full.txt" \
    --quiet

# Pure-Python packages don't have platform-specific wheels — grab any remaining
"$APP_DIR/python-arm64/bin/python3" -m pip download \
    --platform "macosx_11_0_arm64" \
    --python-version "$PY_SHORT" \
    --implementation cp \
    --abi "cp${PY_SHORT}" \
    --dest "$APP_DIR/wheels/arm64/" \
    -r "$APP_DIR/requirements_full.txt" \
    --quiet 2>/dev/null || true

# -----------------------------------------------------------------------------
# Download wheels for x86_64
# -----------------------------------------------------------------------------
info "Downloading wheels for x86_64..."
"$APP_DIR/python-x86_64/bin/python3" -m pip download \
    --platform "macosx_10_15_x86_64" \
    --python-version "$PY_SHORT" \
    --implementation cp \
    --abi "cp${PY_SHORT}" \
    --only-binary :all: \
    --dest "$APP_DIR/wheels/x86_64/" \
    -r "$APP_DIR/requirements_full.txt" \
    --quiet

"$APP_DIR/python-x86_64/bin/python3" -m pip download \
    --platform "macosx_10_15_x86_64" \
    --python-version "$PY_SHORT" \
    --implementation cp \
    --abi "cp${PY_SHORT}" \
    --dest "$APP_DIR/wheels/x86_64/" \
    -r "$APP_DIR/requirements_full.txt" \
    --quiet 2>/dev/null || true

# -----------------------------------------------------------------------------
# Copy run.sh to USB root for easy access
# -----------------------------------------------------------------------------
cp "$SCRIPT_DIR/run.sh" "$APP_DIR/run.sh"
chmod +x "$APP_DIR/run.sh"

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
ARM64_COUNT=$(ls "$APP_DIR/wheels/arm64/" | wc -l | tr -d ' ')
X86_COUNT=$(ls "$APP_DIR/wheels/x86_64/" | wc -l | tr -d ' ')
USB_USED=$(du -sh "$APP_DIR" | awk '{print $1}')

echo ""
info "USB build complete!"
echo "  Path:          $APP_DIR"
echo "  Wheels arm64:  $ARM64_COUNT packages"
echo "  Wheels x86_64: $X86_COUNT packages"
echo "  Total size:    $USB_USED"
echo ""
echo "  To launch on any Mac:"
echo "    $APP_DIR/run.sh"
echo ""
warn "Tip: eject the USB cleanly before moving to the target machine."
