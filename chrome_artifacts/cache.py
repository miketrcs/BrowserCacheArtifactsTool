"""
Chrome Simple Cache image extractor (macOS).

Chrome stores its HTTP cache in Simple Cache format. Each `_0` file contains:
  [24-byte header][key (URL)][stream body][EOF magic][response info][EOF magic][trailer]

Image body data starts immediately after the key and ends just before the
first SimpleFileEOF magic marker. PIL is used to validate each candidate.
"""
import datetime
import io
import logging
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SIMPLE_HEADER_MAGIC = 0xFCFB6D1BA7725C30
SIMPLE_EOF_MAGIC_BYTES = struct.pack('<I', 0xF4FA6F45)

IMAGE_SIGNATURES = {
    b'\xff\xd8\xff':       'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'GIF89a':             'image/gif',
    b'GIF87a':             'image/gif',
    b'RIFF':               'image/webp',   # need to confirm bytes 8-12 = WEBP
    b'<svg':               'image/svg+xml',
}

# AVIF brands (ISOBMFF ftyp box at offset 4): bytes[4:8] == b'ftyp', bytes[8:12] is the brand
_AVIF_BRANDS = {b'avif', b'avis', b'MA1A', b'MiHA'}

# Extensions that imply image content in the URL
IMAGE_URL_HINTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico', '.svg', '.bmp', '.avif')


@dataclass
class CachedImage:
    filename: str          # cache file name (hex hash)
    url: str               # original URL
    mime_type: str
    width: int
    height: int
    size_bytes: int
    data: bytes            # raw image bytes
    cached_at: Optional[datetime.datetime] = None  # HTTP Date: header, or file mtime as fallback


def _detect_sig(data: bytes, offset: int) -> Optional[str]:
    """Return MIME type if data at offset matches a known image signature."""
    chunk = data[offset:]
    for sig, mime in IMAGE_SIGNATURES.items():
        if chunk.startswith(sig):
            if mime == 'image/webp':
                # Confirm WEBP fourcc at bytes 8-12
                if len(chunk) > 12 and chunk[8:12] == b'WEBP':
                    return mime
            else:
                return mime
    # AVIF: ISOBMFF ftyp box — 4-byte size, then 'ftyp', then major brand
    if len(chunk) >= 12 and chunk[4:8] == b'ftyp' and chunk[8:12] in _AVIF_BRANDS:
        return 'image/avif'
    return None


