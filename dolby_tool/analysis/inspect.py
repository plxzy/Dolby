"""ffprobe wrapper that produces a clean spec dict for a video file.

We run two ffprobe passes:
1. -show_format -show_streams → container + per-stream info, including DOVI
   side-data on the video stream.
2. -show_frames -read_intervals "%+#1" on the video stream → frame-level side data, which
   is where HDR10 mastering display, content light level, and HDR10+ dynamic metadata live.

If `mediainfo` is on PATH we also run it for richer Atmos detection (E-AC-3 JOC).
"""

import json
import os
from typing import Any

from ..common import DV_FOURCCS, ToolError, ffprobe_json, run_tool, to_float, to_int, which_any

MEDIAINFO = "mediainfo"


class InspectError(Exception):
    """The file could not be inspected: missing, unreadable, or without a video stream."""


# ---------------------------------------------------------------------------
# external tools


def _ffprobe_streams(path: str) -> dict[str, Any]:
    try:
        return ffprobe_json(path, "-show_format", "-show_streams", timeout=30)
    except ToolError as e:
        raise InspectError(str(e)) from e


def _first_frame_side_data(path: str) -> list[dict[str, Any]]:
    """Side data of the first decoded video frame; best-effort, empty when the probe fails."""
    try:
        first = ffprobe_json(path, "-select_streams", "v:0", "-show_frames", "-read_intervals", "%+#1")
    except ToolError:
        return []
    return (first.get("frames") or [{}])[0].get("side_data_list") or []


def _mediainfo_audio_tracks(path: str) -> list[dict[str, Any]]:
    """MediaInfo's audio tracks, in stream order; empty when mediainfo is unavailable."""
    if not which_any(MEDIAINFO):
        return []
    try:
        info = json.loads(run_tool([MEDIAINFO, "--Output=JSON", path], timeout=30).stdout)
    except ToolError, json.JSONDecodeError:
        return []
    return [t for t in (info.get("media", {}).get("track") or []) if t.get("@type") == "Audio"]


# ---------------------------------------------------------------------------
# DV profile decoding


_DV_COMPAT_NAMES = {
    0: "None",
    1: "HDR10",
    2: "SDR",
    4: "HLG",
    6: "Blu-ray HDR10",
}


def _dv_profile_label(profile: int | None, compat_id: int | None) -> str | None:
    """Return human-readable profile string like '8.1' or '5'."""
    if profile is None:
        return None
    if profile == 8 and compat_id is not None:
        return f"8.{compat_id}"
    return str(profile)


def _dolby_vision(side_data: list[dict[str, Any]]) -> dict[str, Any]:
    """Decode the first DOVI configuration record, or report that there is none."""
    record = next((sd for sd in side_data if "DOVI" in (sd.get("side_data_type") or "")), None)
    if record is None:
        return {"present": False}
    profile = record.get("dv_profile")
    compat = record.get("dv_bl_signal_compatibility_id")
    return {
        "present": True,
        "profile_raw": profile,
        "compatibility_id": compat,
        "compatibility_name": _DV_COMPAT_NAMES.get(compat or 0, f"id={compat}"),
        "profile": _dv_profile_label(profile, compat),
        "level": record.get("dv_level"),
        "rpu_present": bool(record.get("rpu_present_flag")),
        "bl_present": bool(record.get("bl_present_flag")),
        "el_present": bool(record.get("el_present_flag")),
    }


def _fourcc_warning(fourcc: str, dolby_vision: dict[str, Any]) -> str | None:
    """Explain why a DV stream behind a non-DV sample entry will not play as DV."""
    if not dolby_vision["present"] or fourcc in DV_FOURCCS:
        return None
    return (
        f"video sample-entry FourCC is '{fourcc}' — TV.app will NOT engage the DV "
        f"pipeline. Remux with `ffmpeg -c copy -tag:v dvh1 -strict unofficial`."
    )


def _has_side_data(side_data: list[dict[str, Any]], *needles: str) -> bool:
    return any(needle in (sd.get("side_data_type") or "") for sd in side_data for needle in needles)


# ---------------------------------------------------------------------------
# per-section builders


def _video_spec(path: str, stream: dict[str, Any], fmt: dict[str, Any]) -> dict[str, Any]:
    stream_side = stream.get("side_data_list") or []
    frame_side = _first_frame_side_data(path)
    fourcc = (stream.get("codec_tag_string") or "").strip().lower()
    dolby_vision = _dolby_vision(stream_side + frame_side)
    bit_rate = to_int(stream.get("bit_rate")) or to_int(fmt.get("bit_rate"))
    width, height = stream.get("width"), stream.get("height")
    return {
        "codec": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "fourcc": fourcc,
        "fourcc_warning": _fourcc_warning(fourcc, dolby_vision),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else None,
        "pix_fmt": stream.get("pix_fmt"),
        "fps": _avg_frame_rate(stream.get("avg_frame_rate")),
        "bit_rate_bps": bit_rate,
        "bit_rate_mbps": round(bit_rate / 1_000_000, 2) if bit_rate else None,
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
        "color_space": stream.get("color_space"),
        "dolby_vision": dolby_vision,
        "hdr10": {
            "mdcv": _has_side_data(frame_side, "Mastering display"),
            "cll": _has_side_data(frame_side, "Content light level"),
        },
        "hdr10_plus": _has_side_data(frame_side, "SMPTE 2094-40", "HDR10+"),
    }


