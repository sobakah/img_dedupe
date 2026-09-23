"""Persistent state: the action log and resumable review sessions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import project_dir
from .ui import warn

SESSION_VERSION = 1


def state_dir() -> Path:
    """~/.local/state/img_dedupe (or $XDG_STATE_HOME), %LOCALAPPDATA% on Windows."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "img_dedupe"


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def resolve_log_path(setting) -> Path | None:
    """config 'log_file': null = default location, false = off, string = that file.

    Default: ``img_dedupe.log`` in the project folder when running from a
    source checkout, otherwise in the state directory
    (~/.local/state/img_dedupe). Relative paths count from that same folder.
    """
    if setting is False:
        return None
    base = project_dir()
    if base is not None and not os.access(base, os.W_OK):
        warn(f"The project folder {base} is not writable - logging to {state_dir()} instead.")
        base = None
    if base is None:
        base = state_dir()
    if setting in (None, "", True):
        return base / "img_dedupe.log"
    path = Path(str(setting)).expanduser()
    return path if path.is_absolute() else base / path


class ActionLog:
    """Append-only, human-readable record of every change made to files.

    One line per action, fields separated by ' | ' so it is easy to grep:
        2026-09-23 20:15:04 | TRASHED  | /pics/a (1).jpg | kept /pics/a.jpg | exact copy, automatic
    The session header is written lazily, so runs that change nothing leave no trace.
    """

    def __init__(self, path: Path | None, header: str):
        self.path, self.header = path, header
        self.entries = 0
        self._started = False
        self._failed = False

    def _write(self, line: str) -> None:
        if self.path is None or self._failed:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:  # reopened per line: survives crashes
                if not self._started:
                    handle.write(f"\n{_stamp()} | START    | {self.header}\n")
                    self._started = True
                handle.write(line + "\n")
        except OSError as exc:
            self._failed = True
            warn(f"Could not write the log file {self.path}: {exc}")

    def action(self, kind: str, path: Path, kept: Path | None = None, detail: str = "") -> None:
        parts = [_stamp(), f"{kind:<8}", str(path)]
        if kept is not None:
            parts.append(f"kept {kept}")
        if detail:
            parts.append(detail)
        self._write(" | ".join(parts))
        self.entries += 1

    def end(self, text: str) -> None:
        if self._started:
            self._write(f"{_stamp()} | END      | {text}")


class SessionStore:
    """One saved review session per folder, written atomically after every change."""

    def __init__(self, target: Path):
        key = hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:16]
        self.path = state_dir() / "sessions" / f"{key}.json"
        self.target = target
        self.saved = False
        self._warned = False

    def load(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("version") != SESSION_VERSION \
                or data.get("target") != str(self.target) or not data.get("groups"):
            return None
        return data

    def save(self, data: dict) -> None:
        payload = {"version": SESSION_VERSION, "target": str(self.target), **data,
                   "updated": datetime.now().isoformat(timespec="seconds")}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".session-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp, self.path)  # atomic: a crash never leaves half a file
            self.saved = True
        except OSError as exc:
            if not self._warned:
                self._warned = True
                warn(f"Could not save progress to {self.path}: {exc}")

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            warn(f"Could not delete saved progress {self.path}: {exc}")
        self.saved = False
