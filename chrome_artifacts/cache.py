"""
Chrome Simple Cache image extractor (macOS).

Chrome stores its HTTP cache in Simple Cache format. Each `_0` file contains:
  [24-byte header][key (URL)][stream body][EOF magic][response info][EOF magic][trailer]

Image body data starts immediately after the key and ends just before the
first SimpleFileEOF magic marker. PIL is used to validate each candidate.
"""
import io
import logging
import os
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

# Extensions that imply image content in the URL
IMAGE_URL_HINTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico', '.svg', '.bmp')


@dataclass
class CachedImage:
    filename: str          # cache file name (hex hash)
    url: str               # original URL
    mime_type: str
    width: int
    height: int
    size_bytes: int
    data: bytes            # raw image bytes


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
               max_results: int = 500) -> list[CachedImage]:
    """
    Scan Chrome's Simple Cache directory for cached images.

    Args:
        cache_path: Path to the Cache_Data directory.
        url_filter:  Optional substring to filter URLs (case-insensitive).
        min_width:   Minimum image width in pixels (0 = no filter).
        min_height:  Minimum image height in pixels (0 = no filter).
        max_results: Maximum number of results to return.

    Returns:
        List of CachedImage objects, largest-first by byte size.
    """
    cache_dir = Path(cache_path)
    if not cache_dir.is_dir():
        log.warning(f'Cache directory not found: {cache_path}')
        return []

    results: list[CachedImage] = []

    for fname in os.listdir(cache_dir):
        if not fname.endswith('_0'):
            continue

        fpath = cache_dir / fname
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

            # Quick pre-filter: skip entries that don't look image-related
            if not any(hint in url_lower for hint in IMAGE_URL_HINTS):
                # Still try if the body starts with an image signature
                body_off = 24 + key_len
                if _detect_sig(data, body_off) is None:
                    continue

            # Apply user URL filter
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

            results.append(CachedImage(
                filename=fname,
                url=url,
                mime_type=mime,
                width=w,
                height=h,
                size_bytes=len(img_bytes),
                data=img_bytes,
            ))

        except Exception as e:
            log.debug(f'Error processing {fname}: {e}')
            continue

    # Sort by file size descending (larger/more interesting images first)
    results.sort(key=lambda x: x.size_bytes, reverse=True)
    return results[:max_results]


def default_cache_path(profile_path: str) -> str:
    """Return the default Chrome cache directory for a given profile path."""
    # Chrome on macOS keeps cache in a separate Caches directory
    home = Path.home()
    mac_cache = home / 'Library/Caches/Google/Chrome/Default/Cache/Cache_Data'
    if mac_cache.is_dir():
        return str(mac_cache)
    # Fallback: cache inside profile (less common on macOS)
    return str(Path(profile_path) / 'Cache/Cache_Data')