def _audio_tracks(path: str, streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mi_tracks = _mediainfo_audio_tracks(path)
    return [_audio_track(s, mi_tracks[i] if i < len(mi_tracks) else None) for i, s in enumerate(streams)]


def _audio_track(stream: dict[str, Any], mi_audio: dict[str, Any] | None) -> dict[str, Any]:
    tags = stream.get("tags") or {}
    bit_rate = to_int(stream.get("bit_rate"))
    return {
        "index": stream.get("index"),
        "codec": stream.get("codec_name"),
        "codec_tag": (stream.get("codec_tag_string") or "").lower() or None,
        "profile": stream.get("profile"),
        "channels": stream.get("channels"),
        "channel_layout": stream.get("channel_layout"),
        "sample_rate_hz": to_int(stream.get("sample_rate")),
        "bit_rate_bps": bit_rate,
        "bit_rate_kbps": round(bit_rate / 1000) if bit_rate else None,
        "language": tags.get("language"),
        "title": tags.get("title"),
        "atmos": _detect_atmos(stream, mi_audio),
        "default": bool((stream.get("disposition") or {}).get("default")),
    }


def _subtitle_tracks(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tracks = []
    for stream in streams:
        disposition = stream.get("disposition") or {}
        tags = stream.get("tags") or {}
        tracks.append(
            {
                "index": stream.get("index"),
                "codec": stream.get("codec_name"),
                "language": tags.get("language"),
                "title": tags.get("title") or tags.get("handler_name"),
                "default": bool(disposition.get("default")),
                "forced": bool(disposition.get("forced")),
                "hearing_impaired": bool(disposition.get("hearing_impaired")),
            }
        )
    return tracks


def _container(fmt: dict[str, Any], video_stream: dict[str, Any]) -> dict[str, Any]:
    # The video stream's own duration wins: the container's can include trailing audio.
    duration = to_float(video_stream.get("duration")) or to_float(fmt.get("duration"))
    size_bytes = to_int(fmt.get("size"))
    return {
        "format_name": fmt.get("format_name"),
        "format_long_name": fmt.get("format_long_name"),
        "duration_s": duration,
        "duration_pretty": _pretty_duration(duration),
        "size_bytes": size_bytes,
        "size_pretty": _pretty_size(size_bytes),
        "overall_bit_rate_bps": to_int(fmt.get("bit_rate")),
    }


# ---------------------------------------------------------------------------
# audio helpers


def _detect_atmos(stream: dict[str, Any], mi_audio: dict[str, Any] | None) -> bool | None:
    codec = (stream.get("codec_name") or "").lower()
    profile = (stream.get("profile") or "").lower()
    if "atmos" in profile or "joc" in profile:
        return True
    if mi_audio:
        fmt_info = " ".join(
            str(mi_audio.get(k, ""))
            for k in (
                "Format_AdditionalFeatures",
                "Format_Commercial_IfAny",
                "Format_Profile",
                "Format_Settings",
            )
        ).lower()
        if "atmos" in fmt_info or "joc" in fmt_info:
            return True
        if fmt_info:
            return False
    if codec in {"eac3", "ec3"}:
        layout = (stream.get("channel_layout") or "").lower()
        if stream.get("channels", 0) >= 8 and "object" in layout:
            return True
    return None  # unknown


# ---------------------------------------------------------------------------
# main entrypoint


def inspect_file(path: str) -> dict[str, Any]:
    """Return a structured spec for `path`. Raises InspectError on hard failure."""
    if not os.path.isfile(path):
        raise InspectError(f"not a file: {path}")

    probe = _ffprobe_streams(path)
    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise InspectError("no video stream found")

    return {
        "path": path,
        "filename": os.path.basename(path),
        "container": _container(fmt, video_stream),
        "video": _video_spec(path, video_stream, fmt),
        "audio": _audio_tracks(path, [s for s in streams if s.get("codec_type") == "audio"]),
        "subtitles": _subtitle_tracks([s for s in streams if s.get("codec_type") == "subtitle"]),
    }


# ---------------------------------------------------------------------------
# small parse helpers


def _avg_frame_rate(s: str | None) -> float | None:
    if not s or "/" not in s:
        return None
    try:
        num, den = s.split("/", 1)
        n, d = float(num), float(den)
        if d == 0:
            return None
        return round(n / d, 3)
    except ValueError:
        return None


def _pretty_size(b: int | None) -> str | None:
    if not b:
        return None
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(b)
    for u in units:
        if f < 1024:
            return f"{f:.2f} {u}"
        f /= 1024
    return f"{f:.2f} PB"


def _pretty_duration(seconds: float | None) -> str | None:
    if not seconds:
        return None
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {sec:02d}s"
    return f"{m}m {sec:02d}s"
