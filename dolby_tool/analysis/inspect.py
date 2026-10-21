"""ffprobe wrapper that turns a video file into a `MediaSpec`.

We run two ffprobe passes:
1. -show_format -show_streams → container + per-stream info, including DOVI
   side-data on the video stream.
2. -show_frames -read_intervals "%+#1" on the video stream → frame-level side data, which
   is where HDR10+ dynamic metadata lives, and where HDR10 mastering display / content light
   level show up when they are carried in the bitstream instead of the container.

If `mediainfo` is on PATH we also run it for richer Atmos detection (E-AC-3 JOC).
"""

import json
import os
from typing import Any

from ..common import DV_FOURCCS, ToolError, ffprobe_json, run_tool, to_float, to_int, which_any
from .models import AudioTrack, Container, DolbyVision, Hdr10, MediaSpec, SubtitleTrack, VideoSpec

MEDIAINFO = "mediainfo"

# One JSON object as ffprobe / mediainfo print it.
type Raw = dict[str, Any]


class InspectError(Exception):
    """The file could not be inspected: missing, unreadable, or without a video stream."""


def inspect_file(path: str) -> MediaSpec:
    """Return a structured spec for `path`. Raises InspectError on hard failure."""
    if not os.path.isfile(path):
        raise InspectError(f"not a file: {path}")

    probe = _ffprobe_streams(path)
    fmt = probe.get("format") or {}
    streams = probe.get("streams") or []

    video_stream = _primary_video_stream(streams)
    if video_stream is None:
        raise InspectError("no video stream found")

    return MediaSpec(
        path=path,
        filename=os.path.basename(path),
        container=_container(fmt, video_stream),
        video=_video_spec(path, video_stream, fmt),
        audio=_audio_tracks(path, [s for s in streams if s.get("codec_type") == "audio"]),
        subtitles=_subtitle_tracks([s for s in streams if s.get("codec_type") == "subtitle"]),
    )


# ---------------------------------------------------------------------------
# external tools


def _ffprobe_streams(path: str) -> Raw:
    try:
        return ffprobe_json(path, "-show_format", "-show_streams", timeout=30)
    except ToolError as e:
        raise InspectError(str(e)) from e


def _first_frame_side_data(path: str, stream_index: int) -> list[Raw]:
    """Side data of the first decoded frame of one stream; best-effort, empty when the probe fails."""
    try:
        first = ffprobe_json(path, "-select_streams", str(stream_index), "-show_frames", "-read_intervals", "%+#1")
    except ToolError:
        return []
    return (first.get("frames") or [{}])[0].get("side_data_list") or []


def _mediainfo_audio_tracks(path: str) -> list[Raw]:
    """MediaInfo's audio tracks, in stream order; empty when mediainfo is unavailable."""
    if not which_any(MEDIAINFO):
        return []
    try:
        info = json.loads(run_tool([MEDIAINFO, "--Output=JSON", path], timeout=30).stdout)
    except ToolError, json.JSONDecodeError:
        return []
    return [t for t in (info.get("media", {}).get("track") or []) if t.get("@type") == "Audio"]


# ---------------------------------------------------------------------------
# per-section builders


