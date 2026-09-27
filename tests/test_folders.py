"""Finding files, excluding folders and the folder list's selection logic."""

import os

import pytest

from scripts.app import (ROOT, FolderIndex, find_images, folder_entries, normalize_excluded,
                         parse_numbers, switch_folders)


@pytest.fixture
def tree(library):
    library.photo("main.jpg", 1)
    library.photo("Familie/a.jpg", 2)
    library.photo("Urlaub/Strand/b.jpg", 3)
    library.photo("Urlaub/Berge/c.jpg", 4)
    library.photo(".thumbnails/hidden.jpg", 5)
    library.photo(".hidden.jpg", 6)
    return library


def names(paths, base):
    return sorted(p.relative_to(base).as_posix() for p in paths)


def test_hidden_files_and_folders_are_skipped(tree):
    assert names(find_images(tree.path, recursive=True), tree.path) == [
        "Familie/a.jpg", "Urlaub/Berge/c.jpg", "Urlaub/Strand/b.jpg", "main.jpg"]


def test_without_subfolders_only_the_main_folder_is_scanned(tree):
    assert names(find_images(tree.path, recursive=False), tree.path) == ["main.jpg"]


def test_symlinks_are_not_followed(tree):
    os.symlink(tree / "main.jpg", tree / "link.jpg")
    assert "link.jpg" not in names(find_images(tree.path, recursive=True), tree.path)


def test_exclude_takes_the_subfolders_along(tree):
    excluded = normalize_excluded(tree.path, ["Urlaub"])
    assert excluded == {"Urlaub", "Urlaub/Strand", "Urlaub/Berge"}
    assert names(find_images(tree.path, True, excluded), tree.path) == ["Familie/a.jpg", "main.jpg"]


def test_exclude_dot_leaves_out_only_the_main_folder(tree):
    excluded = normalize_excluded(tree.path, ["."])
    assert names(find_images(tree.path, True, excluded), tree.path) == [
        "Familie/a.jpg", "Urlaub/Berge/c.jpg", "Urlaub/Strand/b.jpg"]


def test_exclusions_only_apply_to_recursive_scans(tree):
    assert names(find_images(tree.path, False, {ROOT}), tree.path) == ["main.jpg"]


def test_files_in_scope(tree):
    index = FolderIndex()
    settings = {"recursive": True, "excluded": set(), "videos": True}
    assert index.in_scope(tree.path, settings) == 4
    assert index.in_scope(tree.path, {**settings, "excluded": {"Urlaub/Strand"}}) == 3
    assert index.in_scope(tree.path, {**settings, "recursive": False}) == 1


# --- the folder list ----------------------------------------------------------
@pytest.fixture
def entries(tree):
    found = folder_entries(tree.path, FolderIndex())
    assert [rel for rel, _ in found] == ["Familie", "Urlaub", "Urlaub/Berge", "Urlaub/Strand"]
    return found


def test_each_listed_folder_switches_on_its_own(entries):
    excluded = {"Urlaub", "Urlaub/Berge", "Urlaub/Strand"}           # only Familie selected
    switch_folders(excluded, entries, [4, 1])                        # "4,1": select Strand, unselect Familie
    assert excluded == {"Familie", "Urlaub", "Urlaub/Berge"}


def test_a_parent_takes_its_subfolders_along(entries):
    excluded = set()
    switch_folders(excluded, entries, [2])
    assert excluded == {"Urlaub", "Urlaub/Berge", "Urlaub/Strand"}


def test_a_listed_subfolder_keeps_its_own_choice(entries):
    excluded = {"Urlaub/Berge"}
    switch_folders(excluded, entries, [2, 3])                        # Urlaub off, Berge on
    assert excluded == {"Urlaub", "Urlaub/Strand"}


def test_the_main_folder_is_never_switched_by_numbers(entries):
    excluded = {ROOT}
    switch_folders(excluded, entries, [1, 2, 3, 4])
    assert ROOT in excluded


@pytest.mark.parametrize("text, upper, expected", [
    ("3", 5, [3]), ("2-4", 5, [2, 3, 4]), ("4-2", 5, [2, 3, 4]), ("1,3 5", 5, [1, 3, 5]),
    ("0", 5, None), ("6", 5, None), ("a", 5, None), ("", 5, None),
])
def test_parse_numbers(text, upper, expected):
    assert parse_numbers(text, upper) == expected
