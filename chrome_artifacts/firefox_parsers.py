"""
Firefox artifact parsers for macOS.

Parses: URL history, downloads, cookies, bookmarks from the active Firefox profile.

Default data locations:
  History/Downloads/Bookmarks: ~/Library/Application Support/Firefox/Profiles/<id>.default-release/places.sqlite
  Cookies:                      ~/Library/Application Support/Firefox/Profiles/<id>.default-release/cookies.sqlite
"""
import configparser
import datetime
import json
import logging
import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote

from .artifacts import URLItem, DownloadItem, CookieItem, BookmarkItem, BookmarkFolderItem
from .db import open_db_copy

log = logging.getLogger(__name__)

# Firefox timestamps are microseconds since the Unix epoch.
# Cookies' `expiry` is the exception: plain Unix seconds.
_USEC = 1_000_000


def _ff_to_dt(ts) -> datetime.datetime:
    """Convert a Firefox µs-since-epoch timestamp to a UTC-aware datetime."""
    epoch = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
    if not ts:
        return epoch
    try:
        return datetime.datetime.fromtimestamp(float(ts) / _USEC, datetime.timezone.utc)
    except (OSError, OverflowError, ValueError):
        return epoch


def _open_db(path: str) -> sqlite3.Connection | None:
    """Copy the SQLite DB (and any WAL files) to a temp dir then open it."""
    return open_db_copy(path, prefix='bcat_ff_')


def _places_mtime(profile_dir: Path) -> float:
    """Return the mtime of places.sqlite, or 0 if absent."""
    db = profile_dir / 'places.sqlite'
    try:
        return db.stat().st_mtime if db.exists() else 0.0
    except OSError:
        return 0.0


def default_profile_path() -> str:
    """
    Auto-detect the most recently *active* Firefox profile on macOS.

    Strategy: collect every candidate profile from profiles.ini [InstallXXX]
    sections and from *.default-release / *.default directory globs, then
    rank all of them by the modification time of places.sqlite and return the
    most recently written one.

    Ranking by places.sqlite mtime is more reliable than trusting the ini
    pointer alone — the ini Default= can point at a stale/secondary profile
    that hasn't been used in months while the real active profile lives in a
    different directory.
    """
    real_home = Path(os.environ.get('REAL_HOME', str(Path.home())))
    ff_base = real_home / 'Library/Application Support/Firefox'
    profiles_dir = ff_base / 'Profiles'

    if not profiles_dir.is_dir():
        return str(profiles_dir)

    candidates: list[Path] = []

    # Gather from [InstallXXX] sections in profiles.ini
    ini_path = ff_base / 'profiles.ini'
    if ini_path.exists():
        try:
            cfg = configparser.ConfigParser()
            cfg.read(str(ini_path))
            for section in cfg.sections():
                if section.startswith('Install') and cfg.has_option(section, 'Default'):
                    rel = cfg.get(section, 'Default')
                    candidate = ff_base / rel
                    if candidate.is_dir():
                        candidates.append(candidate)
        except Exception:
            pass

    # Gather from directory globs (catches profiles not in any Install section)
    for pattern in ('*.default-release', '*.default'):
        for p in profiles_dir.glob(pattern):
            if p.is_dir() and p not in candidates:
                candidates.append(p)

    # Also include any other profile directories
    for p in profiles_dir.iterdir():
        if p.is_dir() and p not in candidates:
            candidates.append(p)

    if not candidates:
        return str(profiles_dir)

    # Pick the profile whose places.sqlite was most recently modified —
    # that is definitively the active profile.
    best = max(candidates, key=_places_mtime)
    if _places_mtime(best) == 0:
        # No profile has a places.sqlite — fall back to first candidate
        return str(candidates[0])
    return str(best)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

