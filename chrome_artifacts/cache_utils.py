"""
Shared image-detection helpers used by Chrome, Firefox, and Safari cache scanners.
"""
import io
from typing import Optional

# SVG is intentionally absent: every candidate must also pass PIL validation,
# which cannot open SVGs, so they could never be displayed anyway.
IMAGE_SIGNATURES = {
    b'\xff\xd8\xff':       'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'GIF89a':             'image/gif',
    b'GIF87a':             'image/gif',
    b'RIFF':               'image/webp',
}

_AVIF_BRANDS = {b'avif', b'avis', b'MA1A', b'MiHA'}

IMAGE_URL_HINTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.ico', '.svg', '.bmp', '.avif')


def detect_mime(data: bytes) -> Optional[str]:
    """Return MIME type string if data starts with a known image signature, else None."""
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


def validate_image(raw: bytes) -> Optional[tuple[int, int]]:
    """
    Try to open raw bytes as a PIL Image.
    Returns (width, height) or None if the bytes are not a valid image.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        return img.size
    except Exception:
        return None
