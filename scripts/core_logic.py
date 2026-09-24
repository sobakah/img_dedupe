"""Stage 1 (exact copies) and Stage 2 (visually identical images)."""

from __future__ import annotations

import concurrent.futures
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from send2trash import send2trash

from .image_utils import (JXL_SUPPORTED, STRICTNESS_PRESETS, analyze_image,
                         aspect_ratio_close, comparison_thumbnail,
                         has_copy_number, ignore_sigint, pixel_difference,
                         rename_target, score_image, sha256_file)
from .ui import (Column, StyleUI, badge, confirm, dim, error, info, print_banner,
                print_menu, print_primary_action, print_table, progress,
                safe_input, success, truncate, warn)
from .viewer import open_image_viewer

log = logging.getLogger(__name__)


class UserQuit(Exception):
    """Raised when the user quits mid-run, so the summary is still printed."""


@dataclass
class Stats:
    scanned: int = 0
    exact_deleted: int = 0
    similar_deleted: int = 0
    groups_skipped: int = 0
    groups_ignored: int = 0       # marked "not duplicates"
    renamed: int = 0
    failed: int = 0
    space_saved: int = 0


class RunContext:
    """Services for one run: action log, saved session and the rename default."""

    def __init__(self, action_log=None, store=None, matching: dict | None = None,
                 created: str | None = None, rename: bool = True, dry: bool = False,
                 scanned: int = 0):
        self.log = action_log
        self.store = store
        self.matching = matching or {}
        self.created = created or datetime.now().isoformat(timespec="seconds")
        self.rename = rename
        self.dry = dry            # a dry run: nothing was deleted, the session must say so
        self.scanned = scanned
        self.groups: list[Group] | None = None
        self.exact_groups: list[ExactGroup] = []   # kept for dry runs, to carry them out later
        self.index = 0
        self.finished = False
        self.ignores = None       # IgnoreList of pairs marked "not duplicates"

    def record(self, kind: str, path: Path, kept: Path | None = None, detail: str = "") -> None:
        if self.log is not None:
            self.log.action(kind, path, kept, detail)

    def save(self, groups=None, index: int | None = None, finished: bool | None = None) -> None:
        if groups is not None:
            self.groups = groups
        if index is not None:
            self.index = index
        if finished is not None:
            self.finished = finished
        # Nothing is worth saving before the groups are known, except a finished dry run.
        if self.store is None or (self.groups is None and not self.finished):
            return
        self.store.save({
            "created": self.created, "matching": self.matching, "index": self.index,
            "dry_run": self.dry, "finished": self.finished, "scanned": self.scanned,
            "groups": [g.to_dict() for g in self.groups or []],
            # A real run removed its exact copies right away; a dry run still has to.
            "exact_groups": [e.to_dict() for e in self.exact_groups] if self.dry else [],
        })

    @property
    def has_saved_session(self) -> bool:
        return self.store is not None and self.store.saved


def _popcount(x: int) -> int:
    return x.bit_count() if hasattr(x, "bit_count") else bin(x).count("1")


def format_size(size: float) -> str:
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024.0
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size / 1024:.1f} TB"


def resolve_limit(config: dict) -> tuple[int, str]:
    """Return (pixel difference limit, name of the preset or 'custom')."""
    if config["max_pixel_diff"] is not None:
        return int(config["max_pixel_diff"]), "custom"
    return STRICTNESS_PRESETS[config["strictness"]], config["strictness"]


def mode_badge(delete_mode: str) -> str:
    return {
        "dry_run": badge("DRY RUN", StyleUI.CYAN),
        "trash": badge("TRASH", StyleUI.YELLOW),
        "permanent": badge("PERMANENT", StyleUI.RED),
    }.get(delete_mode, badge(delete_mode.upper(), StyleUI.WHITE))


# --- changing files ----------------------------------------------------------
def _outcome(symbol: str, label: str, color: str, name: str) -> None:
    print(f"  {color}{symbol} {label:<13}{StyleUI.RESET}{name}")


def execute_deletion(keep: Path, others: list[tuple[Path, int]], delete_mode: str,
                     stats: Stats, kind: str, rel, ctx: RunContext, reason: str) -> int:
    """Remove *others*, keeping *keep*. Returns how many were removed."""
    if not keep.exists():  # never delete the last remaining copy
        error(f"  ! Kept file vanished, nothing deleted: {rel(keep)}")
        stats.failed += 1
        return 0
    _outcome("✓", "Kept", StyleUI.GREEN, rel(keep))

    removed = 0
    for path, size in others:
        try:
            if delete_mode == "dry_run":
                _outcome("○", "Would delete", StyleUI.CYAN, rel(path))
            elif delete_mode == "trash":
                send2trash(path)
                _outcome("✗", "Trashed", StyleUI.RED, rel(path))
                ctx.record("TRASHED", path, keep, reason)
            elif delete_mode == "permanent":
                path.unlink()
                _outcome("✗", "Deleted", StyleUI.RED, rel(path))
                ctx.record("DELETED", path, keep, reason)
            else:
                raise ValueError(f"unknown delete_mode {delete_mode!r}")
        except Exception as exc:
            error(f"  ! Failed        {rel(path)}: {exc}")
            ctx.record("FAILED", path, keep, f"{reason}; error: {exc}")
            stats.failed += 1
            continue
        removed += 1
        stats.space_saved += size
        if kind == "exact":
            stats.exact_deleted += 1
        else:
            stats.similar_deleted += 1
    return removed