_VISIT_TYPE_MAP = {
    1: 'link',                    2: 'typed',
    3: 'bookmark',                4: 'embed',
    5: 'redirect (permanent)',    6: 'redirect (temporary)',
    7: 'download',                8: 'framed link',
    9: 'reload',
}


def parse_firefox_history(profile_path: str) -> list[URLItem]:
    """Parse Firefox visit history from places.sqlite."""
    conn = _open_db(os.path.join(profile_path, 'places.sqlite'))
    if not conn:
        return []
    results = []
    try:
        sql = """
            SELECT p.url, p.title, p.visit_count, p.last_visit_date,
                   h.visit_date, h.visit_type
            FROM moz_historyvisits h
            JOIN moz_places p ON h.place_id = p.id
            ORDER BY h.visit_date DESC
        """
        for row in conn.execute(sql):
            results.append(URLItem(
                url=row['url'] or '',
                title=row['title'] or '',
                visit_time=_ff_to_dt(row['visit_date']),
                last_visit_time=_ff_to_dt(row['last_visit_date']),
                visit_count=row['visit_count'] or 1,
                typed_count=0,
                transition_friendly=_VISIT_TYPE_MAP.get(
                    row['visit_type'], str(row['visit_type'])),
            ))
    except sqlite3.Error as e:
        log.error(f'Firefox history error: {e}')
    finally:
        conn.close()
    log.info(f'Parsed {len(results)} Firefox history items')
    return results


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

_DL_STATE_MAP = {
    0: 'in progress', 1: 'complete',  2: 'failed',
    3: 'canceled',    4: 'paused',    5: 'queued',
    6: 'blocked (parental)',          8: 'blocked (policy)',
}


def parse_firefox_downloads(profile_path: str) -> list[DownloadItem]:
    """
    Parse Firefox downloads from places.sqlite annotations.

    Each download has two annotations on the same place_id:
      downloads/metaData          — JSON: state, endTime (ms), fileSize
      downloads/destinationFileURI — file:///path/to/saved/file
    """
    conn = _open_db(os.path.join(profile_path, 'places.sqlite'))
    if not conn:
        return []
    results = []
    try:
        sql = """
            SELECT p.url, p.last_visit_date,
                   MAX(CASE WHEN aa.name = 'downloads/metaData'
                            THEN a.content END) AS meta_json,
                   MAX(CASE WHEN aa.name = 'downloads/destinationFileURI'
                            THEN a.content END) AS dest_uri
            FROM moz_annos a
            JOIN moz_places p           ON a.place_id          = p.id
            JOIN moz_anno_attributes aa ON a.anno_attribute_id = aa.id
            WHERE aa.name IN ('downloads/metaData', 'downloads/destinationFileURI')
            GROUP BY p.id
            ORDER BY p.last_visit_date DESC
        """
        for row in conn.execute(sql):
            try:
                meta = json.loads(row['meta_json'] or '{}')
            except (json.JSONDecodeError, TypeError):
                meta = {}

            state_num = meta.get('state', -1)
            file_size  = int(meta.get('fileSize', 0) or 0)

            end_time = None
            end_ms = meta.get('endTime')
            if end_ms:
                try:
                    end_time = datetime.datetime.fromtimestamp(
                        float(end_ms) / 1000, datetime.timezone.utc)
                except (OSError, OverflowError, ValueError):
                    pass

            target = ''
            dest_uri = row['dest_uri'] or ''
            if dest_uri.startswith('file://'):
                target = unquote(dest_uri[7:])

            results.append(DownloadItem(
                url=row['url'] or '',
                target_path=target,
                start_time=_ff_to_dt(row['last_visit_date']),
                end_time=end_time,
                received_bytes=file_size,
                total_bytes=file_size,
                state=state_num,
                state_friendly=_DL_STATE_MAP.get(state_num, str(state_num)),
            ))
    except sqlite3.Error as e:
        log.error(f'Firefox downloads error: {e}')
    finally:
        conn.close()
    log.info(f'Parsed {len(results)} Firefox download items')
    return results


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------

