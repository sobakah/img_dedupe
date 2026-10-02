"""The pixel comparison: copies must match, edits and other pictures must not."""

import struct

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


def save_high_bit_depth(img, path, bits: int):
    """Save an 8-bit picture as 16-bit greyscale PNG using only the lowest *bits* bits,
    like 12-bit camera or scanner data stored in a 16-bit file."""
    gray = img.convert("L")
    values = [px << (bits - 8) for px in gray.tobytes()]
    Image.frombytes("I;16", gray.size, b"".join(v.to_bytes(2, "little") for v in values)).save(path)
    return path


@pytest.mark.parametrize("bits", [10, 12, 14])
def test_edits_in_12_bit_pictures_do_not_match(library, bits):
    original = pictures.photo(1)
    a = save_high_bit_depth(original, library / "a.png", bits)
    b = save_high_bit_depth(pictures.with_patch(original), library / "b.png", bits)
    copy = save_high_bit_depth(original, library / "copy.tif", bits)
    assert diff(a, b) > LIMIT
    assert diff(a, copy) == 0


def test_float_pictures_are_not_all_black(library):
    def save_float(img, name):
        gray = img.convert("L")
        Image.frombytes("F", gray.size, b"".join(struct.pack("<f", px / 255) for px in gray.tobytes())) \
            .save(library / name)
        return library / name
    a = save_float(pictures.photo(1), "a.tif")
    b = save_float(pictures.photo(2), "b.tif")
    assert diff(a, b) > LIMIT


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


def test_tiff_is_judged_by_its_compression(library):
    library.photo("a.png", 1)
    for compression, lossless in (("tiff_lzw", True), ("tiff_adobe_deflate", True), ("jpeg", False)):
        path = library.variant("a.png", f"{compression}.tif", compression=compression)
        assert analyze_image(path, 8, {})["lossless"] is lossless, compression


def test_jpeg_xl_is_unknown(tmp_path):
    assert is_lossless(tmp_path / "a.jxl", "JXL") is None


def test_prefer_jxl_decides_between_jpeg_xl_and_a_lossless_original():
    common = {"resolution": 100, "numbered": False, "bpp": 2.0, "age": 1.0}
    png = {**common, "lossless": True, "lossless_rank": True, "format_rank": 3}
    jxl = {**common, "lossless": None, "lossless_rank": False, "format_rank": 6}   # default
    assert score_image(png) > score_image(jxl)
    assert score_image({**jxl, "lossless_rank": True}) > score_image(png)      # "prefer_jxl": true


# --- keeping another image than the recommended one ---------------------------------
def found_group(library, names, diffs):
    """A group as Stage 3 would build it; *diffs* are the scores against names[0]."""
    from scripts.core_logic import Group
    metas = [analyze_image(library / name, 8, {}) for name in names]
    return Group(images=metas, diffs=[None, *diffs], limit=LIMIT, borderline_above=LIMIT * 0.8)


COMPARE = {"compare_size": 512, "max_aspect_diff": 0.02}


def test_another_keeper_is_compared_with_every_image_it_replaces(library):
    from scripts.core_logic import regroup_around
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=90)
    library.variant("a.png", "a edited.jpg", pictures.with_patch, quality=90)
    # As if "a edited.jpg" had passed against a.png: it must still not be removed
    # when the user keeps a.jpg instead.
    group = found_group(library, ["a.png", "a edited.jpg", "a.jpg"], [15, 2])
    far = regroup_around(group, 2, COMPARE)
    assert [m["path"].name for m in group.images] == ["a.jpg", "a.png"]
    assert group.diffs[0] is None and group.diffs[1] <= LIMIT
    assert [(m["path"].name, score > LIMIT) for m, score in far] == [("a edited.jpg", True)]


def test_another_keeper_without_any_close_image_changes_nothing(library):
    from scripts.core_logic import regroup_around
    library.photo("a.png", 1)
    library.variant("a.png", "a edited.jpg", pictures.with_patch, quality=90)
    group = found_group(library, ["a.png", "a edited.jpg"], [15])
    assert regroup_around(group, 1, COMPARE) is None
    assert [m["path"].name for m in group.images] == ["a.png", "a edited.jpg"]
