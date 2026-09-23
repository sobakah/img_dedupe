import os
import sys
import subprocess

from .ui import warn

def open_image_viewer(filepaths, viewer_mode="auto"):
    """Handles image viewing based on config settings and OS."""
    # Use absolute paths (resolve) to ensure Flatpaks and external tools can find the files
    paths_str = [str(p.resolve()) for p in filepaths]
    
    if viewer_mode == "timg":
        try:
            subprocess.run(["timg"] + paths_str)
            return
        except FileNotFoundError:
            warn("timg not found in PATH. Falling back to the default viewer.")
            
    elif viewer_mode == "kitty":
        try:
            subprocess.run(["kitty", "+kitten", "icat"] + paths_str)
            return
        except FileNotFoundError:
            warn("kitty not found in PATH. Falling back to the default viewer.")
            
    elif viewer_mode == "identity":
        try:
            # Identity is an image comparison GUI. 
            # Flatpak requires --file-forwarding and @@ to expose host paths to the sandbox via the document portal.
            subprocess.Popen(["flatpak", "run", "--file-forwarding", "org.gnome.gitlab.YaLTeR.Identity", "@@"] + paths_str + ["@@"], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            warn("Flatpak not found in PATH. Falling back to the default viewer.")

    # Fallback / 'auto' OS native viewer
    for path in paths_str:
        if sys.platform.startswith('linux'):
            subprocess.Popen(['xdg-open', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == 'win32':
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