def parse_firefox_cookies(profile_path: str) -> list[CookieItem]:
    """
    Parse Firefox cookies from cookies.sqlite.

    Firefox does not encrypt cookies on macOS — values are stored as plaintext.
    Timestamps: expiry is Unix seconds; lastAccessed/creationTime are µs.
    """
    conn = _open_db(os.path.join(profile_path, 'cookies.sqlite'))
    if not conn:
        return []
    results = []
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(moz_cookies)')}
        update_col = ', updateTime' if 'updateTime' in cols else ''
        sql = f"""
            SELECT host, path, name, value, expiry,
                   lastAccessed, creationTime, isSecure, isHttpOnly{update_col}
            FROM moz_cookies
        """
        for row in conn.execute(sql):
            expiry = row['expiry']
            try:
                expires_utc = (datetime.datetime.fromtimestamp(
                                   float(expiry), datetime.timezone.utc)
                               if expiry else None)
            except (OSError, OverflowError, ValueError):
                expires_utc = None

            update_ts = row['updateTime'] if 'updateTime' in cols else None

            results.append(CookieItem(
                host_key=row['host'] or '',
                path=row['path'] or '',
                name=row['name'] or '',
                value=row['value'] or '',
                creation_utc=_ff_to_dt(row['creationTime']),
                last_access_utc=_ff_to_dt(row['lastAccessed']),
                expires_utc=expires_utc,
                last_update_utc=_ff_to_dt(update_ts) if update_ts else None,
                secure=bool(row['isSecure']),
                httponly=bool(row['isHttpOnly']),
                persistent=bool(expiry),
                has_expires=bool(expiry),
            ))
    except sqlite3.Error as e:
        log.error(f'Firefox cookies error: {e}')
    finally:
        conn.close()
    log.info(f'Parsed {len(results)} Firefox cookie items')
    return results


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

def parse_firefox_bookmarks(profile_path: str) -> list:
    """Parse Firefox bookmarks from places.sqlite."""
    conn = _open_db(os.path.join(profile_path, 'places.sqlite'))
    if not conn:
        return []
    results = []
    try:
        # Build folder map: id → {title, parent}
        folder_map: dict[int, dict] = {}
        for row in conn.execute(
            'SELECT id, parent, title FROM moz_bookmarks WHERE type = 2'
        ):
            folder_map[row['id']] = {
                'title': row['title'] or '',
                'parent': row['parent'],
            }

        def _folder_path(parent_id: int) -> str:
            parts, visited, cur = [], set(), parent_id
            while cur and cur in folder_map and cur not in visited:
                visited.add(cur)
                t = folder_map[cur]['title']
                if t:
                    parts.append(t)
                cur = folder_map[cur]['parent']
            parts.reverse()
            return ' > '.join(parts)

        # Folder items
        for fid, f in folder_map.items():
            if not f['title']:
                continue
            results.append(BookmarkFolderItem(
                name=f['title'],
                date_added=datetime.datetime.fromtimestamp(0, datetime.timezone.utc),
                date_modified=None,
                parent_folder=_folder_path(f['parent']),
            ))

        # Bookmark items
        sql = """
            SELECT b.title, b.dateAdded, b.parent, p.url
            FROM moz_bookmarks b
            JOIN moz_places p ON b.fk = p.id
            WHERE b.type = 1
            ORDER BY b.dateAdded DESC
        """
        for row in conn.execute(sql):
            results.append(BookmarkItem(
                name=row['title'] or '',
                url=row['url'] or '',
                date_added=_ff_to_dt(row['dateAdded']),
                parent_folder=_folder_path(row['parent']),
            ))
    except sqlite3.Error as e:
        log.error(f'Firefox bookmarks error: {e}')
    finally:
        conn.close()
    log.info(f'Parsed {len(results)} Firefox bookmark items')
    return results
