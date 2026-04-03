"""
Chrome artifact parsers for macOS.

Parses: URL history, downloads, cookies, bookmarks.
"""
import datetime
import json
import logging
import os
import sqlite3

from .artifacts import (
    URLItem, DownloadItem, CookieItem,
    BookmarkItem, BookmarkFolderItem,
)
from .db import open_db, table_columns

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timestamp conversion
# Adapted from hindsight/pyhindsight/utils.py — handles all Chrome timestamp
# formats: WebKit microseconds, epoch seconds/ms/µs, and WebKit seconds.
# ---------------------------------------------------------------------------

def to_datetime(timestamp) -> datetime.datetime:
    """Convert a Chrome timestamp (any format) to a UTC-aware datetime."""
    epoch = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    if isinstance(timestamp, datetime.datetime):
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=datetime.timezone.utc)
        return timestamp

    try:
        timestamp = float(timestamp)
    except (TypeError, ValueError):
        return epoch

    if timestamp == 0:
        return epoch

    try:
        # Very large WebKit µs (past year 9999) — clamp
        if timestamp >= 253402300800000000:
            return datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)
        # WebKit µs > 2038 threshold
        elif timestamp > 13700000000000000:
            return epoch + datetime.timedelta(seconds=(timestamp / 1_000_000) - 11644473600)
        # WebKit µs (17 digits, 1981–2049)
        elif timestamp > 12000000000000000:
            return datetime.datetime.fromtimestamp(
                (timestamp / 1_000_000) - 11644473600, datetime.timezone.utc)
        # Epoch µs (16 digits, 2010–2049)
        elif 2500000000000000 > timestamp > 1280000000000000:
            return datetime.datetime.fromtimestamp(timestamp / 1_000_000, datetime.timezone.utc)
        # Epoch ms (13 digits, 2010–2049)
        elif 2500000000000 > timestamp > 1280000000000:
            return datetime.datetime.fromtimestamp(timestamp / 1_000, datetime.timezone.utc)
        # WebKit seconds (11 digits, 2009–2076)
        elif 15000000000 > timestamp >= 12900000000:
            return datetime.datetime.fromtimestamp(
                timestamp - 11644473600, datetime.timezone.utc)
        # Epoch seconds fallback
        else:
            return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    except (OSError, OverflowError, ValueError):
        return epoch


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

def detect_version(profile_path: str, no_copy: bool = False, temp_dir: str = None) -> list[int]:
    """
    Detect Chrome database version by inspecting table schemas.
    Returns a list of possible version numbers (lowest is most relevant).
    Adapted from hindsight's determine_version() — trimmed to tables we use.
    """
    versions = list(range(1, 146))

    def trim_above(v):
        versions[:] = [x for x in versions if x >= v]

    def trim_if_present(col, columns, v):
        if columns:
            if col in columns:
                versions[:] = [x for x in versions if x >= v]
            else:
                versions[:] = [x for x in versions if x < v]

    conn = open_db(profile_path, 'History', no_copy=no_copy, temp_dir=temp_dir)
    if conn:
        try:
            visits_cols = table_columns(conn, 'visits')
            trim_if_present('visit_duration', visits_cols, 20)
            trim_if_present('incremented_omnibox_typed_score', visits_cols, 68)
            trim_if_present('is_known_to_sync', visits_cols, 107)

            dl_cols = table_columns(conn, 'downloads')
            trim_if_present('target_path', dl_cols, 26)
            trim_if_present('end_time', dl_cols, 30)
            trim_if_present('opened', dl_cols, 16)
        finally:
            conn.close()

    conn = open_db(profile_path, 'Cookies', no_copy=no_copy, temp_dir=temp_dir)
    if conn:
        try:
            cookie_cols = table_columns(conn, 'cookies')
            trim_if_present('top_frame_site_key', cookie_cols, 94)
            trim_if_present('last_update_utc', cookie_cols, 103)
        finally:
            conn.close()

    if not versions:
        versions = [1]

    log.info(f'Detected Chrome version range: {versions[0]}–{versions[-1]}')
    return versions


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

_HISTORY_QUERIES = {
    107: '''SELECT urls.url, urls.title, urls.visit_count, urls.typed_count,
                   urls.last_visit_time, urls.hidden,
                   visits.visit_time, visits.from_visit, visits.visit_duration,
                   visits.transition, visit_source.source
            FROM urls
            JOIN visits ON urls.id = visits.url
            LEFT JOIN visit_source ON visits.id = visit_source.id''',

    59:  '''SELECT urls.url, urls.title, urls.visit_count, urls.typed_count,
                   urls.last_visit_time, urls.hidden,
                   visits.visit_time, visits.from_visit, visits.visit_duration,
                   visits.transition, visit_source.source
            FROM urls JOIN visits ON urls.id = visits.url
            LEFT JOIN visit_source ON visits.id = visit_source.id''',

    7:   '''SELECT urls.url, urls.title, urls.visit_count, urls.typed_count,
                   urls.last_visit_time, urls.hidden,
                   visits.visit_time, visits.from_visit,
                   visits.transition, visit_source.source
            FROM urls JOIN visits ON urls.id = visits.url
            LEFT JOIN visit_source ON visits.id = visit_source.id''',

    1:   '''SELECT urls.url, urls.title, urls.visit_count, urls.typed_count,
                   urls.last_visit_time, urls.hidden,
                   visits.visit_time, visits.from_visit, visits.transition
            FROM urls, visits WHERE urls.id = visits.url''',
}

