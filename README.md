# BrowserCacheArtifactsTool

A macOS-focused browser artifact browser with a Streamlit GUI. Browse history, downloads, cookies, bookmarks, and cached images from **Chrome** and **Safari** — entirely local, no data ever leaves your machine.

![Python](https://img.shields.io/badge/python-3.13-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

| | Chrome | Safari |
|---|---|---|
| History | ✅ | ✅ |
| Downloads | ✅ | ✅ |
| Cookies | ✅ (with optional decryption) | ✅ |
| Bookmarks | ✅ | ✅ |
| Cached Images | ✅ Simple Cache | ✅ WebKitCache + Cache.db |
| SQLite Export | ✅ | ✅ |

- **Fully local** — runs on `localhost`, no telemetry, no cloud, no data leaves your machine
- **Zero footprint USB mode** — run from a USB key without writing anything to the target Mac
- **macOS Full Disk Access guidance** — detects permission issues and guides you to the fix

---

## Quick Start (local install)

```bash
git clone https://github.com/miketrcs/BrowserCacheArtifactsTool.git
cd BrowserCacheArtifactsTool
./build_usb.sh          # choose option 2 (local home)
~/BrowserCacheArtifacts/run.sh
```

The installer downloads a self-contained Python and all dependencies — no existing Python installation required.

---

## Install Options

### Option A — Local install (development / personal use)

```bash
git clone https://github.com/miketrcs/BrowserCacheArtifactsTool.git
cd BrowserCacheArtifactsTool
./build_usb.sh
```

When prompted:
```
1) USB key    — bundles both arm64 + x86_64
2) Local home — ~/BrowserCacheArtifacts (current arch only, faster)
Or enter any custom path
```

Choose **2** for a local install, or enter any custom path.

Launch:
```bash
~/BrowserCacheArtifacts/run.sh
```

---

### Option B — USB key (portable forensic use)

Format the USB as **APFS or HFS+** (not exFAT/FAT32 — symlinks required):
> Disk Utility → Erase → Format: APFS → Scheme: GUID Partition Map

```bash
git clone https://github.com/miketrcs/BrowserCacheArtifactsTool.git
cd BrowserCacheArtifactsTool
./build_usb.sh /Volumes/YourUSBName
```

This downloads standalone Python builds for both arm64 and x86_64 plus all wheels — **no internet required on the target machine**.

On the target Mac:
```bash
/Volumes/YourUSBName/BrowserCacheArtifacts/run.sh
```

> **Zero footprint:** `run.sh` redirects all writes (Streamlit config, Python cache, etc.) to the USB. Nothing is written to the target Mac's home directory.

---

## Updating

Pull the latest code and sync it to your existing install:

```bash
cd BrowserCacheArtifactsTool
./update_usb.sh                          # defaults to /Volumes/Samsung
./update_usb.sh ~/BrowserCacheArtifacts  # local install
./update_usb.sh /Volumes/YourUSBName     # specific USB
```

The update script runs `git pull` then rsyncs only the changed Python files — Python and wheels are not re-downloaded.

---

## Safari — Full Disk Access

Safari's files are sandboxed by macOS. The tool will detect this and show a **"Open Privacy & Security Settings"** button in the UI. Steps:

1. Click the button (or go to **System Settings → Privacy & Security → Full Disk Access**)
2. Click **+** and add **Terminal** (or whichever app you launch `run.sh` from)
3. Quit Terminal completely and relaunch
4. Run `run.sh` again

Chrome does not require Full Disk Access.

---

## Usage

### GUI

Open your browser to `http://localhost:8502` after launching `run.sh`.

1. Select **Chrome** or **Safari** in the sidebar
2. Confirm or edit the profile path
3. Click **Load Profile**
4. Browse the five tabs: History · Downloads · Cookies · Bookmarks · Cached Images

**Default paths (auto-populated):**
```
Chrome:  ~/Library/Application Support/Google/Chrome/Default
Safari:  ~/Library/Safari
```

### CLI

```bash
# View all artifact types
python main.py -i "~/Library/Application Support/Google/Chrome/Default/"

# Export to SQLite
python main.py -i "..." -o results.db

# Specific artifact types with limit
python main.py -i "..." --type history downloads --limit 100

# Decrypt Chrome cookie values (reads from macOS Keychain)
python main.py -i "..." --type cookies --decrypt

# Read files directly without copying (faster, requires Chrome to be closed)
python main.py -i "..." --no-copy
```

---

## Project Structure

```
BrowserCacheArtifactsTool/
├── app.py                        # Streamlit GUI (Chrome + Safari)
├── main.py                       # CLI entry point
├── run.sh                        # Portable launcher (USB + local)
├── build_usb.sh                  # Installer (USB or local folder)
├── update_usb.sh                 # Update an existing install from git
├── requirements.txt
├── requirements_full.txt         # Full dependency list for offline install
└── chrome_artifacts/
    ├── artifacts.py              # Shared data classes (URLItem, DownloadItem, …)
    ├── cache.py                  # Chrome Simple Cache image extractor
    ├── db.py                     # SQLite connection helpers
    ├── decrypt.py                # macOS Keychain AES-CBC cookie decryption
    ├── output.py                 # Rich terminal tables + SQLite export
    ├── parsers.py                # Chrome history, downloads, cookies, bookmarks
    ├── safari_cache.py           # Safari WebKitCache + Cache.db image extractor
    └── safari_parsers.py         # Safari history, downloads, cookies, bookmarks
```

---

## Notes

- **Close the browser before loading** to ensure SQLite databases are accessible. The tool copies files before opening them by default; use `--no-copy` to skip this.
- **Chrome cookie decryption** retrieves the AES key from macOS Keychain (`Chrome Safe Storage`). Only works on the same Mac the profile was created on.
- **Chrome cached images** are read from Chrome's Simple Cache (`Cache_Data/`). Each `_0` file encodes the URL and raw response body; images are PIL-validated before display.
- **Safari cached images** are read from two locations:
  - `WebKitCache/Version N/Blobs/` — the full web browser image cache (~500+ images typical)
  - `Cache.db` — Apple-service thumbnails and icons
  URLs are recovered by parsing binary metadata files in `Records/`.
- **Chrome version detection** is automatic based on database schema. Tested against Chrome v1–v145.

---

## About

This tool was built as a focused, macOS-native alternative to [Hindsight](https://github.com/obsidianforensics/hindsight) by [Ryan Benson](https://github.com/obsidianforensics).

A significant portion of the Chrome artifact parsing logic — particularly the versioned SQL queries for history, downloads, and cookies, the multi-format timestamp conversion, and the macOS Keychain decryption approach — is directly adapted from Hindsight's source code. Hindsight is a comprehensive, cross-platform forensics tool that supports far more artifact types and output formats. If you need Windows/Linux support, Excel output, Chrome Sync parsing, or a deeper forensic feature set, Hindsight is the right tool.

**Original project:** [obsidianforensics/hindsight](https://github.com/obsidianforensics/hindsight)  
**Original author:** Ryan Benson ([@obsidianforensics](https://github.com/obsidianforensics))  
**License:** Apache 2.0

---

## License

MIT — see [LICENSE](LICENSE) for details.

This project incorporates logic adapted from [Hindsight](https://github.com/obsidianforensics/hindsight), licensed under the Apache License 2.0. Adapted portions include Chrome artifact parsing queries and decryption logic in `chrome_artifacts/parsers.py` and `chrome_artifacts/decrypt.py`.
