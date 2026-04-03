"""
Safari Cache image extractor (macOS).

Safari maintains two caches:

1. Cache.db (SQLite) — small Apple-service responses (thumbnails, plists).
   cfurl_cache_receiver_data: isDataOnFS=0 → inline BLOB,
                              isDataOnFS=1 → UUID filename in fsCachedData/.

2. WebKitCache/Version N/ — the full HTTP disk cache for all web browsing.
   Blobs/    — raw response bodies named by SHA-1 content hash.
   Records/  — per-origin subdirectories; each Resource/ file is a binary
               metadata record containing the original URL and, somewhere in
               its body, the 20-byte SHA-1 hash of the corresponding Blob.

Strategy:
  • Build a blob_hash→URL index by scanning every metadata file once.
  • Scan Blobs/ for image magic bytes; look up URLs from the index.
  • Also scan Cache.db (inline + fsCachedData) as before.
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


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

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
        return img.size  # (width, height)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cache.db helpers
# ---------------------------------------------------------------------------

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


def _read_blob(row, fs_data_dir: Path) -> Optional[bytes]:
    """Return the raw response body: inline BLOB or on-disk UUID file."""
    raw: bytes = row['receiver_data']
    if not raw:
        return None
    if row['isDataOnFS'] == 0:
        return raw
    # isDataOnFS=1: receiver_data is the UUID filename as ASCII
    try:
        uuid_name = raw.decode('ascii', errors='ignore').strip()
    except Exception:
        return None
    fp = fs_data_dir / uuid_name
    if not fp.exists():
        return None
    try:
        return fp.read_bytes()
    except OSError:
        return None


def _scan_cache_db(cache_base: Path, url_filter: str,
                   min_width: int, min_height: int) -> list[SafariCachedImage]:
    """Scan Cache.db (Apple-service cache) for images."""
    cache_db_path = str(cache_base / 'Cache.db')
    fs_data_dir   = cache_base / 'fsCachedData'

    conn = _open_cache_db(cache_db_path)
    if not conn:
        return []

    results: list[SafariCachedImage] = []
    try:
        sql = """
            SELECT r.request_key AS url, d.isDataOnFS, d.receiver_data
            FROM cfurl_cache_response r
            JOIN cfurl_cache_receiver_data d ON r.entry_ID = d.entry_ID
            WHERE d.receiver_data IS NOT NULL
        """
        for row in conn.execute(sql):
            url: str = row['url'] or ''
            if url_filter and url_filter.lower() not in url.lower():
                continue
            blob = _read_blob(row, fs_data_dir)
            if not blob or len(blob) < 8:
                continue
            mime = _detect_mime(blob)
            if mime is None:
                continue
            dims = _validate_image(blob)
            if dims is None:
                continue
            w, h = dims
            if (min_width and w < min_width) or (min_height and h < min_height):
                continue
            results.append(SafariCachedImage(
                url=url, mime_type=mime, width=w, height=h,
                size_bytes=len(blob), data=blob,
            ))
    except sqlite3.Error as e:
        log.error(f'Error scanning Safari Cache.db: {e}')
    finally:
        conn.close()

    return results


# ---------------------------------------------------------------------------
# WebKitCache helpers
# ---------------------------------------------------------------------------

def _extract_url_from_meta(data: bytes) -> Optional[str]:
    """
    Parse the binary metadata format:
      [uint32][uint32 part_len][0x01][partition]
      [uint32 type_len][0x01][type]
      [uint32 url_len][0x01][url bytes — UTF-8 or UTF-16-LE]
    """
    try:
        off = 4
        part_len = struct.unpack_from('<I', data, off)[0]
        if part_len > 512:
            return None
        off += 4 + 1 + part_len
        type_len = struct.unpack_from('<I', data, off)[0]
        if type_len > 64:
            return None
        off += 4 + 1 + type_len
        url_len = struct.unpack_from('<I', data, off)[0]
        if url_len == 0 or url_len > 8192:
            return None
        off += 4 + 1
        raw = data[off:off + url_len]
        # Try UTF-8
        try:
            url = raw.decode('utf-8')
            if url.startswith('http'):
                return url
        except Exception:
            pass
        # Try UTF-16-LE (some records store URLs this way)
        if len(raw) % 2 == 0:
            try:
                url = raw.decode('utf-16-le')
                if url.startswith('http'):
                    return url
            except Exception:
                pass
    except Exception:
        pass
    return None


def _build_webkit_index(webkit_cache: Path) -> dict[str, str]:
    """
    Return {blob_filename: url} by scanning all WebKitCache metadata files.

    Each metadata file's binary body may contain the raw 20-byte SHA-1 of its
    corresponding Blob anywhere after the URL field.  We scan every offset for
    known blob hashes — O(metadata_files × file_size) rather than O(n²).
    """
    blobs_dir = webkit_cache / 'Blobs'
    if not blobs_dir.is_dir():
        return {}

    # Build set of known blob SHA-1 raw bytes
    blob_set: dict[bytes, str] = {}  # raw 20-byte hash → filename
    for p in blobs_dir.iterdir():
        try:
            blob_set[bytes.fromhex(p.name)] = p.name
        except ValueError:
            pass

    if not blob_set:
        return {}

    index: dict[str, str] = {}  # blob filename → url

    for meta_path in webkit_cache.glob('Records/*/Resource/*'):
        if meta_path.name.endswith('-blob'):
            continue
        try:
            data = meta_path.read_bytes()
        except OSError:
            continue

        url = _extract_url_from_meta(data)
        if not url:
            continue

        # Slide a 20-byte window through the file looking for blob hashes
        for i in range(len(data) - 19):
            candidate = data[i:i + 20]
            fname = blob_set.get(candidate)
            if fname and fname not in index:
                index[fname] = url

    log.debug(f'WebKitCache index: {len(index)} blob→URL mappings')
    return index


def _scan_webkit_cache(webkit_cache: Path, url_filter: str,
                       min_width: int, min_height: int) -> list[SafariCachedImage]:
    """Scan WebKitCache/Version N/Blobs/ for cached images."""
    # Find the latest version directory
    version_dir = None
    for child in sorted(webkit_cache.iterdir(), reverse=True):
        if child.name.startswith('Version') and child.is_dir():
            version_dir = child
            break

    if version_dir is None:
        log.info('No WebKitCache version directory found')
        return []

    log.info(f'Scanning {version_dir}')
    index = _build_webkit_index(version_dir)

    results: list[SafariCachedImage] = []
    blobs_dir = version_dir / 'Blobs'
    if not blobs_dir.is_dir():
        return []

    for blob_path in blobs_dir.iterdir():
        try:
            data = blob_path.read_bytes()
        except OSError:
            continue

        mime = _detect_mime(data)
        if mime is None:
            continue

        url = index.get(blob_path.name, '')
        if url_filter and url_filter.lower() not in url.lower():
            # Still include if URL unknown (url_filter acts as optional hint)
            if url:
                continue

        dims = _validate_image(data)
        if dims is None:
            continue

        w, h = dims
        if (min_width and w < min_width) or (min_height and h < min_height):
            continue

        results.append(SafariCachedImage(
            url=url or f'[unknown — {blob_path.name[:16]}…]',
            mime_type=mime,
            width=w,
            height=h,
            size_bytes=len(data),
            data=data,
        ))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_safari_cache(safari_root: str,
                      url_filter: str = '',
                      min_width: int = 0,
                      min_height: int = 0,
                      max_results: int = 500) -> list[SafariCachedImage]:
    """
    Scan all Safari HTTP caches for cached images.

    Searches both Cache.db (Apple-service cache) and WebKitCache (full browser
    cache).  Returns up to max_results images sorted largest-first.
    """
    real_home = Path(os.environ.get('REAL_HOME', str(Path.home())))
    container = real_home / 'Library/Containers/com.apple.Safari/Data/Library/Caches/com.apple.Safari'

    results: list[SafariCachedImage] = []

    # 1. Cache.db
    results.extend(_scan_cache_db(container, url_filter, min_width, min_height))

    # 2. WebKitCache
    webkit_cache = container / 'WebKitCache'
    if webkit_cache.is_dir():
        results.extend(_scan_webkit_cache(webkit_cache, url_filter, min_width, min_height))
    else:
        log.info('No WebKitCache directory found')

    results.sort(key=lambda x: x.size_bytes, reverse=True)
    log.info(f'Found {len(results)} Safari cached images total')
    return results[:max_results]


def default_safari_cache_path(safari_root: str) -> str:
    """Return the Safari Cache.db path for the given Safari root."""
    real_home = Path(os.environ.get('REAL_HOME', str(Path.home())))
    return str(
        real_home
        / 'Library/Containers/com.apple.Safari/Data/Library/Caches/com.apple.Safari/Cache.db'
    )
