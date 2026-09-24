"""Interactive workflow: folder selection, start screen, running and summary."""

from __future__ import annotations

import os
import re
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .config import __version__
from .core_logic import (ExactGroup, Group, RunContext, Stats, UserQuit,
                        apply_dry_run, find_exact_duplicates, find_remuxes, find_similar_images,
                        dry_run_from_session, format_size, mode_badge, planned_changes,
                        resolve_limit, resume_review, review_dry_run_again)
from .image_utils import IMAGE_EXTENSIONS, STRICTNESS_PRESETS
from .video import VIDEO_EXTENSIONS, is_video
from .video import tools_available as video_tools_available
from .session import ActionLog, IgnoreList, SessionStore, resolve_log_path
from .ui import (StyleUI, badge, confirm, dim, disable_path_completion, display_width,
                enable_path_completion, error, exit_script, info, pad,
                print_banner, print_menu, print_primary_action, safe_input,
                success, terminal_width, transient, truncate, warn)

_NATURAL_RE = re.compile(r"(\d+)")
ROOT = "."

# Choices per setting, in the order the setting key cycles through them.
STAGE_OPTIONS = {"both": "exact + visual", "1": "exact only", "2": "visual only"}
CONFIRM_OPTIONS = {"uncertain": "borderline only", "always": "every group", "never": "never"}
MODE_OPTIONS = {"trash": "trash", "dry_run": "dry run", "permanent": "permanent"}
VIEWER_OPTIONS = {"auto": "auto", "identity": "identity", "imagecompare": "imagecompare",
                  "kitty": "kitty", "timg": "timg"}
SUBFOLDER_OPTIONS = {False: "excluded", True: "included"}
RENAME_OPTIONS = {True: "on", False: "off"}
VIDEO_OPTIONS = {True: "included", False: "excluded"}


# --- file discovery ----------------------------------------------------------
def natural_key(path: Path):
    """Sort 'IMG_2' before 'IMG_10' instead of lexicographically."""
    parts = _NATURAL_RE.split(path.name.lower())
    return (str(path.parent).lower(), [int(p) if p.isdigit() else p for p in parts])


def _natural_parts(rel: str):
    return [[int(p) if p.isdigit() else p for p in _NATURAL_RE.split(part.lower())]
            for part in PurePosixPath(rel).parts]


def folder_of(path: Path, base: Path) -> str:
    """Folder of *path* relative to *base* in '/' form; '.' for base itself."""
    return path.parent.relative_to(base).as_posix()


