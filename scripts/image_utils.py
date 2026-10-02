"""Image loading, hashing and pixel-level comparison. Depends only on Pillow."""
import hashlib
import io
import re
import signal

from PIL import Image, ImageChops, ImageFilter, ImageOps

try:  # Colour-profile conversion (bundled with the official Pillow wheels)
    from PIL import ImageCms
    _SRGB = ImageCms.createProfile("sRGB")
except Exception:  # pragma: no cover - Pillow built without littlecms
    ImageCms = None

# Optional JPEG XL support. Pillow cannot read JXL on its own; without this
# plugin JXL files are still handled by Stage 1 but skipped (and reported) in Stage 3.
try:
    import pillow_jxl  # noqa: F401  (registers the JXL format with Pillow)
    JXL_SUPPORTED = True
except ImportError:
    JXL_SUPPORTED = False

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.jxl',
                    '.bmp', '.tif', '.tiff'}

# Worst allowed average difference (0-255) inside any 8x8 cell of the comparison
# image. Measured on test data: re-encodes/resizes score <= 15, small real edits
# (text, watermark, patch, recolour, shifted subject) score >= 28.
STRICTNESS_PRESETS = {"strict": 12, "normal": 20, "loose": 28}

_CELL = 8


# "photo (1).jpg", "photo (2) (1).jpg": the copy numbers browsers and file
# managers add. "(0)" and other brackets like "(edit)" are left alone.
_COPY_NUMBER_RE = re.compile(r"\s*\([1-9]\d*\)$")


def has_copy_number(path) -> bool:
    return bool(_COPY_NUMBER_RE.search(path.stem))


def strip_copy_number(path):
    """Return *path* with trailing copy numbers removed from the name, or None
    when nothing (or nothing sensible) would remain."""
    stem = path.stem
    while True:
        cleaned = _COPY_NUMBER_RE.sub("", stem)
        if cleaned == stem:
            break
        stem = cleaned
    if stem == path.stem or not stem.strip():
        return None
    return path.with_name(stem + path.suffix)


def copy_base(path) -> str:
    """Name without copy numbers, case-folded: 'Photo (2).JPG' -> 'photo'."""
    stripped = strip_copy_number(path)
    return (stripped or path).stem.casefold()


def rename_target(keep, group_paths):
    """New name for a kept copy, or None.

    Only when another file of the same group shares the base name, which shows
    the number is a copy number ("photo.jpg" + "photo (1).jpg") and not part of
    a series ("Holiday (12).jpg" next to "IMG_5.jpg" is left alone).
    """
    target = strip_copy_number(keep)
    if target is None:
        return None
    base = copy_base(keep)
    if any(copy_base(p) == base for p in group_paths if p != keep):
        return target
    return None