_TRANSITION_MAP = {
    0: 'link', 1: 'typed', 2: 'auto bookmark', 3: 'auto subframe',
    4: 'manual subframe', 5: 'generated', 6: 'start page', 7: 'form submit',
    8: 'reload', 9: 'keyword', 10: 'keyword generated',
}

_SOURCE_MAP = {0: 'synced', 1: 'browsed', 2: 'extension', 3: 'firefox import',
               4: 'ie import', 5: 'safari import'}


def _resolve_query(queries: dict, version: list[int]) -> tuple[int, str] | tuple[None, None]:
    v = version[0]
    while v > 0:
        if v in queries:
            return v, queries[v]
        v -= 1
    return None, None


def parse_history(profile_path: str, version: list[int],
                  no_copy: bool = False, temp_dir: str = None) -> list[URLItem]:
    """Parse URL visit history from the History database."""
    results = []
    ver, sql = _resolve_query(_HISTORY_QUERIES, version)
    if sql is None:
        log.warning('No compatible history query found')
        return results

    conn = open_db(profile_path, 'History', no_copy=no_copy, temp_dir=temp_dir)
    if not conn:
        return results

    try:
        for row in conn.execute(sql):
            duration = None
            if row.get('visit_duration'):
                duration = str(datetime.timedelta(microseconds=row['visit_duration']))

            raw_t = row.get('transition', 0) or 0
            core_t = raw_t & 0xFF
            friendly = _TRANSITION_MAP.get(core_t, f'transition({core_t})')

            results.append(URLItem(
                url=row['url'],
                title=row.get('title') or '',
                visit_time=to_datetime(row['visit_time']),
                last_visit_time=to_datetime(row.get('last_visit_time')),
                visit_count=row.get('visit_count', 0),
                typed_count=row.get('typed_count', 0),
                transition=row.get('transition'),
                visit_duration=duration,
                transition_friendly=friendly,
                visit_source=_SOURCE_MAP.get(row.get('source'), row.get('source')),
            ))
    except sqlite3.Error as e:
        log.error(f'Error parsing history: {e}')
    finally:
        conn.close()

    log.info(f'Parsed {len(results)} history items')
    return results


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

_DOWNLOAD_QUERIES = {
    58: '''SELECT downloads.id, downloads_url_chains.url, downloads.target_path,
                  downloads.start_time, downloads.end_time, downloads.received_bytes,
                  downloads.total_bytes, downloads.state, downloads.danger_type,
                  downloads.interrupt_reason, downloads.opened
           FROM downloads
           JOIN downloads_url_chains ON downloads.id = downloads_url_chains.id''',

    30: '''SELECT downloads.id, downloads_url_chains.url, downloads.target_path,
                  downloads.start_time, downloads.end_time, downloads.received_bytes,
                  downloads.total_bytes, downloads.state, downloads.danger_type,
                  downloads.interrupt_reason, downloads.opened
           FROM downloads
           JOIN downloads_url_chains ON downloads.id = downloads_url_chains.id''',

    26: '''SELECT downloads.id, downloads_url_chains.url, downloads.target_path,
                  downloads.start_time, downloads.received_bytes, downloads.total_bytes,
                  downloads.state, downloads.danger_type, downloads.interrupt_reason
           FROM downloads
           JOIN downloads_url_chains ON downloads.id = downloads_url_chains.id''',

    1:  '''SELECT id, url, full_path AS target_path, start_time,
                  received_bytes, total_bytes, state
           FROM downloads''',
}

_STATE_MAP = {0: 'in progress', 1: 'complete', 2: 'cancelled', 3: 'bug',
              4: 'interrupted'}

_DANGER_MAP = {0: 'safe', 1: 'dangerous', 2: 'antivirus', 3: 'dangerous url',
               4: 'dangerous content', 5: 'maybe dangerous', 6: 'uncommon',
               7: 'user validated', 8: 'dangerous host', 9: 'potentially unwanted'}


def parse_downloads(profile_path: str, version: list[int],
                    no_copy: bool = False, temp_dir: str = None) -> list[DownloadItem]:
    """Parse download records from the History database."""
    results = []
    ver, sql = _resolve_query(_DOWNLOAD_QUERIES, version)
    if sql is None:
        log.warning('No compatible downloads query found')
        return results

    conn = open_db(profile_path, 'History', no_copy=no_copy, temp_dir=temp_dir)
    if not conn:
        return results

    try:
        for row in conn.execute(sql):
            target = row.get('target_path') or row.get('current_path') or ''
            if isinstance(target, bytes):
                target = target.decode('utf-8', errors='replace')

            results.append(DownloadItem(
                url=row.get('url', ''),
                target_path=target,
                start_time=to_datetime(row.get('start_time')),
                end_time=to_datetime(row.get('end_time')) if row.get('end_time') else None,
                received_bytes=row.get('received_bytes', 0),
                total_bytes=row.get('total_bytes', 0),
                state=row.get('state', 0),
                state_friendly=_STATE_MAP.get(row.get('state'), str(row.get('state'))),
                danger_type=row.get('danger_type'),
                interrupt_reason=row.get('interrupt_reason'),
                opened=row.get('opened'),
            ))
    except sqlite3.Error as e:
        log.error(f'Error parsing downloads: {e}')
    finally:
        conn.close()

    log.info(f'Parsed {len(results)} download items')
    return results


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------

