"""End to end: the three stages, deleting, the log, renaming and the command line."""

import json
import os

import pytest

import pictures
from conftest import summary


@pytest.fixture
def mixed(library):
    """Two exact copies, two visual copies, one edited version, one unrelated picture."""
    library.photo("a.png", 1)
    library.copy_of("a.png", "backup/a copy.png")                 # stage 1: identical
    library.variant("a.png", "a.jpg", quality=85)                 # stage 3: visually identical
    library.variant("a.png", "a small.jpg", pictures.downscaled)  # stage 3
    library.variant("a.png", "a text.jpg", pictures.with_text, quality=90)  # edited: keep
    library.photo("b.png", 2)                                     # unrelated
    return library


def test_dry_run_finds_all_stages_and_changes_nothing(mixed, run_cli):
    before = mixed.files()
    result = run_cli(mixed.path, "-r", "--auto", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert summary(result) == {**summary(""), "scanned": 6, "exact": 1, "visual": 2}
    assert mixed.files() == before
    for stage in ("Stage 1 · Identical files", "Stage 3 · Visually identical images"):
        assert stage in result.stdout


def test_real_run_moves_copies_to_the_trash_and_logs_them(mixed, run_cli, isolated):
    result = run_cli(mixed.path, "-r", "--auto")
    assert result.returncode == 0
    assert mixed.files() == ["a text.jpg", "a.png", "b.png"]
    trash = isolated / "home" / ".local" / "share" / "Trash" / "files"
    assert sorted(p.name for p in trash.iterdir()) == ["a copy.png", "a small.jpg", "a.jpg"]
    log = (isolated / "state" / "img_dedupe.log").read_text()
    assert log.count("| TRASHED  |") == 3
    assert "exact copy (identical bytes), automatic" in log


def test_the_copy_number_is_removed_from_a_better_copy(library, run_cli):
    library.photo("photo (1).jpg", 3, size=(640, 480), quality=95)       # the better copy
    library.variant("photo (1).jpg", "photo.jpg", pictures.downscaled)   # smaller copy
    run_cli(library.path, "--auto")
    assert library.files() == ["photo.jpg"]
    assert summary(run_cli(library.path, "--auto"))["visual"] == 0


def test_series_numbers_are_not_removed(library, run_cli):
    library.photo("Holiday (12).png", 4)
    library.variant("Holiday (12).png", "IMG_5.jpg", quality=90)
    run_cli(library.path, "--auto")
    assert library.files() == ["Holiday (12).png"]


def test_hard_links_free_no_space(library, run_cli):
    library.photo("a.png", 5)
    os.link(library / "a.png", library / "b.png")
    result = run_cli(library.path, "--auto")
    assert "only the extra name is removed" in result.stdout
    assert "Space freed: 0 B" in result.stdout


# Without stage 1, the byte-identical copy is still in place for stage 3, which
# then finds it as a visually identical picture too: 3 instead of 2.
@pytest.mark.parametrize("stages, exact, visual", [("1", 1, 0), ("3", 0, 3), ("1,3", 1, 2), ("all", 1, 2), ("both", 1, 2)])
def test_choosing_stages(mixed, run_cli, stages, exact, visual):
    counts = summary(run_cli(mixed.path, "-r", "--auto", "--dry-run", "--stages", stages))
    assert (counts["exact"], counts["visual"]) == (exact, visual)


def test_invalid_stages_are_rejected(mixed, run_cli):
    result = run_cli(mixed.path, "--stages", "4")
    assert result.returncode == 2
    assert "stage numbers 1-3" in result.stderr


def test_exclude_leaves_out_subfolders_too(library, run_cli):
    library.photo("keep.png", 1)
    library.copy_of("keep.png", "Urlaub/Strand/copy.png")
    counts = summary(run_cli(library.path, "-r", "--exclude", "Urlaub", "--auto", "--dry-run"))
    assert (counts["scanned"], counts["exact"]) == (1, 0)


def test_the_home_folder_is_refused_in_auto_mode(run_cli, isolated):
    home = isolated / "home"
    pictures.save(pictures.photo(1), home / "a.png")
    result = run_cli(home, "--auto", "--dry-run")
    assert result.returncode == 2
    assert "Refusing to scan the root or home folder" in result.stdout


@pytest.mark.parametrize("args, code", [
    (["/does/not/exist", "--auto"], 2),
    (["--version"], 0),
])
def test_exit_codes(run_cli, args, code):
    assert run_cli(*args).returncode == code


def test_a_missing_config_file_is_an_error(mixed, run_cli, tmp_path):
    result = run_cli(mixed.path, "--auto", "-c", tmp_path / "nope.json")
    assert result.returncode == 2


def test_invalid_config_numbers_are_reported(mixed, run_cli, config_file):
    config_file({"compare_size": "big"})
    result = run_cli(mixed.path, "-r", "--auto", "--dry-run")
    assert "'compare_size' must be a whole number" in result.stdout
    assert summary(result)["visual"] == 2          # still compares correctly, with the default


def test_marked_pairs_are_left_out(library, run_cli):
    library.photo("a.png", 1)
    library.variant("a.png", "a.jpg", quality=85)
    library.variant("a.png", "a small.jpg", pictures.downscaled)
    # interactively: Enter starts, i marks the only group, Enter finishes
    result = run_cli(library.path, "--dry-run", "-i", keys=["", "i", ""])
    assert "Marked as not duplicates" in result.stdout
    assert (library / ".img_dedupe_ignore.json").exists()
    assert summary(run_cli(library.path, "--auto", "--dry-run"))["visual"] == 0
    assert summary(run_cli(library.path, "--auto", "--dry-run", "--no-ignore"))["visual"] == 2
