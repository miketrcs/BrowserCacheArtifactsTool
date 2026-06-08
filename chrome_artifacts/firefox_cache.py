"""
Firefox cache2 image extractor (macOS).

Firefox stores its HTTP cache in the cache2 format under:
  ~/Library/Caches/Firefox/Profiles/<profile-name>/cache2/entries/

Each entry file layout:
  [0 .. meta_offset-1]  HTTP response body (raw, typically uncompressed for images)
  [meta_offset .. -5]   Metadata (version-dependent binary structure)
  [-4 .. -1]            Big-endian uint32 = meta_offset from start of file

The metadata's exact field layout has changed across Firefox versions, so the
URL is extracted via regex scan rather than fixed field offsets — this works
across all versions observed in testing (Firefox 100+).
"""
import datetime
import logging
import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .cache_utils import detect_mime, validate_image, IMAGE_URL_HINTS

log = logging.getLogger(__name__)

# Matches http(s) URLs in the metadata section; handles Firefox's
# partition-key prefix formats ("a,https://..." or "~site~https://...").
_URL_RE = re.compile(rb'https?://[^\x00\n\r ]{8,}')


@dataclass
class FirefoxCachedImage:
    url: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    data: bytes
    cached_at: Optional[datetime.datetime] = None


def _parse_entry(data: bytes) -> Optional[tuple[bytes, str]]:
    """
    Extract (body, url) from a cache2 entry file, or return None.

    The last 4 bytes of the file give the byte offset where the metadata
    section begins.  The URL is found by scanning the metadata for an
    http(s) pattern — this is robust across Firefox format versions.
    """
    if len(data) < 40:
        return None

    try:
        meta_offset = struct.unpack('>I', data[-4:])[0]
    except struct.error:
        return None

    if meta_offset == 0 or meta_offset >= len(data) - 4:
        return None

    meta = data[meta_offset:-4]
    m = _URL_RE.search(meta)
    if m is None:
        return None

    url = m.group(0).decode('utf-8', errors='replace').rstrip('\x00 ')
    body = data[:meta_offset]
    return body, url


def scan_firefox_cache(profile_path: str,
                       url_filter: str = '',
                       min_width: int = 0,
                       min_height: int = 0,
                       max_results: int = 0,
                       scan_limit: int = 30000) -> list[FirefoxCachedImage]:
    """
    Scan Firefox's cache2 directory for cached images.

    The cache directory mirrors the profile name:
      ~/Library/Caches/Firefox/Profiles/<profile-name>/cache2/entries/

    If that exact path doesn't exist (e.g. profile was renamed), falls back
    to the most recently modified Firefox cache directory.

    Uses the same day-bucketing strategy as Chrome's scan_cache: stat all
    files first (no reads), distribute scan_limit evenly across days so
    results cover the full date range, then read + validate.
    """
    real_home = Path(os.environ.get('REAL_HOME', str(Path.home())))
    profile_name = Path(profile_path).name
    cache_dir = (real_home / 'Library/Caches/Firefox/Profiles'
                 / profile_name / 'cache2/entries')

    if not cache_dir.is_dir():
        ff_caches_base = real_home / 'Library/Caches/Firefox/Profiles'
        if ff_caches_base.is_dir():
            candidates = sorted(
                [c / 'cache2/entries'
                 for c in ff_caches_base.iterdir()
                 if (c / 'cache2/entries').is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                cache_dir = candidates[0]

    if not cache_dir.is_dir():
        log.info(f'Firefox cache2/entries not found (tried {cache_dir})')
        return []

    log.info(f'Scanning Firefox cache: {cache_dir}')

    # Phase 1: stat all files, bucket by day
    day_file_map: dict[str, list[tuple[float, Path]]] = {}
    for entry_path in cache_dir.iterdir():
        if not entry_path.is_file():
            continue
        try:
            mtime = entry_path.stat().st_mtime
        except OSError:
            continue
        day = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).strftime('%Y-%m-%d')
        day_file_map.setdefault(day, []).append((mtime, entry_path))

    total_files = sum(len(v) for v in day_file_map.values())
    num_days = len(day_file_map)
    per_day_scan = max(50, (scan_limit // num_days) if (scan_limit and num_days) else 500)

    selected: list[tuple[float, Path]] = []
    for day_files in day_file_map.values():
        day_files.sort(key=lambda x: x[0], reverse=True)
        selected.extend(day_files[:per_day_scan])

    log.info(f'Cache stat pass: {total_files} files across {num_days} days; '
             f'reading {len(selected)} ({per_day_scan}/day)')

    # Phase 2: read + validate
    buckets: dict[str, list[FirefoxCachedImage]] = {}

    for mtime, entry_path in selected:
        try:
            data = entry_path.read_bytes()
        except OSError:
            continue

        parsed = _parse_entry(data)
        if parsed is None:
            continue
        body, url = parsed

        if not body:
            continue
        if url_filter and url_filter.lower() not in url.lower():
            continue

        mime = detect_mime(body)
        if mime is None:
            continue

        dims = validate_image(body)
        if dims is None:
            continue
        w, h = dims
        if (min_width and w < min_width) or (min_height and h < min_height):
            continue

        cached_at = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc)
        day = cached_at.strftime('%Y-%m-%d')
        buckets.setdefault(day, []).append(FirefoxCachedImage(
            url=url, mime_type=mime, width=w, height=h,
            size_bytes=len(body), data=body, cached_at=cached_at,
        ))

    if not buckets:
        return []

    all_results: list[FirefoxCachedImage] = []
    for day in sorted(buckets.keys(), reverse=True):
        all_results.extend(sorted(buckets[day], key=lambda x: x.size_bytes, reverse=True))

    returned = all_results[:max_results] if max_results else all_results
    log.info(f'  → {len(all_results)} images across {len(buckets)} days, '
             f'returning {len(returned)}')
    return returned