def find_images(base_dir: Path, recursive: bool, excluded=frozenset(), videos: bool = True) -> list[Path]:
    """Image files, skipping hidden files and folders (.thumbnails, .Trash, ...)
    and never following symlinks. *excluded* folders (relative, '/' form) only
    apply to recursive scans; '.' is the main folder itself."""
    extensions = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS if videos else IMAGE_EXTENSIONS
    found: list[Path] = []
    for root, dirs, files in os.walk(base_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if recursive else []
        root_path = Path(root)
        rel = root_path.relative_to(base_dir).as_posix()
        if recursive and rel in excluded:
            continue
        for name in files:
            if name.startswith("."):
                continue
            path = root_path / name
            if path.suffix.lower() in extensions and not path.is_symlink():
                found.append(path)
    return sorted(found, key=natural_key)


class FolderIndex:
    """All images below a folder, cached so screens can redraw instantly."""

    def __init__(self):
        self._cache: dict[Path, list[Path]] = {}

    def images(self, base_dir: Path) -> list[Path]:
        """All images and videos below *base_dir* (filtered by the callers)."""
        if base_dir not in self._cache:
            with transient("Counting files..."):
                self._cache[base_dir] = find_images(base_dir, recursive=True, videos=True)
        return self._cache[base_dir]

    def folder_counts(self, base_dir: Path, videos: bool = True) -> dict[str, int]:
        counts: dict[str, int] = {}
        for path in self.images(base_dir):
            if videos or not is_video(path):
                rel = folder_of(path, base_dir)
                counts[rel] = counts.get(rel, 0) + 1
        return counts

    def in_scope_files(self, base_dir: Path, settings: dict) -> list[Path]:
        files = []
        for path in self.images(base_dir):
            if is_video(path) and not settings.get("videos", True):
                continue
            rel = folder_of(path, base_dir)
            if not settings["recursive"]:
                if rel == ROOT:
                    files.append(path)
            elif rel not in settings["excluded"]:
                files.append(path)
        return files

    def in_scope(self, base_dir: Path, settings: dict) -> int:
        return len(self.in_scope_files(base_dir, settings))

    def clear(self) -> None:
        self._cache.clear()


def resolve_directory(initial: Path | None, index: FolderIndex,
                      allow_cancel: bool = False) -> Path | None:
    """Find a usable folder, prompting until one is given (lrckit behaviour).

    With *allow_cancel* an empty answer returns None instead of quitting.
    """
    current = initial
    while True:
        if current is not None:
            resolved = current.expanduser().resolve()
            if resolved == Path.home() or resolved == Path(resolved.anchor):
                warn(f"\nExecution in root or home directory ('{resolved}') detected.")
                warn("To prevent unintended large-scale deletions, please select a specific picture folder.")
            elif resolved.is_dir():
                if index.images(resolved):
                    return resolved
                warn(f"\nNo supported images or videos found in '{resolved}' (including subfolders).")
            elif not resolved.exists():
                error(f"\nPath does not exist: {resolved}")
            else:
                error(f"\nPath is not a directory: {resolved}")

        hint = "empty to cancel" if allow_cancel else "or 'q' to quit"
        enable_path_completion()
        raw = safe_input(f"{StyleUI.BOLD}Enter picture folder path ({hint}): {StyleUI.RESET}")
        disable_path_completion()

        cleaned = raw.strip().strip("'\" ")
        if not cleaned or cleaned.lower() == "q":
            if allow_cancel:
                return None
            exit_script()
        current = Path(cleaned)


# --- folder picker -----------------------------------------------------------
def folder_entries(base_dir: Path, index: FolderIndex) -> list[tuple[str, int]]:
    """(relative folder, images directly in it) for every subfolder holding images,
    plus the folders above them so the list reads like a tree. The main folder is
    not listed: it is always scanned."""
    counts = index.folder_counts(base_dir, videos=True)
    folders = set(counts)
    for rel in counts:
        if rel != ROOT:
            parts = PurePosixPath(rel).parts
            folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
    ordered = sorted((f for f in folders if f != ROOT), key=_natural_parts)
    return [(rel, counts.get(rel, 0)) for rel in ordered]


def _below(rel: str, entries) -> list[str]:
    """*rel* and every listed folder beneath it."""
    return [r for r, _ in entries if r == rel or r.startswith(rel + "/")]


def parse_numbers(text: str, upper: int) -> list[int] | None:
    """'3', '2-5', '1,4 7' -> numbers in 1..upper, or None when invalid."""
    numbers = []
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            return None
        low, high = int(match[1]), int(match[2] or match[1])
        low, high = min(low, high), max(low, high)
        if low < 1 or high > upper:
            return None
        numbers.extend(range(low, high + 1))
    return numbers or None


def choose_folders(base_dir: Path, index: FolderIndex, settings: dict) -> None:
    """Tree of subfolders with on/off boxes; edits settings['excluded'] in place.

    The main folder is switched only with its own key (m): numbers, ranges,
    "all" and "none" change subfolders only, so it can't be left out by accident."""
    excluded: set[str] = settings["excluded"]
    while True:
        entries = folder_entries(base_dir, index)
        print_banner(f"Choose folders to scan ({len(entries)} subfolders)")
        main_count = index.folder_counts(base_dir).get(ROOT, 0)
        main_on = ROOT not in excluded
        files_text = f"{main_count} file{'s' if main_count != 1 else ''} directly in it"
        if main_on:
            print(f"  [{StyleUI.GREEN}  m{StyleUI.RESET}] {StyleUI.GREEN}[✓]{StyleUI.RESET} {StyleUI.BOLD}Main folder{StyleUI.RESET}"
                  f"  {StyleUI.GRAY}{files_text}{StyleUI.RESET}\n")
        else:
            print(f"  [{StyleUI.GREEN}  m{StyleUI.RESET}] {StyleUI.GRAY}[ ]{StyleUI.RESET} {StyleUI.YELLOW}Main folder - excluded"
                  f"{StyleUI.RESET}  {StyleUI.GRAY}{files_text}{StyleUI.RESET}\n")
        if not entries:
            info("This folder has no subfolders with images or videos.")
            if ROOT in excluded:
                excluded.discard(ROOT)
                info("The main folder was switched back on, since it is the only folder.")
            return

        names = []
        for rel, _ in entries:
            parts = PurePosixPath(rel).parts
            names.append("  " * (len(parts) - 1) + parts[-1] + "/")
        name_width = min(max(display_width(n) for n in names), max(20, terminal_width() - 30))

        for number, ((rel, count), name) in enumerate(zip(entries, names), 1):
            on = rel not in excluded
            box = f"{StyleUI.GREEN}[✓]{StyleUI.RESET}" if on else f"{StyleUI.GRAY}[ ]{StyleUI.RESET}"
            shown = pad(truncate(name, name_width), name_width)
            shown = shown if on else f"{StyleUI.GRAY}{shown}{StyleUI.RESET}"
            amount = f"{count} image{'s' if count != 1 else ''}" if count else "—"
            print(f"  [{StyleUI.GREEN}{number:3d}{StyleUI.RESET}] {box} {shown}  {StyleUI.GRAY}{amount}{StyleUI.RESET}")

        chosen = sum(1 for rel, _ in entries if rel not in excluded)
        main_text = "main folder" if main_on else f"{StyleUI.YELLOW}main folder excluded{StyleUI.RESET}"
        print(f"\n{StyleUI.BOLD}Selected:{StyleUI.RESET} {main_text} + {chosen} of {len(entries)} subfolders · "
              f"{index.in_scope(base_dir, settings)} files")
        print_primary_action("Enter", "Done")
        print_menu([
            ("Select", [
                (f"1-{len(entries)}", "Switch folder and its subfolders on/off"),
                ("a", "Select all subfolders"),
                ("n", "Select no subfolders"),
                ("m", "Switch the main folder on/off"),
            ]),
        ])
        dim("  Several at once: 2-5 or 1,3,7 - each listed folder is switched.")

        choice = safe_input(f"{StyleUI.BOLD}Folders: {StyleUI.RESET}").strip().lower()
        if choice == "":
            if chosen == 0 and not main_on:
                warn("Nothing selected - no files would be scanned.")
            elif chosen == 0:
                warn("No subfolder selected - only the main folder will be scanned.")
            return
        if choice == "m":
            if main_on:
                excluded.add(ROOT)
            else:
                excluded.discard(ROOT)
        elif choice == "a":  # subfolders only: the main folder keeps its own state
            excluded.difference_update(rel for rel, _ in entries)
        elif choice == "n":
            excluded.update(rel for rel, _ in entries)
        else:
            numbers = parse_numbers(choice, len(entries))
            if numbers is None:
                warn("Unknown option.")
                continue
            # Every listed folder switches on its own, judged by its state before
            # this input ("4,1" selects 4 and unselects 1). A folder takes the
            # folders below it along; parents go first, so a listed subfolder's
            # own switch wins over its parent's.
            listed = {entries[n - 1][0] for n in numbers}
            targets = {rel: rel in excluded for rel in listed}  # True = switch on
            for rel in sorted(listed, key=lambda r: r.count("/")):
                for below in _below(rel, entries):
                    if targets[rel]:
                        excluded.discard(below)
                    else:
                        excluded.add(below)


# --- start screen ------------------------------------------------------------
def _cycle(value, options):
    options = list(options)
    return options[(options.index(value) + 1) % len(options)] if value in options else options[0]


_LABEL_WIDTH = 12


def _setting(key: str, label: str, options: dict, current, danger=None) -> None:
    """One settings row: every choice listed, the current one in normal bold
    text and the others greyed out. Without colours the current choice is
    wrapped in ‹ › so it stays recognisable. Wraps to the terminal width."""
    prefix = f"  {StyleUI.GRAY}[{key}]{StyleUI.RESET} {StyleUI.BOLD}{label:<{_LABEL_WIDTH}}{StyleUI.RESET} "
    indent = display_width(prefix)
    gap = "   "
    colored = bool(StyleUI.GRAY)

    cells = []
    for value, text in options.items():
        if value == current:
            color = StyleUI.RED if value == danger else ""
            shown = text if colored else f"‹{text}›"
            cells.append((display_width(shown), f"{color}{StyleUI.BOLD}{shown}{StyleUI.RESET}"))
        else:
            cells.append((display_width(text), f"{StyleUI.GRAY}{text}{StyleUI.RESET}"))

    width = terminal_width()
    lines, line, used = [], [], indent
    for cell_width, cell in cells:
        extra = cell_width + (len(gap) if line else 0)
        if line and used + extra > width:
            lines.append(gap.join(line))
            line, used = [], indent
            extra = cell_width
        line.append(cell)
        used += extra
    lines.append(gap.join(line))
    print(prefix + ("\n" + " " * indent).join(lines))


def _info_row(key: str, label: str, text: str) -> None:
    print(f"  {StyleUI.GRAY}[{key}]{StyleUI.RESET} {StyleUI.BOLD}{label:<{_LABEL_WIDTH}}{StyleUI.RESET} {text}")


def matching_settings(config: dict, settings: dict, target: Path, index: FolderIndex) -> dict:
    """What decided the groups of a session; stored with it and shown on resume."""
    limit, preset = resolve_limit(config)
    if not settings["recursive"]:
        folders = "this folder only"
    else:
        entries = folder_entries(target, index)
        chosen = sum(1 for rel, _ in entries if rel not in settings["excluded"])
        main = "main folder" if ROOT not in settings["excluded"] else "main folder excluded"
        folders = f"{main} + " + ("all subfolders" if chosen == len(entries) else f"{chosen} of {len(entries)} subfolders")
    return {"limit": limit, "preset": preset, "stages": settings["stages"],
            "recursive": settings["recursive"], "excluded": sorted(settings["excluded"]),
            "description": f"{preset} ({limit}) · {folders}"}


def session_state(saved: dict) -> tuple[bool, bool, int]:
    """(is a dry run, dry run finished, open groups) of a saved session."""
    open_ = sum(g["status"] in ("pending", "skipped") for g in saved["groups"])
    dry = bool(saved.get("dry_run"))
    return dry, dry and (bool(saved.get("finished")) or open_ == 0), open_


def saved_dry_run(saved: dict, rename: bool) -> "DryRun":
    exact, groups = dry_run_from_session(saved)
    result = DryRun(exact_groups=exact, groups=groups, scanned=int(saved.get("scanned", 0)))
    result.to_remove, result.to_rename = planned_changes(exact, groups, rename)
    return result


def _session_panel(saved: dict, rename: bool) -> None:
    groups = saved["groups"]
    done = sum(g["status"] == "done" for g in groups)
    skipped = sum(g["status"] == "skipped" for g in groups)
    ignored = sum(g["status"] == "ignored" for g in groups)
    dry, finished, open_ = session_state(saved)
    try:
        when = datetime.fromisoformat(saved.get("updated", "")).strftime("%d %b %Y %H:%M")
    except ValueError:
        when = "earlier"
    kind = badge("DRY RUN", StyleUI.CYAN) + " " if dry else ""
    if finished:
        plan = saved_dry_run(saved, rename)
        text = (f"{kind}{badge('FINISHED', StyleUI.GREEN)} from {when} · {len(groups)} group(s) · "
                f"{plan.to_remove} file(s) to remove, {plan.to_rename} to rename")
    else:
        text = (f"{kind}{badge('RESUMABLE', StyleUI.CYAN)} from {when} · {len(groups)} groups: "
                f"{StyleUI.GREEN}{done} done{StyleUI.RESET} · {StyleUI.YELLOW}{skipped} skipped{StyleUI.RESET} · "
                + (f"{StyleUI.MAGENTA}{ignored} not duplicates{StyleUI.RESET} · " if ignored else "")
                + f"{open_ - skipped} open")
    print(f"\n{StyleUI.BOLD}Saved session:{StyleUI.RESET} {text}")
    description = saved.get("matching", {}).get("description")
    if description:
        dim(f"               Matched with: {description}. Settings 1, 2, r and f only affect new scans.")


def _fate(mode: str, count: int) -> str:
    return {"trash": f"move {count} file(s) to the trash",
            "permanent": f"permanently delete {count} file(s)"}.get(mode, f"remove {count} file(s)")


def start_screen(target: Path, config: dict, settings: dict, index: FolderIndex,
                 store: SessionStore | None) -> tuple[str, dict | None]:
    """Show folder, saved session and settings.

    Returns (action, saved session) with action one of 'start', 'resume',
    'apply_saved', 'review_saved' or 'change'.
    """
    while True:
        saved = store.load() if store else None
        counts = index.folder_counts(target, videos=settings["videos"])
        direct, total = counts.get(ROOT, 0), sum(counts.values())
        scope_files = index.in_scope_files(target, settings)
        in_scope = len(scope_files)
        scope_videos = sum(1 for p in scope_files if is_video(p))

        print_banner("img_dedupe · Duplicate image finder")
        print(f"{StyleUI.BOLD}Folder:{StyleUI.RESET}  {StyleUI.GRAY}{target}{StyleUI.RESET}")
        scope = (f"{StyleUI.BOLD}{in_scope}{StyleUI.RESET} file(s) in scope "
                 f"{StyleUI.GRAY}({in_scope - scope_videos} images, {scope_videos} videos){StyleUI.RESET}")
        if not settings["recursive"] and total > direct:
            scope += f" {StyleUI.GRAY}({total - direct} more in subfolders - press r){StyleUI.RESET}"
        print(f"{StyleUI.BOLD}Files:{StyleUI.RESET}   {scope}")

        if saved:
            _session_panel(saved, config["rename_numbered"])
            saved_dry, saved_finished, open_groups = session_state(saved)
        else:
            saved_dry = saved_finished = False
            open_groups = 0

        limit, preset = resolve_limit(config)
        mode = config["delete_mode"]
        strictness = {name: f"{name} ({value})" for name, value in STRICTNESS_PRESETS.items()}
        if preset == "custom":
            strictness = {"custom": f"custom ({limit})", **strictness}

        print(f"\n{StyleUI.BOLD}Settings:{StyleUI.RESET} {mode_badge(mode)}")
        _setting("1", "Stages", STAGE_OPTIONS, settings["stages"])
        _setting("2", "Strictness", strictness, preset)
        _setting("3", "Confirm", CONFIRM_OPTIONS, settings["confirm"])
        _setting("4", "Delete mode", MODE_OPTIONS, mode, danger="permanent")
        _setting("5", "Viewer", VIEWER_OPTIONS, config["viewer"])
        _setting("6", "Rename (1)", RENAME_OPTIONS, config["rename_numbered"])
        _setting("7", "Videos", VIDEO_OPTIONS, settings["videos"])
        if settings["videos"] and not video_tools_available():
            dim(f"  {' ' * (_LABEL_WIDTH + 3)}ffmpeg not found: identical video copies are found, remuxes are not.")
        _setting("r", "Subfolders", SUBFOLDER_OPTIONS, settings["recursive"])
        if settings["recursive"]:
            entries = folder_entries(target, index)
            chosen = sum(1 for rel, _ in entries if rel not in settings["excluded"])
            main_text = ("main folder" if ROOT not in settings["excluded"]
                         else f"{StyleUI.YELLOW}main folder excluded{StyleUI.RESET}{StyleUI.BOLD}")
            subs = f"all {len(entries)} subfolders" if chosen == len(entries) else f"{chosen} of {len(entries)} subfolders"
            text = f"{StyleUI.BOLD}{main_text} + {subs}{StyleUI.RESET}"
            _info_row("f", "Folders", f"{text} {StyleUI.GRAY}- press f to choose{StyleUI.RESET}")
        print(f"  {StyleUI.GRAY}{' ' * (_LABEL_WIDTH + 5)}Press a setting's key to switch to the next choice.{StyleUI.RESET}")
        if mode == "dry_run":
            dim(f"  {' ' * (_LABEL_WIDTH + 3)}Dry runs change nothing and are not logged; their progress is saved.")

        if saved and saved_finished:
            plan = saved_dry_run(saved, config["rename_numbered"])
            what = _fate(real_delete_mode(config), plan.to_remove)
            if plan.to_rename:
                what += f", rename {plan.to_rename}"
            print_primary_action("Enter", f"Carry out the saved dry run: {what}")
        elif saved and open_groups:
            label = "dry run" if saved_dry else "session"
            print_primary_action("Enter", f"Resume saved {label} ({open_groups} open group(s))")
        elif in_scope < 2:
            warn("\nFewer than two files in scope - nothing to compare.")
        else:
            print_primary_action("Enter", f"Start scan ({in_scope} files)")

        settings_menu = [("1-7", "Next choice for a setting"), ("r", "Toggle subfolders")]
        if settings["recursive"]:
            settings_menu.append(("f", "Choose folders"))
        groups_menu = [("Settings", settings_menu)]
        if saved:
            session_menu = [("x", "Discard saved session"), ("n", "New scan instead")]
            if saved_finished:
                session_menu.insert(0, ("e", "Review its groups again for real"))
            groups_menu.append(("Session", session_menu))
        groups_menu.append(("Navigate", [("c", "Change directory"), ("q", "Quit")]))
        print_menu(groups_menu)

        choice = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
        if choice == "":
            if saved and saved_finished:
                return "apply_saved", saved
            if saved and open_groups:
                return "resume", saved
            if in_scope >= 2:
                return "start", None
            warn("Nothing to scan here - include subfolders (r) or change directory (c).")
        elif choice == "q":
            exit_script()
        elif choice == "c":
            return "change", None
        elif choice == "e" and saved and saved_finished:
            return "review_saved", saved
        elif choice in ("x", "n") and saved:
            if confirm(f"{StyleUI.YELLOW}Discard the saved session for this folder?{StyleUI.RESET}"):
                store.delete()
                info("Saved session discarded.")
                if choice == "n" and in_scope >= 2:
                    return "start", None
        elif choice == "1":
            settings["stages"] = _cycle(settings["stages"], STAGE_OPTIONS)
        elif choice == "2":
            config["strictness"] = _cycle(config["strictness"] if config["max_pixel_diff"] is None else "loose",
                                          STRICTNESS_PRESETS)
            config["max_pixel_diff"] = None  # a preset chosen here wins over a custom limit
        elif choice == "3":
            settings["confirm"] = _cycle(settings["confirm"], CONFIRM_OPTIONS)
        elif choice == "4":
            config["delete_mode"] = _cycle(config["delete_mode"], MODE_OPTIONS)
            if config["delete_mode"] == "permanent":
                warn("Files will be deleted permanently, not moved to the trash.")
        elif choice == "5":
            config["viewer"] = _cycle(config["viewer"], VIEWER_OPTIONS)
        elif choice == "6":
            config["rename_numbered"] = not config["rename_numbered"]
        elif choice == "7":
            settings["videos"] = not settings["videos"]
        elif choice == "r":
            settings["recursive"] = not settings["recursive"]
        elif choice == "f" and settings["recursive"]:
            choose_folders(target, index, settings)
        else:
            warn("Unknown option.")


# --- running -----------------------------------------------------------------
def print_summary(stats: Stats, delete_mode: str, action_log: ActionLog | None = None,
                  note: str | None = None) -> None:
    dry = delete_mode == "dry_run"
    verb = "to remove" if dry else "removed"
    print_banner("Summary" + (" - dry run" if dry else ""))
    print(f"  Scanned: {stats.scanned}   "
          f"{StyleUI.GREEN}Exact copies {verb}: {stats.exact_deleted}{StyleUI.RESET}   "
          + (f"{StyleUI.GREEN}Remuxed videos {verb}: {stats.remux_deleted}{StyleUI.RESET}   " if stats.remux_deleted else "")
          + 
          f"{StyleUI.GREEN}Identical images {verb}: {stats.similar_deleted}{StyleUI.RESET}")
    print(f"  {StyleUI.GREEN}{'To rename' if dry else 'Renamed'}: {stats.renamed}{StyleUI.RESET}   "
          f"{StyleUI.YELLOW}Groups skipped: {stats.groups_skipped}{StyleUI.RESET}   "
          + (f"{StyleUI.MAGENTA}Not duplicates: {stats.groups_ignored}{StyleUI.RESET}   " if stats.groups_ignored else "")
          + 
          f"{StyleUI.RED}Failed: {stats.failed}{StyleUI.RESET}   "
          f"{StyleUI.BOLD}Space {'that would be freed' if dry else 'freed'}: {format_size(stats.space_saved)}{StyleUI.RESET}")
    if dry:
        print(f"  {StyleUI.GRAY}Nothing was deleted or renamed.{StyleUI.RESET}")
    if action_log is not None and action_log.entries:
        print(f"  {StyleUI.GRAY}Log ({action_log.entries} entries): {action_log.path}{StyleUI.RESET}")
    if note:
        print(f"  {StyleUI.CYAN}{note}{StyleUI.RESET}")


@dataclass
class DryRun:
    """What a finished dry run found and decided, so it can be carried out without a rescan."""
    exact_groups: list[ExactGroup] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    scanned: int = 0
    to_remove: int = 0
    to_rename: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.to_remove or self.to_rename)