def ignore_sigint():
    """Worker-process initializer: let only the main process handle Ctrl+C."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def sha256_file(path, chunk_size=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


# Bit depths that greyscale pictures with more than 8 bits are usually stored in.
_DEPTHS = (8, 10, 12, 14, 16)


def _to_8_bit(img):
    """16/32-bit integer and float greyscale to 8 bit, scaled to the bit depth the
    values actually use.

    A plain convert() clips everything above 255 to white, which makes unrelated
    16-bit pictures look identical. Dividing by 256 instead squeezes 10- or 12-bit
    data (stored in a 16-bit file) into the darkest few levels, where an edit
    scores far below the limit. So the values are scaled from the smallest usual
    depth that holds them; float pictures from 0-1 when they stay in that range."""
    if img.mode == "F":
        _, high = img.getextrema()
        if high <= 1.0:
            return img.point(lambda v: v * 255).convert("L")
    else:
        img = img.convert("I")
        _, high = img.getextrema()
    high = max(0, int(high))
    bits = next((b for b in _DEPTHS if high < 1 << b), high.bit_length())
    return img.point(lambda v: v * (256 / (1 << bits))).convert("L")


def _normalize(img, max_side):
    """Return an RGB image, orientation-corrected, colour-managed, with transparency
    flattened, downscaled so its longest side is at most `max_side`."""
    if img.format == "JPEG":
        img.draft("RGB", (max_side, max_side))  # fast DCT-domain downscaling
    icc = img.info.get("icc_profile")
    img = ImageOps.exif_transpose(img)

    if img.mode in ("I", "I;16", "I;16B", "I;16L", "I;16N", "F"):
        img = _to_8_bit(img)

    img.thumbnail((max_side, max_side), Image.LANCZOS, reducing_gap=3.0)

    # Flatten transparency onto white; otherwise hidden RGB values under transparent
    # pixels (often black) decide the comparison.
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        img = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
        img = img.convert("RGB")

    # Convert to sRGB so the same photo exported with different profiles still matches.
    if icc and ImageCms is not None:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if img.mode not in ("RGB", "L", "CMYK"):
                img = img.convert("RGB")
            return ImageCms.profileToProfile(img, src, _SRGB, outputMode="RGB")
        except Exception:
            pass
    return img.convert("RGB")


def _dhash(gray, size):
    """Horizontal + vertical difference hash as one int of 2*size*size bits."""
    bits = 0
    h = gray.resize((size + 1, size), Image.LANCZOS).tobytes()
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (h[base + col] > h[base + col + 1])
    v = gray.resize((size, size + 1), Image.LANCZOS).tobytes()
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (v[row * size + col] > v[(row + 1) * size + col])
    return bits


def _webp_is_lossless(path):
    """Walk the RIFF chunks: 'VP8L' = lossless bitstream, 'VP8 ' = lossy."""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            return False
        while chunk := f.read(8):
            if len(chunk) < 8:
                return False
            fourcc, size = chunk[:4], int.from_bytes(chunk[4:], "little")
            if fourcc == b"VP8L":
                return True
            if fourcc == b"VP8 ":
                return False
            f.seek(size + (size & 1), 1)
    return False


# TIFF compressions that lose detail; all others (none, LZW, Deflate, PackBits,
# CCITT, ...) keep every pixel. Pillow reports them in img.info["compression"].
_LOSSY_TIFF = {"jpeg", "tiff_jpeg", "webp", "tiff_sgilog", "tiff_sgilog24"}


def is_lossless(path, fmt, compression=None):
    """Best-effort guess whether the file stores pixels without lossy compression:
    True, False, or None when it can't be told.

    JPEG XL can be either, and Pillow can't tell which, so it is None ("unknown").
    TIFF is judged by its *compression*."""
    if fmt in ("PNG", "BMP"):
        return True
    if fmt == "TIFF":
        return compression not in _LOSSY_TIFF
    if fmt == "JXL":
        return None
    if fmt == "WEBP":
        try:
            return _webp_is_lossless(path)
        except OSError:
            return False
    return False  # JPEG, AVIF, GIF (palette-reduced) and unknown formats


def analyze_image(path, hash_size, format_ranks, prefer_jxl=False):
    """Collect metadata and perceptual hash in one decode. Runs in worker processes.
    Returns a dict; on failure the dict contains an 'error' key.

    *prefer_jxl* (config "prefer_jxl"): rank JPEG XL files like lossless ones, so
    a JXL copy is kept instead of a lossless original (PNG, TIFF, ...)."""
    try:
        stat = path.stat()
        with Image.open(path) as img:
            fmt = (img.format or path.suffix[1:]).upper()
            animated = getattr(img, "n_frames", 1) > 1
            width, height = img.size
            orientation = img.getexif().get(0x0112, 1)
            compression = img.info.get("compression")
            if orientation in (5, 6, 7, 8):  # displayed rotated by 90 degrees
                width, height = height, width
            norm = _normalize(img, 256)
        lossless = is_lossless(path, fmt, compression)
        resolution = width * height
        return {
            'path': path,
            'width': width,
            'height': height,
            'resolution': resolution,
            'format': fmt,
            'format_rank': format_ranks.get(fmt, -1),
            'lossless': lossless,
            'lossless_rank': lossless is True or (fmt == "JXL" and prefer_jxl),
            'bpp': (stat.st_size * 8) / resolution if resolution else 0.0,
            'age': stat.st_mtime,
            'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns,
            'numbered': has_copy_number(path),
            'animated': animated,
            'hash': _dhash(norm.convert("L"), hash_size),
        }
    except Exception as e:
        return {'path': path, 'error': f"{type(e).__name__}: {e}"}


def comparison_thumbnail(path, max_side):
    """Square RGB thumbnail used for pixel verification: (side, raw_bytes).
    Never upscales beyond the image's own short side."""
    with Image.open(path) as img:
        norm = _normalize(img, max_side * 2)
    side = max(64, min(max_side, norm.width, norm.height)) // _CELL * _CELL
    thumb = norm.resize((side, side), Image.LANCZOS, reducing_gap=3.0)
    return side, thumb.tobytes()


def pixel_difference(thumb_a, thumb_b):
    """Worst 8x8-cell mean difference (0-255) between two comparison thumbnails.
    The images are compared at the smaller of the two resolutions, lightly blurred
    to absorb resampling/compression noise; the per-cell maximum (not a global mean)
    is what catches small local edits such as added text."""
    (sa, da), (sb, db) = thumb_a, thumb_b
    side = min(sa, sb)
    a = Image.frombytes("RGB", (sa, sa), da)
    b = Image.frombytes("RGB", (sb, sb), db)
    if sa != side:
        a = a.resize((side, side), Image.LANCZOS)
    if sb != side:
        b = b.resize((side, side), Image.LANCZOS)
    blur = ImageFilter.GaussianBlur(1)
    diff = ImageChops.difference(a.filter(blur), b.filter(blur))
    r, g, bl = diff.split()
    worst_channel = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    cells = worst_channel.resize((side // _CELL, side // _CELL), Image.BOX)
    return cells.getextrema()[1]


def aspect_ratio_close(a, b, tolerance):
    if min(a['width'], a['height'], b['width'], b['height']) <= 0:
        return False
    ra, rb = a['width'] / a['height'], b['width'] / b['height']
    return abs(ra - rb) / max(ra, rb) <= tolerance


def score_image(meta):
    """Higher is better: resolution, then lossless over lossy or unknown (a lossy
    re-encode must never beat its lossless master; JXL counts as lossless here
    with "prefer_jxl"), then format preference, then a name without
    a copy number ("photo.jpg" beats "photo (1).jpg"), then bits-per-pixel (less
    compressed, meaningful within one format), then oldest file."""
    return (meta['resolution'], meta.get('lossless_rank', meta['lossless'] is True), meta['format_rank'],
            not meta['numbered'],
            meta['bpp'], -meta['age'])
