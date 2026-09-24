"""Video files: metadata via ffprobe and container-independent stream hashes via ffmpeg.

Only exact content is compared here (no visual matching). A remuxed copy (the
same video stream in another container, e.g. MP4 -> MKV -> MPEG-TS) is found by
hashing the video stream's compressed picture data, which remuxing does not change.
"""

from __future__ import annotations

import json
import shutil
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
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        return None

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
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "audio": [[s.get("codec_name", "?"), int(s.get("channels") or 0), lang(s)]
                  for s in streams if s.get("codec_type") == "audio"],
        "subtitles": [[s.get("codec_name", "?"), lang(s)]
                      for s in streams if s.get("codec_type") == "subtitle"],
    }


def video_stream_hash(path: Path, codec: str) -> str | None:
    """SHA-256 of the first video stream's picture data, or None if ffmpeg fails.

    Reads the whole file but decodes nothing (-c copy)."""
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
            "-map", "0:v:0", "-c", "copy"]
    if codec in _PICTURE_ONLY_FILTERS:
        argv += ["-bsf:v", _PICTURE_ONLY_FILTERS[codec]]
    argv += ["-f", "streamhash", "-hash", "sha256", "-"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError:
        return None
    if result.returncode != 0:  # includes ffmpeg crashing on a damaged file
        return None
    for line in result.stdout.splitlines():
        if "SHA256=" in line:
            return line.split("SHA256=", 1)[1].strip()
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


def track_signature(meta: dict) -> tuple:
    """What a clean remux keeps unchanged: audio and subtitle tracks."""
    return (tuple(tuple(a) for a in meta["audio"]), tuple(tuple(s) for s in meta["subtitles"]))
