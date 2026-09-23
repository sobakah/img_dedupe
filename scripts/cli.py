"""Command-line entry point: find and remove duplicate images."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from pathlib import Path

from . import ui
from .config import __version__, load_config

REQUIRED_PACKAGES = {"PIL": "Pillow", "send2trash": "send2trash"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="img_dedupe",
        description="Find exact and visually identical duplicate images and remove the "
                    "redundant copies, keeping the best one.",
    )
    parser.add_argument("path", nargs="?", default=".", help="picture directory (default: current directory)")
    parser.add_argument("--version", action="version", version=f"img_dedupe {__version__}")
    parser.add_argument("-c", "--config", type=Path, metavar="FILE", help="use this config file instead of the search paths")

    scan = parser.add_argument_group("scanning")
    scan.add_argument("-r", "--recursive", action="store_true", help="include subdirectories")
    scan.add_argument("--exclude", action="append", metavar="DIR", default=[],
                      help="with -r, skip this subfolder (relative to the scanned folder; repeatable)")
    scan.add_argument("--stages", choices=("1", "2", "both"), default="both",
                      help="1 = exact copies only, 2 = visual matches only, both (default)")
    scan.add_argument("--strictness", choices=("strict", "normal", "loose"), help="override the configured strictness")

    review = parser.add_argument_group("review")
    mode = review.add_mutually_exclusive_group()
    mode.add_argument("-i", "--interactive", action="store_true", help="ask before resolving every group")
    mode.add_argument("-y", "--auto", action="store_true",
                      help="no start screen and no questions; resolve every group automatically")
    review.add_argument("--dry-run", action="store_true", help="show what would be deleted, delete nothing")
    review.add_argument("--no-rename", action="store_true",
                        help='keep "(1)" in the names of kept copies')

    output = parser.add_argument_group("output")
    output.add_argument("--color", choices=("auto", "always", "never"), help="override colour handling")
    output.add_argument("-v", "--verbose", action="count", default=0, help="increase log verbosity (repeatable)")
    return parser


def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)


def missing_packages() -> list[str]:
    return [pip for module, pip in REQUIRED_PACKAGES.items() if importlib.util.find_spec(module) is None]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    config, warnings = load_config(args.config)
    if args.color:
        config["color"] = args.color
    config["_configured_delete_mode"] = config["delete_mode"]  # what "for real" means after a dry run
    if args.dry_run:
        config["delete_mode"] = "dry_run"
    if args.no_rename:
        config["rename_numbered"] = False
    if args.strictness:
        config["strictness"], config["max_pixel_diff"] = args.strictness, None
    ui.apply_color_settings(config["color"])

    for message in warnings:
        ui.warn(f"Warning: {message}")
    if args.config and not args.config.is_file():
        ui.error(f"Config file not found: {args.config}")
        return 2

    missing = missing_packages()
    if missing:
        ui.error(f"Missing required packages: {', '.join(missing)}")
        print("Install img_dedupe with its dependencies, e.g.:")
        print("  pipx install .          (from the project folder)")
        print("  pip install -e .        (inside a virtual environment, for development)")
        return 2
    if not ui.HAS_READLINE and not args.auto:
        ui.warn("readline is unavailable - Tab completion and prefilled prompts are disabled. "
                "On Windows: pip install pyreadline3")

    settings = {
        "stages": args.stages,
        "recursive": args.recursive,
        "confirm": "always" if args.interactive else "never" if args.auto else config["confirm"],
    }

    from . import app  # imports Pillow/send2trash, so only after the dependency check

    try:
        return app.run(Path(args.path), config, settings, auto=args.auto, exclude=args.exclude)
    except KeyboardInterrupt:
        print("\n\nProgram aborted by user.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
