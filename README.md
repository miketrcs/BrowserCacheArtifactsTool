# BrowserCacheArtifactsTool

A macOS-focused Chrome browser artifact browser with a Streamlit GUI. Browse history, downloads, cookies, bookmarks, and cached images directly from your Chrome profile — entirely local, no data ever leaves your machine.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **History** — Full URL visit timeline with transition type, visit count, and duration
- **Downloads** — Complete download history with file paths, sizes, and states
- **Cookies** — Cookie browser with optional macOS Keychain decryption of encrypted values
- **Bookmarks** — Bookmark tree with folder hierarchy and date added
- **Cached Images** — Extracts and displays images (JPEG, PNG, GIF, WebP, SVG) directly from Chrome's on-disk Simple Cache with clickable source URLs
- **SQLite export** — All artifacts exportable to a single SQLite database
- **Fully local** — Runs on `localhost`, no telemetry, no cloud, no data leaves your machine

---

## Requirements

- macOS (cookie decryption requires the macOS Keychain)
- Python 3.9+
- Chrome (or Chromium-based browser) installed

---

## Installation

```bash
git clone https://github.com/miketrcs/BrowserCacheArtifactsTool.git
cd BrowserCacheArtifactsTool

pip install -r requirements.txt
pip install streamlit pillow pandas   # GUI dependencies
```

---

## Usage

### GUI (recommended)

```bash
python -m streamlit run app.py
```

Opens automatically at `http://localhost:8501`. Enter your Chrome profile path in the sidebar and click **Load Profile**.

**Default Chrome profile location on macOS:**
```
~/Library/Application Support/Google/Chrome/Default/
```

### CLI

```bash
# View all artifact types
python main.py -i "~/Library/Application Support/Google/Chrome/Default/"

# Export to SQLite
python main.py -i "..." -o results.db

# Specific artifact types
python main.py -i "..." --type history downloads --limit 100

# Decrypt cookie values (reads from macOS Keychain)
python main.py -i "..." --type cookies --decrypt

# Read files directly without copying (faster, requires Chrome to be closed)
python main.py -i "..." --no-copy
```

---

## Project Structure

```
BrowserCacheArtifactsTool/
├── app.py                        # Streamlit GUI
├── main.py                       # CLI entry point
├── requirements.txt
└── chrome_artifacts/
    ├── artifacts.py              # Data classes (URLItem, DownloadItem, etc.)
    ├── cache.py                  # Chrome Simple Cache image extractor
    ├── db.py                     # SQLite connection helpers
    ├── decrypt.py                # macOS Keychain AES decryption
    ├── parsers.py                # History, downloads, cookies, bookmarks parsers
    └── output.py                 # Rich terminal tables + SQLite export
```

---

## Notes

- **Close Chrome before loading** if you want to ensure all database files are accessible. Chrome locks its SQLite databases while running. The tool copies files before opening them by default to minimize this issue; use `--no-copy` to skip the copy step.
- **Cookie decryption** (`--decrypt` / the sidebar toggle) retrieves the AES key from macOS Keychain (`Chrome Safe Storage`). This only works when running on the same Mac the Chrome profile is from.
- **Cached images** are read from `~/Library/Caches/Google/Chrome/Default/Cache/Cache_Data/`. Chrome uses the Simple Cache format — each `_0` file encodes the original URL and raw response body. Only verified image files (validated with PIL) are shown.

---

## Supported Chrome Versions

Version detection is automatic based on database schema. Tested against Chrome v1–v145.

---

## About

This tool was built as a focused, macOS-native alternative to [Hindsight](https://github.com/obsidianforensics/hindsight) by [Ryan Benson](https://github.com/obsidianforensics).

A significant portion of the Chrome artifact parsing logic — particularly the versioned SQL queries for history, downloads, and cookies, the multi-format timestamp conversion, and the macOS Keychain decryption approach — is directly adapted from Hindsight's source code. Hindsight is a comprehensive, cross-platform forensics tool that supports far more artifact types and output formats than this project. If you need Windows/Linux support, Excel output, Chrome Sync parsing, or a deeper forensic feature set, Hindsight is the right tool.

**Original project:** [obsidianforensics/hindsight](https://github.com/obsidianforensics/hindsight)  
**Original author:** Ryan Benson ([@obsidianforensics](https://github.com/obsidianforensics))  
**License:** Apache 2.0

---

## License

MIT — see [LICENSE](LICENSE) for details.

This project incorporates logic adapted from [Hindsight](https://github.com/obsidianforensics/hindsight), which is licensed under the Apache License 2.0. The adapted portions are the Chrome artifact parsing queries and decryption logic in `chrome_artifacts/parsers.py` and `chrome_artifacts/decrypt.py`.
