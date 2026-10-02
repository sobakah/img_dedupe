"""Video files: metadata via ffprobe and container-independent stream hashes via ffmpeg.

Only exact content is compared here (no visual matching). A remuxed copy (the
same video stream in another container, e.g. MP4 -> MKV -> MPEG-TS) is found by
hashing the video stream's compressed picture data, which remuxing does not change.
The audio and subtitle tracks are hashed as well, so that copies with the same
picture but other sound are never taken for identical.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
import subprocess
from pathlib import Path

VIDEO_EXTENSIONS = {'.mp4', '.m4v', '.mkv', '.webm', '.mov', '.avi', '.wmv', '.flv',
                    '.mpg', '.mpeg', '.ts', '.m2ts', '.mts', '.3gp', '.ogv'}

# Containers store H.264/HEVC with different framing and may add access-unit
# delimiters, parameter sets or SEI messages when remuxing (MPEG-TS does). These
# filters convert to one framing and keep only the picture data, so all remuxes
# of one stream hash alike. Other codecs are hashed as they are.
_PICTURE_ONLY_FILTERS = {
    "h264": "h264_mp4toannexb,filter_units=remove_types=6|7|8|9",
    "hevc": "hevc_mp4toannexb,filter_units=remove_types=32|33|34|35|39|40",
}
# MPEG-TS stores AAC with ADTS headers, MP4 and MKV without; this filter removes
# them, so an AAC track hashes alike in every container. It accepts only AAC.
_AUDIO_FILTERS = {"aac": "aac_adtstoasc"}


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def tools_available() -> bool:
    return shutil.which("ffprobe") is not None and shutil.which("ffmpeg") is not None


def probe(path: Path) -> dict | None:
    """Stream details read from the container headers (no decoding). None if unreadable."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120)
        data = json.loads(result.stdout or "{}")
        stat = path.stat()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    streams = data.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    # The first real video stream; cover art is stored as a video stream too.
    video_index = next((i for i, s in enumerate(videos)
                        if not (s.get("disposition") or {}).get("attached_pic")), None)
    if video_index is None:
        return None
    video = videos[video_index]

    def lang(stream):
        return (stream.get("tags") or {}).get("language", "und")

    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    return {
        "path": path,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "duration": duration,
        "codec": video.get("codec_name", "?"),
        "video_index": video_index,           # among the video streams, for ffmpeg's -map 0:v:N
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "audio": [[s.get("codec_name", "?"), int(s.get("channels") or 0), lang(s)]
                  for s in streams if s.get("codec_type") == "audio"],
        "subtitles": [[s.get("codec_name", "?"), lang(s)]
                      for s in streams if s.get("codec_type") == "subtitle"],
    }


def _hash_streams(meta: dict, tracks: bool) -> dict | None:
    """Run ffmpeg's streamhash; returns {output stream index: (type, sha256)} or None."""
    path, codec = meta["path"], meta["codec"]
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
            "-map", f"0:v:{meta.get('video_index', 0)}"]
    if tracks:
        argv += ["-map", "0:a?", "-map", "0:s?"]
    argv += ["-c", "copy"]
    if codec in _PICTURE_ONLY_FILTERS:
        argv += ["-bsf:v", _PICTURE_ONLY_FILTERS[codec]]
    if tracks:
        for number, (audio_codec, *_rest) in enumerate(meta["audio"]):
            if audio_codec in _AUDIO_FILTERS:
                argv += [f"-bsf:a:{number}", _AUDIO_FILTERS[audio_codec]]
    argv += ["-f", "streamhash", "-hash", "sha256", "-"]
    try:
        size = path.stat().st_size
        # Generous: 2 minutes plus 5 MB/s, so a 50 GB file on a slow network drive
        # still gets hours, while a hung ffmpeg doesn't block the scan forever.
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120 + size / (5 * 1024 * 1024))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:  # includes ffmpeg crashing on a damaged file
        return None
    hashes = {}
    for line in result.stdout.splitlines():  # "0,v,SHA256=..." per stream
        parts = line.split(",", 2)
        if len(parts) == 3 and parts[2].startswith("SHA256="):
            hashes[parts[0]] = (parts[1], parts[2][len("SHA256="):].strip())
    return hashes


def stream_hashes(meta: dict) -> tuple[str, tuple | None] | None:
    """(hash of the video stream's picture data, hashes of the audio and subtitle
    tracks), or None if the video stream can't be read.

    Reads the whole file but decodes nothing (-c copy). If the tracks can't be
    hashed (an unusual subtitle format, say), they are returned as None, which
    counts as "different from every other copy"."""
    hashes = _hash_streams(meta, tracks=True)
    if hashes is not None and "0" in hashes:
        others = tuple(f"{kind}:{digest}" for index, (kind, digest) in sorted(hashes.items(), key=lambda h: int(h[0]))
                       if index != "0")
        return hashes["0"][1], others
    hashes = _hash_streams(meta, tracks=False)
    if hashes is not None and "0" in hashes:
        return hashes["0"][1], None
    return None


def tracks_identical(metas: list[dict]) -> bool:
    """All copies have the same audio and subtitle tracks, byte for byte."""
    tracks = [m.get("tracks") for m in metas]
    return all(t is not None for t in tracks) and len(set(tracks)) == 1


def covering_copy(metas: list[dict]) -> int | None:
    """Index of the first copy that contains every audio and subtitle track of the
    others (compared by content), so removing the others loses no track; else None."""
    if any(m.get("tracks") is None for m in metas):
        return None
    for i, meta in enumerate(metas):
        have = Counter(meta["tracks"])
        if all(not Counter(other["tracks"]) - have for other in metas):
            return i
    return None


def _duration_text(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def describe(meta: dict) -> str:
    """'H264 1280x720 · 0:30 · 2 audio (eng, deu) · 1 subtitle (eng)'."""
    parts = [f"{meta['codec'].upper()} {meta['width']}x{meta['height']}", _duration_text(meta["duration"])]
    audio, subs = meta["audio"], meta["subtitles"]
    if audio:
        langs = ", ".join(a[2] for a in audio)
        parts.append(f"{len(audio)} audio ({langs})")
    else:
        parts.append("no audio")
    if subs:
        parts.append(f"{len(subs)} subtitle{'s' if len(subs) != 1 else ''} ({', '.join(s[1] for s in subs)})")
    return " · ".join(parts)
