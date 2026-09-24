"""Terminal presentation: colours, prompts, menus, tables and progress.

Follows the same conventions as lrckit's ui module: plain ANSI styling via
StyleUI, banners, [BADGE] tags, grouped key menus with a highlighted primary
action, transient status lines, and readline-based path completion.
No third-party dependencies.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import sys
import time
import unicodedata

# --- readline shim -----------------------------------------------------------
# The stdlib module is POSIX-only. On Windows we fall back to pyreadline3 and,
# if that is missing too, to a no-op stub so the program still runs.
try:  # pragma: no cover - platform dependent
    import readline as _readline
except ImportError:  # pragma: no cover
    try:
        import pyreadline3 as _readline  # type: ignore[no-redef]
    except ImportError:
        _readline = None

HAS_READLINE = _readline is not None


def _rl(name: str):
    """Return a readline callable, or None when unavailable."""
    return getattr(_readline, name, None) if _readline else None


# --- colours -----------------------------------------------------------------
_ANSI = {
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
    "RED": "\033[31m",
    "GREEN": "\033[32m",
    "YELLOW": "\033[33m",
    "BLUE": "\033[34m",
    "MAGENTA": "\033[35m",
    "CYAN": "\033[36m",
    "WHITE": "\033[37m",
    "GRAY": "\033[90m",
    "STRIKE": "\033[9m",
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class StyleUI:
    RESET = BOLD = DIM = RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = GRAY = STRIKE = ""


_color_mode = "auto"


def colors_enabled() -> bool:
    mode = str(_color_mode).lower()
    if mode in ("never", "off", "false", "0"):
        return False
    if mode in ("always", "on", "true", "1"):
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return sys.stdout.isatty()


def apply_color_settings(mode: str | None = None) -> None:
    """(Re)configure StyleUI according to *mode* ('auto'/'always'/'never')."""
    global _color_mode
    if mode:
        _color_mode = mode
    enabled = colors_enabled()
    if enabled and os.name == "nt":  # pragma: no cover - platform dependent
        enabled = _enable_windows_ansi()
    for name, code in _ANSI.items():
        setattr(StyleUI, name, code if enabled else "")


def _enable_windows_ansi() -> bool:  # pragma: no cover - platform dependent
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


apply_color_settings()


# --- primitives --------------------------------------------------------------
def badge(text: str, color: str) -> str:
    return f"{color}{StyleUI.BOLD}[{text}]{StyleUI.RESET}"


def print_banner(text: str) -> None:
    width = min(terminal_width(), 72)
    print(f"\n{StyleUI.CYAN}{StyleUI.BOLD}{'─' * width}{StyleUI.RESET}")
    print(f"{StyleUI.CYAN}{StyleUI.BOLD} {text}{StyleUI.RESET}")
    print(f"{StyleUI.CYAN}{StyleUI.BOLD}{'─' * width}{StyleUI.RESET}")


def info(msg: str) -> None:
    print(f"{StyleUI.CYAN}{msg}{StyleUI.RESET}")


def success(msg: str) -> None:
    print(f"{StyleUI.GREEN}{msg}{StyleUI.RESET}")


def warn(msg: str) -> None:
    print(f"{StyleUI.YELLOW}{msg}{StyleUI.RESET}")


def error(msg: str) -> None:
    print(f"{StyleUI.RED}{msg}{StyleUI.RESET}")


def dim(msg: str) -> None:
    print(f"{StyleUI.GRAY}{msg}{StyleUI.RESET}")


def terminal_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:  # pragma: no cover
        return default


def safe_input(prompt: str = "", default_text: str = "") -> str:
    """input() with optional readline prefill.

    Ctrl+C and end-of-input both surface as KeyboardInterrupt, which the CLI
    turns into a clean "aborted by user" exit.
    """
    set_hook = _rl("set_startup_hook")
    insert_text = _rl("insert_text")
    try:
        if default_text and set_hook and insert_text:
            set_hook(lambda: insert_text(default_text))
        elif default_text:
            # No readline: show the prefill so the user can retype or accept it.
            print(f"{StyleUI.GRAY}(current: {default_text}){StyleUI.RESET}")
        return input(prompt)
    except EOFError:
        raise KeyboardInterrupt from None
    finally:
        if set_hook:
            set_hook(None)


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = safe_input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "j", "ja")


def exit_script(code: int = 0) -> None:
    print(f"\n{StyleUI.YELLOW}Program terminated.{StyleUI.RESET}")
    sys.exit(code)


# --- width-aware text --------------------------------------------------------
def display_width(text: str) -> int:
    """Terminal columns taken by *text*: ANSI codes are free, CJK is double."""
    width = 0
    for char in _ANSI_RE.sub("", text):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def truncate(text: str, width: int, keep_end: bool = False) -> str:
    """Shorten plain *text* to *width* columns with an ellipsis.

    ``keep_end=True`` cuts from the left, which keeps the file name of a path.
    """
    if display_width(text) <= width:
        return text
    budget = max(1, width - 1)
    chars = reversed(text) if keep_end else iter(text)
    kept, used = [], 0
    for char in chars:
        cost = display_width(char)
        if used + cost > budget:
            break
        kept.append(char)
        used += cost
    if keep_end:
        return "…" + "".join(reversed(kept))
    return "".join(kept) + "…"


def pad(text: str, width: int, align: str = "left") -> str:
    gap = " " * max(0, width - display_width(text))
    return gap + text if align == "right" else text + gap


def preview_text(text: str, max_lines: int = 16) -> None:
    lines = text.splitlines()
    body_width = max(20, terminal_width() - 4)

    print(f"\n{StyleUI.GRAY}┌─── PREVIEW ({min(len(lines), max_lines)}/{len(lines)} lines) ───{StyleUI.RESET}")
    for line in lines[:max_lines]:
        print(f"{StyleUI.GRAY}│{StyleUI.RESET} {truncate(line, body_width)}")
    if len(lines) > max_lines:
        print(f"{StyleUI.GRAY}│ ... ({len(lines) - max_lines} more lines){StyleUI.RESET}")
    print(f"{StyleUI.GRAY}└{'─' * 42}{StyleUI.RESET}")


# --- tables ------------------------------------------------------------------
class Column:
    """Table column. *flex* columns shrink (cutting from the left) to fit the
    terminal; *optional* columns are dropped first when space runs out."""

    def __init__(self, title: str, align: str = "left", flex: bool = False,
                 optional: bool = False, min_width: int = 12):
        self.title, self.align, self.flex = title, align, flex
        self.optional, self.min_width = optional, min_width


def print_table(columns: list[Column], rows: list[list], indent: int = 2, gap: int = 2) -> None:
    """Print aligned rows. A cell is plain text or ``(text, style)``; the special
    style ``"key"`` renders ``[n]`` with a green number, like lrckit's lists."""

    def split(cell):
        return (cell, "") if isinstance(cell, str) else cell

    active = list(range(len(columns)))

    def widths_for(indices):
        result = {}
        for i in indices:
            cells = [display_width(split(row[i])[0]) for row in rows]
            result[i] = max([display_width(columns[i].title)] + cells)
        return result

    usable = terminal_width() - indent
    widths = widths_for(active)

    def total(ws):
        return sum(ws.values()) + gap * (len(ws) - 1)

    def narrowest(ws):  # width with every flexible column at its minimum
        return sum(min(w, columns[j].min_width) if columns[j].flex else w for j, w in ws.items()) \
            + gap * (len(ws) - 1)

    # Drop optional columns, rightmost first, until the table can fit.
    for i in reversed(range(len(columns))):
        if narrowest(widths) <= usable:
            break
        if columns[i].optional:
            active.remove(i)
            widths.pop(i)

    # Shrink flexible columns to what is left.
    overflow = total(widths) - usable
    for i in active:
        if overflow <= 0:
            break
        if columns[i].flex:
            shrink = min(overflow, widths[i] - columns[i].min_width)
            if shrink > 0:
                widths[i] -= shrink
                overflow -= shrink

    def render(cell, i):
        text, style = split(cell)
        column = columns[i]
        if column.flex:
            text = truncate(text, widths[i], keep_end=True)
        last = i == active[-1] and column.align == "left"
        padded = text if last else pad(text, widths[i], column.align)  # no trailing blanks
        if style == "key" and text.startswith("[") and text.endswith("]"):
            inner = text[1:-1]
            return padded.replace(text, f"[{StyleUI.GREEN}{inner}{StyleUI.RESET}]", 1)
        if style:
            return f"{style}{padded}{StyleUI.RESET}"
        return padded

    spacer = " " * gap
    header = spacer.join(pad(columns[i].title, widths[i], columns[i].align) for i in active)
    print(" " * indent + f"{StyleUI.GRAY}{header.rstrip()}{StyleUI.RESET}")
    for row in rows:
        print(" " * indent + spacer.join(render(row[i], i) for i in active).rstrip())