def real_delete_mode(base_config: dict) -> str:
    """The configured delete mode, or 'trash' if the configured one is a dry run too."""
    mode = base_config.get("_configured_delete_mode", base_config.get("delete_mode", "trash"))
    return "trash" if mode == "dry_run" else mode


def _summary_text(stats: Stats) -> str:
    return (f"{stats.exact_deleted + stats.remux_deleted + stats.similar_deleted} removed, {stats.renamed} renamed, "
            f"{stats.groups_skipped} skipped, {stats.groups_ignored} marked not duplicates, {stats.failed} failed")


def run_scan(target: Path, config: dict, settings: dict, index: FolderIndex,
             store: SessionStore | None, resume: dict | None = None,
             from_dry: DryRun | None = None, review_again: bool = False) -> tuple[Stats, bool, DryRun | None]:
    """Run a new scan, resume a saved one, or carry out a finished dry run.

    Returns (stats, quit_requested, dry_run_result). The last one is only set
    after a dry run (new or resumed) that finished normally.
    """
    dry = config["delete_mode"] == "dry_run"
    applying = from_dry is not None and not review_again
    # Applying asks nothing, so it saves nothing new. The saved dry run is only
    # deleted once applying has finished, so an interrupted apply can be repeated.
    ctx_store = None if applying else store

    fresh = resume is None and from_dry is None
    files = find_images(target, settings["recursive"], settings["excluded"], settings["videos"]) if fresh else []
    if fresh:
        scanned = len(files)
    else:
        scanned = from_dry.scanned if from_dry else int(resume.get("scanned", 0))
    stats = Stats(scanned=scanned)
    limit, preset = resolve_limit(config)
    kind = ("resumed dry run" if resume and dry else "resumed session" if resume
            else "dry run applied" if applying else "dry run reviewed again" if from_dry else "new scan")
    action_log = ActionLog(
        None if dry else resolve_log_path(config["log_file"]),
        f"img_dedupe {__version__} · folder {target} · mode {config['delete_mode']} · "
        f"limit {limit} ({preset}) · {kind}",
    )
    ctx = RunContext(
        action_log, ctx_store,
        matching=resume.get("matching") if resume else matching_settings(config, settings, target, index),
        created=resume.get("created") if resume else None,
        rename=config["rename_numbered"], dry=dry, scanned=scanned,
    )
    ctx.ignores = IgnoreList(target, enabled=not settings.get("no_ignore", False))

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(target))
        except ValueError:
            return str(path)

    try:
        keep_session = False
        if resume:
            info(f"\nResuming the saved {'dry run' if dry else 'session'} for {target}")
            keep_session = resume_review(resume, config, settings["confirm"], stats, rel, ctx)
        elif from_dry and review_again:
            info(f"\nReviewing the dry run's groups again for real ({MODE_OPTIONS[config['delete_mode']]}) - no rescan")
            keep_session = review_dry_run_again(from_dry.exact_groups, from_dry.groups, config,
                                                settings["confirm"], stats, rel, ctx)
        elif from_dry:
            info(f"\nApplying the dry run for real ({MODE_OPTIONS[config['delete_mode']]}) - no rescan")
            apply_dry_run(from_dry.exact_groups, from_dry.groups, config, stats, rel, ctx)
        else:
            videos = sum(1 for f in files if is_video(f))
            info(f"\nScanning {len(files) - videos} images and {videos} videos in {target}")
            if settings["stages"] in ("1", "both"):
                remaining, exact_groups = find_exact_duplicates(files, config, stats, rel, ctx)
                ctx.exact_groups = exact_groups
                remaining, remux_groups = find_remuxes(remaining, config, settings["confirm"], stats, rel, ctx)
                ctx.exact_groups = exact_groups + remux_groups
            else:
                remaining = files
            remaining = [f for f in remaining if not is_video(f)]  # visual matching is for images only
            if settings["stages"] in ("2", "both"):
                if len(remaining) > 1:
                    keep_session, groups = find_similar_images(remaining, config, settings["confirm"],
                                                               stats, rel, ctx)
                    ctx.groups = groups
                else:
                    warn("\nNot enough images left for a visual comparison.")
    except UserQuit:
        warn("\nStopped by user.")
        note = None
        if ctx.has_saved_session:
            if confirm("Save progress so you can continue this comparison later?", default=True):
                note = "Progress saved - choose this folder again to resume."
            else:
                store.delete()
                note = "Saved progress deleted."
        action_log.end(f"quit by user · {_summary_text(stats)}")
        print_summary(stats, config["delete_mode"], action_log, note)
        return stats, True, None
    except KeyboardInterrupt:
        action_log.end(f"interrupted (Ctrl+C) · {_summary_text(stats)}")
        note = None
        if ctx.has_saved_session:
            note = "Progress saved - run img_dedupe on this folder again to resume."
        elif applying and store is not None and store.load() is not None:
            note = ("The saved dry run is still there - carry it out again to finish; "
                    "files already handled are skipped.")
        print()
        print_summary(stats, config["delete_mode"], action_log, note)
        raise

    note = None
    result = None
    if dry and from_dry is None:
        result = DryRun(exact_groups=ctx.exact_groups, groups=ctx.groups or [], scanned=scanned)
        result.to_remove, result.to_rename = planned_changes(result.exact_groups, result.groups, ctx.rename)
        if store is not None:
            if result.has_changes:
                ctx.save(finished=True)  # kept until it is carried out
                note = "Dry run saved - it can also be carried out later from the start screen."
            else:
                store.delete()
    elif store is not None:
        if keep_session:
            note = "Session kept - choose this folder again to review the skipped groups."
        else:
            store.delete()
    action_log.end(f"finished · {_summary_text(stats)}")
    print_summary(stats, config["delete_mode"], action_log, note)
    return stats, False, result