def _validate_image(raw: bytes) -> Optional[tuple[int, int, str]]:
    """
    Try to open raw bytes as a PIL Image.
    Returns (width, height, format) or None if invalid.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        fmt = img.format or 'unknown'
        w, h = img.size
        return w, h, fmt
    except Exception:
        return None


_HTTP_DATE_RE = re.compile(
    rb'Date: ([A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT)'
)


def _extract_http_date(data: bytes) -> Optional[datetime.datetime]:
    """
    Extract the HTTP Date: response header from a cache entry's raw bytes.
    This header is written once when the entry is created and never changes,
    unlike the file mtime which Chrome may update when it rewrites entries.
    Returns a UTC datetime, or None if the header is absent or malformed.
    """
    m = _HTTP_DATE_RE.search(data)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(
            m.group(1).decode('ascii'), '%a, %d %b %Y %H:%M:%S GMT'
        ).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, UnicodeDecodeError):
        return None


def _extract_image(data: bytes, body_off: int) -> Optional[tuple[bytes, str, int, int]]:
    """
    Extract image bytes from a cache file's body region.

    Strategy: image data begins at body_off. We scan forward for the
    SimpleFileEOF magic and try each candidate endpoint with PIL.

    Returns (image_bytes, mime_type, width, height) or None.
    """
    mime = _detect_sig(data, body_off)
    if mime is None:
        return None

    # Find all EOF magic positions after body_off
    search_start = body_off
    eof_positions = []
    while True:
        idx = data.find(SIMPLE_EOF_MAGIC_BYTES, search_start)
        if idx == -1:
            break
        eof_positions.append(idx)
        search_start = idx + 1

    # Try each EOF position as the end of the image chunk
    for eof_pos in eof_positions:
        chunk = data[body_off:eof_pos]
        if len(chunk) < 8:
            continue
        result = _validate_image(chunk)
        if result:
            w, h, _ = result
            return chunk, mime, w, h

    return None


def scan_cache(cache_path: str,
               url_filter: str = '',
               min_width: int = 0,
               min_height: int = 0,
               max_results: int = 500,
               scan_limit: int = 30000) -> list[CachedImage]:
    """
    Scan Chrome's Simple Cache directory for cached images.

    Chrome's cache can contain 100K+ entries.  A two-phase approach keeps
    the scan fast while giving date-diverse results:

      Phase 1 — stat only (no reads): list all _0 files, sort by mtime
        descending.  Optionally cap at scan_limit files (default 30 000)
        to bound I/O time; pass scan_limit=0 to scan everything (~50 s on
        a large cache).

      Phase 2 — full read + PIL validation on the selected files.

    Cache date: uses the HTTP Date: response header when present (written
    once at fetch time, unaffected by Chrome rewriting the entry on access).
    Falls back to file mtime when the header is absent.
    """
    cache_dir = Path(cache_path)
    if not cache_dir.is_dir():
        log.warning(f'Cache directory not found: {cache_path}')
        return []

    # Phase 1: stat ALL _0 files — no reads, just mtime (fast even for 174 K files).
    # Build a per-day bucket of (mtime, fpath, fname) tuples, then sample
    # scan_limit / num_days files from each day so we cover the full date
    # range instead of saturating on the most-recently-modified entries.
    day_file_map: dict[str, list[tuple[float, Path, str]]] = {}
    for fname in os.listdir(cache_dir):
        if not fname.endswith('_0'):
            continue
        fpath = cache_dir / fname
        try:
            mtime = fpath.stat().st_mtime
        except OSError:
            continue
        day = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).strftime('%Y-%m-%d')
        day_file_map.setdefault(day, []).append((mtime, fpath, fname))

    total_files = sum(len(v) for v in day_file_map.items())  # noqa: SIM118 (compat)
    total_files = sum(len(v) for v in day_file_map.values())
    num_days = len(day_file_map)

    # How many files to read per day — divvy up scan_limit evenly across days
    per_day_scan = max(50, (scan_limit // num_days) if (scan_limit and num_days) else 500)

    selected: list[tuple[float, Path, str]] = []
    for day_files in day_file_map.values():
        # Within each day, prefer largest files (they're more likely to be real images)
        day_files.sort(key=lambda x: x[0], reverse=True)  # newest-within-day first
        selected.extend(day_files[:per_day_scan])

    total_to_scan = len(selected)
    log.info(f'Cache stat pass: {total_files} files across {num_days} days; '
             f'reading {total_to_scan} ({per_day_scan}/day)')

    # Phase 2: read + validate
    buckets: dict[str, list[CachedImage]] = {}

    for _mtime, fpath, fname in selected:
        try:
            with open(fpath, 'rb') as fh:
                data = fh.read()
        except OSError:
            continue

        if len(data) < 60:
            continue

        try:
            hdr_magic, version, key_len = struct.unpack_from('<QII', data, 0)
            if hdr_magic != SIMPLE_HEADER_MAGIC:
                continue
            if 24 + key_len > len(data):
                continue

            url = data[24:24 + key_len].decode('utf-8', errors='replace')
            url_lower = url.lower()

            if not any(hint in url_lower for hint in IMAGE_URL_HINTS):
                body_off = 24 + key_len
                if _detect_sig(data, body_off) is None:
                    continue

            if url_filter and url_filter.lower() not in url_lower:
                continue

            body_off = 24 + key_len
            extracted = _extract_image(data, body_off)
            if extracted is None:
                continue

            img_bytes, mime, w, h = extracted

            if min_width and w < min_width:
                continue
            if min_height and h < min_height:
                continue

            cached_at = (_extract_http_date(data)
                         or datetime.datetime.fromtimestamp(_mtime, datetime.timezone.utc))

            day = cached_at.strftime('%Y-%m-%d')
            buckets.setdefault(day, []).append(CachedImage(
                filename=fname,
                url=url,
                mime_type=mime,
                width=w,
                height=h,
                size_bytes=len(img_bytes),
                data=img_bytes,
                cached_at=cached_at,
            ))

        except Exception as e:
            log.debug(f'Error processing {fname}: {e}')
            continue

    if not buckets:
        return []

    # Flatten: newest day first, largest image first within each day
    all_results: list[CachedImage] = []
    for day in sorted(buckets.keys(), reverse=True):
        all_results.extend(sorted(buckets[day], key=lambda x: x.size_bytes, reverse=True))

    returned = all_results[:max_results] if max_results else all_results
    log.info(f'  → {len(all_results)} images across {len(buckets)} days, '
             f'returning {len(returned)}')
    return returned


def default_cache_path(profile_path: str) -> str:
    """Return the default cache directory for a given Chromium-based profile path."""
    home = Path(os.environ.get('REAL_HOME', str(Path.home())))
    # Infer browser from profile path to pick the right Caches subfolder
    if 'Microsoft Edge' in profile_path:
        mac_cache = home / 'Library/Caches/Microsoft Edge/Default/Cache/Cache_Data'
    else:
        mac_cache = home / 'Library/Caches/Google/Chrome/Default/Cache/Cache_Data'
    if mac_cache.is_dir():
        return str(mac_cache)
    return str(Path(profile_path) / 'Cache/Cache_Data')
