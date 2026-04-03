"""
Safari artifact parsers for macOS.

Parses: URL history, downloads, cookies (BinaryCookies), bookmarks.

Default data locations:
  History:   ~/Library/Safari/History.db
  Downloads: ~/Library/Safari/Downloads.plist
  Bookmarks: ~/Library/Safari/Bookmarks.plist
  Cookies:   ~/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies
"""
import datetime
import logging
import os
import plistlib
import shutil
import sqlite3
import struct
import tempfile
from pathlib import Path

from .artifacts import URLItem, DownloadItem, CookieItem, BookmarkItem, BookmarkFolderItem

log = logging.getLogger(__name__)

# Safari timestamps: seconds since 2001-01-01 00:00:00 UTC (Mac absolute time)
_MAC_EPOCH_OFFSET = 978307200


def _mac_to_dt(ts) -> datetime.datetime:
    """Convert Mac absolute time to UTC datetime."""
    epoch = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
    if not ts:
        return epoch
    try:
        return datetime.datetime.fromtimestamp(float(ts) + _MAC_EPOCH_OFFSET, datetime.timezone.utc)
    except (OSError, OverflowError, ValueError):
        return epoch


def _open_db(path: str) -> sqlite3.Connection | None:
    """Open a Safari SQLite DB (copies WAL files alongside)."""
    if not os.path.exists(path):
        log.warning(f'Not found: {path}')
        return None
    tmp = tempfile.mkdtemp(prefix='bcat_safari_')
    fname = os.path.basename(path)
    dst = os.path.join(tmp, fname)
    try:
        for suffix in ('', '-wal', '-shm'):
            src = path + suffix
            if os.path.exists(src):
                shutil.copyfile(src, dst + suffix)
        conn = sqlite3.connect(dst)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        log.error(f'Could not open {path}: {e}')
        return None


def default_paths(safari_root: str) -> dict:
    """
    Resolve all Safari data file paths from the Safari root directory
    (typically ~/Library/Safari). Also checks the sandboxed Container path.
    """
    root = Path(safari_root)
    real_home = Path(os.environ.get('REAL_HOME', str(Path.home())))

    # Cookies live in the sandbox container, not ~/Library/Safari
    container_cookies = (
        real_home
        / 'Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies'
    )

    return {
        'history':   str(root / 'History.db'),
        'downloads': str(root / 'Downloads.plist'),
        'bookmarks': str(root / 'Bookmarks.plist'),
        'cookies':   str(container_cookies),
    }


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def parse_safari_history(safari_root: str) -> list[URLItem]:
    """Parse Safari visit history from History.db."""
    paths = default_paths(safari_root)
    conn = _open_db(paths['history'])
    if not conn:
        return []

    results = []
    try:
        sql = """
            SELECT hi.url,
                   hv.title,
                   hv.visit_time,
                   hi.visit_count,
                   hv.load_successful
            FROM history_visits hv
            JOIN history_items hi ON hv.history_item = hi.id
            ORDER BY hv.visit_time DESC
        """
        for row in conn.execute(sql):
            results.append(URLItem(
                url=row['url'] or '',
                title=row['title'] or '',
                visit_time=_mac_to_dt(row['visit_time']),
                last_visit_time=_mac_to_dt(row['visit_time']),
                visit_count=row['visit_count'] or 1,
                typed_count=0,
                transition_friendly='load' if row['load_successful'] else 'failed',
            ))
    except sqlite3.Error as e:
        log.error(f'Error parsing Safari history: {e}')
    finally:
        conn.close()

    log.info(f'Parsed {len(results)} Safari history items')
    return results


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def parse_safari_downloads(safari_root: str) -> list[DownloadItem]:
    """Parse Safari download history from Downloads.plist."""
    paths = default_paths(safari_root)
    dl_path = paths['downloads']

    if not os.path.exists(dl_path):
        log.info('No Safari Downloads.plist found')
        return []

    try:
        with open(dl_path, 'rb') as f:
            data = plistlib.load(f)
    except Exception as e:
        log.error(f'Could not read Downloads.plist: {e}')
        return []

    results = []
    for entry in data.get('DownloadHistory', []):
        total = entry.get('DownloadEntryProgressTotalToLoad', 0)
        received = entry.get('DownloadEntryProgressBytesSoFar', 0)
        state = 'complete' if received == total and total > 0 else 'incomplete'

        start = entry.get('DownloadEntryDateAddedKey')
        end = entry.get('DownloadEntryDateFinishedKey')

        # plistlib returns datetime objects for date fields
        def _ensure_utc(dt):
            if dt is None:
                return None
            if isinstance(dt, datetime.datetime):
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=datetime.timezone.utc)
                return dt.astimezone(datetime.timezone.utc)
            return None

        results.append(DownloadItem(
            url=entry.get('DownloadEntryURL', ''),
            target_path=entry.get('DownloadEntryPath', ''),
            start_time=_ensure_utc(start),
            end_time=_ensure_utc(end),
            received_bytes=received,
            total_bytes=total,
            state=0 if state == 'incomplete' else 1,
            state_friendly=state,
        ))

    log.info(f'Parsed {len(results)} Safari download items')
    return results


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

