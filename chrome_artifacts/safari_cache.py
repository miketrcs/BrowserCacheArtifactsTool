"""
Safari Cache image extractor (macOS).

Safari stores its HTTP cache in a SQLite database (Cache.db) with two key tables:
  cfurl_cache_response      — entry_ID, request_key (URL), time_stamp
  cfurl_cache_receiver_data — entry_ID, isDataOnFS (0=blob, 1=on-disk), receiver_data BLOB

Images are extracted from receiver_data BLOBs and validated with PIL.
"""
import io
import logging
import os
import shutil
import sqlite3
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

IMAGE_SIGNATURES = {
    b'\xff\xd8\xff':       'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'GIF89a':             'image/gif',
    b'GIF87a':             'image/gif',
    b'RIFF':               'image/webp',
    b'<svg':               'image/svg+xml',
}

IMAGE_URL_HINTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico', '.svg', '.bmp')


@dataclass
class SafariCachedImage:
    url: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    data: bytes


def _detect_mime(data: bytes) -> Optional[str]:
    for sig, mime in IMAGE_SIGNATURES.items():
        if data.startswith(sig):
            if mime == 'image/webp':
                if len(data) > 12 and data[8:12] == b'WEBP':
                    return mime
            else:
                return mime
    return None


def _validate_image(raw: bytes) -> Optional[tuple[int, int]]:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        return w, h
    except Exception:
        return None


def _open_cache_db(path: str) -> Optional[sqlite3.Connection]:
    if not os.path.exists(path):
        log.warning(f'Safari Cache.db not found: {path}')
        return None
    tmp = tempfile.mkdtemp(prefix='bcat_safari_cache_')
    dst = os.path.join(tmp, 'Cache.db')
    try:
        for suffix in ('', '-wal', '-shm'):
            src = path + suffix
            if os.path.exists(src):
                shutil.copyfile(src, dst + suffix)
        conn = sqlite3.connect(dst)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        log.error(f'Could not open Safari Cache.db: {e}')
        return None


def scan_safari_cache(safari_root: str,
                      url_filter: str = '',
                      min_width: int = 0,
                      min_height: int = 0,
                      max_results: int = 500) -> list[SafariCachedImage]:
    """
    Scan Safari's Cache.db for cached images.

    Args:
        safari_root: Path to Safari data root (typically ~/Library/Safari).
                     The cache lives in the adjacent Containers path.
        url_filter:  Optional substring to filter URLs (case-insensitive).
        min_width:   Minimum image width in pixels (0 = no filter).
        min_height:  Minimum image height in pixels (0 = no filter).
        max_results: Maximum number of results to return.

    Returns:
        List of SafariCachedImage objects, largest-first by byte size.
    """
    real_home = Path(os.environ.get('REAL_HOME', str(Path.home())))
    cache_db_path = str(
        real_home
        / 'Library/Containers/com.apple.Safari/Data/Library/Caches/com.apple.Safari/Cache.db'
    )

    conn = _open_cache_db(cache_db_path)
    if not conn:
        return []

    results: list[SafariCachedImage] = []
    try:
        sql = """
            SELECT r.request_key AS url,
                   d.receiver_data
            FROM cfurl_cache_response r
            JOIN cfurl_cache_receiver_data d ON r.entry_ID = d.entry_ID
            WHERE d.isDataOnFS = 0
              AND d.receiver_data IS NOT NULL
        """
        for row in conn.execute(sql):
            url: str = row['url'] or ''
            blob: bytes = row['receiver_data']

            if not blob or len(blob) < 8:
                continue

            url_lower = url.lower()

            # Pre-filter: skip entries that clearly aren't images
            if not any(hint in url_lower for hint in IMAGE_URL_HINTS):
                if _detect_mime(blob) is None:
                    continue

            if url_filter and url_filter.lower() not in url_lower:
                continue

            mime = _detect_mime(blob)
            if mime is None:
                continue

            dims = _validate_image(blob)
            if dims is None:
                continue

            w, h = dims
            if min_width and w < min_width:
                continue
            if min_height and h < min_height:
                continue

            results.append(SafariCachedImage(
                url=url,
                mime_type=mime,
                width=w,
                height=h,
                size_bytes=len(blob),
                data=blob,
            ))

    except sqlite3.Error as e:
        log.error(f'Error scanning Safari Cache.db: {e}')
    finally:
        conn.close()

    results.sort(key=lambda x: x.size_bytes, reverse=True)
    log.info(f'Found {len(results)} Safari cached images')
    return results[:max_results]


def default_safari_cache_path(safari_root: str) -> str:
    """Return the Safari Cache.db path for the given Safari root."""
    real_home = Path(os.environ.get('REAL_HOME', str(Path.home())))
    return str(
        real_home
        / 'Library/Containers/com.apple.Safari/Data/Library/Caches/com.apple.Safari/Cache.db'
    )
