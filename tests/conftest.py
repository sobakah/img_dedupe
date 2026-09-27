"""Shared fixtures and helpers for the img_dedupe tests.

Every test runs isolated: its own home folder, config and state folder, so
your real config, log, sessions and trash are never touched.

The helpers are meant for writing new tests quickly:

    def test_my_case(library, run_cli):
        library.photo("a.png", seed=1)
        library.copy_of("a.png", "backup/a copy.png")
        result = run_cli(library.path, "-r", "--auto", "--dry-run")
        assert summary(result)["exact"] == 1
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import pictures

REPO = Path(__file__).resolve().parent.parent
FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not installed")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point home, config, state and trash into the test's temporary folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))  # the trash lives here
    monkeypatch.setenv("IMG_DEDUPE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NO_COLOR", "1")
    return tmp_path


# --- building picture folders ------------------------------------------------
class Library:
    """A folder of test pictures; paths are relative to it."""

    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)

    def __truediv__(self, rel: str) -> Path:
        return self.path / rel

    def photo(self, rel: str, seed: int, size=(320, 240), **options) -> Path:
        """A new photo-like picture; the format follows the extension."""
        return pictures.save(pictures.photo(seed, size), self / rel, **options)

    def variant(self, source: str, rel: str, change=None, **options) -> Path:
        """Save *source* again (e.g. another format or quality), optionally changed
        by *change*, a function from the pictures module such as with_text."""
        from PIL import Image
        with Image.open(self / source) as img:
            img = img.convert("RGB")
        if change is not None:
            img = change(img)
        return pictures.save(img, self / rel, **options)

    def copy_of(self, source: str, rel: str) -> Path:
        """A byte-identical copy (same content, other name or folder)."""
        (self / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self / source, self / rel)
        return self / rel

    def files(self) -> list[str]:
        """All files below the folder, relative, sorted, hidden ones included."""
        return sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*") if p.is_file())


@pytest.fixture
def library(tmp_path) -> Library:
    return Library(tmp_path / "pictures")


# --- running the program -----------------------------------------------------
@pytest.fixture
def config_file(tmp_path):
    """Write a config for run_cli: config_file({"strictness": "strict"})."""
    path = tmp_path / "config.json"
    path.write_text("{}")

    def write(data: dict) -> Path:
        path.write_text(json.dumps(data))
        return path

    write.path = path
    return write


@pytest.fixture
def run_cli(config_file):
    """Run img_dedupe as a user would: run_cli(folder, "--auto", keys=["", "q"]).

    *keys* are typed answers, one per prompt (use "" for Enter). The program
    always gets the test's own config file, so no real config is read.
    """
    def run(*args, keys=(), env=None, timeout=180):
        argv = [sys.executable, "-m", "scripts", "-c", str(config_file.path), "--color", "never", *map(str, args)]
        stdin = "".join(f"{key}\n" for key in keys)
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=REPO,
                              env={**os.environ, **(env or {})}, timeout=timeout)
    return run


_SUMMARY_FIELDS = {
    "scanned": r"Scanned: (\d+)",
    "exact": r"Exact copies (?:removed|to remove): (\d+)",
    "remux": r"Remuxed videos (?:removed|to remove): (\d+)",
    "visual": r"Identical images (?:removed|to remove): (\d+)",
    "renamed": r"(?:Renamed|To rename): (\d+)",
    "skipped": r"Groups skipped: (\d+)",
    "not_duplicates": r"Not duplicates: (\d+)",
    "failed": r"Failed: (\d+)",
}


def summary(result) -> dict:
    """The numbers of the last summary printed by run_cli (missing ones are 0)."""
    out = result.stdout if hasattr(result, "stdout") else str(result)
    last = out.rsplit("Summary", 1)[-1]
    found = {}
    for key, pattern in _SUMMARY_FIELDS.items():
        match = re.search(pattern, last)
        found[key] = int(match.group(1)) if match else 0
    return found


# --- stand-in programs -------------------------------------------------------
@pytest.fixture
def fake_command(tmp_path, monkeypatch):
    """Put a stand-in program on PATH that records how it was called.

    cmd = fake_command("xdg-open"); ...; cmd.calls() -> list of argument lists.
    With exit_code, `flatpak info` style checks can be answered.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def make(name: str, exit_code: int = 0, script: str = ""):
        log = tmp_path / f"{name}.calls"
        path = bindir / name
        path.write_text(f"#!/bin/sh\n{script}\nprintf '%s\\n' \"$*\" >> '{log}'\nexit {exit_code}\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

        class Command:
            def calls(self, count: int = 1, wait: float = 5.0) -> list[list[str]]:
                """The recorded calls, waiting up to *wait* seconds for *count* of
                them: viewers are started in the background and may take a moment."""
                def read():
                    return [line.split(" ") for line in log.read_text().splitlines()] if log.exists() else []
                end = time.time() + wait
                while len(read()) < count and time.time() < end:
                    time.sleep(0.05)
                return read()
        return Command()
    return make


# --- videos ------------------------------------------------------------------
def make_video(path: Path, seconds: int = 3, codec: str = "libx264", audio: bool = True, size="320x240") -> Path:
    """A short test video (moving test pattern, optional sine tone) made with ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
    if audio:
        argv += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    argv += ["-c:v", codec, "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(argv, check=True)
    return path


def remux(source: Path, target: Path, *extra: str) -> Path:
    """The same streams in another container (the extension decides which)."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-map", "0", "-c", "copy", *extra, str(target)], check=True)
    return target
