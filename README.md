# img_dedupe

Finds and removes duplicate images in two stages:

1. **Stage 1 – Exact duplicates:** files with identical bytes (SHA-256). Only files that share a size are read, so this is fast even on large folders. A name without a copy number like "(1)" is kept, otherwise the oldest copy.
2. **Stage 2 – Visually identical images:** the same picture saved in a different format, quality or resolution (JPEG vs. WebP vs. PNG, a downscaled copy, an EXIF-rotated copy, a different colour profile).

Stage 2 is deliberately conservative. Images that are merely *similar* (an added watermark or date stamp, a crop, a brightness/colour edit, a different frame of a burst) are **not** treated as duplicates.

## How Stage 2 decides

1. **Candidate search (fast, lenient):** a 128-bit difference hash (horizontal + vertical) finds pairs that *might* be the same image. This step only narrows the search; it never decides anything on its own. Perceptual hashes are computed from a tiny ~9×8 thumbnail, so they cannot see small differences.
2. **Pixel verification (decides):** each candidate is compared with the image that would be *kept*. It is compared at the smaller image's resolution (up to 512 px), after a light blur that absorbs compression and resampling noise. The score is the worst average difference found in any 8×8 cell (0–255). Using the worst cell rather than a global average is what catches small local edits like text.
3. Images whose aspect ratios differ by more than 2% are never grouped, and animated images are only handled by Stage 1.

Measured on test data: re-encodes and resizes (including JPEG quality 30 and 1/3 downscales) score ≤ 15, while small real edits score ≥ 28.

## Which copy is kept

In order: highest resolution → lossless over lossy → `format_ranks` → a name without a copy number → higher bits-per-pixel → oldest file. For exact copies (Stage 1) it is simply: a name without a copy number, then the oldest file.

"Lossless over lossy" ensures a small lossy WebP/JPEG re-encode never replaces a lossless master. PNG, BMP, TIFF, lossless WebP and JXL count as lossless. JXL is *assumed* lossless, since Pillow can't tell lossy JXL apart.

### Copy numbers like "(1)"

Browsers and file managers name copies `photo (1).jpg`, `photo (2).jpg`. Between otherwise equal files, the one **without** such a number is kept. If a numbered file is kept anyway (because it is the better copy, e.g. higher resolution), the number is removed from its name after the other files are gone: `photo (1).jpg` → `photo.jpg`.

To avoid damaging series names, this only happens when another file in the same group has the same base name. `photo.jpg` + `photo (1).jpg`, or `photo (1)` + `photo (2)`, qualify; `Holiday (12).jpg` next to `IMG_5.jpg` does not, because the 12 may be its place in a series. The rename is also skipped if the new name is already taken. Turn it off with setting `6` on the start screen, `--no-rename`, or `"rename_numbered": false`; inside a group, `n` switches it for that group only.

## Installation

