"""Artwork URL templating + fetch, ported from Subler's AppleTV.Image extension.

Apple TV image URLs are templates like
`https://is1-ssl.mzstatic.com/.../source/{w}x{h}.{f}` — we substitute the requested
size and format. We always fetch JPEG.
"""

from dataclasses import dataclass
from typing import Literal

import httpx

Size = Literal["square", "rectangle", "standard"]


def _size_of(width: int, height: int) -> Size:
    if width > height:
        return "rectangle"
    if width == height:
        return "square"
    return "standard"


def _full_size(size: Size) -> str:
    return {"square": "1600x1600.jpg", "rectangle": "1920x1080.jpg", "standard": "1200x1800.jpg"}[size]


def _thumb_size(size: Size) -> str:
    return {"square": "329x329.jpg", "rectangle": "329x185.jpg", "standard": "185x329.jpg"}[size]


@dataclass(frozen=True)
class Artwork:
    url: str
    thumb_url: str
    size: Size


def from_image(image: dict | None) -> Artwork | None:
    """Build an Artwork from an Apple TV `Image` JSON object."""
    if not image or "url" not in image:
        return None
    base = image["url"].replace("{w}x{h}.{f}", "")
    size = _size_of(int(image.get("width", 1)), int(image.get("height", 1)))
    return Artwork(url=base + _full_size(size), thumb_url=base + _thumb_size(size), size=size)


def download(url: str, timeout: float = 30.0) -> bytes:
    """Fetch artwork bytes (JPEG)."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content
