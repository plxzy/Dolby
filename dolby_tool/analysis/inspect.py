"""ffprobe wrapper that produces a clean spec dict for a video file.

We run two ffprobe passes:
1. -show_format -show_streams -show_chapters → container + per-stream info, including DOVI
   side-data on the video stream.
2. -show_frames -read_intervals "%+#1" on the video stream → frame-level side data, which
   is where HDR10 mastering display, content light level, and HDR10+ dynamic metadata live.

If `mediainfo` is on PATH we also run it for richer Atmos detection (E-AC-3 JOC).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

FFPROBE = "ffprobe"
MEDIAINFO = "mediainfo"


class InspectError(Exception):
    pass


# ---------------------------------------------------------------------------
# subprocess helpers


def _run(cmd: list[str], timeout: int = 30) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as e:
        raise InspectError(f"command not found: {cmd[0]}") from e
    if out.returncode != 0:
        msg = (out.stderr or out.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
        raise InspectError(f"{cmd[0]} failed: {msg[0]}")
    return out.stdout


def _ffprobe_streams(path: str) -> dict[str, Any]:
    raw = _run(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            path,
        ]
    )
    return json.loads(raw)


def _ffprobe_first_frame(path: str, video_stream_index: int) -> dict[str, Any]:
    raw = _run(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-select_streams",
            f"v:{video_stream_index}",
            "-show_frames",
            "-read_intervals",
            "%+#1",
            path,
        ],
        timeout=60,
    )
    return json.loads(raw)


def _mediainfo_json(path: str) -> dict[str, Any] | None:
    if not shutil.which(MEDIAINFO):
        return None
    try:
        raw = _run([MEDIAINFO, "--Output=JSON", path])
        return json.loads(raw)
    except (InspectError, json.JSONDecodeError):
        return None


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

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

    if not video_streams:
        raise InspectError("no video stream found")

    vstream = video_streams[0]

    try:
        first = _ffprobe_first_frame(path, 0)
        frame_side = (first.get("frames") or [{}])[0].get("side_data_list") or []
    except (InspectError, IndexError):
        frame_side = []

    stream_side = vstream.get("side_data_list") or []

    # --- video ---------------------------------------------------------------
    fourcc = (vstream.get("codec_tag_string") or "").strip().lower()
    is_dv_fourcc = fourcc in {"dvh1", "dvhe", "dvav", "dva1"}

    dv_info: dict[str, Any] = {"present": False}
    for sd in stream_side + frame_side:
        if "DOVI" in (sd.get("side_data_type") or ""):
            profile = sd.get("dv_profile")
            compat = sd.get("dv_bl_signal_compatibility_id")
            dv_info = {
                "present": True,
                "profile_raw": profile,
                "compatibility_id": compat,
                "compatibility_name": _DV_COMPAT_NAMES.get(
                    compat or 0, f"id={compat}"
                ),
                "profile": _dv_profile_label(profile, compat),
                "level": sd.get("dv_level"),
                "rpu_present": bool(sd.get("rpu_present_flag")),
                "bl_present": bool(sd.get("bl_present_flag")),
                "el_present": bool(sd.get("el_present_flag")),
            }
            break

    fourcc_warning = None
    if dv_info["present"] and not is_dv_fourcc:
        fourcc_warning = (
            f"video sample-entry FourCC is '{fourcc}' — TV.app will NOT engage the DV "
            f"pipeline. Remux with `ffmpeg -c copy -tag:v dvh1 -strict unofficial`."
        )

    hdr10 = {
        "mdcv": any(
            "Mastering display" in (sd.get("side_data_type") or "") for sd in frame_side
        ),
        "cll": any(
            "Content light level" in (sd.get("side_data_type") or "") for sd in frame_side
        ),
    }
    hdr10_plus = any(
        "SMPTE 2094-40" in (sd.get("side_data_type") or "")
        or "HDR10+" in (sd.get("side_data_type") or "")
        for sd in frame_side
    )

    bit_rate = _to_int(vstream.get("bit_rate")) or _to_int(fmt.get("bit_rate"))
    duration = _to_float(vstream.get("duration")) or _to_float(fmt.get("duration"))

    video = {
        "codec": vstream.get("codec_name"),
        "profile": vstream.get("profile"),
        "fourcc": fourcc,
        "fourcc_warning": fourcc_warning,
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "resolution": (
            f"{vstream.get('width')}x{vstream.get('height')}"
            if vstream.get("width") and vstream.get("height")
            else None
        ),
        "pix_fmt": vstream.get("pix_fmt"),
        "fps": _avg_frame_rate(vstream.get("avg_frame_rate")),
        "bit_rate_bps": bit_rate,
        "bit_rate_mbps": round(bit_rate / 1_000_000, 2) if bit_rate else None,
        "color_transfer": vstream.get("color_transfer"),
        "color_primaries": vstream.get("color_primaries"),
        "color_space": vstream.get("color_space"),
        "dolby_vision": dv_info,
        "hdr10": hdr10,
        "hdr10_plus": hdr10_plus,
    }

    # --- audio ---------------------------------------------------------------
    mi = _mediainfo_json(path)
    mi_audio_tracks = []
    if mi:
        mi_audio_tracks = [
            t
            for t in (mi.get("media", {}).get("track") or [])
            if t.get("@type") == "Audio"
        ]

    audio_out = []
    for i, a in enumerate(audio_streams):
        mi_audio = mi_audio_tracks[i] if i < len(mi_audio_tracks) else None
        a_bitrate = _to_int(a.get("bit_rate"))
        audio_out.append(
            {
                "index": a.get("index"),
                "codec": a.get("codec_name"),
                "codec_tag": (a.get("codec_tag_string") or "").lower() or None,
                "profile": a.get("profile"),
                "channels": a.get("channels"),
                "channel_layout": a.get("channel_layout"),
                "sample_rate_hz": _to_int(a.get("sample_rate")),
                "bit_rate_bps": a_bitrate,
                "bit_rate_kbps": round(a_bitrate / 1000) if a_bitrate else None,
                "language": (a.get("tags") or {}).get("language"),
                "title": (a.get("tags") or {}).get("title"),
                "atmos": _detect_atmos(a, mi_audio),
                "default": bool((a.get("disposition") or {}).get("default")),
            }
        )

    # --- subtitles -----------------------------------------------------------
    sub_out = []
    for s in sub_streams:
        disp = s.get("disposition") or {}
        tags = s.get("tags") or {}
        sub_out.append(
            {
                "index": s.get("index"),
                "codec": s.get("codec_name"),
                "language": tags.get("language"),
                "title": tags.get("title") or tags.get("handler_name"),
                "default": bool(disp.get("default")),
                "forced": bool(disp.get("forced")),
                "hearing_impaired": bool(disp.get("hearing_impaired")),
            }
        )

    # --- container -----------------------------------------------------------
    size_bytes = _to_int(fmt.get("size"))
    container = {
        "format_name": fmt.get("format_name"),
        "format_long_name": fmt.get("format_long_name"),
        "duration_s": duration,
        "duration_pretty": _pretty_duration(duration),
        "size_bytes": size_bytes,
        "size_pretty": _pretty_size(size_bytes),
        "overall_bit_rate_bps": _to_int(fmt.get("bit_rate")),
    }

    return {
        "path": path,
        "filename": os.path.basename(path),
        "container": container,
        "video": video,
        "audio": audio_out,
        "subtitles": sub_out,
    }


# ---------------------------------------------------------------------------
# small parse helpers


def _to_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


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
