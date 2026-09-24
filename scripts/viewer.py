"""Opening the images of a duplicate group in a viewer."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .ui import warn

# Comparison apps: viewer name -> (display name, Flatpak app id, native command or None).
COMPARE_APPS = {
    "identity": ("Identity", "org.gnome.gitlab.YaLTeR.Identity", None),
    "imagecompare": ("Image Compare", "io.github.gimletlove.imagecompare", "imagecompare"),
}

_flatpak_state: dict[str, bool | None] = {}


def _flatpak_installed(app_id: str) -> bool | None:
    """True/False whether the Flatpak app is installed; None if Flatpak itself is missing."""
    if app_id not in _flatpak_state:
        if shutil.which("flatpak") is None:
            _flatpak_state[app_id] = None
        else:
            result = subprocess.run(["flatpak", "info", app_id],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _flatpak_state[app_id] = result.returncode == 0
    return _flatpak_state[app_id]


def _start(argv: list[str]) -> None:
    # Own session: Ctrl+C in img_dedupe must not close a comparison window you still look at.
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _open_compare_app(viewer: str, paths: list[str]) -> str | None:
    """Open *paths* in a comparison app. Returns its name, or None if it is unavailable."""
    name, app_id, native = COMPARE_APPS[viewer]
    if native and shutil.which(native):  # e.g. Image Compare installed from its RPM/DEB
        _start([native, *paths])
        return name
    installed = _flatpak_installed(app_id)
    if installed is None:
        warn(f"{name} needs Flatpak, which is not installed. Falling back to the default viewer.")
        return None
    if not installed:
        warn(f"{name} is not installed. Install it with:  flatpak install flathub {app_id}")
        warn("Falling back to the default viewer.")
        return None
    # The sandbox cannot see your folders; --file-forwarding with @@ ... @@ hands
    # exactly these files to the app through the document portal.
    _start(["flatpak", "run", "--file-forwarding", app_id, "@@", *paths, "@@"])
    return name


def open_image_viewer(filepaths, viewer_mode: str = "auto") -> str:
    """Open the images with the configured viewer. Returns the name of the viewer used."""
    # Absolute paths, so Flatpaks and external tools find the files.
    paths = [str(p.resolve()) for p in filepaths]

    if viewer_mode in COMPARE_APPS:
        used = _open_compare_app(viewer_mode, paths)
        if used:
            return used
    elif viewer_mode == "timg":
        try:
            subprocess.run(["timg", *paths])
            return "timg"
        except FileNotFoundError:
            warn("timg not found in PATH. Falling back to the default viewer.")
    elif viewer_mode == "kitty":
        try:
            subprocess.run(["kitty", "+kitten", "icat", *paths])
            return "kitty"
        except FileNotFoundError:
            warn("kitty not found in PATH. Falling back to the default viewer.")

    # 'auto', and the fallback: the system's default viewer, one window per image.
    for path in paths:
        if sys.platform.startswith("linux"):
            _start(["xdg-open", path])
        elif sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            _start(["open", path])
    return "the default viewer"