def normalize_excluded(target: Path, folders) -> set[str]:
    result = set()
    for folder in folders or ():
        path = Path(folder).expanduser()
        if path.is_absolute():
            try:
                path = path.resolve().relative_to(target)
            except ValueError:
                warn(f"--exclude {folder} is not inside {target}, ignored.")
                continue
        rel = path.as_posix().strip("/") or ROOT
        if rel != ROOT and not (target / rel).is_dir():
            warn(f"--exclude {folder}: no such folder in {target}, ignored.")
            continue
        result.add(rel)
    return result


def run(initial: Path, base_config: dict, base_settings: dict, auto: bool, exclude=()) -> int:
    index = FolderIndex()
    use_sessions = bool(base_config.get("save_sessions", True)) and not auto

    if auto:
        target = initial.expanduser().resolve()
        if not target.is_dir():
            error(f"Not a directory: {target}")
            return 2
        settings = {**base_settings, "excluded": normalize_excluded(target, exclude)}
        stats, _, _ = run_scan(target, dict(base_config), settings, index, store=None)
        return 1 if stats.failed else 0

    target = resolve_directory(initial, index)
    config = dict(base_config)
    settings = {**base_settings, "excluded": normalize_excluded(target, exclude)}
    while True:
        store = SessionStore(target) if use_sessions else None
        action, saved = start_screen(target, config, settings, index, store)
        if action == "change":
            changed = resolve_directory(None, index, allow_cancel=True)
            if changed is None:
                info("Keeping the current folder.")
            else:
                target = changed
                settings["excluded"] = set()  # folder choices belong to the old folder
                success(f"Switched to {target}.")
            continue

        choice = None
        if action in ("apply_saved", "review_saved"):
            stats = carry_out_dry_run(target, config, base_config, settings, index, store,
                                      saved_dry_run(saved, config["rename_numbered"]),
                                      review=(action == "review_saved"))
            if stats is None:  # declined the permanent-deletion question
                continue
        else:
            run_config = config
            if action == "resume":
                run_config = {**config, "delete_mode": resume_mode(saved, config)}
            stats, quit_requested, dry_result = run_scan(target, run_config, settings, index, store,
                                                         resume=saved if action == "resume" else None)
            index.clear()  # files were removed or renamed, recount on the next screen
            if quit_requested:
                exit_script(1 if stats.failed else 0)
            if dry_result is not None and dry_result.has_changes:
                choice = continue_after_dry_run(target, config, base_config, settings, index, store, dry_result)

        if choice is None:
            print_primary_action("Enter", "Quit")
            print_menu([("Navigate", [("b", "Back to settings (scan again)"),
                                      ("c", "Change directory"), ("q", "Quit")])])
            answer = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
            choice = {"b": "back", "c": "change"}.get(answer, "quit")
        if choice == "change":
            changed = resolve_directory(None, index, allow_cancel=True)
            if changed is not None:
                target = changed
                settings["excluded"] = set()
                success(f"Switched to {target}.")
        elif choice != "back":
            exit_script(1 if stats.failed else 0)