Python 3.9+ and [pipx](https://pipx.pypa.io/). The dependencies (Pillow, send2trash; pyreadline3 on Windows) are installed automatically. pipx is the easiest route on distributions that block global `pip install` (PEP 668, e.g. Fedora: `sudo dnf install pipx`).

### From GitHub (recommended)

No clone needed; pipx fetches the repository, builds it, and puts the `img_dedupe` command on your PATH:

```bash
pipx install git+https://github.com/<username>/img_dedupe.git
```

With JPEG XL support (the quotes are needed because of the brackets):

```bash
pipx install "img-dedupe[jxl] @ git+https://github.com/<username>/img_dedupe.git"
```

A specific release or branch goes after an `@` at the end of the URL, e.g. `...img_dedupe.git@v2.2.0`.

| Task | Command |
|---|---|
| Update to the latest commit | `pipx reinstall img-dedupe` |
| Uninstall | `pipx uninstall img-dedupe` |

Note that the pipx name is `img-dedupe` (with a hyphen), while the command is `img_dedupe`. `pipx upgrade img-dedupe` only installs something new when the version number went up; `pipx reinstall` always fetches the latest commit.

If `img_dedupe` is not found after installing, run `pipx ensurepath` once and open a new terminal.

### From a local clone

```bash
git clone https://github.com/<username>/img_dedupe.git
cd img_dedupe
pipx install .                 # or: pipx install ".[jxl]"
```

To update, `git pull` and then `pipx install --force .`.

**For development**, use an editable install; changes to the code take effect immediately:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[jxl]"
```

Without installing at all, `python3 -m img_dedupe` works from the project folder once Pillow and send2trash are available.

AVIF is read natively by recent Pillow versions. Without the JXL extra, `.jxl` files are still covered by Stage 1, and Stage 2 reports them as unreadable instead of silently skipping them.

### Where files are kept

| | Running from the project folder (`python3 -m img_dedupe`, `pip install -e .`) | Installed with pipx (from GitHub or a clone) |
|---|---|---|
| Config | `config.json` in the project folder, else `~/.config/img_dedupe/config.json` | `~/.config/img_dedupe/config.json` |
| Log | `img_dedupe.log` in the project folder | `~/.local/state/img_dedupe/img_dedupe.log` |
| Saved sessions | `~/.local/state/img_dedupe/sessions/` | the same |

`$XDG_CONFIG_HOME` and `$XDG_STATE_HOME` are respected; on Windows the state folder is `%LOCALAPPDATA%\img_dedupe`. A regular install lives inside Python's `site-packages`, which is why it keeps nothing there. `config.json` and `img_dedupe.log` are listed in `.gitignore`, so personal settings and the log never end up in the repository. Copy `config.example.json` to `config.json` to start customising.

## Usage

```bash
img_dedupe /path/to/pictures
```

### Start screen and folder selection

The program opens on a **start screen** showing the folder, how many images are in scope, and the settings. Each setting lists all its choices: the current one in normal bold text, the others greyed out (without colours, the current one is marked `‹like this›`). Press a setting's digit to switch to its next choice, `r` to include subfolders, `c` to change the folder (with Tab completion), and **Enter** to start. Hidden folders such as `.thumbnails` or `.Trash` are never scanned, and the home or root directory is refused.

With subfolders included, `f` opens the **folder list**: every folder containing images, indented like a tree, with a `[✓]` box and its image count. All folders are selected by default. A number switches that folder *and everything below it* on or off; `2-5` or `1,3,7` switch several together (following the first one's new state); `a` selects all, `n` none. The start screen then shows e.g. `3 of 5 folders`. From the command line: `-r --exclude Familie --exclude "Urlaub/Raw"`.

### Reviewing groups

Stage 2 collects every duplicate group first, then walks through them. Each group is numbered by its position in the full list (**`[3/12]`**), both in the banner and in the prompt, and the image that will be kept is marked with **`▶`**.

| Key | In a group |
|---|---|
| **Enter** | Keep the ▶ recommendation (#0) and remove the rest; the Enter line names the file and says what happens to the others |
| `1`–`n` | Keep that image instead; ▶ moves to it before anything is deleted |
| `s` | Skip the group (keep all files) and go to the next one |
| `n` | Switch the "(1)" rename on/off for this group (shown only when it applies) |
| `v` | Open all images of the group in the viewer; the group list is shown again afterwards |
| `p` | Back to the previous group that is still open (e.g. one you skipped) |
| `t` | Overview of all groups with their status (DONE / SKIPPED / OPEN); jump to any by number |
| `q` | Quit; asks whether to save the progress, then prints the summary |

By default only **borderline** groups (worst difference above 60% of the limit) get a screen; confident groups are resolved as the walk reaches them. When the walk ends you land on the overview, so skipped groups can be revisited before finishing. In `permanent` delete mode, every interactive deletion asks a `[y/N]` question whose default is No, so pressing Enter twice never deletes anything permanently. In `trash` mode, removed files can be restored from the system trash.

| Option | Meaning |
|---|---|
| `-r`, `--recursive` | Include subdirectories |
| `--stages {1,2,both}` | Run only exact, only visual, or both (default) |
| `--strictness {strict,normal,loose}` | Pixel difference limit 12 / 20 / 28 |
| `-i`, `--interactive` | Show a screen for every group |
| `-y`, `--auto` | No start screen, no questions: resolve every group automatically (for scripts) |
| `--dry-run` | Show what would be deleted; delete nothing |
| `--exclude DIR` | With `-r`, skip this subfolder (repeatable) |
| `--no-rename` | Keep "(1)" in the names of kept copies |
| `-c FILE`, `--config FILE` | Use this config file |
| `--color {auto,always,never}` | Colour handling; `auto` respects `NO_COLOR` and non-terminal output |
| `-v`, `-vv` | More logging on stderr (`-vv` logs every pixel comparison) |

### After a dry run: doing it for real without rescanning

When a dry run finishes with something to change, the groups it found are kept in memory and you get two ways to continue. Both use the delete mode from your config (`trash` if the config itself says `dry_run`), not the dry run.

| Key | Continue for real |
|---|---|
| **Enter** | **Apply exactly what the dry run showed**: the same deletions, your choices (a different image kept, skipped groups) and the same renames, without asking again |
| `r` | **Review the groups again**: exact copies are removed, then the visual groups are walked through afresh with your confirm setting |

Before anything is deleted, each file is compared with its size and modification time from the dry run; files that changed in between are left alone and reported. With `permanent` as the configured mode, applying asks one `[y/N]` question first. The log marks these deletions "as shown in the dry run" or "chosen by user in the dry run". `--auto` runs never ask, so they end after the dry run.

### Saved sessions: continuing later

After the groups are found, the review is saved after every step, so an accidental Ctrl+C, a closed terminal or a crash loses nothing. Next time you choose the same folder, the start screen shows a `[RESUMABLE]` session with its progress; **Enter** resumes at the group where you stopped, `n` starts a new scan instead, `x` discards the session.

* **`q`** asks *"Save progress so you can continue this comparison later?"* (Enter = yes; `n` deletes the saved progress).
* **Finishing** deletes the session, unless groups were skipped: then you are asked whether to keep it for them.
* **On resume**, every open group is checked against the disk first. Images that were deleted or modified in the meantime are dropped (a group whose recommended file changed is dropped entirely), so a stale session never deletes anything it has not verified.
* The groups of a session were matched with the settings of that scan (shown on the start screen); strictness and folder changes only apply to new scans. Delete mode, viewer, confirm and rename can be changed before resuming.
* Dry runs and `--auto` runs are not saved. Sessions live in `~/.local/state/img_dedupe/sessions/` (`$XDG_STATE_HOME`; `%LOCALAPPDATA%` on Windows). Disable with `"save_sessions": false`.

### Log

Every change to your files is appended to `img_dedupe.log` (in the project folder, or `~/.local/state/img_dedupe/` for a regular install; see *Where files are kept*): one line per file, with time, action, full path, the file that was kept instead, and why (exact copy or visual match with its score, and whether it was automatic or confirmed by you). Each run gets a START and END line; the summary shows the log's path.

```
2026-09-23 20:39:37 | START    | img_dedupe 2.1.0 · folder /pics · mode trash · limit 20 (normal) · new scan
2026-09-23 20:39:37 | TRASHED  | /pics/a (1).png | kept /pics/a.png | exact copy (identical bytes), automatic
2026-09-23 20:39:37 | TRASHED  | /pics/photo.jpg | kept /pics/photo (1).jpg | visual match (worst diff 5/20), automatic
2026-09-23 20:39:37 | RENAMED  | /pics/photo (1).jpg | new name /pics/photo.jpg
2026-09-23 20:39:37 | END      | finished · 6 removed, 1 renamed, 0 skipped, 0 failed
```

Trashed files can be restored from the system trash using the paths in the log. Dry runs write nothing. Set `"log_file"` to another path (relative paths count from the log's default folder), or `false` to disable logging. If the project folder is not writable, the log goes to `~/.local/state/img_dedupe/` instead, with a warning.

Exit codes: `0` success, `1` some files could not be removed, `2` bad path/config or missing packages, `130` aborted with Ctrl+C.

## Configuration

`config.json` is looked up as described in *Where files are kept*; `-c FILE` uses a specific file instead. Missing keys fall back to the defaults, and unknown, deprecated or invalid values are reported as warnings at startup.

| Key | Default | Meaning |
|---|---|---|
| `delete_mode` | `trash` | `trash`, `permanent` or `dry_run` |
| `viewer` | `auto` | `auto` (OS default), `identity` (Flatpak), `kitty`, `timg` |
| `confirm` | `uncertain` | `uncertain`, `always`, `never` |
| `strictness` | `normal` | preset for the pixel difference limit |
| `max_pixel_diff` | `null` | a number here overrides the preset |
| `uncertain_ratio` | `0.6` | fraction of the limit above which a group counts as borderline |
| `compare_size` | `512` | maximum comparison resolution (higher = catches smaller watermarks, slower) |
| `max_aspect_diff` | `0.02` | aspect-ratio tolerance |
| `hash_size`, `hash_max_distance` | `8`, `28` | candidate filter; raising the distance finds more candidates (slower, never less accurate) |
| `color` | `auto` | `auto`, `always`, `never` |
| `rename_numbered` | `true` | remove "(1)" from kept copies (see above) |
| `save_sessions` | `true` | save review progress so it can be resumed |
| `log_file` | `null` | `null` = default location (see *Where files are kept*), `false` = no log, or a file path |
| `format_ranks` | JXL > WEBP > AVIF > PNG/TIFF > JPEG > GIF > BMP | tie-breaker between equally lossless/lossy files |

The old keys `threshold` and `hash_algo` are no longer used and are ignored.

## Tuning the comparison, in plain terms

**How the score works.** Both images are shrunk to the same small size, slightly blurred, and cut into little 8×8-pixel squares. For each square the program measures how different the colours are on average, from 0 (identical) to 255 (black vs. white). The group's score is its *worst* square, so one small changed spot, like a date stamp, is enough to push the score up even if the rest is identical. Lower means more alike.

Measured on test images: re-saves, format conversions and downscaled copies scored 1–15. A 4% brightness change scored 8–9 (invisible, so it counts as a duplicate). A small date stamp on a 2000-px photo scored 26–49, and a 15% brightness edit 31–34.

| Setting | What it means | Raising it | Lowering it |
|---|---|---|---|
| `strictness` / `max_pixel_diff` | The highest score that still counts as "the same picture". Presets: strict 12, normal 20, loose 28. | Accepts more heavily compressed copies, but at `loose` the smallest edits (tiny watermarks) start to slip through | Safer, but heavily compressed or tiny copies are no longer recognised and simply stay on disk |
| `uncertain_ratio` | Which matches count as borderline and get asked about (with `confirm: uncertain`). 0.6 × limit 20 = anything scoring above 12. | Fewer questions (1.0 = never asked) | More questions (0 = every group asked) |
| `compare_size` | How large (px) the images are when compared. Never larger than the smaller image of the pair. | Finer detail is visible, so small watermarks score higher and get caught; slower | Faster, but small differences blur away (at 256 px a small date stamp scored only 13–24) |
| `max_aspect_diff` | Shape check: images whose width-to-height ratio differs by more than this (0.02 = 2%) are never compared, e.g. a cropped version. | Slightly cropped/stretched copies reach the pixel check (they will usually still fail it) | Below ~0.01, small resized copies can be wrongly rejected because pixel sizes get rounded |
| `hash_max_distance` | A quick "fingerprint" pre-check deciding which pairs are worth comparing at all. Out of 128 fingerprint bits; unrelated photos usually differ by about 64. It never decides that images are duplicates. | More pairs checked: slower, but never more false matches | Faster, but real duplicates can be skipped (at 16, 2 of 100 small test images were missed) |
| `hash_size` | Detail of the fingerprint. The fingerprint has 2 × size × size bits, so `hash_max_distance` must be scaled with it. | Leave at 8 | Leave at 8 |

`format_ranks` does not affect matching; it only decides which copy of a group is recommended.

**If you see…**
* different pictures grouped together → lower the limit (`strict`), or raise `compare_size` (e.g. 768) so small edits become visible;
* obvious duplicates not found → if they are very small or heavily compressed, try `loose`; otherwise raise `hash_max_distance` (e.g. 36);
* too many questions → raise `uncertain_ratio` (e.g. 0.75);
* scans too slow on a big folder → lower `compare_size` to 384.

Always check a change with `--dry-run` first.

## Releasing a new version

Raise `__version__` in `img_dedupe/config.py` (it is the single source of the version, `pyproject.toml` reads it from there), commit, and tag the release so it can be pinned:

```bash
git tag v2.3.0
git push && git push --tags
```

Without the version bump, `pipx upgrade img-dedupe` will not see the new commit (`pipx reinstall` still will).

## Known limitations

* Upscaled copies win on resolution (an upscaled copy isn't detectable as such).
* Very small watermarks on very large photos can disappear at 512 px. Raise `compare_size` or use `strict` if that matters for your library.
* Stage 2 compares candidates pairwise; folders with tens of thousands of images work, but the candidate search takes longer as the folder grows.
