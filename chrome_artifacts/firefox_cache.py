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
import io
import logging
import os
import re
import struct
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

_AVIF_BRANDS = {b'avif', b'avis', b'MA1A', b'MiHA'}

# Matches http(s) URLs in the metadata section; handles Firefox's
# partition-key prefix formats ("a,https://..." or "~site~https://...").
_URL_RE = re.compile(rb'https?://[^\x00\n\r ]{8,}')

IMAGE_URL_HINTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico', '.svg',
                   '.bmp', '.avif')


@dataclass
class FirefoxCachedImage:
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
    if len(data) >= 12 and data[4:8] == b'ftyp' and data[8:12] in _AVIF_BRANDS:
        return 'image/avif'
    return None


def _validate_image(raw: bytes) -> Optional[tuple[int, int]]:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        return img.size
    except Exception:
        return None


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
                       max_results: int = 500) -> list[FirefoxCachedImage]:
    """
    Scan Firefox's cache2 directory for cached images.

    The cache directory mirrors the profile name:
      ~/Library/Caches/Firefox/Profiles/<profile-name>/cache2/entries/

    If that exact path doesn't exist (e.g. profile was renamed), falls back
    to the most recently modified Firefox cache directory.
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
    results: list[FirefoxCachedImage] = []

    for entry_path in cache_dir.iterdir():
        if not entry_path.is_file():
            continue
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

        mime = _detect_mime(body)
        if mime is None:
            continue

        dims = _validate_image(body)
        if dims is None:
            continue
        w, h = dims
        if (min_width and w < min_width) or (min_height and h < min_height):
            continue

        results.append(FirefoxCachedImage(
            url=url, mime_type=mime, width=w, height=h,
            size_bytes=len(body), data=body,
        ))

    results.sort(key=lambda x: x.size_bytes, reverse=True)
    log.info(f'Found {len(results)} Firefox cached images')
    return results[:max_results]