_COOKIE_QUERIES = {
    103: '''SELECT host_key, path, name, value, creation_utc, last_access_utc,
                   expires_utc, last_update_utc, is_secure AS secure,
                   is_httponly AS httponly, is_persistent AS persistent,
                   has_expires, priority, encrypted_value, top_frame_site_key
            FROM cookies''',

    94:  '''SELECT host_key, path, name, value, creation_utc, last_access_utc,
                   expires_utc, is_secure AS secure, is_httponly AS httponly,
                   is_persistent AS persistent, has_expires, priority,
                   encrypted_value, top_frame_site_key
            FROM cookies''',

    66:  '''SELECT host_key, path, name, value, creation_utc, last_access_utc,
                   expires_utc, is_secure AS secure, is_httponly AS httponly,
                   is_persistent AS persistent, has_expires, priority, encrypted_value
            FROM cookies''',

    33:  '''SELECT host_key, path, name, value, creation_utc, last_access_utc,
                   expires_utc, secure, httponly, persistent, has_expires,
                   priority, encrypted_value
            FROM cookies''',

    1:   '''SELECT host_key, path, name, value, creation_utc, last_access_utc,
                   expires_utc, secure, httponly
            FROM cookies''',
}


def parse_cookies(profile_path: str, version: list[int],
                  decryptor=None,
                  no_copy: bool = False, temp_dir: str = None) -> list[CookieItem]:
    """
    Parse cookies from the Cookies database.
    Pass a MacDecryptor instance as `decryptor` to decrypt encrypted values.
    """
    results = []
    ver, sql = _resolve_query(_COOKIE_QUERIES, version)
    if sql is None:
        log.warning('No compatible cookies query found')
        return results

    conn = open_db(profile_path, 'Cookies', no_copy=no_copy, temp_dir=temp_dir)
    if not conn:
        return results

    try:
        for row in conn.execute(sql):
            enc = row.get('encrypted_value')
            if enc and len(enc) >= 2:
                if decryptor:
                    value = decryptor.decrypt(enc) or row.get('value', '')
                else:
                    value = '<encrypted>'
            else:
                value = row.get('value', '')

            results.append(CookieItem(
                host_key=row['host_key'],
                path=row['path'],
                name=row['name'],
                value=value,
                creation_utc=to_datetime(row['creation_utc']),
                last_access_utc=to_datetime(row['last_access_utc']),
                expires_utc=to_datetime(row.get('expires_utc')) if row.get('expires_utc') else None,
                last_update_utc=to_datetime(row.get('last_update_utc')) if row.get('last_update_utc') else None,
                secure=bool(row.get('secure')),
                httponly=bool(row.get('httponly')),
                persistent=row.get('persistent'),
                has_expires=row.get('has_expires'),
                top_frame_site_key=row.get('top_frame_site_key'),
            ))
    except sqlite3.Error as e:
        log.error(f'Error parsing cookies: {e}')
    finally:
        conn.close()

    log.info(f'Parsed {len(results)} cookie items')
    return results


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

def parse_bookmarks(profile_path: str,
                    version: list[int] = None) -> list[BookmarkItem | BookmarkFolderItem]:
    """Parse bookmarks from the Bookmarks JSON file."""
    results = []
    bookmarks_path = os.path.join(profile_path, 'Bookmarks')

    if not os.path.exists(bookmarks_path):
        log.info('No Bookmarks file found')
        return results

    try:
        with open(bookmarks_path, encoding='utf-8', errors='replace') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.error(f'Could not read Bookmarks: {e}')
        return results

    def walk(parent_label: str, children: list):
        for child in children:
            if child.get('type') == 'url':
                results.append(BookmarkItem(
                    name=child.get('name', ''),
                    url=child.get('url', ''),
                    date_added=to_datetime(child.get('date_added')),
                    parent_folder=parent_label,
                ))
            elif child.get('type') == 'folder':
                label = f"{parent_label} > {child.get('name', '')}"
                results.append(BookmarkFolderItem(
                    name=child.get('name', ''),
                    date_added=to_datetime(child.get('date_added')),
                    date_modified=child.get('date_modified'),
                    parent_folder=parent_label,
                ))
                if child.get('children'):
                    walk(label, child['children'])

    roots = data.get('roots', {})
    for root_key, root_val in roots.items():
        if isinstance(root_val, dict) and root_val.get('children'):
            folder_name = root_val.get('name', root_key)
            walk(folder_name, root_val['children'])

    log.info(f'Parsed {len(results)} bookmark items')
    return results
