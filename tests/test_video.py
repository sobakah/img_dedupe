"""Videos: identical copies and remuxes (needs ffmpeg; skipped without it)."""

import subprocess

import pytest

from conftest import make_video, remux, requires_ffmpeg, summary

pytestmark = requires_ffmpeg


def encoders() -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout


# H.264 takes the "picture data only" path; MPEG-4 (DivX/Xvid) is available in every
# ffmpeg build. Each codec is tested with the containers it is found in.
# (MPEG-4 inside MPEG-TS can't be read by ffmpeg; such files are left alone.)
CASES = [
    pytest.param("libx264", ["mkv", "ts", "avi"],
                 marks=pytest.mark.skipif(" libx264 " not in encoders(), reason="no H.264 encoder")),
    pytest.param("mpeg4", ["mkv", "avi"]),
]


@pytest.mark.parametrize("codec, containers", CASES)
def test_remuxes_are_found_and_re_encodes_are_not(library, run_cli, codec, containers):
    clip = make_video(library / "clip.mp4", codec=codec)
    for ext in containers:
        remux(clip, library / f"clip.{ext}")
    make_video(library / "other.mp4", codec=codec, size="320x180")          # a different video
    counts = summary(run_cli(library.path, "--auto", "--dry-run"))
    assert (counts["scanned"], counts["remux"], counts["exact"]) == (len(containers) + 2, len(containers), 0)


def test_mpeg4_in_mpeg_ts_is_left_alone(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip.ts")              # ffmpeg can't read its picture size: not comparable
    before = library.files()
    counts = summary(run_cli(library.path, "--auto"))
    assert (counts["remux"], counts["failed"]) == (0, 0)
    assert library.files() == before


def test_identical_video_copies_are_exact_copies(library, run_cli):
    make_video(library / "clip.mp4", codec="mpeg4")
    library.copy_of("clip.mp4", "backup/clip copy.mp4")
    counts = summary(run_cli(library.path, "-r", "--auto", "--dry-run"))
    assert (counts["exact"], counts["remux"]) == (1, 0)


def test_when_tracks_differ_the_copy_with_audio_is_kept(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "silent.mkv", "-an")                             # the same video without audio
    result = run_cli(library.path, "--auto", "--dry-run")
    assert "Kept         clip.mp4" in result.stdout
    assert "Would delete silent.mkv" in result.stdout


def test_when_tracks_differ_the_user_is_asked(library, run_cli):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "silent.mkv", "-an")
    result = run_cli(library.path, "--dry-run", keys=["", "s", "q"])     # start, skip the question, quit
    assert "TRACKS DIFFER" in result.stdout and "Skipped - all files kept." in result.stdout


def test_without_ffmpeg_remuxes_are_skipped_with_a_notice(library, run_cli, tmp_path):
    clip = make_video(library / "clip.mp4", codec="mpeg4")
    remux(clip, library / "clip.mkv")
    library.copy_of("clip.mp4", "clip copy.mp4")
    empty = tmp_path / "empty-path"
    empty.mkdir()
    result = run_cli(library.path, "--auto", "--dry-run", env={"PATH": str(empty)})
    assert "remuxed videos can't be detected" in result.stdout
    assert summary(result)["exact"] == 1


def test_no_videos_leaves_them_out(library, run_cli):
    make_video(library / "clip.mp4", codec="mpeg4")
    library.copy_of("clip.mp4", "clip copy.mp4")
    assert summary(run_cli(library.path, "--auto", "--dry-run", "--no-videos"))["scanned"] == 0
