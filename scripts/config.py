"""Configuration loading (lrckit-style: defaults + user overrides + warnings)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

__version__ = "1.0"
PROJECT_URL = "https://github.com/<username>/img_dedupe"

DEFAULT_CONFIG: dict[str, Any] = {
    "delete_mode": "trash",       # 'trash', 'permanent', 'dry_run'
    "viewer": "auto",             # 'auto', 'identity', 'imagecompare', 'kitty', 'timg'
    "confirm": "uncertain",       # 'uncertain' (ask for borderline groups), 'always', 'never'
    "strictness": "normal",       # 'strict', 'normal', 'loose' (see STRICTNESS_PRESETS)
    "max_pixel_diff": None,       # number overrides the strictness preset
    "uncertain_ratio": 0.6,       # groups above this fraction of the limit count as borderline
    "compare_size": 512,          # max resolution of the pixel comparison
    "max_aspect_diff": 0.02,      # images whose aspect ratios differ more are never duplicates
    "hash_size": 8,               # dhash grid; hash has 2*size*size bits (128 by default)
    "hash_max_distance": 28,      # candidate filter only (out of 128 bits); lenient on purpose
    "color": "auto",              # 'auto', 'always', 'never'
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
        elif key in ("rename_numbered", "save_sessions") and not isinstance(value, bool):
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
        config["_source"] = str(path)
        return config, warnings

    config = copy.deepcopy(DEFAULT_CONFIG)
    config["_source"] = "built-in defaults"
    return config, warnings
