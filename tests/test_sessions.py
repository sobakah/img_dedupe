"""Saved sessions, marks, resuming, and the start screen with a saved session."""

import json
import os
from pathlib import Path

import pytest

import pictures
from conftest import summary
from scripts import session as session_mod
from scripts.core_logic import (Group, RunContext, Stats, stats_from_session, tighten_groups,
                                validate_groups)
from scripts.session import IgnoreList, SessionStore


def meta(path: Path) -> dict:
    stat = path.stat()
    return {"path": path, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


FIELDS = {"width": 320, "height": 240, "resolution": 76800, "format": "JPEG", "format_rank": 2,
          "lossless": False, "bpp": 2.0, "age": 1.0, "size": 1, "mtime_ns": 1, "numbered": False}


def group(*diffs, limit=20, status="pending", images=None):
    images = images or [{**FIELDS, "path": Path(f"/x/{i}.jpg")} for i in range(len(diffs) + 1)]
    return Group(images=images, diffs=[None, *diffs], limit=limit, borderline_above=limit * 0.8, status=status)


# --- the session file ---------------------------------------------------------
def test_save_and_load(tmp_path):
    store = SessionStore(tmp_path)
    store.save({"groups": [{"status": "pending"}], "exact_groups": []})
    assert store.load()["groups"] == [{"status": "pending"}]


def test_a_failed_save_leaves_no_temporary_file(tmp_path, monkeypatch, capsys):
    store = SessionStore(tmp_path)
    store.save({"groups": [{"status": "pending"}]})
    def disk_full(*args, **kwargs):
        raise OSError("No space left on device")
    monkeypatch.setattr(session_mod.json, "dump", disk_full)
    store.save({"groups": [{"status": "done"}]})
    monkeypatch.undo()
    assert "Could not save progress" in capsys.readouterr().out
    assert store.load()["groups"] == [{"status": "pending"}]        # the previous save is intact
    assert [p.name for p in store.path.parent.iterdir()] == [store.path.name]


def test_sessions_from_the_old_location_are_found_and_moved(tmp_path, monkeypatch):
    monkeypatch.delenv("IMG_DEDUPE_STATE_DIR")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(session_mod, "project_dir", lambda: project)
    old = SessionStore(tmp_path)
    old.path = old._legacy                                         # as saved by an older version
    old.save({"groups": [{"status": "pending"}]})
    store = SessionStore(tmp_path)
    assert store.path.parent == project / "sessions"
    assert store.load() is not None
    store.save(store.load())
    assert store.path.exists() and not store._legacy.exists()


# --- "not duplicates" marks -------------------------------------------------------
def test_marks_are_remembered_until_a_file_changes(library):
    a, b = library.photo("a.png", 1), library.photo("b.png", 2)
    IgnoreList(library.path).add(meta(a), meta(b))
    assert (library / ".img_dedupe_ignore.json").exists()
    assert IgnoreList(library.path).contains(meta(b), meta(a))
    os.utime(b, ns=(b.stat().st_atime_ns, b.stat().st_mtime_ns + 10**9))   # "edited"
    assert not IgnoreList(library.path).contains(meta(a), meta(b))


def test_no_ignore_shows_marked_pairs(library):
    a, b = library.photo("a.png", 1), library.photo("b.png", 2)
    IgnoreList(library.path).add(meta(a), meta(b))
    assert not IgnoreList(library.path, enabled=False).contains(meta(a), meta(b))


# --- resuming ------------------------------------------------------------------
def test_changed_or_missing_files_are_dropped_on_resume(library):
    keeper, same, changed, gone = (library.photo(f"{n}.png", i) for i, n in enumerate("kscg", 1))
    g = group(5, 5, 5, images=[meta(keeper), meta(same), meta(changed), meta(gone)])
    os.utime(changed, ns=(changed.stat().st_atime_ns, changed.stat().st_mtime_ns + 10**9))
    gone.unlink()
    groups, dropped_groups, dropped_images = validate_groups([g])
    assert (dropped_groups, dropped_images) == (0, 2)
    assert [m["path"] for m in groups[0].images] == [keeper, same]


def test_a_stricter_limit_takes_out_images_above_it():
    groups, removed = tighten_groups([group(5, 15), group(14), group(18, limit=20, status="done")], 12)
    assert removed == 2
    assert [(len(g.images), g.status) for g in groups] == [(2, "pending"), (2, "done")]  # 2nd group gone
    assert groups[0].limit == 12
    assert groups[1].limit == 20                                   # finished groups are left as they are


def test_a_looser_limit_changes_nothing():
    groups, removed = tighten_groups([group(5, 15)], 28)
    assert removed == 0 and groups[0].limit == 20


def test_statistics_continue_from_the_session():
    stats = stats_from_session({"stats": {"scanned": 51, "exact_deleted": 1, "similar_deleted": 7}}, 0)
    assert (stats.scanned, stats.exact_deleted, stats.similar_deleted) == (51, 1, 7)
    assert stats_from_session({}, 12).scanned == 12              # sessions from before 1.2


def test_automatic_progress_is_saved_at_most_once_a_second():
    class CountingStore:
        saved = False
        count = 0
        def save(self, data):
            self.count += 1
            self.saved = True
    store = CountingStore()
    ctx = RunContext(store=store)
    ctx.stats, ctx.groups = Stats(), [group(5)]
    for _ in range(100):
        ctx.save(throttle=True)
    assert store.count == 1
    ctx.flush()
    assert store.count == 2


# --- end to end ----------------------------------------------------------------
@pytest.fixture
def three_groups(library):
    for seed in (1, 2, 3):
        library.photo(f"p{seed}.png", seed)
        library.variant(f"p{seed}.png", f"p{seed}.jpg", quality=85)
    return library


def test_an_interrupted_session_ends_with_the_same_totals(three_groups, run_cli, library, tmp_path):
    single = tmp_path / "single"
    import shutil
    shutil.copytree(library.path, single)
    expected = summary(run_cli(single, "--auto"))

    # run 1: start, resolve group 1, quit at group 2, keep the progress
    first = run_cli(library.path, "-i", keys=["", "", "q", ""])
    assert "Progress saved" in first.stdout
    # run 2: resume, resolve groups 2 and 3, finish, quit
    second = run_cli(library.path, "-i", keys=["", "", "", "", ""])
    assert "Resuming" in second.stdout
    totals = summary(second)
    assert (totals["scanned"], totals["visual"]) == (expected["scanned"], expected["visual"]) == (6, 3)
    assert "Totals for the whole session" in second.stdout


def test_the_start_screen_locks_the_session_settings(three_groups, run_cli):
    run_cli(three_groups.path, "-i", keys=["", "q", ""])            # leave a saved session
    screen = run_cli(three_groups.path, keys=["2", "q"]).stdout
    assert "[1] Stages" in screen and "(locked)" in screen and "Resume saved session" in screen
    after_2 = screen.split("[2] Strictness")[2].splitlines()[0]    # second start screen, after pressing 2
    assert "‹strict (12)›" in after_2 and "✗loose (28)" in after_2


def test_n_discards_the_session_and_unlocks_everything(three_groups, run_cli):
    run_cli(three_groups.path, "-i", keys=["", "q", ""])
    screen = run_cli(three_groups.path, keys=["n", "y", "q"]).stdout
    assert "Saved session discarded" in screen
    last = screen.rsplit("[1] Stages", 1)[1]
    assert "(locked)" not in last and "Start scan" in last