def _primary_video_stream(streams: list[Raw]) -> Raw | None:
    """The first real video stream: cover art (an attached picture) is not the video."""
    return next(
        (s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )


def _container(fmt: Raw, video_stream: Raw) -> Container:
    # The video stream's own duration wins: the container's can include trailing audio.
    duration = to_float(video_stream.get("duration")) or to_float(fmt.get("duration"))
    size_bytes = to_int(fmt.get("size"))
    return Container(
        format_name=fmt.get("format_name"),
        format_long_name=fmt.get("format_long_name"),
        duration_s=duration,
        duration_pretty=_pretty_duration(duration),
        size_bytes=size_bytes,
        size_pretty=_pretty_size(size_bytes),
        overall_bit_rate_bps=to_int(fmt.get("bit_rate")),
    )


def _video_spec(path: str, stream: Raw, fmt: Raw) -> VideoSpec:
    stream_side = stream.get("side_data_list") or []
    frame_side = _first_frame_side_data(path, stream["index"])
    fourcc = (stream.get("codec_tag_string") or "").strip().lower()
    side_data = stream_side + frame_side
    dolby_vision = _dolby_vision(side_data)
    bit_rate = to_int(stream.get("bit_rate")) or to_int(fmt.get("bit_rate"))
    width, height = stream.get("width"), stream.get("height")
    return VideoSpec(
        codec=stream.get("codec_name"),
        profile=stream.get("profile"),
        fourcc=fourcc,
        fourcc_warning=_fourcc_warning(fourcc, dolby_vision),
        width=width,
        height=height,
        resolution=f"{width}x{height}" if width and height else None,
        pix_fmt=stream.get("pix_fmt"),
        fps=_avg_frame_rate(stream.get("avg_frame_rate")),
        bit_rate_bps=bit_rate,
        bit_rate_mbps=round(bit_rate / 1_000_000, 2) if bit_rate else None,
        color_transfer=stream.get("color_transfer"),
        color_primaries=stream.get("color_primaries"),
        color_space=stream.get("color_space"),
        dolby_vision=dolby_vision,
        hdr10=Hdr10(
            mdcv=_has_side_data(side_data, "Mastering display"),
            cll=_has_side_data(side_data, "Content light level"),
        ),
        hdr10_plus=_has_side_data(frame_side, "SMPTE 2094-40", "HDR10+"),
    )


def _audio_tracks(path: str, streams: list[Raw]) -> list[AudioTrack]:
    mi_tracks = _mediainfo_audio_tracks(path)
    return [_audio_track(s, mi_tracks[i] if i < len(mi_tracks) else None) for i, s in enumerate(streams)]


def _audio_track(stream: Raw, mi_audio: Raw | None) -> AudioTrack:
    tags = stream.get("tags") or {}
    bit_rate = to_int(stream.get("bit_rate"))
    return AudioTrack(
        index=stream["index"],
        codec=stream.get("codec_name"),
        codec_tag=(stream.get("codec_tag_string") or "").lower() or None,
        profile=stream.get("profile"),
        channels=stream.get("channels"),
        channel_layout=stream.get("channel_layout"),
        sample_rate_hz=to_int(stream.get("sample_rate")),
        bit_rate_bps=bit_rate,
        bit_rate_kbps=round(bit_rate / 1000) if bit_rate else None,
        language=tags.get("language"),
        title=tags.get("title"),
        atmos=_detect_atmos(stream, mi_audio),
        default=bool((stream.get("disposition") or {}).get("default")),
    )


def _subtitle_tracks(streams: list[Raw]) -> list[SubtitleTrack]:
    return [_subtitle_track(s) for s in streams]


def _subtitle_track(stream: Raw) -> SubtitleTrack:
    disposition = stream.get("disposition") or {}
    tags = stream.get("tags") or {}
    return SubtitleTrack(
        index=stream["index"],
        codec=stream.get("codec_name"),
        language=tags.get("language"),
        title=tags.get("title") or tags.get("handler_name"),
        default=bool(disposition.get("default")),
        forced=bool(disposition.get("forced")),
        hearing_impaired=bool(disposition.get("hearing_impaired")),
    )


# ---------------------------------------------------------------------------
# Dolby Vision / HDR side data


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


def _dolby_vision(side_data: list[Raw]) -> DolbyVision:
    """Decode the first DOVI configuration record, or report that there is none."""
    record = next((sd for sd in side_data if "DOVI" in (sd.get("side_data_type") or "")), None)
    if record is None:
        return DolbyVision()
    profile = record.get("dv_profile")
    compat = record.get("dv_bl_signal_compatibility_id")
    return DolbyVision(
        present=True,
        profile_raw=profile,
        compatibility_id=compat,
        compatibility_name=_DV_COMPAT_NAMES.get(compat or 0, f"id={compat}"),
        profile=_dv_profile_label(profile, compat),
        level=record.get("dv_level"),
        rpu_present=bool(record.get("rpu_present_flag")),
        bl_present=bool(record.get("bl_present_flag")),
        el_present=bool(record.get("el_present_flag")),
    )


def _fourcc_warning(fourcc: str, dolby_vision: DolbyVision) -> str | None:
    """Explain why a DV stream behind a non-DV sample entry will not play as DV."""
    if not dolby_vision.present or fourcc in DV_FOURCCS:
        return None
    prefix = f"video sample-entry FourCC is '{fourcc}' — TV.app will NOT engage the DV pipeline."
    if dolby_vision.profile_raw == 7:
        return (
            f"{prefix} This is dual-layer Dolby Vision profile 7, which Apple players do not "
            "support at all: convert it to profile 8.1 first (e.g. `dovi_tool -m 2 convert`), "
            "then remux with `-tag:v dvh1`."
        )
    return f"{prefix} Remux with `ffmpeg -c copy -tag:v dvh1 -strict unofficial`."


def _has_side_data(side_data: list[Raw], *needles: str) -> bool:
    return any(needle in (sd.get("side_data_type") or "") for sd in side_data for needle in needles)


# ---------------------------------------------------------------------------
# audio helpers


def _detect_atmos(stream: Raw, mi_audio: Raw | None) -> bool | None:
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
        if (stream.get("channels") or 0) >= 8 and "object" in layout:
            return True
    return None  # unknown


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
