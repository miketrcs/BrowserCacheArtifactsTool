#!/usr/bin/env bash
# =============================================================================
# update_usb.sh — Pull latest code and sync to an existing install
#
# Usage:
#   ./update_usb.sh                              # syncs to /Volumes/Samsung
#   ./update_usb.sh /Volumes/YourUSB            # syncs to a USB volume
#   ./update_usb.sh ~/BrowserCacheArtifacts     # syncs to a local install
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
fatal() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-/Volumes/Samsung}"

# Expand ~ if present
DEST="${DEST/#\~/$HOME}"
APP_DIR="$DEST/BrowserCacheArtifacts"

[ -d "$APP_DIR" ] || fatal "Install not found at $APP_DIR — run build_usb.sh first."

# Pull latest from GitHub
info "Pulling latest from GitHub..."
git -C "$SRC" pull

# Sync top-level app files
info "Syncing app files to $APP_DIR..."
for f in app.py main.py requirements.txt requirements_full.txt run.sh; do
    [ -f "$SRC/$f" ] && cp "$SRC/$f" "$APP_DIR/$f"
done
chmod +x "$APP_DIR/run.sh"

# Sync python package
rsync -a --include='*.py' --exclude='*' \
    "$SRC/chrome_artifacts/" "$APP_DIR/chrome_artifacts/"

info "Done. Run $APP_DIR/run.sh to launch."
