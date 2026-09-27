"""The pixel comparison: copies must match, edits and other pictures must not."""

import pytest
from PIL import Image

import pictures
from scripts.image_utils import (analyze_image, aspect_ratio_close, comparison_thumbnail,
                                 is_lossless, pixel_difference, score_image)

LIMIT = 20  # the "normal" strictness


def diff(a, b):
    return pixel_difference(comparison_thumbnail(a, 512), comparison_thumbnail(b, 512))


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_reencoded_and_resized_copies_match(library, seed):
    original = library.photo("original.png", seed)
    copies = [
        library.variant("original.png", "q85.jpg", quality=85),
        library.variant("original.png", "q50.jpg", quality=50),
        library.variant("original.png", "copy.webp", quality=70),
        library.variant("original.png", "half.jpg", pictures.downscaled, quality=85),
    ]
    for copy in copies:
        assert diff(original, copy) <= LIMIT, copy.name


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_small_edits_do_not_match(library, seed):
    original = library.photo("original.png", seed)
    for change in (pictures.with_text, pictures.with_patch, pictures.brighter):
        edited = library.variant("original.png", f"{change.__name__}.jpg", change, quality=90)
        assert diff(original, edited) > LIMIT, change.__name__


def test_unrelated_pictures_do_not_match(library):
    assert diff(library.photo("a.png", 1), library.photo("b.png", 2)) > 100


def test_exif_rotation_is_applied(library):
    original = library.photo("upright.png", 3)
    with Image.open(original) as img:
        rotated = img.transpose(Image.ROTATE_90)       # pixels turned; the tag turns them back
    exif = rotated.getexif()
    exif[0x0112] = 6
    rotated.save(library / "rotated.jpg", quality=92, exif=exif)
    meta = analyze_image(library / "rotated.jpg", 8, {})
    assert (meta["width"], meta["height"]) == (320, 240)
    assert diff(original, library / "rotated.jpg") <= LIMIT


def test_hidden_colours_under_transparency_are_ignored(library):
    def square(hidden, visible, name):
        img = Image.new("RGBA", (200, 200), hidden + (0,))      # fully transparent background
        img.paste(visible + (255,), (50, 50, 150, 150))
        img.save(library / name)
        return library / name
    a = square((0, 0, 0), (200, 30, 30), "a.png")
    b = square((0, 255, 0), (200, 30, 30), "b.png")              # other hidden colour, same picture
    c = square((0, 0, 0), (30, 30, 200), "c.png")                # visibly different
    assert diff(a, b) == 0
    assert diff(a, c) > LIMIT


def test_different_16_bit_pictures_do_not_look_alike(library):
    for name, seed in (("a.png", 1), ("b.png", 2)):
        gray = pictures.photo(seed).convert("L")
        Image.frombytes("I;16", gray.size, bytes(b for px in gray.tobytes() for b in (0, px))).save(library / name)
    assert diff(library / "a.png", library / "b.png") > LIMIT


def test_lossless_detection(library):
    library.photo("a.png", 1)
    assert is_lossless(library / "a.png", "PNG")
    assert not is_lossless(library.variant("a.png", "a.jpg"), "JPEG")
    assert is_lossless(library.variant("a.png", "l.webp", lossless=True), "WEBP")
    assert not is_lossless(library.variant("a.png", "q.webp", quality=80), "WEBP")


def test_keeper_ranking(library):
    library.photo("big.png", 1, size=(640, 480))
    meta = {name: analyze_image(library / name, 8, {"PNG": 3, "JPEG": 2}) for name in (
        "big.png",
        library.variant("big.png", "small.png", pictures.downscaled).name,
        library.variant("big.png", "big.jpg", quality=95).name,
        library.copy_of("big.png", "big (1).png").name,
    )}
    ranked = sorted(meta, key=lambda n: score_image(meta[n]), reverse=True)
    assert ranked[0] == "big.png"                             # full size, lossless, no copy number
    assert ranked.index("big (1).png") < ranked.index("big.jpg")   # lossless before lossy
    assert ranked[-1] == "small.png"                          # resolution comes first


def test_zero_sized_images_never_match():
    empty = {"width": 0, "height": 0}
    assert aspect_ratio_close(empty, {"width": 10, "height": 10}, 0.02) is False
