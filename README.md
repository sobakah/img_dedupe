# img_dedupe

Finds duplicate images and videos in a folder and removes the redundant copies, keeping the best one.

- **Exact copies:** identical images and videos, whatever their names and folders.
- **Remuxed videos:** the same video in another container, e.g. `clip.mp4`, `clip.mkv` and `clip.ts`.
- **Visually identical images:** the same picture saved in another format, quality or size (JPEG vs. WebP vs. PNG, a downscaled or EXIF-rotated copy).

It is deliberately cautious: pictures that merely *look similar* (an edited version, an added watermark, a crop, the next frame of a burst) are left alone. Files go to the system trash by default, every change is logged, and a dry run shows everything before anything happens.

## Installation

You need Python 3.9 or newer and [pipx](https://pipx.pypa.io/) (on Fedora: `sudo dnf install pipx`).

```bash
pipx install git+https://github.com/sobakah/img_dedupe.git
```

With JPEG XL support: `pipx install "img-dedupe[jxl] @ git+https://github.com/sobakah/img_dedupe.git"`. To install a specific release, add it to the URL: `...img_dedupe.git@v1.1`.

**For videos**, also install ffmpeg (a system package): on Fedora `sudo dnf install ffmpeg-free`, or `ffmpeg` from RPM Fusion. Without it, identical video copies are still found, but not remuxes.

| Task | Command |
|---|---|
| Update | `pipx upgrade img-dedupe` (new version) or `pipx reinstall img-dedupe` (latest commit) |
| Uninstall | `pipx uninstall img-dedupe` |
| `img_dedupe` not found | run `pipx ensurepath` once and open a new terminal |

The package is called `img-dedupe` (with a hyphen); the command is `img_dedupe`. Other ways to install or run it are described in [Other ways to run it](#other-ways-to-run-it).

## First run

```bash
img_dedupe ~/Pictures --dry-run
```

`--dry-run` shows what would be deleted and changes nothing. Afterwards you can carry out exactly what it showed with one keypress, without scanning again.

The program opens on a **start screen** with the folder, the number of files in scope and all settings. Each setting shows its choices: the current one in bold, the others greyed out. Press a setting's number to switch it, and **Enter** to start.

| Key | Setting |
|---|---|
| `1` | Stages: exact + visual, exact only, visual only |
| `2` | Strictness of the visual comparison: strict, normal, loose |
| `3` | When to ask: borderline groups only, every group, never |
| `4` | Delete mode: trash, dry run, permanent |
| `5` | Viewer for comparing images (see [Viewers](#viewers)) |
| `6` | Remove "(1)" from the names of kept copies: on/off |
| `7` | Include videos: on/off |
| `r` | Include subfolders |
| `f` | Choose subfolders (with `r` on) |
| `c` | Change folder (with Tab completion) |

**Choosing subfolders (`f`):** a tree of all subfolders with images or videos, all selected by default. A number switches that subfolder and everything below it; `2-5` or `1,3,7` switch several at once; `a` selects all subfolders, `n` none. The **main folder** is switched only with its own key `m`, so it can't be left out by accident; the start screen shows the choice, e.g. `main folder + 2 of 4 subfolders`.

Hidden folders such as `.thumbnails` or `.Trash` are never scanned, and the home or root folder is refused.

## How it decides

**Stage 1 – exact copies.** Files with identical content (SHA-256) are grouped; only files of equal size are read, so this is fast. Then videos are checked for **remuxes**: ffprobe reads each video's details from its header, and only videos with the same codec, resolution and length are read in full and their video stream compared. Nothing is decoded. A re-encoded video (other codec, size or quality) is *not* treated as a duplicate.

For remuxes only the video stream decides. If all copies have the same audio and subtitle tracks, they are handled like exact copies; if the tracks differ, you are asked, and the copy with the most tracks is recommended.

**Stage 2 – visually identical images** (images only). A quick fingerprint finds pairs that *might* match; then each is compared pixel by pixel with the image that would be kept. Both are shrunk to the same size (at most 512 px) and cut into 8×8-pixel squares; the score is the difference in the *most different* square, from 0 (identical) to 255. One small changed spot, like a date stamp, is therefore enough to keep two images apart. In tests, re-saves and resizes scored up to 15 and real edits 28 or more; the default limit is 20. Images whose shape differs by more than 2% are never compared, and animated images only take part in Stage 1.

**Which copy is kept:**

1. the highest resolution;
2. lossless over lossy (a small lossy copy never replaces a lossless original; JXL counts as lossless);
3. the preferred format (`format_ranks`);
4. a name without a copy number like "(1)";
5. the less compressed file;
6. the oldest file.

For exact copies it is simply: a name without a copy number, then the oldest.

**Copy numbers:** if the kept file is called `photo (1).jpg` and another file of its group is `photo.jpg`, the number is removed after the others are gone: `photo (1).jpg` → `photo.jpg`. This only happens when another file of the group has the same base name, so series names like `Holiday (12).jpg` keep their number, and never when the new name is taken.

## Reviewing groups

The groups are numbered (`[3/12]`) and the file to keep is marked `▶`. By default, only **borderline** matches are shown for a decision; clear ones are handled automatically.

| Key | Action |
|---|---|
| **Enter** | Keep the recommended file ▶ and remove the others |
| `1`–`n` | Keep that file instead |
| `s` | Skip: keep all files; the group is shown again next time |
| `i` | Not duplicates: keep all files and never show this group again |
| `i2` | Only image #2 is not a duplicate; decide on the rest as usual |
| `n` | Remove "(1)" from the kept name: on/off for this group |
| `v` | Open the images in the viewer |
| `p` / `t` | Previous open group / overview of all groups |
| `q` | Quit (asks whether to keep your progress) |

In `permanent` mode, every deletion asks `[y/N]` with No as the default, so pressing Enter twice never deletes anything permanently.

**"Not duplicates" marks** (`i`) are saved in a hidden file, `.img_dedupe_ignore.json`, in the scanned folder, so they stay with your pictures. A mark stops applying when one of its files changes. `--no-ignore` shows marked pairs again for one run; deleting the file forgets all marks.

## Dry runs and saved progress

**After a dry run** you can carry it out for real, with the delete mode from your config (`trash` if the config says `dry_run`):

- **Enter:** apply exactly what the dry run showed, including your choices, without asking again.
- **`r`:** go through the groups again for real.

Before anything is deleted, every file is checked against the dry run; files that changed in the meantime are left alone.

**Progress is saved after every step**, in dry runs too, so Ctrl+C, a closed terminal or a crash loses nothing. When you open the same folder again, the start screen offers to **resume** (Enter), start a new scan (`n`) or discard the saved session (`x`). A finished dry run stays saved until you carry it out, so you can also do that later: on the start screen, Enter carries it out and `e` goes through its groups again. Before resuming, all files are checked again. A dry run always resumes as a dry run, and a real run never as a dry run.

## Viewers

`v` opens all images of a group at once, with the viewer chosen by setting `5` or `"viewer"` in the config:

| Viewer | What you get |
|---|---|
| `auto` | Your default image viewer, one window per image |
| `imagecompare` | [Image Compare](https://github.com/gimletlove/imagecompare): side by side or in a grid, synchronised zoom; recommended. `flatpak install flathub io.github.gimletlove.imagecompare` |
| `identity` | [Identity](https://apps.gnome.org/Identity/): tabs or side by side. `flatpak install flathub org.gnome.gitlab.YaLTeR.Identity` |
| `kitty`, `timg` | Previews inside the terminal (kitty terminal, or `timg`) |

If the chosen app is missing, img_dedupe says so and uses your default viewer. Neither app can be told to open maximized: on KDE use a window rule (*System Settings → Window Management → Window Rules*), on GNOME press Super+↑.

## Command-line options

| Option | Meaning |
|---|---|
| `-r`, `--recursive` | Include subfolders |
| `--exclude DIR` | With `-r`: skip this subfolder (repeatable); `--exclude .` skips the main folder |
| `--stages {1,2,both}` | Exact copies only, visual only, or both (default) |
| `--strictness {strict,normal,loose}` | Visual limit 12 / 20 / 28 |
| `--dry-run` | Show what would be deleted; change nothing |
| `-i`, `--interactive` | Ask for every group |
| `-y`, `--auto` | No start screen, no questions, no saved progress (for scripts) |
| `--no-rename` | Keep "(1)" in the names of kept copies |
| `--no-videos` | Leave videos out |
| `--no-ignore` | Show pairs marked "not duplicates" again |
| `-c FILE`, `--config FILE` | Use this config file |
| `--color {auto,always,never}` | Colours; `auto` respects `NO_COLOR` |
| `-v`, `-vv`, `--verbose` | More log output on stderr |

Exit codes: `0` success, `1` some files could not be removed, `2` bad path, config or missing packages, `130` stopped with Ctrl+C.

## Configuration

All settings can be stored in `config.json`; copy `config.example.json` to start. Missing keys use the defaults, and invalid values are reported at startup.

| Key | Default | Meaning |
|---|---|---|
| `delete_mode` | `trash` | `trash`, `permanent` or `dry_run` |
| `confirm` | `uncertain` | ask for `uncertain` (borderline) groups, `always` or `never` |
| `viewer` | `auto` | see [Viewers](#viewers) |
| `strictness` | `normal` | visual limit: `strict` 12, `normal` 20, `loose` 28 |
| `max_pixel_diff` | `null` | a number here replaces the strictness preset |
| `include_videos` | `true` | also check videos |
| `rename_numbered` | `true` | remove "(1)" from kept copies |
| `save_sessions` | `true` | save progress so it can be resumed |
| `log_file` | `null` | `null` = default place, `false` = no log, or a file path |
| `color` | `auto` | `auto`, `always`, `never` |
| `format_ranks` | JXL > WEBP > AVIF > PNG/TIFF > JPEG > GIF > BMP | preferred formats when choosing the copy to keep |
| `uncertain_ratio`, `compare_size`, `max_aspect_diff`, `hash_size`, `hash_max_distance` | `0.6`, `512`, `0.02`, `8`, `28` | fine-tuning, see below |

### Fine-tuning the visual comparison

| Key | What it does | Higher | Lower |
|---|---|---|---|
| `max_pixel_diff` / `strictness` | Highest score that still counts as the same picture | Also finds heavily compressed copies; tiny edits may slip through | Safer; heavily compressed copies stay on disk |
| `uncertain_ratio` | Share of the limit above which you are asked (0.6 × 20 = above 12) | Fewer questions (1.0 = never) | More questions (0 = always) |
| `compare_size` | Comparison size in pixels | Catches smaller watermarks; slower | Faster; small differences blur away |
| `max_aspect_diff` | Allowed difference in shape (0.02 = 2%) | Slightly cropped copies get compared | Below 0.01, small resized copies can be missed |
| `hash_max_distance` | How loose the quick pre-check is (of 128 bits) | Finds more candidates; slower, never less accurate | Faster; real duplicates can be missed |

Leave `hash_size` at 8. If different pictures get grouped, use `strict` or raise `compare_size` (e.g. 768); if obvious duplicates are missed, try `loose` or raise `hash_max_distance` (e.g. 36); if you're asked too often, raise `uncertain_ratio` (e.g. 0.75). Check any change with `--dry-run` first.

## Files and where they are kept

| File | Installed with pipx | Running from the project folder |
|---|---|---|
| Config `config.json` | `~/.config/img_dedupe/` | the project folder, else `~/.config/img_dedupe/` |
| Log `img_dedupe.log` | `~/.local/state/img_dedupe/` | the project folder |
| Saved sessions | `~/.local/state/img_dedupe/sessions/` | `sessions/` in the project folder |
| "Not duplicates" marks | `.img_dedupe_ignore.json` in the scanned folder | the same |

`$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` are respected; on Windows the state folder is `%LOCALAPPDATA%\img_dedupe`. In the project folder, `config.json`, the log and `sessions/` are ignored by git.

**The log** records every change, one line per file: what happened, the full path, the file kept instead, and why. Trashed files can be restored from the system trash using these paths. Dry runs are not logged.

```
2026-09-23 20:39:37 | TRASHED  | /pics/a (1).png | kept /pics/a.png | exact copy (identical bytes), automatic
2026-09-23 20:39:37 | TRASHED  | /pics/photo.jpg | kept /pics/photo (1).jpg | visual match (worst diff 5/20), automatic
2026-09-23 20:39:37 | RENAMED  | /pics/photo (1).jpg | new name /pics/photo.jpg
```

## Other ways to run it

**From a local clone:** `git clone https://github.com/sobakah/img_dedupe.git`, then `pipx install .` in that folder. To update, `git pull` and `pipx install --force .`.

**For development:** an editable install, where code changes take effect immediately:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[jxl]"
```

**Without installing:** get the code (clone it, or *Code → Download ZIP* on GitHub and rename `img_dedupe-main` to `img_dedupe`), set up the two packages once, and start it as a module:

```bash
cd ~/img_dedupe
python3 -m venv .venv
.venv/bin/pip install Pillow send2trash        # optional: pillow-jxl-plugin
.venv/bin/python -m scripts ~/Pictures
```

From another folder, tell Python where the project is: `PYTHONPATH=~/img_dedupe ~/img_dedupe/.venv/bin/python -m scripts ~/Pictures`. As a shortcut, add this line to `~/.bashrc`:

```bash
alias img_dedupe='PYTHONPATH=~/img_dedupe ~/img_dedupe/.venv/bin/python -m scripts'
```

| Error | Cause |
|---|---|
| *attempted relative import with no known parent package* | started as `python3 scripts/…`; use `python3 -m scripts` |
| *No module named scripts* | started from another folder without `PYTHONPATH` |
| *Missing required packages* | started without `.venv/bin/python` |

On Windows (not tested yet): `py -m venv .venv`, `.venv\Scripts\pip install Pillow send2trash pyreadline3`, `.venv\Scripts\python -m scripts C:\Pictures`.

## Releasing a new version

Raise `__version__` in `scripts/config.py` (the only place the version is set), add the changes to `RELEASE_NOTES.md`, commit, then tag and push:

```bash
git tag -a v1.2 -m "img_dedupe 1.2"
git push && git push --tags
```

Without the version bump, `pipx upgrade img-dedupe` doesn't see the new release.

## Known limitations

- An upscaled copy counts as the better one, because it has the higher resolution.
- Very small watermarks on very large photos can disappear at the 512 px comparison size; raise `compare_size` or use `strict` if that matters.
- During the visual comparison, a thumbnail of every candidate image is kept in memory (about 768 KB each), so thousands of candidates can need several GB of RAM.
- Videos are only compared by content; a re-encoded copy is not recognised.
- Developed and tested on Linux; macOS and Windows are supported by the code but untested.