def resume_mode(saved: dict, config: dict) -> str:
    """A session continues the way it was started: a dry run as a dry run, a
    real run never as a dry run (its earlier deletions already happened)."""
    if saved.get("dry_run"):
        mode = "dry_run"
        if config["delete_mode"] != mode:
            info("The saved session is a dry run, so it continues as a dry run.")
    else:
        mode = config["delete_mode"] if config["delete_mode"] != "dry_run" else real_delete_mode(config)
        if config["delete_mode"] == "dry_run":
            info(f"The saved session is a real run, so it continues with: {MODE_OPTIONS[mode]}.")
    return mode


def carry_out_dry_run(target: Path, config: dict, base_config: dict, settings: dict,
                      index: FolderIndex, store, dry: DryRun, review: bool) -> Stats | None:
    """Apply a dry run (or review its groups again) for real. None if declined."""
    real_mode = real_delete_mode(base_config)
    if real_mode == "permanent" and not review and not confirm(
            f"{StyleUI.RED}Permanently delete {dry.to_remove} file(s)? This cannot be undone.{StyleUI.RESET}"):
        return None
    real_config = {**config, "delete_mode": real_mode}
    stats, quit_requested, _ = run_scan(target, real_config, settings, index, store,
                                        from_dry=dry, review_again=review)
    index.clear()
    if quit_requested:
        exit_script(1 if stats.failed else 0)
    return stats


