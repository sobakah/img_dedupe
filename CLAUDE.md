# img_dedupe – notes for Claude Code

Finds duplicate images and videos and removes redundant copies, keeping the best one.
Terminal app (no GUI), Python 3.9+, dependencies Pillow and send2trash; ffmpeg/ffprobe optional (videos).
Owner: sobakah (GitHub `sobakah/img_dedupe`, public). Developed on Fedora, installed with pipx (Python 3.14).

## Layout – important

- The package lives in **`scripts/`** (owner's choice, keep it) but is installed as the package
  **`img_dedupe`** via `package-dir = { img_dedupe = "scripts" }` in `pyproject.toml`.
  All imports inside `scripts/` are relative. The command is `img_dedupe`; the pipx name is `img-dedupe`.
- From the checkout: `python3 -m scripts <folder>` (not `-m img_dedupe`).
- `scripts/cli.py` arguments → `app.py` (start screen, folder list, run_scan, sessions flow)
  → `core_logic.py` (the three stages, review loop, deletion, resume) → `image_utils.py`
  (normalising, hashing, pixel comparison), `video.py` (ffprobe, stream hash), `session.py`
  (log, saved sessions, "not duplicates" marks), `config.py` (defaults, validation, version),
  `ui.py` (terminal styling, menus, tables), `viewer.py`.
- `core_logic.py` mixes logic and terminal output (21 of 46 functions print/ask). Known; a GUI
  would need that separated first.

## Commands

```bash
python3 -m pytest                 # 138 tests, ~10 s; video tests skip without ffmpeg
python3 -m pytest -k video        # a subset
python3 -m scripts ~/Pictures --dry-run      # run from the checkout
```

Run the tests with a Python that has Pillow, send2trash and pytest. If those are missing, the CLI
tests fail with misleading assertions like `(0, 0) == (1, 0)` (the program exits "Missing required
packages"). With the pipx install: `pipx inject img-dedupe pytest`, then
`"$(pipx environment --value PIPX_LOCAL_VENVS)/img-dedupe/bin/python" -m pytest`.

## Tests

- `tests/conftest.py`: autouse isolation (own HOME, XDG dirs, trash and `IMG_DEDUPE_STATE_DIR`),
  `library` (build picture folders: `photo`, `variant`, `copy_of`), `run_cli(folder, *args,
  keys=[...])` (runs the real CLI, typed answers via stdin), `summary(result)` (parses the final
  numbers), `fake_command` (stand-in programs on PATH; `.calls(count=…)` waits for background calls),
  `make_video` / `remux` (ffmpeg).
- `tests/pictures.py`: deterministic photo-like pictures. Calibrated: re-encodes/resizes score ≤ 16,
  edits (`with_text`, `with_patch`, `brighter`) ≥ 32, limit 20.
- `IMG_DEDUPE_STATE_DIR` redirects log and sessions (otherwise a checkout writes them into the
  project folder). Never let tests touch the real project folder, config or trash.
- Every fix gets a test that fails without the fix. Prefer reproducing a reported bug first.

## Behaviour that must not regress (safety rules)

- Files go to the trash by default; nothing is deleted without being compared against the file
  that is **kept** (stage 3 compares each member with the keeper, never via chains).
- Enter keeps the recommended file; in `permanent` mode deletions in interactive screens ask `[y/N]`
  (default No). Home and root folder are refused in every mode (`is_protected`, one shared message).
- Choosing another keeper than the recommended one (`regroup_around`) compares every other image
  with the chosen one afresh; images above the limit stay on disk and leave the group.
- Before resuming a session or carrying out a dry run, every file is checked against the disk
  (size + mtime); changed or missing files are left alone.
- `--dry-run` on the command line always wins (`config["_cli_dry_run"]`): nothing is deleted or
  renamed in that run. The start screen locks the delete mode, doesn't resume a saved real session
  or carry out a saved dry run, and no carry-out is offered after the dry run; `run_scan` raises if
  a real mode slips through. A dry run chosen with setting 4 may still be carried out right after.
- `permanent` mode asks once before an interactive run starts (`confirm_permanent`), since
  automatic decisions then delete without further questions. `--auto` asks nothing (open point).
- Stage 2 hashes audio and subtitle tracks by content too (`stream_hashes`; AAC normalised with
  `aac_adtstoasc`, so MPEG-TS remuxes match). Automatic only when all tracks are identical; a copy
  containing every track of the others (`covering_copy`) is recommended; without questions a group
  is only resolved when such a copy exists, otherwise left alone and counted as skipped.
- A dry run resumes as a dry run; settings that decided a session's groups (stages, strictness
  looser, videos, subfolders) are locked on the start screen; stricter strictness works without a
  rescan (`tighten_groups`).
- The main folder is only excluded with its own key `m` (or `--exclude .`); numbers/ranges/a/n in
  the folder list change subfolders only; each listed folder switches on its own (`switch_folders`).
- Copy numbers "(n)" are only removed from a kept file when another file of the group shares the
  base name (series names like `Holiday (12).jpg` stay); never overwrite an existing name.
- Keeper ranking: resolution → lossless over lossy/unknown → `format_ranks` → no copy number → bpp
  → oldest. JPEG XL is "unknown" (Pillow can't tell lossy from lossless), so a lossless original
  wins; config `prefer_jxl: true` ranks JXL like lossless (`lossless_rank`). TIFF is lossy only
  when JPEG/WebP/SGILog-compressed.
- Greyscale with more than 8 bits (I;16, I, F) is scaled by the bit depth the values use
  (`_to_8_bit`: 8/10/12/14/16 bit; floats 0–1), never by a fixed /256.

## Stages and terms

1 identical files (SHA-256, same-size files only) · 2 remuxed videos (ffprobe preselects, then a
container-independent hash of the video stream; H.264/HEVC "picture data only" via bitstream
filters; audio/subtitle tracks hashed as well) · 3 visually identical images (128-bit dhash candidates, then worst 8×8-cell pixel
difference at ≤ 512 px; presets strict 12 / normal 20 / loose 28; borderline above
`uncertain_ratio` × limit, default 0.8). `--stages` takes numbers (`1,2`, `3`, `all`; `both` = all).

## Conventions

- UI style follows the owner's other project lrckit: plain ANSI via `StyleUI`, banners, `[BADGE]`s,
  grouped key menus, highlighted Enter action, `▶` marker, greyed/struck-through unavailable choices,
  `‹ ›` markers when colours are off. Keep new screens consistent.
- Config: new keys go into `DEFAULT_CONFIG`, get validated in `config.py` (types/ranges → warning +
  default), and appear in `config.example.json` (regenerate from `DEFAULT_CONFIG`) and the README.
- README is in English, concise, reader-first order; every option/key documented once. Keep it in
  sync with behaviour (reviews found mismatches before).
- Commit messages: English, subject ≤ 72 chars, body wrapped at 72, explain what and why.
- Versions: `__version__` in `scripts/config.py` is the only source. Behaviour changes → minor
  version (1.3 → 1.4), pure fixes → patch. Release: update `RELEASE_NOTES.md` (newest first,
  sections New/Changed/Fixed, upgrade notes), run tests, commit, `git tag -a vX.Y -m "img_dedupe X.Y"`,
  push with tags. Without a version bump `pipx upgrade` doesn't pick up changes.
- Shell scripts/examples: the owner's shell is bash on Fedora; don't rely on tools only some
  distributions ship.

## Environment notes

- Fedora's `ffmpeg-free` lacks some encoders (H.264); H.264 video tests skip there. Remux detection
  itself needs no decoders.
- Known limitations (README): upscaled copies win on resolution; tiny watermarks can vanish at
  512 px; all candidate thumbnails are held in memory during stage 3 (~768 KB each); MPEG-4 Part 2
  inside MPEG-TS can't be read by ffmpeg (left alone); macOS/Windows untested.

## Working style the owner expects

- Verify claims by running code before acting on them (several outside reviews contained wrong
  claims); say clearly what was tested and what wasn't.
- Small, focused changes with tests; summarise what changed, which files, and a commit message.
- Don't change the package folder name, the default delete mode (trash) or Enter's meaning without
  asking.

## Open tasks (target: release 1.4)

Done since 1.3 (uncommitted at the time of writing): the shared home/root message
(`protected_message`) and the data-safety fixes above (dry-run, remux tracks, keeper recompare,
permanent confirmation, JXL/TIFF, high bit depth); release notes are under "Unreleased".

- **Ask the owner:** `--auto` with `permanent` – only a warning, or an extra explicit option?
  Dry-run "not duplicates" marks write `.img_dedupe_ignore.json` into the picture folder, which
  contradicts "changes nothing" – change the README wording or store marks elsewhere (see E3).
- **Quick fixes:** Tab completion with spaces (`set_delims` in `ui.py` → `"\t\n"`); unhandled
  `p.stat()` in `find_exact_duplicates` when a file vanishes; README describes `n`/`x` differently
  though both discard the session; don't group 0-byte files; warn on `--exclude` without `-r`;
  case-insensitive `format_ranks` keys; friendlier goodbye than "Program terminated."; summary
  "Visually identical images" (adjust `summary()` regex in `tests/conftest.py`); `kitty` viewer
  fallback outside kitty (check return code); pytest should stop early with a clear message naming
  `sys.executable` when Pillow/send2trash are missing; "Missing required packages" should name the
  Python used; `--auto` should print excluded folders.
- **Performance:** with defaults the candidate search is always all-pairs (128 bits / 29 chunks =
  4 bits; 0.9 s for 5,000 images, ~90 s for 50,000, no progress shown) – numpy XOR+popcount or a
  BK-tree, at least a progress line. Pixel comparisons run serially and re-blur the anchor per
  pair – cache blurred thumbnails. All candidate thumbnails are held in memory – process clusters
  in batches of ~200.
- **Possible features (owner decides):** E1 folder priority (`--prefer DIR` / reference folders
  never deleted from – rated most valuable); E2 HEIC/HEIF and RAW (extensions for stage 1,
  optional `pillow-heif` for stage 3); E3 marks tied to content instead of relative paths; E4 list
  files with damaged EXIF (Pillow's "Truncated File Read" names no file); E5 lock against two
  instances, log rotation, cleanup of old sessions; E6 README `curl` command to fetch
  `config.example.json` for pipx users.
- **Release 1.4:** `__version__` → 1.4, turn "Unreleased" into 1.4 in `RELEASE_NOTES.md` (add the
  home/root message under Changed), README in sync, tests, commit, tag `v1.4`, push with tags.