# --- status lines ------------------------------------------------------------
def transient(msg: str):
    """Context manager printing a status line that is cleared afterwards."""

    class _Transient:
        def __enter__(self):
            if sys.stdout.isatty():
                print(f"{StyleUI.GRAY}{msg}{StyleUI.RESET}", end="\r", flush=True)
            return self

        def __exit__(self, *exc):
            if sys.stdout.isatty():
                print(" " * (display_width(msg) + 2), end="\r", flush=True)
            return False

    return _Transient()


class progress:
    """Transient ``label 120/3100 (4%)`` counter, cleared when done.

    Only drawn on a terminal, and at most ten times per second.
    """

    def __init__(self, label: str, total: int):
        self.label, self.total, self.done = label, total, 0
        self._last_draw = 0.0
        self._last_width = 0
        self._enabled = sys.stdout.isatty() and total > 0

    def __enter__(self):
        self._draw(force=True)
        return self

    def advance(self, amount: int = 1) -> None:
        self.done += amount
        self._draw()

    def _draw(self, force: bool = False) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < 0.1 and self.done < self.total:
            return
        self._last_draw = now
        percent = int(100 * self.done / self.total) if self.total else 100
        line = f"{self.label}... {self.done}/{self.total} ({percent}%)"
        self._last_width = display_width(line)
        print(f"\r{StyleUI.GRAY}{line}{StyleUI.RESET}", end="", flush=True)

    def __exit__(self, *exc):
        if self._enabled:
            print("\r" + " " * (self._last_width + 2) + "\r", end="", flush=True)
        return False


