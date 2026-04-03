"""
Output formatters: rich terminal display and SQLite export.
"""
import datetime
import logging
import sqlite3

from rich.console import Console
from rich.table import Table
from rich import box

from .artifacts import URLItem, DownloadItem, CookieItem, BookmarkItem, BookmarkFolderItem

log = logging.getLogger(__name__)
console = Console()


def _fmt_dt(dt) -> str:
    if dt is None:
        return ''
    if isinstance(dt, datetime.datetime):
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    return str(dt)


def _truncate(s: str, n: int) -> str:
    if not s:
        return ''
    return s if len(s) <= n else s[:n - 1] + '…'


# ---------------------------------------------------------------------------
# Rich terminal display
# ---------------------------------------------------------------------------

def display_history(items: list[URLItem], limit: int = 50):
    t = Table(title=f'URL History  ({len(items)} total, showing {min(limit, len(items))})',
              box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column('Visited', style='cyan', no_wrap=True, width=19)
    t.add_column('Title', style='white', max_width=35)
    t.add_column('URL', style='blue', max_width=55)
    t.add_column('Visits', justify='right', style='green', width=6)
    t.add_column('Transition', style='dim', width=14)

    for item in sorted(items, key=lambda x: x.visit_time, reverse=True)[:limit]:
        t.add_row(
            _fmt_dt(item.visit_time),
            _truncate(item.title, 35),
            _truncate(item.url, 55),
            str(item.visit_count),
            item.transition_friendly or '',
        )
    console.print(t)


def display_downloads(items: list[DownloadItem], limit: int = 50):
    t = Table(title=f'Downloads  ({len(items)} total, showing {min(limit, len(items))})',
              box=box.SIMPLE_HEAD)
    t.add_column('Started', style='cyan', no_wrap=True, width=19)
    t.add_column('URL', style='blue', max_width=45)
    t.add_column('Saved To', style='white', max_width=40)
    t.add_column('Size', justify='right', style='green', width=10)
    t.add_column('State', style='yellow', width=12)

    for item in sorted(items, key=lambda x: x.start_time or datetime.datetime.min, reverse=True)[:limit]:
        size = f'{item.total_bytes:,}' if item.total_bytes else ''
        t.add_row(
            _fmt_dt(item.start_time),
            _truncate(item.url, 45),
            _truncate(item.target_path, 40),
            size,
            item.state_friendly or '',
        )
    console.print(t)


def display_cookies(items: list[CookieItem], limit: int = 50):
    t = Table(title=f'Cookies  ({len(items)} total, showing {min(limit, len(items))})',
              box=box.SIMPLE_HEAD)
    t.add_column('Created', style='cyan', no_wrap=True, width=19)
    t.add_column('Host', style='blue', max_width=30)
    t.add_column('Name', style='white', max_width=30)
    t.add_column('Value', style='dim', max_width=35)
    t.add_column('Secure', justify='center', width=6)
    t.add_column('Expires', style='dim', width=19)

    for item in sorted(items, key=lambda x: x.creation_utc, reverse=True)[:limit]:
        t.add_row(
            _fmt_dt(item.creation_utc),
            _truncate(item.host_key, 30),
            _truncate(item.name, 30),
            _truncate(item.value, 35),
            '✓' if item.secure else '',
            _fmt_dt(item.expires_utc),
        )
    console.print(t)


def display_bookmarks(items: list, limit: int = 100):
    t = Table(title=f'Bookmarks  ({len(items)} total, showing {min(limit, len(items))})',
              box=box.SIMPLE_HEAD)
    t.add_column('Added', style='cyan', no_wrap=True, width=19)
    t.add_column('Name', style='white', max_width=35)
    t.add_column('URL', style='blue', max_width=50)
    t.add_column('Folder', style='dim', max_width=30)

    url_items = [x for x in items if isinstance(x, BookmarkItem)]
    for item in sorted(url_items, key=lambda x: x.date_added, reverse=True)[:limit]:
        t.add_row(
            _fmt_dt(item.date_added),
            _truncate(item.name, 35),
            _truncate(item.url, 50),
            _truncate(item.parent_folder, 30),
        )
    console.print(t)


# ---------------------------------------------------------------------------
# SQLite export
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT,
    title           TEXT,
    visit_time      TEXT,
    last_visit_time TEXT,
    visit_count     INTEGER,
    typed_count     INTEGER,
    transition      TEXT,
    visit_duration  TEXT,
    visit_source    TEXT
);