def rename_kept(keep: Path, group_paths: list[Path], delete_mode: str, stats: Stats,
                rel, ctx: RunContext) -> Path:
    """Remove the copy number from the kept file's name. Returns its (new) path."""
    target = rename_target(keep, group_paths)
    if target is None or not keep.exists():
        return keep
    being_removed = {p for p in group_paths if p != keep}
    if delete_mode == "dry_run":
        if target.exists() and target not in being_removed:
            _outcome("!", "Name taken", StyleUI.YELLOW, f"{target.name} exists, {keep.name} would keep its name")
        else:
            _outcome("○", "Would rename", StyleUI.CYAN, f"{rel(keep)} → {target.name}")
            stats.renamed += 1
        return keep
    if target.exists():
        _outcome("!", "Not renamed", StyleUI.YELLOW, f"{target.name} already exists")
        return keep
    try:
        keep.rename(target)
    except OSError as exc:
        error(f"  ! Rename failed {rel(keep)}: {exc}")
        ctx.record("FAILED", keep, detail=f"rename to {target.name}; error: {exc}")
        stats.failed += 1
        return keep
    _outcome("✎", "Renamed", StyleUI.GREEN, f"{rel(keep)} → {target.name}")
    ctx.record("RENAMED", keep, detail=f"new name {target}")
    stats.renamed += 1
    return target


# --- Stage 1 -----------------------------------------------------------------
@dataclass
class ExactGroup:
    """Byte-identical files; files[0] is kept. Each entry: path, size, mtime_ns."""
    files: list[dict]

    @property
    def paths(self) -> list[Path]:
        return [f["path"] for f in self.files]

    def to_dict(self) -> dict:
        return {"files": [{**f, "path": str(f["path"])} for f in self.files]}

    @classmethod
    def from_dict(cls, data: dict) -> "ExactGroup":
        return cls([{**f, "path": Path(f["path"])} for f in data["files"]])


EXACT_REASON = "exact copy (identical bytes), automatic"


def _apply_exact(group: ExactGroup, number: int, total: int, config: dict, stats: Stats,
                 rel, ctx: RunContext, reason: str = EXACT_REASON) -> Path:
    """Show one exact group and remove its copies. Returns the kept file's path."""
    keep, size = group.paths[0], group.files[0]["size"]
    print(f"\n{StyleUI.BOLD}[{number}/{total}]{StyleUI.RESET} {badge('IDENTICAL', StyleUI.MAGENTA)} "
          f"{len(group.files)} files · {format_size(size)} each")
    rows = [[("▶", StyleUI.GREEN + StyleUI.BOLD) if i == 0 else " ",
             (f"[{i}]", "key"),
             ("KEEP", StyleUI.GREEN) if i == 0 else ("DEL", StyleUI.RED),
             (rel(p), StyleUI.BOLD) if i == 0 else rel(p)]
            for i, p in enumerate(group.paths)]
    print_table([Column(""), Column("#"), Column("Action"), Column("File", flex=True)], rows)
    execute_deletion(keep, [(p, size) for p in group.paths[1:]], config["delete_mode"], stats, "exact",
                     rel, ctx, reason)
    if ctx.rename:
        keep = rename_kept(keep, group.paths, config["delete_mode"], stats, rel, ctx)
    return keep


def find_exact_duplicates(files: list[Path], config: dict, stats: Stats, rel,
                          ctx: RunContext) -> tuple[list[Path], list[ExactGroup]]:
    """Returns (files left for Stage 2, the exact groups found)."""
    print_banner("Stage 1 · Exact duplicates")

    by_size = defaultdict(list)
    for f in files:
        try:
            by_size[f.stat().st_size].append(f)
        except OSError as exc:
            error(f"Cannot read {rel(f)}: {exc}")

    # Only files sharing a size can be identical, so most files are never read.
    remaining = [grp[0] for grp in by_size.values() if len(grp) == 1]
    to_hash = [f for grp in by_size.values() if len(grp) > 1 for f in grp]
    by_hash = defaultdict(list)
    with progress("Hashing same-size files", len(to_hash)) as bar:
        for f in to_hash:
            try:
                by_hash[sha256_file(f)].append(f)
            except OSError as exc:
                error(f"Cannot read {rel(f)}: {exc}")
            bar.advance()

    groups: list[ExactGroup] = []
    for paths in by_hash.values():
        if len(paths) == 1:
            remaining.append(paths[0])
            continue
        # Keep a name without "(1)" if there is one, otherwise the oldest copy.
        stats_by_path = {p: p.stat() for p in paths}
        paths.sort(key=lambda p: (has_copy_number(p), stats_by_path[p].st_mtime, str(p)))
        groups.append(ExactGroup([{"path": p, "size": stats_by_path[p].st_size,
                                   "mtime_ns": stats_by_path[p].st_mtime_ns} for p in paths]))

    if not groups:
        dim("No exact duplicates found.")
    for number, group in enumerate(groups, 1):
        remaining.append(_apply_exact(group, number, len(groups), config, stats, rel, ctx))

    return sorted(remaining), groups


