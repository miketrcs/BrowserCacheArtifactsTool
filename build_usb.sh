#!/usr/bin/env bash
# =============================================================================
# build_usb.sh — BrowserCacheArtifactsTool installer
#
# Run this ONCE on your own Mac to install to a USB key or local folder.
# Requires internet access. The resulting install requires none.
#
# Usage:
#   ./build_usb.sh /Volumes/YourUSBName     # install to USB key (both archs)
#   ./build_usb.sh ~/BrowserCacheArtifacts  # install to local folder (current arch only)
#   ./build_usb.sh                           # prompts for destination
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

# -----------------------------------------------------------------------------

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { printf "${GREEN}[+]${NC} %s\n" "$*"; }
warn()    { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
fatal()   { printf "${RED}[✗]${NC} %s\n" "$*" >&2; exit 1; }

# -----------------------------------------------------------------------------
# Detect current architecture
# -----------------------------------------------------------------------------
ARCH=$(uname -m)
case "$ARCH" in
    arm64)  NATIVE_ARCH="arm64"  ;;
    x86_64) NATIVE_ARCH="x86_64" ;;
    *)      fatal "Unsupported architecture: $ARCH" ;;
esac

# -----------------------------------------------------------------------------
# Resolve destination
# -----------------------------------------------------------------------------
DEST="${1:-}"

if [ -z "$DEST" ]; then
    printf "\nWhere would you like to install BrowserCacheArtifactsTool?\n"
    printf "  1) USB key    (e.g. /Volumes/Samsung)  — bundles both arm64 + x86_64\n"
    printf "  2) Local home (~/BrowserCacheArtifacts) — current arch only, faster\n"
    printf "  Or enter any custom path\n\n"
    read -rp "Choice or path [1/2/path]: " DEST
    [ -z "$DEST" ] && fatal "No destination provided."

    case "$DEST" in
        1)
            read -rp "Enter USB volume path (e.g. /Volumes/Samsung): " DEST
            [ -z "$DEST" ] && fatal "No path provided."
            ;;
        2)
            DEST="$HOME/BrowserCacheArtifacts"
            printf "Installing to: %s\n" "$DEST"
            ;;
    esac
fi

# Expand ~ if present
DEST="${DEST/#\~/$HOME}"

# -----------------------------------------------------------------------------
# Filesystem check + determine if we need both architectures
# -----------------------------------------------------------------------------
IS_VOLUME=false
if [[ "$DEST" == /Volumes/* ]]; then
    IS_VOLUME=true
    [ -d "$DEST" ] || fatal "Mount point not found: $DEST"
    FS=$(diskutil info "$DEST" 2>/dev/null | awk '/File System Personality/ {print $NF}')
    info "USB filesystem: $FS"
    if [[ "$FS" == *"FAT"* ]] || [[ "$FS" == *"ExFAT"* ]]; then
        fatal "USB is formatted as $FS — symlinks not supported. Reformat as APFS or HFS+ in Disk Utility."
    fi
    DUAL_ARCH=true
else
    mkdir -p "$DEST"
    DUAL_ARCH=false
    info "Local install — downloading $NATIVE_ARCH only"
fi

# Check available space
NEEDED=$([ "$DUAL_ARCH" = true ] && echo 800 || echo 450)
AVAIL=$(df -m "$DEST" | awk 'NR==2 {print $4}')
[ "$AVAIL" -lt "$NEEDED" ] && fatal "Not enough space (need ~${NEEDED}MB, have ${AVAIL}MB)"

info "Installing to: $DEST"

# -----------------------------------------------------------------------------
# Directory structure
# -----------------------------------------------------------------------------
APP_DIR="$DEST/BrowserCacheArtifacts"

if [ "$DUAL_ARCH" = true ]; then
    mkdir -p \
        "$APP_DIR/python-arm64" \
        "$APP_DIR/python-x86_64" \
        "$APP_DIR/wheels/arm64" \
        "$APP_DIR/wheels/x86_64" \
        "$APP_DIR/chrome_artifacts"
else
    mkdir -p \
        "$APP_DIR/python-$NATIVE_ARCH" \
        "$APP_DIR/wheels/$NATIVE_ARCH" \
        "$APP_DIR/chrome_artifacts"
fi

# -----------------------------------------------------------------------------
# Copy app files
# -----------------------------------------------------------------------------
info "Copying app files..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$SCRIPT_DIR/app.py"                "$APP_DIR/"
cp "$SCRIPT_DIR/main.py"               "$APP_DIR/"
cp "$SCRIPT_DIR/requirements.txt"      "$APP_DIR/"
cp "$SCRIPT_DIR/requirements_full.txt" "$APP_DIR/"
cp "$SCRIPT_DIR/run.sh"                "$APP_DIR/"
chmod +x "$APP_DIR/run.sh"
cp "$SCRIPT_DIR/chrome_artifacts/"*.py "$APP_DIR/chrome_artifacts/"
touch "$APP_DIR/chrome_artifacts/__init__.py"

# -----------------------------------------------------------------------------
# Helper: download + extract one Python build
# -----------------------------------------------------------------------------
TMPDIR_PBS=$(mktemp -d)
trap 'rm -rf "$TMPDIR_PBS"' EXIT

download_python() {
    local arch="$1" url="$2"
    local tgz="$TMPDIR_PBS/python-${arch}.tar.gz"
    info "Downloading Python ${PY_VER} for ${arch}..."
    curl -fL --progress-bar -o "$tgz" "$url" || fatal "Failed to download ${arch} Python"
    info "Extracting ${arch} Python..."
    tar -xzf "$tgz" -C "$APP_DIR/python-${arch}" --strip-components=1
}

download_wheels() {
    local arch="$1"
    info "Downloading wheels for ${arch}..."
    "$APP_DIR/python-${arch}/bin/python3" -m pip download \
        --dest "$APP_DIR/wheels/${arch}/" \
        -r "$APP_DIR/requirements_full.txt" \
        --quiet
}

# -----------------------------------------------------------------------------
# Download Python + wheels for required architectures
# -----------------------------------------------------------------------------
if [ "$DUAL_ARCH" = true ]; then
    download_python "arm64"  "$PY_ARM64_URL"
    download_python "x86_64" "$PY_X86_URL"
    download_wheels "arm64"
    download_wheels "x86_64"
else
    case "$NATIVE_ARCH" in
        arm64)  download_python "arm64"  "$PY_ARM64_URL" ;;
        x86_64) download_python "x86_64" "$PY_X86_URL"  ;;
    esac
    download_wheels "$NATIVE_ARCH"
fi

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
USED=$(du -sh "$APP_DIR" | awk '{print $1}')

printf "\n"
info "Install complete!"
printf "  Path:       %s\n" "$APP_DIR"
printf "  Total size: %s\n" "$USED"
printf "\n  To launch:\n    %s/run.sh\n\n" "$APP_DIR"

if [ "$IS_VOLUME" = true ]; then
    warn "Tip: eject the USB cleanly before moving to the target machine."
fi