def continue_after_dry_run(target: Path, config: dict, base_config: dict, settings: dict,
                           index: FolderIndex, store, dry: DryRun) -> str | None:
    """Offer to carry out a finished dry run without rescanning.

    Returns 'back' or 'change' when the user navigates away, None after a real
    run has been done (the normal end menu follows).
    """
    real_mode = real_delete_mode(base_config)
    while True:
        choice = after_dry_run_menu(dry, real_mode)
        if choice == "quit":
            if store is not None:
                info("The dry run stays saved - choose this folder again to carry it out.")
            exit_script(0)
        if choice in ("back", "change"):
            return choice
        if choice not in ("apply", "review"):
            warn("Unknown option.")
            continue
        if carry_out_dry_run(target, config, base_config, settings, index, store, dry,
                             review=(choice == "review")) is not None:
            return None


def after_dry_run_menu(dry: DryRun, real_mode: str) -> str:
    """Offer to carry out the dry run. Returns 'apply', 'review', 'back', 'change' or 'quit'."""
    what = _fate(real_mode, dry.to_remove)
    if dry.to_rename:
        what += f", rename {dry.to_rename}"
    print(f"\n{StyleUI.BOLD}Dry run finished.{StyleUI.RESET} The groups found are kept, so continuing needs no rescan. "
          f"Real runs use the delete mode {mode_badge(real_mode)} from your config.")
    print_primary_action("Enter", f"Apply exactly what the dry run showed: {what}")
    print_menu([
        ("Continue for real", [("r", "Review the groups again (asks as configured)")]),
        ("Navigate", [("b", "Back to settings"), ("c", "Change directory"), ("q", "Quit")]),
    ])
    answer = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
    return {"": "apply", "r": "review", "b": "back", "c": "change", "q": "quit"}.get(answer, "unknown")