CREATE TABLE IF NOT EXISTS downloads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT,
    target_path     TEXT,
    start_time      TEXT,
    end_time        TEXT,
    received_bytes  INTEGER,
    total_bytes     INTEGER,
    state           TEXT,
    danger_type     TEXT,
    opened          INTEGER
);

CREATE TABLE IF NOT EXISTS cookies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    host_key            TEXT,
    path                TEXT,
    name                TEXT,
    value               TEXT,
    creation_utc        TEXT,
    last_access_utc     TEXT,
    expires_utc         TEXT,
    last_update_utc     TEXT,
    secure              INTEGER,
    httponly            INTEGER,
    persistent          INTEGER,
    has_expires         INTEGER,
    top_frame_site_key  TEXT
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT,
    url             TEXT,
    date_added      TEXT,
    parent_folder   TEXT,
    item_type       TEXT
);
"""


def export_sqlite(path: str,
                  history=None, downloads=None,
                  cookies=None, bookmarks=None):
    """Write parsed artifacts to a SQLite database at `path`."""
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)

    if history:
        conn.executemany(
            'INSERT INTO history (url, title, visit_time, last_visit_time, visit_count, '
            'typed_count, transition, visit_duration, visit_source) VALUES (?,?,?,?,?,?,?,?,?)',
            [(i.url, i.title, _fmt_dt(i.visit_time), _fmt_dt(i.last_visit_time),
              i.visit_count, i.typed_count, i.transition_friendly,
              i.visit_duration, str(i.visit_source or ''))
             for i in history]
        )

    if downloads:
        conn.executemany(
            'INSERT INTO downloads (url, target_path, start_time, end_time, received_bytes, '
            'total_bytes, state, danger_type, opened) VALUES (?,?,?,?,?,?,?,?,?)',
            [(i.url, i.target_path, _fmt_dt(i.start_time), _fmt_dt(i.end_time),
              i.received_bytes, i.total_bytes, i.state_friendly,
              _DANGER_MAP_STR.get(i.danger_type, str(i.danger_type)), i.opened)
             for i in downloads]
        )

    if cookies:
        conn.executemany(
            'INSERT INTO cookies (host_key, path, name, value, creation_utc, last_access_utc, '
            'expires_utc, last_update_utc, secure, httponly, persistent, has_expires, '
            'top_frame_site_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(i.host_key, i.path, i.name, i.value,
              _fmt_dt(i.creation_utc), _fmt_dt(i.last_access_utc),
              _fmt_dt(i.expires_utc), _fmt_dt(i.last_update_utc),
              int(i.secure), int(i.httponly),
              i.persistent, i.has_expires, i.top_frame_site_key)
             for i in cookies]
        )

    if bookmarks:
        conn.executemany(
            'INSERT INTO bookmarks (name, url, date_added, parent_folder, item_type) VALUES (?,?,?,?,?)',
            [(i.name,
              getattr(i, 'url', ''),
              _fmt_dt(i.date_added),
              i.parent_folder,
              i.row_type)
             for i in bookmarks]
        )

    conn.commit()
    conn.close()
    log.info(f'Exported artifacts to {path}')


_DANGER_MAP_STR = {
    0: 'safe', 1: 'dangerous', 2: 'antivirus', 3: 'dangerous url',
    4: 'dangerous content', 5: 'maybe dangerous', 6: 'uncommon',
    7: 'user validated', 8: 'dangerous host', 9: 'potentially unwanted',
}
