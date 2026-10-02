"""Configuration loading (lrckit-style: defaults + user overrides + warnings)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

__version__ = "1.4"
PROJECT_URL = "https://github.com/sobakah/img_dedupe"

DEFAULT_CONFIG: dict[str, Any] = {
    "delete_mode": "trash",       # 'trash', 'permanent', 'dry_run'
    "viewer": "auto",             # 'auto', 'identity', 'imagecompare', 'kitty', 'timg'
    "confirm": "uncertain",       # 'uncertain' (ask for borderline groups), 'always', 'never'
    "strictness": "normal",       # 'strict', 'normal', 'loose' (see STRICTNESS_PRESETS)
    "max_pixel_diff": None,       # number overrides the strictness preset
    "uncertain_ratio": 0.8,       # groups above this fraction of the limit count as borderline
    "compare_size": 512,          # max resolution of the pixel comparison
    "max_aspect_diff": 0.02,      # images whose aspect ratios differ more are never duplicates
    "hash_size": 8,               # dhash grid; hash has 2*size*size bits (128 by default)
    "hash_max_distance": 28,      # candidate filter only (out of 128 bits); lenient on purpose
    "color": "auto",              # 'auto', 'always', 'never'
    "include_videos": True,       # also find identical and remuxed copies of videos
    "prefer_jxl": False,          # keep a JPEG XL copy instead of a lossless original (PNG, TIFF, ...)
    "rename_numbered": True,      # strip "(1)" from a kept copy when the group shows it is a copy number
    "save_sessions": True,        # save review progress so it can be resumed (not for dry runs)
    "log_file": None,             # null = project folder or state dir (see README), false = off, or a path
    "format_ranks": {
        "JXL": 6, "WEBP": 5, "AVIF": 4, "PNG": 3, "TIFF": 3,
        "JPEG": 2, "JPG": 2, "GIF": 1, "BMP": 0
    },
}

VALID_CHOICES = {
    "delete_mode": ("trash", "permanent", "dry_run"),
    "viewer": ("auto", "identity", "imagecompare", "kitty", "timg"),
    "confirm": ("uncertain", "always", "never"),
    "strictness": ("strict", "normal", "loose"),
    "color": ("auto", "always", "never"),
}
DEPRECATED_KEYS = {"threshold", "hash_algo"}

# Numeric settings: (type, minimum, maximum). Invalid values fall back to the default,
# instead of silently breaking the comparison later on.
NUMERIC_RANGES = {
    "max_pixel_diff": (int, 0, 255),
    "uncertain_ratio": (float, 0.0, 1.0),
    "compare_size": (int, 64, 4096),
    "max_aspect_diff": (float, 0.0, 1.0),
    "hash_size": (int, 4, 32),
    "hash_max_distance": (int, 0, 2048),
}


def _number_ok(key: str, value) -> bool:
    kind, low, high = NUMERIC_RANGES[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if kind is int and not float(value).is_integer():
        return False
    return low <= value <= high


def project_dir() -> Path | None:
    """The project folder when running from a source checkout (git clone,
    ``python -m img_dedupe`` or ``pip install -e .``), else None.

    A regular install lives in site-packages, which is no place for a user's
    config or log file.
    """
    root = Path(__file__).resolve().parent.parent
    return root if (root / "pyproject.toml").is_file() else None


def config_search_paths() -> list[Path]:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else Path.home() / ".config"
    paths = [config_home / "img_dedupe" / "config.json"]
    project = project_dir()
    if project is not None:
        paths.insert(0, project / "config.json")
    return paths


def _merge(user_config: dict, path: Path, warnings: list[str]) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in user_config.items():
        if key in DEPRECATED_KEYS:
            warnings.append(f"'{key}' in {path.name} is no longer used and is ignored (see README).")
        elif key not in DEFAULT_CONFIG:
            warnings.append(f"Unknown key '{key}' in {path.name} ignored.")
        elif key in VALID_CHOICES and value not in VALID_CHOICES[key]:
            warnings.append(f"Invalid value {value!r} for '{key}', using {DEFAULT_CONFIG[key]!r}. "
                            f"Choices: {', '.join(VALID_CHOICES[key])}.")
        elif key in NUMERIC_RANGES and not (key == "max_pixel_diff" and value is None) and not _number_ok(key, value):
            kind, low, high = NUMERIC_RANGES[key]
            what = "a whole number" if kind is int else "a number"
            warnings.append(f"'{key}' must be {what} from {low} to {high}, using {DEFAULT_CONFIG[key]!r}.")
        elif key in ("rename_numbered", "save_sessions", "include_videos", "prefer_jxl") \
                and not isinstance(value, bool):
            warnings.append(f"'{key}' must be true or false, using {DEFAULT_CONFIG[key]!r}.")
        elif key == "log_file" and not (value is None or value is False or isinstance(value, str)):
            warnings.append("'log_file' must be null, false or a path, using the default location.")
        elif isinstance(DEFAULT_CONFIG[key], dict):
            if isinstance(value, dict):
                config[key].update(value)  # partial format_ranks keep the other defaults
            else:
                warnings.append(f"'{key}' must be an object, using defaults.")
        else:
            config[key] = value
    return config


def _rank_ok(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_config(explicit_path: Path | None = None) -> tuple[dict, list[str]]:
    """Return ``(config, warnings)``. Defaults are always present."""
    warnings: list[str] = []
    paths = [explicit_path] if explicit_path else config_search_paths()

    for path in paths:
        if path is None or not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Configuration file '{path}' is invalid, using defaults: {exc}")
            continue
        if not isinstance(loaded, dict):
            warnings.append(f"Configuration file '{path}' is not a JSON object, using defaults.")
            continue
        config = _merge(loaded, path, warnings)
        bits = 2 * int(config["hash_size"]) ** 2
        if config["hash_max_distance"] > bits:
            warnings.append(f"'hash_max_distance' can't exceed {bits} (2 x hash_size^2), using {min(28, bits)}.")
            config["hash_max_distance"] = min(28, bits)
        for key in ("hash_size", "hash_max_distance", "compare_size"):
            config[key] = int(config[key])
        if config["max_pixel_diff"] is not None:
            config["max_pixel_diff"] = int(config["max_pixel_diff"])
        if not all(_rank_ok(v) for v in config["format_ranks"].values()):
            warnings.append("'format_ranks' values must be numbers, using the defaults.")
            config["format_ranks"] = copy.deepcopy(DEFAULT_CONFIG["format_ranks"])
        config["_source"] = str(path)
        return config, warnings

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["_source"] = "built-in defaults"
    return config, warnings
