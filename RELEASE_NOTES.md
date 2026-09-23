# img_dedupe 1.0

*Released 2026-09-23 · first public release*

img_dedupe finds duplicate images in a folder and removes the redundant copies, keeping the best one. It recognises byte-identical files as well as the same picture saved in another format, quality or size, and it is deliberately conservative: pictures that merely look similar, like an edited version or the next frame of a burst, are left alone.

## Installation

```bash
pipx install git+https://github.com/sobakah/img_dedupe.git
```

With JPEG XL support:

```bash
pipx install "img-dedupe[jxl] @ git+https://github.com/sobakah/img_dedupe.git"
```

To install exactly this release, add `@v1.0` to the end of the URL. Requires Python 3.9 or newer; Pillow and send2trash are installed automatically.

## Highlights

- **Two stages.** Exact copies are found by their content (SHA-256). Visually identical images are found by comparing the actual pixels, after a quick fingerprint check has picked the candidates.
- **Keeps the best copy.** Higher resolution first, then lossless over lossy, then your preferred format, then a name without a copy number like "(1)", then the less compressed file, then the oldest.
- **Safe by default.** Files go to the system trash, never straight to deletion. A dry run shows everything in advance and can then be applied without scanning again.
- **Traceable.** Every change is written to a log, with the file that was kept instead and the reason.
- **Interruptible.** Review progress is saved after every step, so you can stop at any time and continue later.

## Finding duplicates

- **What counts as the same picture:** re-saved and converted copies (JPEG, PNG, WebP, AVIF, TIFF, BMP, GIF, JPEG XL), downscaled copies, copies rotated by an EXIF tag, and copies saved with a different colour profile.
- **What does not:** crops, added text or watermarks, brightness and colour edits. Images with a different aspect ratio are never compared.
- **Strictness presets** `strict`, `normal` (default) and `loose` set how different two pictures may be; each match shows its score, so you can see how close it was.
- **Copy numbers:** when the better copy is called `photo (1).jpg`, it is renamed to `photo.jpg` after the other copy is removed. This only happens when another file in the same group shows that the number is a copy number, so names like `Holiday (12).jpg` in a series keep their number.

## Reviewing

- **Start screen** with every setting and all its choices visible, plus a folder list to choose which subfolders are scanned.
- **Numbered groups** (`[3/12]`) with the image to keep marked `▶`. Enter keeps the recommendation, a number keeps another image, `s` skips the group, and `v` opens the images in a viewer (system default, Identity, kitty or timg).
- **Overview** of all groups with their status, to jump back to skipped ones.
- **You decide how much to confirm:** only borderline matches (default), every group, or nothing (`--auto` for scripts).

## Safety and traceability

- **Delete modes:** trash (default), dry run, or permanent. Permanent deletion always asks first, with No as the default answer.
- **After a dry run,** you can apply exactly what it showed, or review the groups again, using the delete mode from your config.
- **The log** records every trashed, deleted or renamed file with its full path.
- **Saved sessions** survive quitting, Ctrl+C and crashes. Before a saved session or a dry run is carried out, every file is checked against the disk, and anything that changed in the meantime is left alone.

## Command line

`-r` include subfolders · `--exclude DIR` skip a subfolder · `--stages 1|2|both` · `--strictness strict|normal|loose` · `-i` confirm every group · `-y`/`--auto` no questions · `--dry-run` · `--no-rename` · `-c FILE` config file · `--color auto|always|never` · `-v` more logging

All settings can also be set in `config.json`; `config.example.json` lists every option with its default.

## Known limitations

- A copy that was *upscaled* counts as the better one, because it has the higher resolution.
- Very small watermarks on very large photos can disappear at the 512 px comparison size; raise `compare_size` or use `strict` if that matters for your pictures.
- During the pixel comparison, a thumbnail of every candidate image is held in memory at once (about 768 KB each). Folders with thousands of candidates can need several GB of RAM for this step.
- Progress is saved from the moment the duplicate groups are found; interrupting the analysis before that means the scan starts over.
- Dry runs and `--auto` runs are not saved as sessions.
- Animated images are only checked for exact copies.
- Developed and tested on Linux. macOS and Windows are supported by the code but have not been tested yet.
