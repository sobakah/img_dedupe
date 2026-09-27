"""Synthetic test pictures that behave like photos: deterministic, no downloads.

A picture mixes smooth colour areas, medium detail, fine texture and a few
shapes, so that re-encoded copies score low in img_dedupe's comparison while
small edits (text, a patch, a crop) score high.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


def _noise(rng: random.Random, width: int, height: int) -> Image.Image:
    return Image.frombytes("RGB", (width, height), bytes(rng.randrange(256) for _ in range(width * height * 3)))


def photo(seed: int, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A photo-like RGB picture, the same for the same seed."""
    rng = random.Random(seed)
    w, h = size
    base = _noise(rng, 8, 6).resize(size, Image.BICUBIC)
    detail = _noise(rng, 48, 36).resize(size, Image.BICUBIC)
    texture = _noise(rng, w // 8, h // 8).resize(size, Image.BICUBIC)
    img = Image.blend(Image.blend(base, detail, 0.35), texture, 0.06)
    draw = ImageDraw.Draw(img)
    for _ in range(6):
        x, y = rng.randrange(w), rng.randrange(h)
        r = rng.randrange(min(w, h) // 12, min(w, h) // 5)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rng.randrange(256) for _ in range(3)))
    return img.filter(ImageFilter.GaussianBlur(1.2))  # real photos have soft edges


def save(img: Image.Image, path: Path, **options) -> Path:
    """Save with the format taken from the file extension."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, **options)
    return path


# --- variants: the same picture (should match) ----------------------------------
def reencoded(img: Image.Image, quality: int = 80) -> Image.Image:
    from io import BytesIO
    buffer = BytesIO()
    img.save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def downscaled(img: Image.Image, factor: float = 0.5) -> Image.Image:
    return img.resize((int(img.width * factor), int(img.height * factor)), Image.LANCZOS)


# --- edits: a different picture (must not match) --------------------------------
def with_text(img: Image.Image, text: str = "2024-06-01 Copyright") -> Image.Image:
    out = img.copy()
    size = max(14, img.height // 18)
    ImageDraw.Draw(out).text((img.width * 0.05, img.height * 0.82), text, fill=(255, 255, 255),
                             font=ImageFont.load_default(size=size))
    return out


def with_patch(img: Image.Image) -> Image.Image:
    out = img.copy()
    w, h = img.size
    ImageDraw.Draw(out).rectangle((w * 0.1, h * 0.1, w * 0.22, h * 0.2), fill=(20, 20, 20))
    return out


def brighter(img: Image.Image, factor: float = 1.15) -> Image.Image:
    return ImageEnhance.Brightness(img).enhance(factor)