# --- Stage 2: finding groups -------------------------------------------------
_SAVED_FIELDS = ("width", "height", "resolution", "format", "format_rank", "lossless",
                 "bpp", "age", "size", "mtime_ns", "numbered")


@dataclass
class Group:
    images: list[dict]            # images[0] is the recommended keeper
    diffs: list[int | None]       # difference of each image to images[0]
    limit: int
    borderline_above: float
    status: str = "pending"       # pending | skipped | done | ignored ("not duplicates")
    kept: int | None = None
    removed: int = 0
    rename: bool = True           # strip "(1)" from the kept file's name
    decision: str | None = None   # 'automatic' or 'user', for the log

    @property
    def worst(self) -> int:
        return max(d for d in self.diffs if d is not None)

    @property
    def borderline(self) -> bool:
        return self.worst > self.borderline_above

    @property
    def paths(self) -> list[Path]:
        return [m["path"] for m in self.images]

    @property
    def is_open(self) -> bool:
        """Still waiting for a decision (not resolved, not marked "not duplicates")."""
        return self.status in ("pending", "skipped")

    def verdict(self) -> str:
        if self.borderline:
            return badge("BORDERLINE", StyleUI.YELLOW)
        return badge("CONFIDENT ", StyleUI.GREEN)

    def status_badge(self) -> str:
        if self.status == "done":
            return badge("DONE   ", StyleUI.GREEN)
        if self.status == "skipped":
            return badge("SKIPPED", StyleUI.YELLOW)
        if self.status == "ignored":
            return badge("NOT DUP", StyleUI.MAGENTA)
        return badge("OPEN   ", StyleUI.GRAY)

    def to_dict(self) -> dict:
        return {
            "images": [{"path": str(m["path"]), **{k: m[k] for k in _SAVED_FIELDS}} for m in self.images],
            "diffs": self.diffs, "limit": self.limit, "borderline_above": self.borderline_above,
            "status": self.status, "kept": self.kept, "removed": self.removed, "rename": self.rename,
            "decision": self.decision,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Group":
        images = [{**m, "path": Path(m["path"])} for m in data["images"]]
        return cls(images=images, diffs=list(data["diffs"]), limit=data["limit"],
                   borderline_above=data["borderline_above"], status=data["status"],
                   kept=data.get("kept"), removed=data.get("removed", 0), rename=data.get("rename", True),
                   decision=data.get("decision"))


def _candidate_pairs(hashes: list[int], n_bits: int, max_dist: int) -> set[tuple[int, int]]:
    """All index pairs with Hamming distance <= max_dist.

    Uses multi-index hashing (split the hash into max_dist+1 chunks; any pair
    within max_dist agrees exactly on at least one chunk) when the chunks are
    big enough to be selective, otherwise compares every pair directly.
    """
    k = max_dist + 1
    width = n_bits // k
    if width < 6:
        return {(i, j) for i in range(len(hashes)) for j in range(i + 1, len(hashes))
                if _popcount(hashes[i] ^ hashes[j]) <= max_dist}
    pairs, checked = set(), set()
    for c in range(k):
        start = c * width
        w = width if c < k - 1 else n_bits - start
        mask = (1 << w) - 1
        buckets = defaultdict(list)
        for idx, h in enumerate(hashes):
            buckets[(h >> start) & mask].append(idx)
        for members in buckets.values():
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    pair = (members[a], members[b])
                    if pair in checked:
                        continue
                    checked.add(pair)
                    if _popcount(hashes[pair[0]] ^ hashes[pair[1]]) <= max_dist:
                        pairs.add(pair)
    return pairs


def _components(n: int, pairs) -> list[list[int]]:
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    comps = defaultdict(list)
    for i in range(n):
        comps[find(i)].append(i)
    return [c for c in comps.values() if len(c) > 1]


def _safe_thumbnail(path: Path, size: int):
    try:
        return comparison_thumbnail(path, size)
    except Exception:
        return None


def _parallel(executor, fn, items: list, label: str, *extra) -> list:
    results = []
    with progress(label, len(items)) as bar:
        for result in executor.map(fn, items, *[[e] * len(items) for e in extra], chunksize=8):
            results.append(result)
            bar.advance()
    return results


def drop_ignored(groups: list[Group], ignores) -> tuple[list[Group], int]:
    """Leave out images marked "not duplicates" of their group's recommended image.
    Returns (remaining groups, images left out)."""
    if ignores is None or not ignores.enabled or not len(ignores):
        return groups, 0
    kept, hidden = [], 0
    for group in groups:
        if not group.is_open:
            kept.append(group)
            continue
        pairs = [(m, d) for m, d in zip(group.images[1:], group.diffs[1:])
                 if not ignores.contains(group.images[0], m)]
        hidden += len(group.images) - 1 - len(pairs)
        if pairs:
            group.images = [group.images[0]] + [m for m, _ in pairs]
            group.diffs = [None] + [d for _, d in pairs]
            kept.append(group)
    return kept, hidden


def report_ignored(hidden: int, groups: list[Group]) -> None:
    if hidden:
        remaining = sum(g.is_open for g in groups)
        dim(f"{hidden} image(s) you marked as not duplicates were left out, {remaining} group(s) remain "
            f"(--no-ignore shows them again).")


def find_groups(files: list[Path], config: dict, rel) -> list[Group]:
    limit, _ = resolve_limit(config)
    borderline_above = limit * float(config["uncertain_ratio"])
    hash_size = config["hash_size"]
    max_hash = config["hash_max_distance"]

    # Workers ignore Ctrl+C; the main process cancels queued work so an
    # interrupt stops at once instead of finishing every pending image.
    executor = concurrent.futures.ProcessPoolExecutor(initializer=ignore_sigint)
    try:
        results = _parallel(executor, analyze_image, files, "Analyzing images",
                            hash_size, config["format_ranks"])

        failed = [r for r in results if "error" in r]
        animated = [r for r in results if "error" not in r and r["animated"]]
        metas = [r for r in results if "error" not in r and not r["animated"]]
        if failed:
            warn(f"{len(failed)} file(s) could not be decoded and were skipped:")
            for r in failed[:10]:
                dim(f"  • {rel(r['path'])}: {r['error']}")
            if len(failed) > 10:
                dim(f"  • ... and {len(failed) - 10} more")
            if any(r["path"].suffix.lower() == ".jxl" for r in failed) and not JXL_SUPPORTED:
                warn("  JPEG XL needs the optional plugin: pip install pillow-jxl-plugin")
        if animated:
            dim(f"{len(animated)} animated image(s) skipped (only exact duplicates are handled for those).")

        pairs = _candidate_pairs([m["hash"] for m in metas], 2 * hash_size * hash_size, max_hash)
        components = _components(len(metas), pairs)
        involved = sorted({i for comp in components for i in comp})

        thumbs = {}
        if involved:
            paths = [metas[i]["path"] for i in involved]
            for i, t in zip(involved, _parallel(executor, _safe_thumbnail, paths,
                                                 "Comparing pixels", config["compare_size"])):
                if t is not None:
                    thumbs[i] = t
    except BaseException:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    executor.shutdown(wait=True)

    groups: list[Group] = []
    for comp in components:
        comp = [i for i in comp if i in thumbs]
        comp.sort(key=lambda i: score_image(metas[i]), reverse=True)
        while len(comp) > 1:
            anchor, rest = comp[0], comp[1:]
            members, leftover = [], []
            for i in rest:
                # Every deleted image is checked directly against the image that is
                # KEPT, so A~B and B~C can never chain into deleting a C unlike A.
                distance = _popcount(metas[anchor]["hash"] ^ metas[i]["hash"])
                if distance <= max_hash and aspect_ratio_close(metas[anchor], metas[i], config["max_aspect_diff"]):
                    diff = pixel_difference(thumbs[anchor], thumbs[i])
                    log.debug("%s vs %s: hash distance %d, pixel diff %d",
                              metas[anchor]["path"].name, metas[i]["path"].name, distance, diff)
                    if diff <= limit:
                        members.append((i, diff))
                        continue
                leftover.append(i)
            if members:
                groups.append(Group(
                    images=[metas[anchor]] + [metas[i] for i, _ in members],
                    diffs=[None] + [d for _, d in members],
                    limit=limit,
                    borderline_above=borderline_above,
                ))
            comp = leftover

    # Stable, predictable order: by the keeper's path.
    groups.sort(key=lambda g: str(g.images[0]["path"]).lower())
    info(f"Analyzed {len(metas)} images · {len(pairs)} candidate pair(s) · "
         f"{len(groups)} duplicate group(s)")
    return groups


# --- Stage 2: presenting and resolving groups --------------------------------
def _group_table(group: Group, rel, keep_index: int = 0, done: bool = False) -> None:
    rows = []
    for i, (meta, diff) in enumerate(zip(group.images, group.diffs)):
        kept = i == keep_index
        if done:
            action = ("KEPT", StyleUI.GREEN) if kept else ("GONE", StyleUI.GRAY)
        else:
            action = ("KEEP", StyleUI.GREEN) if kept else ("DEL", StyleUI.RED)
        if diff is None:
            diff_cell = ("—", StyleUI.GRAY)
        else:
            diff_cell = (str(diff), StyleUI.YELLOW if diff > group.borderline_above else StyleUI.GREEN)
        rows.append([
            ("▶", StyleUI.GREEN + StyleUI.BOLD) if kept else " ",
            (f"[{i}]", "key"),
            action,
            (rel(meta["path"]), StyleUI.BOLD) if kept else rel(meta["path"]),
            (meta["format"], StyleUI.MAGENTA),
            ("lossless" if meta["lossless"] else "lossy", StyleUI.GRAY),
            f"{meta['width']}x{meta['height']}",
            format_size(meta["size"]),
            (f"{meta['bpp']:.2f}", StyleUI.GRAY),
            diff_cell,
        ])
    print_table([
        Column(""), Column("#"), Column("Action"), Column("File", flex=True, min_width=16),
        Column("Fmt"), Column("Type", optional=True), Column("Resolution", align="right"),
        Column("Size", align="right"), Column("BPP", align="right", optional=True),
        Column("Diff", align="right"),
    ], rows)


def _group_heading(group: Group) -> str:
    return (f"{group.verdict()} worst difference {group.worst} of {group.limit} allowed "
            f"{StyleUI.GRAY}(0 = pixel-identical){StyleUI.RESET}")


def _how(automatic: bool, keep_index: int) -> str:
    if automatic:
        return "automatic"
    return "confirmed by user" if keep_index == 0 else f"user kept #{keep_index}"


def _resolve(group: Group, keep_index: int, config: dict, stats: Stats, rel,
             ctx: RunContext, automatic: bool, reason: str | None = None) -> None:
    keep = group.images[keep_index]["path"]
    others = [(m["path"], m["size"]) for i, m in enumerate(group.images) if i != keep_index]
    group.decision = "automatic" if automatic else "user"
    if reason is None:
        reason = f"visual match (worst diff {group.worst}/{group.limit}), {_how(automatic, keep_index)}"
    group.removed = execute_deletion(keep, others, config["delete_mode"], stats, "similar", rel, ctx, reason)
    if group.rename:
        group.images[keep_index]["path"] = rename_kept(keep, group.paths, config["delete_mode"], stats, rel, ctx)
    group.kept = keep_index
    group.status = "done"


def _keep_description(group: Group, delete_mode: str, rel) -> str:
    """Text for the Enter line: which file stays, its new name, what happens to the rest."""
    others = len(group.images) - 1
    keep = group.images[0]["path"]
    text = f"Keep ▶ #0 {truncate(rel(keep), 34, keep_end=True)}"
    target = rename_target(keep, group.paths) if group.rename else None
    if target is not None:
        text += f" as {truncate(target.name, 24, keep_end=True)}"
    fate = {
        "trash": f"move the other {others} to the trash",
        "permanent": f"permanently delete the other {others}",
        "dry_run": f"delete the other {others} (dry run)",
    }.get(delete_mode, f"remove the other {others}")
    return f"{text}, {fate}"


def _decision_screen(group: Group, position: str, config: dict, stats: Stats, rel, ctx: RunContext) -> str:
    """Full screen for one group. Returns 'next', 'prev' or 'overview'."""
    count = len(group.images)
    others = "1" if count == 2 else f"1-{count - 1}"
    renamable = any(rename_target(p, group.paths) for p in group.paths)
    while True:
        # Always redraw the group: inline viewers (timg, kitty) and messages push
        # the list off screen, and the options are useless without it.
        print_banner(f"[{position}] Duplicate group · {count} images")
        print(_group_heading(group))
        if group.status == "skipped":
            dim("You skipped this group earlier.")
        print()
        _group_table(group, rel)

        print_primary_action("Enter", _keep_description(group, config["delete_mode"], rel))
        resolve = [(others, "Keep that image instead"), ("s", "Skip, keep all files"),
                   ("i", "Not duplicates - don't show again")]
        if count > 2:
            resolve.append((f"i1-i{count - 1}", "Only that image is not a duplicate"))
        if renamable:
            resolve.append(("n", f"Remove \"(1)\" from kept name: {'on' if group.rename else 'off'}"))
        print_menu([
            ("Resolve", resolve),
            ("Compare", [("v", "Open images in viewer")]),
            ("Navigate", [("p", "Previous open group"), ("t", "Groups overview"), ("q", "Quit")]),
        ])

        choice = safe_input(f"{StyleUI.BOLD}Group {position} action: {StyleUI.RESET}").strip().lower()

        if choice == "s":
            if group.status == "pending":
                group.status = "skipped"
                stats.groups_skipped += 1
            dim("Skipped - all files kept.")
            return "next"
        if choice == "q":
            raise UserQuit
        if choice == "p":
            return "prev"
        if choice == "t":
            return "overview"
        if choice == "i":
            for meta in group.images[1:]:
                ctx.ignores.add(group.images[0], meta)
            if group.status == "skipped":
                stats.groups_skipped -= 1
            group.status = "ignored"
            stats.groups_ignored += 1
            dim("Marked as not duplicates - this group won't be shown again.")
            return "next"
        single = re.fullmatch(r"i(\d+)", choice)
        if single:
            number = int(single[1])
            if not 1 <= number < count:
                warn(f"Choose an image from 1 to {count - 1}.")
                continue
            ctx.ignores.add(group.images[0], group.images[number])
            dim(f"Marked #{number} {rel(group.images[number]['path'])} as not a duplicate.")
            del group.images[number], group.diffs[number]
            if len(group.images) == 1:
                if group.status == "skipped":
                    stats.groups_skipped -= 1
                group.status = "ignored"
                stats.groups_ignored += 1
                return "next"
            count = len(group.images)
            others = "1" if count == 2 else f"1-{count - 1}"
            renamable = any(rename_target(p, group.paths) for p in group.paths)
            ctx.save()
            continue
        if choice == "n" and renamable:
            group.rename = not group.rename
            ctx.save()
            continue
        if choice == "v":
            used = open_image_viewer(group.paths, config["viewer"])
            dim(f"Opened {count} images with {used}.")
            continue

        # Enter keeps the recommendation; "k" still works for old muscle memory.
        keep_index = 0 if choice in ("", "k") else int(choice) if choice.isdigit() else None
        if keep_index is None or not 0 <= keep_index < count:
            warn("Unknown option.")
            continue

        if keep_index != 0:  # show the new selection before acting on it
            print()
            _group_table(group, rel, keep_index=keep_index)
        if config["delete_mode"] == "permanent" and not confirm(
                f"{StyleUI.RED}Permanently delete {count - 1} file(s)? This cannot be undone.{StyleUI.RESET}"):
            info("Nothing deleted.")
            continue
        if group.status == "skipped":
            stats.groups_skipped -= 1
        _resolve(group, keep_index, config, stats, rel, ctx, automatic=False)
        return "next"


def _handled_screen(group: Group, position: str, rel) -> str:
    print_banner(f"[{position}] Duplicate group · {len(group.images)} images")
    print(_group_heading(group))
    print()
    if group.status == "ignored":
        _group_table(group, rel)
        info("\nMarked as not duplicates - all files were kept. Delete .img_dedupe_ignore.json "
             "in the scanned folder to be asked about such pairs again.")
    else:
        _group_table(group, rel, keep_index=group.kept or 0, done=True)
        info(f"\nAlready handled: kept #{group.kept}, {group.removed} file(s) removed.")
    print_primary_action("Enter", "Continue")
    print_menu([("Navigate", [("p", "Previous open group"), ("t", "Groups overview"), ("q", "Quit")])])
    choice = safe_input(f"{StyleUI.BOLD}Group {position} action: {StyleUI.RESET}").strip().lower()
    if choice == "q":
        raise UserQuit
    return {"p": "prev", "t": "overview"}.get(choice, "next")


def _overview(groups: list[Group], current: int | None, rel) -> int | None:
    """Tree-style list of all groups. Returns a group index, or None to finish."""
    while True:
        print_banner(f"Duplicate groups overview ({len(groups)} groups)")
        for number, group in enumerate(groups, 1):
            selected = current == number - 1
            marker = f"{StyleUI.GREEN}{StyleUI.BOLD}▶{StyleUI.RESET}" if selected else " "
            keeper = group.images[group.kept if group.kept is not None else 0]["path"]
            name = f"{StyleUI.BOLD}{rel(keeper)}{StyleUI.RESET}" if selected else rel(keeper)
            print(f"{marker} [{StyleUI.GREEN}{number:3d}{StyleUI.RESET}] {group.status_badge()} "
                  f"{group.verdict()} {len(group.images)} images · diff {group.worst}/{group.limit} · {name}")

        done = sum(g.status == "done" for g in groups)
        skipped = sum(g.status == "skipped" for g in groups)
        ignored = sum(g.status == "ignored" for g in groups)
        pending = len(groups) - done - skipped - ignored
        progress_line = (f"\n{StyleUI.BOLD}Progress:{StyleUI.RESET} {StyleUI.GREEN}{done} done{StyleUI.RESET}   "
                         f"{StyleUI.YELLOW}{skipped} skipped{StyleUI.RESET}   ")
        if ignored:
            progress_line += f"{StyleUI.MAGENTA}{ignored} not duplicates{StyleUI.RESET}   "
        print(progress_line + f"{StyleUI.GRAY}{pending} open{StyleUI.RESET}")
        if current is not None:
            print_primary_action("Enter", f"Continue with group {current + 1}")
        else:
            print_primary_action("Enter", "Finish and show summary")
        print_menu([("Navigate", [(f"1-{len(groups)}", "Jump to group"), ("q", "Quit")])])

        choice = safe_input(f"{StyleUI.BOLD}Action: {StyleUI.RESET}").strip().lower()
        if choice == "":
            return current
        if choice == "q":
            raise UserQuit
        if choice.isdigit() and 1 <= int(choice) <= len(groups):
            return int(choice) - 1
        warn("Unknown option.")


def review_groups(groups: list[Group], config: dict, confirm_mode: str, stats: Stats, rel,
                  ctx: RunContext, start: int = 0) -> bool:
    """Walk through the groups like lrckit walks through tracks.

    Groups that need no confirmation are resolved as the walk reaches them;
    the others get a full screen. Numbers ([3/12]) always refer to the
    position in the complete list. The session is saved at every step.
    Returns True when the user wants to keep the saved session.
    """
    total = len(groups)
    index = start
    revisit = start > 0    # arrived via overview/prev/resume: show handled groups too
    interactive = False    # at least one decision screen was shown

    while True:
        ctx.save(groups, min(index, total - 1))
        if index >= total:
            if interactive:
                success("\nReached the last group.")
                choice = _overview(groups, None, rel)
                if choice is not None:
                    index, revisit = choice, True
                    continue
            if ctx.dry:  # a dry run's session is always kept, to carry it out later
                return True
            left = sum(g.is_open for g in groups)
            if left and ctx.store is not None:
                return confirm(f"Keep the saved session to come back to the {left} skipped group(s) later?")
            return False

        group = groups[index]
        position = f"{index + 1}/{total}"
        needs_prompt = confirm_mode == "always" or (confirm_mode == "uncertain" and group.borderline)

        if not group.is_open:
            if not revisit:
                index += 1
                continue
            navigation = _handled_screen(group, position, rel)
        elif group.status == "pending" and not needs_prompt:
            print(f"\n{StyleUI.BOLD}[{position}]{StyleUI.RESET} {_group_heading(group)}")
            _group_table(group, rel)
            _resolve(group, 0, config, stats, rel, ctx, automatic=True)
            index += 1
            continue
        else:
            interactive = True
            navigation = _decision_screen(group, position, config, stats, rel, ctx)

        revisit = False
        if navigation == "next":
            index += 1
        elif navigation == "prev":
            earlier = [i for i in range(index) if groups[i].is_open]
            if earlier:
                index, revisit = earlier[-1], True
            else:
                warn("No earlier open group.")
                revisit = True
        elif navigation == "overview":
            choice = _overview(groups, index, rel)
            index, revisit = (index if choice is None else choice), True


def find_similar_images(files: list[Path], config: dict, confirm_mode: str, stats: Stats, rel,
                        ctx: RunContext) -> tuple[bool, list[Group]]:
    """Returns (keep the saved session?, the groups found)."""
    limit, preset = resolve_limit(config)
    print_banner("Stage 2 · Visually identical images")
    print(f"{StyleUI.BOLD}Limit:{StyleUI.RESET} {limit} ({preset})   "
          f"{StyleUI.BOLD}Confirm:{StyleUI.RESET} {confirm_mode}   {mode_badge(config['delete_mode'])}")

    groups, hidden = drop_ignored(find_groups(files, config, rel), ctx.ignores)
    report_ignored(hidden, groups)
    if not groups:
        dim("No visually identical images found.")
        return False, groups
    for group in groups:
        group.rename = ctx.rename
    return review_groups(groups, config, confirm_mode, stats, rel, ctx), groups


# --- resuming ----------------------------------------------------------------
def _stamp_matches(meta: dict) -> bool:
    try:
        stat = meta["path"].stat()
    except OSError:
        return False
    return stat.st_mtime_ns == meta["mtime_ns"] and stat.st_size == meta["size"]


def validate_groups(groups: list[Group]) -> tuple[list[Group], int, int]:
    """Check open groups against the disk. Images that vanished or changed are
    dropped; a group whose keeper changed, or with fewer than two images left,
    is dropped entirely. Returns (groups, dropped groups, dropped images)."""
    kept_groups, dropped_groups, dropped_images = [], 0, 0
    for group in groups:
        if not group.is_open:
            kept_groups.append(group)
            continue
        if not _stamp_matches(group.images[0]):
            dropped_groups += 1
            continue
        pairs = [(m, d) for m, d in zip(group.images[1:], group.diffs[1:]) if _stamp_matches(m)]
        dropped_images += len(group.images) - 1 - len(pairs)
        if not pairs:
            dropped_groups += 1
            continue
        group.images = [group.images[0]] + [m for m, _ in pairs]
        group.diffs = [None] + [d for _, d in pairs]
        kept_groups.append(group)
    return kept_groups, dropped_groups, dropped_images


def resume_review(data: dict, config: dict, confirm_mode: str, stats: Stats, rel, ctx: RunContext) -> bool:
    print_banner("Stage 2 · Visually identical images (resumed)" + (" - dry run" if ctx.dry else ""))
    ctx.exact_groups = [ExactGroup.from_dict(e) for e in data.get("exact_groups", [])]
    groups = [Group.from_dict(g) for g in data["groups"]]
    groups, dropped_groups, dropped_images = validate_groups(groups)
    ctx.groups = groups
    if dropped_groups or dropped_images:
        warn(f"Files changed on disk since the session was saved: {dropped_groups} group(s) and "
             f"{dropped_images} image(s) were dropped. Run a new scan to pick them up again.")
    groups, hidden = drop_ignored(groups, ctx.ignores)
    ctx.groups = groups
    report_ignored(hidden, groups)
    open_groups = [i for i, g in enumerate(groups) if g.is_open]
    if not open_groups:
        info("Nothing left to review in the saved session.")
        return False
    done = len(groups) - len(open_groups)
    info(f"Resuming: {done} of {len(groups)} groups already done, {len(open_groups)} to go.")
    saved_index = int(data.get("index", 0))
    start = saved_index if saved_index in open_groups else open_groups[0]
    return review_groups(groups, config, confirm_mode, stats, rel, ctx, start=start)


# --- continuing after a dry run ----------------------------------------------
def _file_unchanged(path: Path, size: int, mtime_ns: int) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size == size and stat.st_mtime_ns == mtime_ns


def _check_exact(groups: list[ExactGroup]) -> tuple[list[ExactGroup], int]:
    """Drop files that changed since the dry run; returns (groups, files dropped)."""
    checked, dropped = [], 0
    for group in groups:
        if not _file_unchanged(group.paths[0], group.files[0]["size"], group.files[0]["mtime_ns"]):
            dropped += len(group.files)
            continue
        files = [group.files[0]] + [f for f in group.files[1:]
                                    if _file_unchanged(f["path"], f["size"], f["mtime_ns"])]
        dropped += len(group.files) - len(files)
        if len(files) > 1:
            checked.append(ExactGroup(files))
    return checked, dropped


def _check_decided(groups: list[Group]) -> tuple[list[Group], int]:
    """Like validate_groups, but the keeper is the image chosen in the dry run."""
    checked, dropped = [], 0
    for group in groups:
        keep_index = group.kept if group.kept is not None else 0
        keeper = group.images[keep_index]
        if not _stamp_matches(keeper):
            dropped += len(group.images)
            continue
        others = [i for i, m in enumerate(group.images) if i != keep_index and _stamp_matches(m)]
        dropped += len(group.images) - 1 - len(others)
        if not others:
            continue
        # Rebuild with the keeper first. Diffs were measured against the original
        # #0; that image itself gets its diff to the kept one (stored on the keeper).
        diffs = [None]
        for i in others:
            diffs.append(group.diffs[i] if group.diffs[i] is not None else group.diffs[keep_index])
        group.images = [keeper] + [group.images[i] for i in others]
        group.diffs = diffs
        group.kept = 0
        checked.append(group)
    return checked, dropped


def _follow_renames(groups: list[Group], renamed: dict[Path, Path]) -> None:
    """Stage 1 may have renamed a file ("a (1).png" -> "a.png") that Stage 2 groups refer to."""
    for group in groups:
        for meta in group.images:
            if meta["path"] in renamed:
                meta["path"] = renamed[meta["path"]]


def _apply_all_exact(exact_groups: list[ExactGroup], config: dict, stats: Stats, rel,
                     ctx: RunContext, reason: str) -> dict[Path, Path]:
    renamed = {}
    if exact_groups:
        print_banner("Stage 1 · Exact duplicates")
    for number, group in enumerate(exact_groups, 1):
        new_path = _apply_exact(group, number, len(exact_groups), config, stats, rel, ctx, reason)
        if new_path != group.paths[0]:
            renamed[group.paths[0]] = new_path
    return renamed


def apply_dry_run(exact_groups: list[ExactGroup], groups: list[Group], config: dict,
                  stats: Stats, rel, ctx: RunContext) -> None:
    """Carry out exactly what the dry run showed, without asking again."""
    exact_groups, dropped_files = _check_exact(exact_groups)
    renamed = _apply_all_exact(exact_groups, config, stats, rel, ctx,
                               "exact copy (identical bytes), as shown in the dry run")
    _follow_renames(groups, renamed)

    decided = [g for g in groups if g.status == "done"]
    skipped = [g for g in groups if g.status != "done"]
    decided, dropped_images = _check_decided(decided)
    if dropped_files or dropped_images:
        warn(f"{dropped_files + dropped_images} file(s) changed on disk since the dry run and were left alone.")
    if decided or skipped:
        print_banner("Stage 2 · Visually identical images")
    for number, group in enumerate(decided, 1):
        how = "automatic" if group.decision == "automatic" else "chosen by user"
        reason = f"visual match (worst diff {group.worst}/{group.limit}), {how} in the dry run"
        print(f"\n{StyleUI.BOLD}[{number}/{len(decided)}]{StyleUI.RESET} {_group_heading(group)}")
        _group_table(group, rel)
        automatic = group.decision == "automatic"
        _resolve(group, 0, config, stats, rel, ctx, automatic=automatic, reason=reason)
    if skipped:
        stats.groups_skipped += sum(g.status != "ignored" for g in skipped)
        dim(f"\n{len(skipped)} group(s) skipped or marked as not duplicates in the dry run were left as they are.")


def review_dry_run_again(exact_groups: list[ExactGroup], groups: list[Group], config: dict,
                         confirm_mode: str, stats: Stats, rel, ctx: RunContext) -> bool:
    """Apply the exact copies, then walk through the visual groups afresh."""
    exact_groups, dropped_files = _check_exact(exact_groups)
    renamed = _apply_all_exact(exact_groups, config, stats, rel, ctx, EXACT_REASON)
    _follow_renames(groups, renamed)
    for group in groups:  # forget the dry run's decisions, but not "not duplicates" marks
        if group.status != "ignored":
            group.status, group.kept, group.removed, group.decision = "pending", None, 0, None
    groups, dropped_groups, dropped_images = validate_groups(groups)
    groups, hidden = drop_ignored(groups, ctx.ignores)
    report_ignored(hidden, groups)
    if dropped_files or dropped_groups or dropped_images:
        warn("Some files changed on disk since the dry run and were left alone.")
    if not groups:
        return False
    print_banner("Stage 2 · Visually identical images")
    return review_groups(groups, config, confirm_mode, stats, rel, ctx)


def planned_changes(exact_groups: list[ExactGroup], groups: list[Group], rename: bool) -> tuple[int, int]:
    """(files a dry run would remove, files it would rename)."""
    remove = sum(len(e.files) - 1 for e in exact_groups)
    remove += sum(g.removed for g in groups if g.status == "done")
    renames = sum(1 for e in exact_groups if rename and rename_target(e.paths[0], e.paths))
    renames += sum(1 for g in groups if g.status == "done" and g.rename
                   and rename_target(g.images[g.kept or 0]["path"], g.paths))
    return remove, renames


def dry_run_from_session(data: dict) -> tuple[list[ExactGroup], list[Group]]:
    return ([ExactGroup.from_dict(e) for e in data.get("exact_groups", [])],
            [Group.from_dict(g) for g in data.get("groups", [])])
