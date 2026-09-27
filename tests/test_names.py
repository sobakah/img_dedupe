"""Copy numbers like "photo (1).jpg": recognising, preferring and removing them."""

from pathlib import Path

import pytest

from scripts.image_utils import has_copy_number, rename_target, strip_copy_number


@pytest.mark.parametrize("name, numbered, renamed", [
    ("photo (1).jpg", True, "photo.jpg"),
    ("photo(2).png", True, "photo.png"),
    ("photo (2) (1).jpg", True, "photo.jpg"),
    ("Urlaub 2023 (12).webp", True, "Urlaub 2023.webp"),
    ("photo (0).jpg", False, None),
    ("photo (edit).jpg", False, None),
    ("(1).jpg", True, None),          # nothing sensible would remain
    ("IMG_2041.jpg", False, None),
])
def test_copy_numbers(name, numbered, renamed):
    path = Path(name)
    assert has_copy_number(path) is numbered
    result = strip_copy_number(path)
    assert (result.name if result else None) == renamed


def test_renamed_when_the_group_shows_it_is_a_copy_number():
    keep = Path("pics/photo (1).jpg")
    assert rename_target(keep, [keep, Path("pics/photo.jpg")]) == Path("pics/photo.jpg")
    assert rename_target(keep, [keep, Path("pics/photo (2).png")]) == Path("pics/photo.jpg")


def test_series_numbers_are_left_alone():
    keep = Path("pics/Holiday (12).jpg")
    assert rename_target(keep, [keep, Path("pics/IMG_5.jpg")]) is None
