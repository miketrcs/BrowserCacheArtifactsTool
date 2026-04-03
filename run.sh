#!/usr/bin/env bash
# =============================================================================
# run.sh — BrowserCacheArtifactsTool portable launcher
#
# Run this from the install directory on any Mac.
# All writes stay in the install directory — nothing is written to the
# target Mac's home directory.
#
# Usage:
#   ./run.sh [--port 8502] [--no-browser]
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { printf "${GREEN}[+]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
fatal() { printf "${RED}[✗]${NC} %s\n" "$*" >&2; exit 1; }

# -----------------------------------------------------------------------------
# Locate the install directory (where this script lives)
# -----------------------------------------------------------------------------
USB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -----------------------------------------------------------------------------
# Parse args
# -----------------------------------------------------------------------------
PORT=8502
OPEN_BROWSER=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)       PORT="$2"; shift 2 ;;
        --no-browser) OPEN_BROWSER=false; shift ;;
        *) warn "Unknown argument: $1"; shift ;;
    esac
done

# -----------------------------------------------------------------------------
# Detect architecture
# -----------------------------------------------------------------------------
ARCH=$(uname -m)
case "$ARCH" in
    arm64)  ARCH_DIR="arm64"  ;;
    x86_64) ARCH_DIR="x86_64" ;;
    *)      fatal "Unsupported architecture: $ARCH" ;;
esac

PYTHON="$USB_DIR/python-$ARCH_DIR/bin/python3"
WHEELS="$USB_DIR/wheels/$ARCH_DIR"
LIB_DIR="$USB_DIR/lib-$ARCH_DIR"

[ -f "$PYTHON" ] || fatal "Python not found at $PYTHON — did you run build_usb.sh?"
[ -d "$WHEELS" ] || fatal "Wheels not found at $WHEELS — did you run build_usb.sh?"

# -----------------------------------------------------------------------------
# Redirect ALL home-directory writes to the install directory
# (covers ~/.streamlit, ~/.cache, ~/.local, etc.)
# Nothing is written to the target Mac's home directory.
# -----------------------------------------------------------------------------
FAKE_HOME="$USB_DIR/runtime/home"
mkdir -p "$FAKE_HOME"
export REAL_HOME="$HOME"   # save the target Mac's real home before overriding
export HOME="$FAKE_HOME"

# Redirect Python bytecode cache
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$USB_DIR/runtime/pycache"

# Disable Streamlit's file watcher and telemetry
export STREAMLIT_SERVER_FILE_WATCHER_TYPE=none
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
export STREAMLIT_SERVER_HEADLESS=true

# -----------------------------------------------------------------------------
# Install packages on first run (offline, from bundled wheels)
# -----------------------------------------------------------------------------
if [ ! -f "$LIB_DIR/.installed" ]; then
    info "First run — installing packages (no internet required)..."
    mkdir -p "$LIB_DIR"

    "$PYTHON" -m pip install \
        --no-index \
        --find-links="$WHEELS" \
        --target="$LIB_DIR" \
        --quiet \
        -r "$USB_DIR/requirements_full.txt"

    touch "$LIB_DIR/.installed"
    info "Packages installed."
fi

# Add lib dir to Python path
export PYTHONPATH="$LIB_DIR:${PYTHONPATH:-}"

# -----------------------------------------------------------------------------
# Create Streamlit config
# -----------------------------------------------------------------------------
STREAMLIT_CONFIG_DIR="$FAKE_HOME/.streamlit"
mkdir -p "$STREAMLIT_CONFIG_DIR"

cat > "$STREAMLIT_CONFIG_DIR/config.toml" <<TOML
[server]
port = ${PORT}
headless = true
fileWatcherType = "none"
enableCORS = false

[browser]
gatherUsageStats = false
serverAddress = "localhost"
serverPort = ${PORT}

[global]
developmentMode = false
TOML

# -----------------------------------------------------------------------------
# Launch
# -----------------------------------------------------------------------------
printf "\n"
printf "${CYAN}╔══════════════════════════════════════╗${NC}\n"
printf "${CYAN}║  BrowserCacheArtifactsTool           ║${NC}\n"
printf "${CYAN}╚══════════════════════════════════════╝${NC}\n"
printf "\n"
info "Architecture:  $ARCH"
info "Python:        $("$PYTHON" --version)"
info "Install dir:   $USB_DIR"
info "Packages:      $LIB_DIR"
info "Fake HOME:     $FAKE_HOME"
printf "\n"
info "Starting on http://localhost:${PORT}"
warn "Nothing is written to the target Mac — all data stays in the install directory."
printf "\n  Press Ctrl+C to stop.\n\n"

if [ "$OPEN_BROWSER" = true ]; then
    (sleep 3 && open "http://localhost:${PORT}") &
fi

cd "$USB_DIR"
exec "$PYTHON" -m streamlit run app.py \
    --server.port "$PORT" \
    --server.headless true \
    --server.fileWatcherType none \
    --browser.gatherUsageStats false