# --- menus -------------------------------------------------------------------
def print_menu(groups: list[tuple[str, list[tuple[str, str]]]], lead: str | None = None) -> None:
    """Render keyboard options as labelled groups in aligned columns.

    *groups* is a list of (group title, [(key, label), ...]). Column count and
    width adapt to the terminal, so a long option list stays readable instead
    of running into one wall of text.
    """
    entries = [entry for _, items in groups for entry in items]
    if not entries:
        return

    key_width = max(len(key) for key, _ in entries)
    label_width = max(len(label) for _, label in entries)
    cell_width = key_width + label_width + 4  # "[k] label"
    gap = 3
    usable = max(20, terminal_width() - 2)
    columns = max(1, (usable + gap) // (cell_width + gap))

    if lead:
        print(f"\n{lead}")
    else:
        print()

    for title, items in groups:
        if title:
            print(f"{StyleUI.GRAY}{title}{StyleUI.RESET}")
        for start in range(0, len(items), columns):
            row = items[start:start + columns]
            cells = []
            for position, (key, label) in enumerate(row):
                colored = f"[{_key_color(key)}{key}{StyleUI.RESET}]{' ' * (key_width - len(key))} {label}"
                padding = cell_width - (key_width + len(label) + 4)
                last = position == len(row) - 1
                cells.append(colored + ("" if last else " " * (padding + gap)))
            print("  " + "".join(cells).rstrip())


_DESTRUCTIVE_KEYS = {"q", "d", "k"}
_NAVIGATION_KEYS = {"p", "s", "t", "b", "c"}


def _key_color(key: str) -> str:
    lowered = key.lower()
    if lowered in _DESTRUCTIVE_KEYS:
        return StyleUI.RED
    if lowered in _NAVIGATION_KEYS:
        return StyleUI.YELLOW
    return StyleUI.GREEN


def print_primary_action(key: str, description: str) -> None:
    """Highlight the action Enter would take, above the regular menu."""
    print(f"\n  [{StyleUI.CYAN}{StyleUI.BOLD}{key}{StyleUI.RESET}] {StyleUI.BOLD}{description}{StyleUI.RESET}")


# --- path completion ---------------------------------------------------------
def complete_path(text: str, state: int):
    expanded = os.path.expanduser(text)
    matches = glob.glob(expanded + "*")
    results = [m + (os.sep if os.path.isdir(m) else " ") for m in matches]
    return results[state] if state < len(results) else None


def enable_path_completion() -> None:
    set_delims = _rl("set_completer_delims")
    parse_bind = _rl("parse_and_bind")
    set_completer = _rl("set_completer")
    if not (set_delims and parse_bind and set_completer):
        return
    set_delims(" \t\n;")
    parse_bind("tab: complete")
    set_completer(complete_path)


def disable_path_completion() -> None:
    set_completer = _rl("set_completer")
    if set_completer:
        set_completer(None)