def parse_safari_bookmarks(safari_root: str) -> list:
    """Parse Safari bookmarks from Bookmarks.plist."""
    paths = default_paths(safari_root)
    bm_path = paths['bookmarks']

    if not os.path.exists(bm_path):
        log.info('No Safari Bookmarks.plist found')
        return []

    try:
        with open(bm_path, 'rb') as f:
            data = plistlib.load(f)
    except Exception as e:
        log.error(f'Could not read Bookmarks.plist: {e}')
        return []

    results = []

    def _mac_date_to_dt(val):
        if val is None:
            return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
        if isinstance(val, datetime.datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=datetime.timezone.utc)
            return val
        return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    def walk(node, parent_label: str):
        bm_type = node.get('WebBookmarkType', '')
        title = node.get('Title', '') or node.get('URIDictionary', {}).get('title', '')

        if bm_type == 'WebBookmarkTypeLeaf':
            url = node.get('URLString', '')
            date_added = _mac_date_to_dt(node.get('dateAdded'))
            results.append(BookmarkItem(
                name=title,
                url=url,
                date_added=date_added,
                parent_folder=parent_label,
            ))

        elif bm_type == 'WebBookmarkTypeList':
            # Skip Reading List and special internal folders
            if title in ('com.apple.ReadingList', 'History'):
                return
            label = f'{parent_label} > {title}' if parent_label else title
            date_added = _mac_date_to_dt(node.get('dateAdded'))
            if parent_label:  # Don't emit the root node itself
                results.append(BookmarkFolderItem(
                    name=title,
                    date_added=date_added,
                    date_modified=None,
                    parent_folder=parent_label,
                ))
            for child in node.get('Children', []):
                walk(child, label)

    for child in data.get('Children', []):
        walk(child, '')

    log.info(f'Parsed {len(results)} Safari bookmark items')
    return results


# ---------------------------------------------------------------------------
# Cookies (BinaryCookies)
# ---------------------------------------------------------------------------

def parse_safari_cookies(safari_root: str) -> list[CookieItem]:
    """
    Parse Safari cookies from the BinaryCookies binary format.

    Format:
      [magic 'cook'][num_pages uint32 BE][page_sizes uint32 BE * n]
      Each page:
        [magic 0x00000100][num_cookies uint32 LE][offsets uint32 LE * n]
        Each cookie:
          [size][unknown][flags][unknown]
          [domain_off][name_off][path_off][value_off][end_hdr]
          [expiry float64 LE Mac epoch][creation float64 LE Mac epoch]
          [null-terminated strings: domain, name, path, value]
    """
    paths = default_paths(safari_root)
    cookies_path = paths['cookies']

    if not os.path.exists(cookies_path):
        log.info(f'No BinaryCookies file found at {cookies_path}')
        return []

    try:
        with open(cookies_path, 'rb') as f:
            data = f.read()
    except OSError as e:
        log.error(f'Could not read Cookies.binarycookies: {e}')
        return []

    if data[:4] != b'cook':
        log.error('Invalid BinaryCookies magic')
        return []

    num_pages = struct.unpack('>I', data[4:8])[0]
    page_sizes = [struct.unpack('>I', data[8 + i*4:12 + i*4])[0] for i in range(num_pages)]

    results = []
    offset = 8 + num_pages * 4

    for page_size in page_sizes:
        page = data[offset:offset + page_size]
        offset += page_size
        _parse_cookie_page(page, results)

    log.info(f'Parsed {len(results)} Safari cookie items')
    return results


def _parse_cookie_page(page: bytes, results: list):
    if len(page) < 8:
        return

    num_cookies = struct.unpack('<I', page[4:8])[0]
    if num_cookies == 0:
        return

    for i in range(num_cookies):
        if 8 + (i + 1) * 4 > len(page):
            break
        cookie_offset = struct.unpack('<I', page[8 + i*4:12 + i*4])[0]
        _parse_one_cookie(page, cookie_offset, results)


def _parse_one_cookie(page: bytes, off: int, results: list):
    if off + 56 > len(page):
        return
    try:
        flags      = struct.unpack('<I', page[off + 8:off + 12])[0]
        domain_off = struct.unpack('<I', page[off + 16:off + 20])[0]
        name_off   = struct.unpack('<I', page[off + 20:off + 24])[0]
        path_off   = struct.unpack('<I', page[off + 24:off + 28])[0]
        value_off  = struct.unpack('<I', page[off + 28:off + 32])[0]
        expiry     = struct.unpack('<d', page[off + 40:off + 48])[0]
        creation   = struct.unpack('<d', page[off + 48:off + 56])[0]
    except struct.error:
        return

    def _str(rel_off):
        abs_off = off + rel_off
        end = page.find(b'\x00', abs_off)
        if end == -1:
            return ''
        return page[abs_off:end].decode('utf-8', errors='replace')

    results.append(CookieItem(
        host_key=_str(domain_off),
        path=_str(path_off),
        name=_str(name_off),
        value=_str(value_off),
        creation_utc=_mac_to_dt(creation),
        last_access_utc=_mac_to_dt(creation),
        expires_utc=_mac_to_dt(expiry) if expiry else None,
        last_update_utc=None,
        secure=bool(flags & 0x1),
        httponly=bool(flags & 0x4),
        persistent=expiry > 0,
        has_expires=expiry > 0,
    ))
