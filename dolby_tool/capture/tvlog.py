"""macOS `log stream` wrapper that captures TV.app playback events and parses them.

Usage from server:

    capture = LogCapture()
    capture.start()              # spawns `log stream` subprocess
    ...                          # events stream into capture.events
    summary = capture.stop()     # collapses the buffer into a structured summary

`events` is a list[dict]. Each dict has `kind`, `raw`, `ts`, plus parsed fields.

The predicate targets the four processes that emit useful playback telemetry on macOS:
mediaplaybackd, VTDecoderXPCService, coremediaxpc, audiomxd — covering both HLS
streaming (Apple TV+) and FigFilePlayer (local library) paths.
"""
from __future__ import annotations

import asyncio
import re
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

PREDICATE = (
    'process == "mediaplaybackd"'
    ' OR process == "VTDecoderXPCService"'
    ' OR process == "coremediaxpc"'
    ' OR process == "audiomxd"'
    ' OR process == "TV"'
)

LOCAL_PLAYER_PREDICATE = (
    PREDICATE
    + ' OR process == "QuickTime Player"'
    + ' OR process == "Safari"'
    + ' OR process CONTAINS "WebKit"'
)


# ---------------------------------------------------------------------------
# regexes — kept loose, errors of omission are fine, errors of commission are not


# FigAlternate(504) [Peak/Avg 30570719/24765202] [3840x1606] [dvh1.05.06,ec-3]
# <FigAlternate(504):[0x...] [Peak/Avg 30570719/24765202] [3840x1606] [AudioGroup ...] [dvh1.05.06,ec-3]
# [VideoRange PQ] [HDCP Type1] [FrameRate 23.976]
RE_FIG_ALT = re.compile(
    r"FigAlternate\(\s*(?P<id>\d+)\s*\)"
)
RE_FIG_ALT_PEAK = re.compile(r"\[Peak/Avg\s+(?P<peak>\d+)/(?P<avg>\d+)\]")
RE_FIG_ALT_RES = re.compile(r"\[(?P<width>\d+)x(?P<height>\d+)\]")
RE_FIG_ALT_CODECS = re.compile(r"\[(?P<codecs>(?:avc1|hvc1|dvh1|dvhe|ac-3|ec-3|mp4a)[^\]]*)\]")
RE_FIG_ALT_AUDIO_GROUP = re.compile(r"\[AudioGroup\s+(?P<audio_group>[^\]]+)\]")
RE_FIG_ALT_RANGE = re.compile(r"\[VideoRange\s+(?P<range>\w+)\]")
RE_FIG_ALT_HDCP = re.compile(r"\[HDCP\s+(?P<hdcp>[^\]]+)\]")
RE_FIG_ALT_FPS = re.compile(r"\[FrameRate\s+(?P<fps>[\d.]+)\]")

# Normalized/compact capture output may only preserve the variant resolution.
# HLS_VARIANT nullxnull
RE_HLS_VARIANT_SUMMARY = re.compile(
    r"HLS_VARIANT\s+(?P<width>\d+|null)x(?P<height>\d+|null)",
    re.IGNORECASE,
)

# CodecType: dvh1 (HW decoder), DecodedPixelBuffer: &xv0, 3840 x 1600
# CODEC_TYPE qdh1 (HW decoder)
RE_CODEC_TYPE = re.compile(
    r"(?:CodecType:\s*|CODEC_TYPE\s+)(?P<fourcc>\w+)"
    r"(?:\s*\((?P<decoder>[^)]+)\))?"
    r"(?:[^,]*?,\s*(?P<width>\d+)\s*x\s*(?P<height>\d+))?"
)
RE_RESOLUTION = re.compile(r"(?P<width>\d+)\s*x\s*(?P<height>\d+)")

# codecType: HEVC, encryptionScheme N, W x H
# FILE_PLAYER HEVC enc=4 3840x1606
RE_FILE_PLAYER = re.compile(
    r"(?:codecType:\s*|FILE_PLAYER\s+)(?P<codec>\w+)"
    r"(?:,\s*encryptionScheme\s+|\s+enc=)?(?P<enc>\d+)?"
    r"(?:,\s*|\s+)?(?P<width>\d+)?\s*x?\s*(?P<height>\d+)?",
    re.IGNORECASE,
)

# FigFilePlayer / FigStreamPlayer pipeline engine detection.
# These functions appear in the log line that precedes (or contains) an audio report.
# itemfig_ReportAudioPlaybackThroughFigLog → FigFilePlayer (local files)
# fpfs_ReportAudioPlaybackThroughFigLog   → FigStreamPlayer (HLS / Apple TV+)
RE_PIPELINE_ENGINE = re.compile(
    r"(?P<engine>itemfig_ReportAudioPlaybackThroughFigLog"
    r"|fpfs_ReportAudioPlaybackThroughFigLog)",
    re.IGNORECASE,
)
RE_PIPELINE_MARKER = re.compile(
    r"<<<<\s*(?P<engine>Fig(?:File|Stream)Player)\s*>>>>",
    re.IGNORECASE,
)

# [item requires immersive rendering yes|no]
RE_IMMERSIVE_RENDERING = re.compile(
    r"\[item requires immersive rendering\s+(?P<value>yes|no)\]",
    re.IGNORECASE,
)

# AVCFPlayerItemSetAllowedAudioSpatializationFormats for playerItem ...: 0xN
RE_SPAT_FORMATS_MASK = re.compile(
    r"AVCFPlayerItemSetAllowedAudioSpatializationFormats"
    r"(?:[^:]*?):\s*(?P<mask>0x[0-9a-fA-F]+|\d+)",
    re.IGNORECASE,
)

# [AudioFormat ec+3] [AudioChannels 16] [SampleRate 48000] [Spatialization Eligible yes] [Spatialization yes]
# AUDIO_FORMAT qc+3 is decodable ch=16
RE_AUDIO_FORMAT = re.compile(
    r"(?:"
    r"\[AudioFormat\s+(?P<bracket_format>[^\]]+)\]"
    r"(?:\s*\[AudioChannels\s+(?P<bracket_channels>\d+)\])?"
    r"(?:\s*\[SampleRate\s+(?P<bracket_rate>\d+)\])?"
    r"(?:\s*\[Spatialization\s+Eligible\s+(?P<bracket_spat_elig>\w+)\])?"
    r"(?:\s*\[Spatialization\s+(?P<bracket_spat>\w+)\])?"
    r"|"
    r"AUDIO_FORMAT\s+(?P<summary_format>\S+)"
    r"(?:\s+is\s+(?P<summary_decodable>decodable|not decodable))?"
    r"(?:\s+ch=(?P<summary_channels>\d+))?"
    r")",
    re.IGNORECASE,
)

# luma depth 10 chroma format 1
# LUMA_CHROMA luma=10 chroma=1
RE_LUMA = re.compile(
    r"(?:luma depth\s+|LUMA_CHROMA\s+luma=)(?P<luma>\d+)"
    r".*?(?:chroma format\s+|chroma=)(?P<chroma>\d+)",
    re.IGNORECASE,
)

RE_AUDIO_STATUS = re.compile(
    r"^(?P<format>\S+)"
    r"(?:\s+is\s+(?P<decodable>decodable|not decodable))?"
    r"(?:\s+ch=(?P<channels>\d+))?$",
    re.IGNORECASE,
)
RE_BRACKET_FIELD = re.compile(r"\[(?P<key>[A-Za-z ]+)\s+(?P<value>[^\]]+)\]")

# Renderer / route evidence. These are intentionally narrow: they capture
# high-signal lines seen from TV.app/CoreAudio without turning the full debug
# stream into structured noise.
RE_MEDIA_FORMATINFO = re.compile(
    r"mediaFormatinfo .*?"
    r"asbdFormatID = (?P<format>[^,]+),\s*"
    r"(?P<label>.*?),\s*"
    r"asbdNumChannels = (?P<channels>\d+),\s*"
    r"asbdSampleRate = (?P<sample_rate>[\d.]+)\s*kHz,\s*"
    r"is (?P<not_rendering>not )?rendering spatial audio",
    re.IGNORECASE,
)
RE_SPATIAL_POWER = re.compile(
    r"Logging power event: .*?"
    r"spatialization = (?P<spatialization>[01]),\s*"
    r"stereoUpmix = (?P<stereo_upmix>[01]),\s*"
    r"headTracking = (?P<head_tracking>[01])",
    re.IGNORECASE,
)
RE_HEAD_TRACKING_PREF = re.compile(
    r"prefersHeadTrackedSpatialization = (?P<head_tracking>[01])",
    re.IGNORECASE,
)
RE_ROUTE = re.compile(r"Route = (?P<route>.+)", re.IGNORECASE)
# SpatialMgr per-binding capability lines (one field per log line within the
# binding block). Useful as a per-app verdict on whether the route is
# spatial-capable at all and which source identifier got registered.
RE_MAX_SPAT_CHANNELS = re.compile(
    r"maxSpatializableChannels\s*=\s*(?P<channels>\d+)", re.IGNORECASE
)
RE_SPATIAL_AUDIO_SOURCES = re.compile(
    r"spatialAudioSources\s*=\s*\[\s*(?P<sources>[^\]]*?)\s*\]", re.IGNORECASE
)
RE_SPATIAL_BINDING_APP = re.compile(
    r"^\s*App\s*=\s*(?P<app>[^\s{}][^\n]*?)\s*$", re.IGNORECASE | re.MULTILINE
)
RE_ATMOS_DECODER_STATE = re.compile(
    r"ACDDPAtmosDecoder\.cpp:\d+.*?"
    r"mIsAtmos = (?P<is_atmos>[01]),\s*"
    r"mIsTVOS = (?P<is_tvos>[01]),\s*"
    r"mIsOARMode = (?P<oar_mode>[01])",
    re.IGNORECASE,
)
RE_ATMOS_DECODER_SUBTYPE = re.compile(
    r"ACDDPAtmosDecoder\.cpp:\d+.*?subType = '(?P<subtype>[^']+)'",
    re.IGNORECASE,
)
RE_COREAUDIO_INPUT_FORMAT = re.compile(
    r"(?:Input format:|AudioQueueNewOutput)\s*"
    r"(?P<channels>\d+)\s*ch,\s*"
    r"(?P<sample_rate>\d+)\s*Hz,\s*"
    r"(?P<format>[a-z0-9+.-]+)",
    re.IGNORECASE,
)
RE_MIXER_SPATIAL_STATUS = re.compile(
    r"MEMixerChannel\.cpp:\d+\s+"
    r"mFormatID='(?P<format>[^']+)',\s*"
    r"mNumChannels=(?P<channels>\d+),\s*"
    r"mBestAvailableContentType=(?P<content_type>-?\d+),\s*"
    r"mContentspatializable=(?P<content_spatializable>[01]),\s*"
    r"mSpatializationStatus=(?P<spatialization_status>-?\d+),\s*"
    r"err=(?P<err>-?\d+)",
    re.IGNORECASE,
)
RE_SPATIAL_RENDERING_NOTIFICATION = re.compile(
    r"AVCFPlayerItemSpatialAudioRenderingDidChangeNotification",
    re.IGNORECASE,
)
RE_AUSPATIAL_MIXER = re.compile(r"AUSpatialMixerV2", re.IGNORECASE)
RE_AUSPATIAL_LAYOUT = re.compile(
    r"AUSpatialMixerV2.*?Setting audio channel layout (?P<layout>[A-Za-z0-9_]+)",
    re.IGNORECASE,
)
RE_AUSPATIAL_CHANNEL_PROCESSORS = re.compile(
    r"AUSpatialMixerV2.*?Initializing (?P<channels>\d+) channel processors",
    re.IGNORECASE,
)
RE_AUDIOQUEUE_FORCE_714 = re.compile(
    r"Forcing 7\.1\.4 decoder for Atmos",
    re.IGNORECASE,
)

NOISE_AUDIO_FORMATS = {"table:"}


def _parse_line(line: str) -> dict[str, Any] | None:
    if m := RE_FIG_ALT.search(line):
        peak = RE_FIG_ALT_PEAK.search(line)
        resolution = RE_FIG_ALT_RES.search(line)
        codecs = RE_FIG_ALT_CODECS.search(line)
        audio_group = RE_FIG_ALT_AUDIO_GROUP.search(line)
        video_range = RE_FIG_ALT_RANGE.search(line)
        hdcp = RE_FIG_ALT_HDCP.search(line)
        fps = RE_FIG_ALT_FPS.search(line)
        return {
            "kind": "hls_variant",
            "raw": line.rstrip(),
            "id": _int(m.group("id")),
            "peak_bps": _int(peak.group("peak") if peak else None),
            "avg_bps": _int(peak.group("avg") if peak else None),
            "width": _int(resolution.group("width") if resolution else None),
            "height": _int(resolution.group("height") if resolution else None),
            "codecs": codecs.group("codecs") if codecs else None,
            "audio_group": audio_group.group("audio_group") if audio_group else None,
            "video_range": video_range.group("range") if video_range else None,
            "hdcp": hdcp.group("hdcp") if hdcp else None,
            "fps": _float(fps.group("fps") if fps else None),
            "pipeline_engine": _pipeline_engine_from_line(line),
        }
    if m := RE_HLS_VARIANT_SUMMARY.search(line):
        d = m.groupdict()
        return {
            "kind": "hls_variant",
            "raw": line.rstrip(),
            "id": None,
            "peak_bps": None,
            "avg_bps": None,
            "width": _int(d["width"]),
            "height": _int(d["height"]),
            "codecs": None,
            "video_range": None,
            "hdcp": None,
            "fps": None,
        }
    if m := RE_CODEC_TYPE.search(line):
        d = m.groupdict()
        resolution = RE_RESOLUTION.search(line)
        return {
            "kind": "codec_type",
            "raw": line.rstrip(),
            "fourcc": d["fourcc"],
            "decoder": d["decoder"],
            "width": _int(d["width"] or (resolution.group("width") if resolution else None)),
            "height": _int(d["height"] or (resolution.group("height") if resolution else None)),
        }
    if m := RE_AUDIO_FORMAT.search(line):
        d = m.groupdict()
        fmt = d["bracket_format"] or d["summary_format"]
        channels = d["bracket_channels"] or d["summary_channels"]
        decodable = d["summary_decodable"] == "decodable" if d["summary_decodable"] else None
        fmt, channels, decodable = _normalize_audio_fields(fmt, channels, decodable)
        bracket_fields = _audio_bracket_fields(line)
        channels = channels or bracket_fields.get("audiochannels")
        if not fmt:
            return None
        # Detect pipeline engine from the reporting function name on the same line
        pipeline_engine = _pipeline_engine_from_line(line)
        # Detect immersive rendering flag from the same log block
        immersive: str | None = None
        if im := RE_IMMERSIVE_RENDERING.search(line):
            immersive = im.group("value").lower()
        event: dict[str, Any] = {
            "kind": "audio_format",
            "raw": line.rstrip(),
            "format": fmt,
            "channels": _int(channels),
            "sample_rate": _int(d["bracket_rate"] or bracket_fields.get("samplerate")),
            "spatialization_eligible": d["bracket_spat_elig"] or bracket_fields.get("spatializationeligible"),
            "spatialization": d["bracket_spat"] or bracket_fields.get("spatialization"),
            "decodable": decodable,
        }
        if pipeline_engine is not None:
            event["pipeline_engine"] = pipeline_engine
        if immersive is not None:
            event["immersive_rendering_requested"] = immersive == "yes"
        return event
    if m := RE_COREAUDIO_INPUT_FORMAT.search(line):
        return {
            "kind": "audio_format",
            "raw": line.rstrip(),
            "format": m.group("format"),
            "channels": _int(m.group("channels")),
            "sample_rate": _int(m.group("sample_rate")),
            "spatialization_eligible": None,
            "spatialization": None,
            "decodable": None,
            "source": "coreaudio_input_format",
        }
    if renderer_event := _parse_renderer_line(line):
        return renderer_event
    if engine := _pipeline_engine_from_line(line):
        return {
            "kind": "pipeline_engine",
            "raw": line.rstrip(),
            "pipeline_engine": engine,
        }
    if m := RE_FILE_PLAYER.search(line):
        d = m.groupdict()
        if d["codec"]:
            return {
                "kind": "file_player",
                "raw": line.rstrip(),
                "codec": d["codec"],
                "encryption_scheme": _int(d["enc"]),
                "width": _int(d["width"]),
                "height": _int(d["height"]),
            }
    if m := RE_LUMA.search(line):
        d = m.groupdict()
        return {
            "kind": "luma_chroma",
            "raw": line.rstrip(),
            "luma_depth": _int(d["luma"]),
            "chroma_format": _int(d["chroma"]),
        }
    return None


def _parse_renderer_line(line: str) -> dict[str, Any] | None:
    if m := RE_MEDIA_FORMATINFO.search(line):
        d = m.groupdict()
        return {
            "kind": "renderer_hint",
            "hint": "media_formatinfo",
            "raw": line.rstrip(),
            "format": d["format"].strip(),
            "label": d["label"].strip(),
            "channels": _int(d["channels"]),
            "sample_rate": _float(d["sample_rate"]) * 1000 if _float(d["sample_rate"]) else None,
            "rendering_spatial_audio": d["not_rendering"] is None,
        }
    if m := RE_SPATIAL_POWER.search(line):
        d = m.groupdict()
        return {
            "kind": "renderer_hint",
            "hint": "spatial_power",
            "raw": line.rstrip(),
            "spatialization": _bool01(d["spatialization"]),
            "stereo_upmix": _bool01(d["stereo_upmix"]),
            "head_tracking": _bool01(d["head_tracking"]),
        }
    if m := RE_HEAD_TRACKING_PREF.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "head_tracking_preference",
            "raw": line.rstrip(),
            "prefers_head_tracked_spatialization": _bool01(m.group("head_tracking")),
        }
    if m := RE_ROUTE.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "route",
            "raw": line.rstrip(),
            "route": m.group("route").strip(),
        }
    if m := RE_MAX_SPAT_CHANNELS.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "max_spatializable_channels",
            "raw": line.rstrip(),
            "max_spatializable_channels": _int(m.group("channels")),
        }
    if m := RE_SPATIAL_AUDIO_SOURCES.search(line):
        raw = m.group("sources").strip()
        # Sources look like "'mlti'" or "'?src'" or "'mlti', 'atms'". Strip
        # quotes and split on commas. '?src' is the sentinel for an
        # unrecognized source (e.g. wired headset route).
        sources = [s.strip().strip("'\"") for s in raw.split(",") if s.strip()]
        return {
            "kind": "renderer_hint",
            "hint": "spatial_audio_sources",
            "raw": line.rstrip(),
            "spatial_audio_sources": sources,
            "has_unknown_source": "?src" in sources,
        }
    if m := RE_SPATIAL_BINDING_APP.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "spatial_binding_app",
            "raw": line.rstrip(),
            "spatial_binding_app": m.group("app").strip(),
        }
    if m := RE_ATMOS_DECODER_STATE.search(line):
        d = m.groupdict()
        return {
            "kind": "renderer_hint",
            "hint": "atmos_decoder_state",
            "raw": line.rstrip(),
            "decoder_is_atmos": _bool01(d["is_atmos"]),
            "decoder_is_tvos": _bool01(d["is_tvos"]),
            "decoder_oar_mode": _bool01(d["oar_mode"]),
        }
    if m := RE_ATMOS_DECODER_SUBTYPE.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "atmos_decoder_subtype",
            "raw": line.rstrip(),
            "decoder_subtype": m.group("subtype").strip(),
        }
    if RE_AUDIOQUEUE_FORCE_714.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "audioqueue_forced_atmos_714",
            "raw": line.rstrip(),
            "forced_atmos_714": True,
        }
    if m := RE_AUSPATIAL_LAYOUT.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "auspatial_channel_layout",
            "raw": line.rstrip(),
            "channel_layout": m.group("layout"),
        }
    if m := RE_AUSPATIAL_CHANNEL_PROCESSORS.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "auspatial_channel_processors",
            "raw": line.rstrip(),
            "channel_processors": _int(m.group("channels")),
        }
    if RE_AUSPATIAL_MIXER.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "auspatial_mixer",
            "raw": line.rstrip(),
        }
    if m := RE_MIXER_SPATIAL_STATUS.search(line):
        d = m.groupdict()
        return {
            "kind": "renderer_hint",
            "hint": "mixer_spatial_status",
            "raw": line.rstrip(),
            "format": d["format"].strip(),
            "channels": _int(d["channels"]),
            "best_available_content_type": _int(d["content_type"]),
            "content_spatializable": _bool01(d["content_spatializable"]),
            "spatialization_status": _int(d["spatialization_status"]),
            "err": _int(d["err"]),
        }
    if RE_SPATIAL_RENDERING_NOTIFICATION.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "spatial_rendering_changed",
            "raw": line.rstrip(),
        }
    if m := RE_SPAT_FORMATS_MASK.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "allowed_spatialization_formats_mask",
            "raw": line.rstrip(),
            "mask": m.group("mask"),
        }
    if m := RE_IMMERSIVE_RENDERING.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "immersive_rendering_requested",
            "raw": line.rstrip(),
            "immersive_rendering_requested": m.group("value").lower() == "yes",
        }
    return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _bool01(v: Any) -> bool | None:
    if v == "1" or v == 1:
        return True
    if v == "0" or v == 0:
        return False
    return None


def _pipeline_engine_from_line(line: str) -> str | None:
    if pm := RE_PIPELINE_ENGINE.search(line):
        fn = pm.group("engine").lower()
        return "FigFilePlayer" if fn.startswith("itemfig") else "FigStreamPlayer"
    if pm := RE_PIPELINE_MARKER.search(line):
        engine = pm.group("engine")
        return "FigFilePlayer" if engine.lower() == "figfileplayer" else "FigStreamPlayer"
    return None


def _audio_bracket_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in RE_BRACKET_FIELD.finditer(line):
        key = match.group("key").replace(" ", "").lower()
        fields[key] = match.group("value").strip()
    return fields


def _normalize_audio_fields(
    audio_format: Any,
    channels: Any,
    decodable: bool | None,
) -> tuple[str | None, Any, bool | None]:
    fmt = str(audio_format).strip() if audio_format is not None else ""
    if not fmt or fmt.lower() in NOISE_AUDIO_FORMATS:
        return None, channels, decodable

    if m := RE_AUDIO_STATUS.match(fmt):
        d = m.groupdict()
        fmt = d["format"]
        if channels is None:
            channels = d["channels"]
        if decodable is None and d["decodable"]:
            decodable = d["decodable"] == "decodable"

    if fmt.lower() in NOISE_AUDIO_FORMATS:
        return None, channels, decodable
    return fmt, channels, decodable


def _audio_summary(event: dict[str, Any]) -> dict[str, Any]:
    audio_format = (event.get("format") or "").lower()
    is_known_atmos = audio_format in {"ec+3", "ec-3", "ec3", "qc+3"} and event.get("spatialization") == "yes"
    summary = {
        "format": event.get("format"),
        "channels": event.get("channels"),
        "sample_rate": event.get("sample_rate"),
        "spatialization": event.get("spatialization"),
        "spatialization_eligible": event.get("spatialization_eligible"),
        "decodable": event.get("decodable"),
        "is_atmos": is_known_atmos,
    }
    if audio_format == "qc+3":
        summary["diagnosis"] = (
            "Audio format 'qc+3' was reported as decodable by TV.app. "
            "Preserving it as an Apple/CoreMedia Dolby-like runtime path, not confirmed Atmos."
        )
    return summary


def _audio_key(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        event.get("format"),
        event.get("channels"),
        event.get("sample_rate"),
        event.get("spatialization"),
        event.get("spatialization_eligible"),
        event.get("decodable"),
    )


def _audio_rank(event: dict[str, Any]) -> tuple[int, int, int]:
    audio_format = (event.get("format") or "").lower()
    channels = event.get("channels") or 0
    is_dolby = audio_format in {"ec+3", "ec-3", "ec3", "qc+3"}
    if is_dolby and channels >= 16:
        tier = 5
    elif is_dolby and channels > 2:
        tier = 4
    elif audio_format in {"ac-3", "ac3"} and channels > 2:
        tier = 3
    elif audio_format in {"qaac", "aac", "aacp"} and channels <= 2:
        tier = 1
    else:
        tier = 2 if channels > 2 else 0
    decodable = 1 if event.get("decodable") is True else 0
    return (tier, channels, decodable)


# ---------------------------------------------------------------------------


SUMMARY_RENDERER_EVENT_LIMIT = 1000


class LogCapture:
    """Spawns `log stream` and accumulates parsed events until stopped."""

    def __init__(self, predicate: str | None = None, raw_event_limit: int = 5000) -> None:
        self.raw_event_limit = max(0, raw_event_limit)
        self.events: deque[dict[str, Any]] | list[dict[str, Any]] = deque(maxlen=self.raw_event_limit)
        self.event_count = 0
        self._hls_seen = False
        self._last_hls_variant: dict[str, Any] | None = None
        self._codec_seen = False
        self._last_codec: dict[str, Any] | None = None
        self._audio_seen = False
        self._last_audio: dict[str, Any] | None = None
        self._best_audio: dict[str, Any] | None = None
        self._observed_audio: list[dict[str, Any]] = []
        self._observed_audio_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._file_seen = False
        self._last_file: dict[str, Any] | None = None
        self._renderer_events: list[dict[str, Any]] = []
        self._renderer_event_keys: set[tuple[tuple[str, str], ...]] = set()
        self._spatial_rendering_changed_count = 0
        self._pipeline_engines: list[str] = []
        self.predicate = predicate or PREDICATE
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._started_at: float | None = None
        self._listeners: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        if self._proc is not None:
            return
        self._loop = loop
        self._started_at = time.time()
        self._proc = subprocess.Popen(
            [
                "log",
                "stream",
                "--info",
                "--debug",
                "--style",
                "compact",
                "--predicate",
                self.predicate,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=2)
        self._proc = None
        self._reader = None
        return self.summarize()

    # -- subscription ------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._listeners:
            self._listeners.remove(q)

    # -- reader thread -----------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            if self._stop.is_set():
                break
            event = _parse_line(line)
            if not event:
                continue
            event["ts"] = time.time()
            self.add_event(event)
            self._broadcast(event)

    def add_event(self, event: dict[str, Any]) -> None:
        self.event_count += 1
        self._record_summary_event(event)
        if self.raw_event_limit <= 0:
            return
        self.events.append(event)
        if isinstance(self.events, list) and len(self.events) > self.raw_event_limit:
            del self.events[: len(self.events) - self.raw_event_limit]

    def _record_summary_event(self, event: dict[str, Any]) -> None:
        pipeline_engine = event.get("pipeline_engine")
        if pipeline_engine and pipeline_engine not in self._pipeline_engines:
            self._pipeline_engines.append(pipeline_engine)

        kind = event.get("kind")
        if kind == "hls_variant":
            self._hls_seen = True
            self._last_hls_variant = event
        elif kind == "codec_type":
            self._codec_seen = True
            self._last_codec = event
        elif kind == "audio_format":
            self._audio_seen = True
            self._last_audio = event
            if self._best_audio is None or _audio_rank(event) > _audio_rank(self._best_audio):
                self._best_audio = event
            key = _audio_key(event)
            if key not in self._observed_audio_by_key:
                item = _audio_summary(event)
                item["count"] = 0
                self._observed_audio_by_key[key] = item
                self._observed_audio.append(item)
            self._observed_audio_by_key[key]["count"] += 1
        elif kind == "file_player":
            self._file_seen = True
            self._last_file = event
        elif kind == "renderer_hint":
            if event.get("hint") == "spatial_rendering_changed":
                self._spatial_rendering_changed_count += 1
            key = tuple(
                sorted((k, repr(v)) for k, v in event.items() if k not in {"raw", "ts"})
            )
            if key not in self._renderer_event_keys:
                self._renderer_event_keys.add(key)
                if len(self._renderer_events) < SUMMARY_RENDERER_EVENT_LIMIT:
                    self._renderer_events.append(event)

    def _broadcast(self, event: dict[str, Any]) -> None:
        loop = self._loop
        for q in list(self._listeners):
            if loop and loop.is_running():
                try:
                    loop.call_soon_threadsafe(q.put_nowait, event)
                except (RuntimeError, asyncio.QueueFull):
                    pass

    # -- summary -----------------------------------------------------------

    def summarize(self) -> dict[str, Any]:
        retained_events = list(self.events)
        total_events = self.event_count or len(retained_events)
        events_returned = len(retained_events)
        events_dropped = max(0, total_events - events_returned)
        if not retained_events and not self.event_count:
            return {
                "started_at": self._started_at,
                "duration_s": time.time() - self._started_at if self._started_at else 0,
                "event_count": total_events,
                "events_returned": events_returned,
                "events_dropped": events_dropped,
                "raw_event_limit": self.raw_event_limit,
                "playback": None,
                "events": [],
                "predicate": self.predicate,
            }

        if self.event_count:
            hls_variants = [self._last_hls_variant] if self._last_hls_variant else []
            last_variant = self._last_hls_variant
            codec_events = [self._last_codec] if self._last_codec else []
            last_codec = self._last_codec
            audio_events = [self._last_audio] if self._last_audio else []
            last_audio = self._last_audio
            best_audio = self._best_audio
            observed_audio = self._observed_audio
            file_events = [self._last_file] if self._last_file else []
            last_file = self._last_file
            renderer_events = self._renderer_events
            pipeline_engines = self._pipeline_engines
        else:
            # Some tests build captures by assigning events directly. Preserve that
            # compatibility path while live captures use compact aggregate state.
            hls_variants = [e for e in retained_events if e["kind"] == "hls_variant"]
            last_variant = hls_variants[-1] if hls_variants else None
            codec_events = [e for e in retained_events if e["kind"] == "codec_type"]
            last_codec = codec_events[-1] if codec_events else None
            audio_events = [e for e in retained_events if e["kind"] == "audio_format"]
            last_audio = audio_events[-1] if audio_events else None
            best_audio = max(audio_events, key=_audio_rank) if audio_events else None
            observed_audio = []
            observed_audio_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
            for audio_event in audio_events:
                key = _audio_key(audio_event)
                if key not in observed_audio_by_key:
                    item = _audio_summary(audio_event)
                    item["count"] = 0
                    observed_audio_by_key[key] = item
                    observed_audio.append(item)
                observed_audio_by_key[key]["count"] += 1
            file_events = [e for e in retained_events if e["kind"] == "file_player"]
            last_file = file_events[-1] if file_events else None
            renderer_events = [e for e in retained_events if e["kind"] == "renderer_hint"]
            pipeline_events = [e for e in retained_events if e["kind"] == "pipeline_engine"]
            pipeline_engines = _unique_keep_order(
                [e["pipeline_engine"] for e in audio_events if e.get("pipeline_engine")]
                + [e["pipeline_engine"] for e in hls_variants if e.get("pipeline_engine")]
                + [e["pipeline_engine"] for e in pipeline_events if e.get("pipeline_engine")]
            )

        playback: dict[str, Any] = {
            "source": "hls" if hls_variants else ("local_file" if file_events else "unknown"),
        }
        if last_variant:
            playback["hls_variant"] = last_variant
            # parse codecs string e.g. "dvh1.05.06,ec-3"
            codecs = (last_variant.get("codecs") or "").split(",")
            playback["video_fourcc"] = codecs[0].split(".")[0] if codecs and codecs[0] else None
            playback["audio_codec"] = codecs[1] if len(codecs) > 1 else None
            playback["selected_hls_audio_group"] = last_variant.get("audio_group")
            playback["selected_hls_audio_group_kind"] = _hls_audio_group_kind(
                last_variant.get("audio_group"), playback.get("audio_codec")
            )
            playback["hls_delivery"] = _hls_delivery_label(last_variant.get("audio_group"))
            playback["peak_mbps"] = (
                round(last_variant["peak_bps"] / 1_000_000, 2)
                if last_variant.get("peak_bps")
                else None
            )
            playback["avg_mbps"] = (
                round(last_variant["avg_bps"] / 1_000_000, 2)
                if last_variant.get("avg_bps")
                else None
            )
        if last_codec:
            playback["decoded_fourcc"] = last_codec.get("fourcc")
            playback["decoder"] = last_codec.get("decoder")
            if last_codec.get("width"):
                playback["decoded_resolution"] = (
                    f"{last_codec['width']}x{last_codec['height']}"
                )
        if last_audio:
            playback["audio"] = _audio_summary(last_audio)
        if best_audio:
            playback["best_audio"] = _audio_summary(best_audio)
        if observed_audio:
            playback["observed_audio"] = observed_audio
        if last_variant:
            verdict = _movpkg_hls_verdict(
                selected_group=last_variant.get("audio_group"),
                audio_codec=playback.get("audio_codec"),
                observed_audio=observed_audio,
                renderer_events=renderer_events,
            )
            if verdict:
                playback["downloaded_hls_verdict"] = verdict
        if last_file:
            playback["file_player"] = last_file
        # pipeline_engine from audio events (most reliable source when it appears in the same log block)
        if pipeline_engines:
            playback["pipeline_engine"] = _choose_pipeline_engine(playback["source"], pipeline_engines)
            playback["pipeline_engines_observed"] = pipeline_engines

        if renderer_events:
            playback["renderer_evidence"] = _renderer_summary(
                renderer_events, started_at=self._started_at
            )
            if self.event_count and self._spatial_rendering_changed_count:
                playback["renderer_evidence"]["spatial_rendering_changed_count"] = (
                    self._spatial_rendering_changed_count
                )
        playback["capture_quality"] = _capture_quality(
            codec_events=codec_events,
            audio_events=audio_events,
            renderer_events=renderer_events,
        )

        # DV verdict
        decoded = (playback.get("decoded_fourcc") or "").lower()
        if decoded in {"dvh1", "dvhe"}:
            playback["dolby_vision_active"] = True
        elif decoded == "hvc1" and playback.get("source") == "local_file":
            playback["dolby_vision_active"] = False
            playback["dv_diagnosis"] = (
                "Local file decoded as 'hvc1' — TV.app did NOT engage DV pipeline. "
                "Re-tag sample-entry to 'dvh1' to fix."
            )
        elif decoded == "qdh1":
            playback["dolby_vision_active"] = None
            if playback.get("source") == "hls":
                playback["dv_label"] = "Apple HLS private HDR/DV path (qdh1)"
                playback["dv_diagnosis"] = (
                    "Decoded as 'qdh1' on the HLS path. This is Apple's private CoreMedia "
                    "HDR/Dolby Vision identifier for streaming content — it is the expected "
                    "fourcc for Apple TV+ HLS with DV video, not a fallback or degraded path."
                )
            else:
                playback["dv_label"] = "Apple private HDR path (qdh1) — source unknown"
                playback["dv_diagnosis"] = (
                    "Decoded as 'qdh1' via hardware decoder. Not the hvc1 SDR fallback, "
                    "but also not the standard dvh1/dvhe open-DV marker. Likely Apple's "
                    "private CoreMedia HDR/DV path. Correlate with display output to confirm."
                )
        else:
            playback["dolby_vision_active"] = None

        return {
            "started_at": self._started_at,
            "duration_s": time.time() - self._started_at if self._started_at else 0,
            "event_count": total_events,
            "events_returned": events_returned,
            "events_dropped": events_dropped,
            "raw_event_limit": self.raw_event_limit,
            "playback": playback,
            "events": retained_events,
            "predicate": self.predicate,
        }


def summarize_log_lines(
    lines: list[str],
    predicate: str | None = None,
    raw_event_limit: int = 5000,
) -> dict[str, Any]:
    """Parse saved `log stream --style compact` lines into a capture summary."""
    capture = LogCapture(predicate=predicate, raw_event_limit=raw_event_limit)
    for line in lines:
        event = _parse_line(line)
        if event:
            capture.add_event(event)
    return capture.summarize()


def summarize_log_file(path: str | Path, predicate: str | None = None) -> dict[str, Any]:
    """Parse a saved raw log file into the same shape as a live capture."""
    text = Path(path).read_text(errors="replace")
    return summarize_log_lines(text.splitlines(), predicate=predicate)


def tvlog_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a compact Markdown summary for CLI use."""
    playback = summary.get("playback") or {}
    renderer = playback.get("renderer_evidence") or {}
    lines = ["# TV/log playback summary\n"]
    lines.append(f"- events parsed: `{summary.get('event_count', 0)}`")
    lines.append(f"- source: `{playback.get('source')}`")
    if playback.get("pipeline_engine"):
        lines.append(f"- pipeline engine: `{playback.get('pipeline_engine')}`")
    if playback.get("selected_hls_audio_group"):
        lines.append(f"- selected HLS AudioGroup: `{playback.get('selected_hls_audio_group')}`")
    if playback.get("selected_hls_audio_group_kind"):
        lines.append(f"- HLS AudioGroup kind: `{playback.get('selected_hls_audio_group_kind')}`")
    if playback.get("audio_codec"):
        lines.append(f"- HLS audio codec: `{playback.get('audio_codec')}`")
    if playback.get("hls_delivery"):
        lines.append(f"- HLS delivery: `{playback.get('hls_delivery')}`")
    if playback.get("downloaded_hls_verdict"):
        lines.append(f"- downloaded HLS verdict: `{playback.get('downloaded_hls_verdict')}`")
    if playback.get("audio"):
        lines.append(f"- current audio: `{_format_audio_summary(playback['audio'])}`")
    if playback.get("best_audio"):
        lines.append(f"- best observed audio: `{_format_audio_summary(playback['best_audio'])}`")
    if renderer:
        lines.append(f"- renderer verdict: `{renderer.get('verdict')}`")
        if renderer.get("app_spatial_rendering_ever_true") is not None:
            lines.append(
                "- TV.app app spatial ever true: "
                f"`{renderer.get('app_spatial_rendering_ever_true')}`"
            )
        if renderer.get("lower_level_spatialization_active") is not None:
            lines.append(
                "- lower-level spatialization active: "
                f"`{renderer.get('lower_level_spatialization_active')}`"
            )
        if renderer.get("atmos_decoder_active") is not None:
            lines.append(f"- Atmos decoder active: `{renderer.get('atmos_decoder_active')}`")
        if renderer.get("oar_mode_active") is not None:
            lines.append(f"- OAR mode active: `{renderer.get('oar_mode_active')}`")
        if renderer.get("audioqueue_forced_atmos_714") is not None:
            lines.append(
                "- AudioQueue forced 7.1.4 Atmos: "
                f"`{renderer.get('audioqueue_forced_atmos_714')}`"
            )
        if renderer.get("auspatial_channel_layouts"):
            layouts = ", ".join(f"`{layout}`" for layout in renderer["auspatial_channel_layouts"])
            lines.append(f"- AUSpatialMixer layouts: {layouts}")
    return "\n".join(lines) + "\n"


def _format_audio_summary(audio: dict[str, Any]) -> str:
    parts = [str(audio.get("format"))]
    if audio.get("channels") is not None:
        parts.append(f"ch={audio.get('channels')}")
    if audio.get("sample_rate") is not None:
        parts.append(f"{audio.get('sample_rate')} Hz")
    if audio.get("spatialization") is not None:
        parts.append(f"spatialization={audio.get('spatialization')}")
    return " ".join(parts)


def _choose_pipeline_engine(source: str, engines: list[str]) -> str:
    """Choose the engine most likely attached to the summarized playback source."""
    if source == "local_file" and "FigFilePlayer" in engines:
        return "FigFilePlayer"
    if source == "hls" and "FigStreamPlayer" in engines:
        return "FigStreamPlayer"
    return engines[-1]


def _hls_audio_group_kind(audio_group: str | None, audio_codec: str | None) -> str | None:
    group = (audio_group or "").lower()
    codec = (audio_codec or "").lower()
    if "atmos" in group or codec in {"ec-3", "ec3"}:
        return "atmos"
    if "audio-stereo" in group or codec.startswith("mp4a"):
        return "stereo"
    if "descriptive" in group or "description" in group or group.endswith("_ad"):
        return "audio_description"
    return None


def _hls_delivery_label(audio_group: str | None) -> str | None:
    group = (audio_group or "").lower()
    if "download-ap-aoc" in group:
        return "downloaded_movpkg"
    if "vod-ap-aoc" in group:
        return "online_hls"
    return None


def _movpkg_hls_verdict(
    *,
    selected_group: str | None,
    audio_codec: str | None,
    observed_audio: list[dict[str, Any]],
    renderer_events: list[dict[str, Any]],
) -> str | None:
    group_kind = _hls_audio_group_kind(selected_group, audio_codec)
    delivery = _hls_delivery_label(selected_group)
    if delivery != "downloaded_movpkg":
        return None

    atmos_seen = any(
        (audio.get("format") or "").lower() in {"ec+3", "ec-3", "ec3", "qc+3"}
        and (audio.get("channels") or 0) >= 16
        for audio in observed_audio
    )
    atmos_seen = atmos_seen or any(
        e.get("hint") == "mixer_spatial_status"
        and (e.get("format") or "").lower() in {"ec+3", "ec-3", "ec3", "qc+3"}
        and (e.get("channels") or 0) >= 16
        and e.get("content_spatializable") is True
        for e in renderer_events
    )

    if group_kind == "stereo" and atmos_seen:
        return "movpkg_atmos_variant_present_but_not_selected"
    if group_kind == "stereo":
        return "movpkg_figstreamplayer_selected_stereo"
    if group_kind == "atmos":
        return "movpkg_atmos_variant_selected"
    return None


def _renderer_summary(
    events: list[dict[str, Any]],
    started_at: float | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"observed_hints": sorted({e["hint"] for e in events})}

    routes = [e.get("route") for e in events if e["hint"] == "route" and e.get("route")]
    if routes:
        summary["routes"] = _unique_keep_order(routes)
        summary["route_capability_label"] = _route_capability_label(summary["routes"][-1])

    media_events = [e for e in events if e["hint"] == "media_formatinfo"]
    if media_events:
        app_states = [e.get("rendering_spatial_audio") for e in media_events]
        app_states = [state for state in app_states if state is not None]
        summary["app_spatial_rendering_ever_true"] = any(app_states)
        summary["app_spatial_rendering_last_state"] = app_states[-1] if app_states else None
        # first_true_at_seconds: offset from capture start when rendering_spatial_audio first became true
        first_true_at: float | None = None
        if started_at is not None:
            for e in media_events:
                if e.get("rendering_spatial_audio") is True and e.get("ts") is not None:
                    first_true_at = round(e["ts"] - started_at, 2)
                    break
        if first_true_at is not None:
            summary["first_true_at_seconds"] = first_true_at
        summary["media_formatinfo"] = [
            {
                "format": e.get("format"),
                "label": e.get("label"),
                "channels": e.get("channels"),
                "sample_rate": e.get("sample_rate"),
                "rendering_spatial_audio": e.get("rendering_spatial_audio"),
            }
            for e in _unique_events(media_events, ("format", "channels", "rendering_spatial_audio"))
        ]

    power_events = [e for e in events if e["hint"] == "spatial_power"]
    if power_events:
        summary["spatial_power_active"] = any(e.get("spatialization") is True for e in power_events)
        summary["head_tracking_active"] = any(e.get("head_tracking") is True for e in power_events)
        summary["spatial_power"] = [
            {
                "spatialization": e.get("spatialization"),
                "stereo_upmix": e.get("stereo_upmix"),
                "head_tracking": e.get("head_tracking"),
            }
            for e in _unique_events(power_events, ("spatialization", "stereo_upmix", "head_tracking"))
        ]

    head_tracking = [
        e.get("prefers_head_tracked_spatialization")
        for e in events
        if e["hint"] == "head_tracking_preference"
    ]
    if head_tracking:
        summary["prefers_head_tracked_spatialization"] = _unique_keep_order(head_tracking)

    decoder_states = [e for e in events if e["hint"] == "atmos_decoder_state"]
    if decoder_states:
        summary["atmos_decoder_active"] = any(e.get("decoder_is_atmos") is True for e in decoder_states)
        summary["oar_mode_active"] = any(e.get("decoder_oar_mode") is True for e in decoder_states)
        summary["atmos_decoder_states"] = [
            {
                "decoder_is_atmos": e.get("decoder_is_atmos"),
                "decoder_oar_mode": e.get("decoder_oar_mode"),
            }
            for e in _unique_events(decoder_states, ("decoder_is_atmos", "decoder_oar_mode"))
        ]

    decoder_subtypes = [
        e.get("decoder_subtype")
        for e in events
        if e["hint"] == "atmos_decoder_subtype" and e.get("decoder_subtype")
    ]
    if decoder_subtypes:
        summary["decoder_subtypes"] = _unique_keep_order(decoder_subtypes)

    mixer_events = [e for e in events if e["hint"] == "mixer_spatial_status"]
    if mixer_events:
        statuses = [
            e.get("spatialization_status")
            for e in mixer_events
            if e.get("spatialization_status") is not None
        ]
        summary["mixer_content_spatializable"] = any(
            e.get("content_spatializable") is True for e in mixer_events
        )
        summary["mixer_spatialization_statuses"] = _unique_keep_order(statuses)
        summary["mixer_spatial_status"] = [
            {
                "format": e.get("format"),
                "channels": e.get("channels"),
                "content_spatializable": e.get("content_spatializable"),
                "spatialization_status": e.get("spatialization_status"),
            }
            for e in _unique_events(
                mixer_events,
                ("format", "channels", "content_spatializable", "spatialization_status"),
            )
        ]

    if any(e["hint"].startswith("auspatial_") for e in events):
        summary["auspatial_mixer_active"] = True
    layouts = [
        e.get("channel_layout")
        for e in events
        if e["hint"] == "auspatial_channel_layout" and e.get("channel_layout")
    ]
    if layouts:
        summary["auspatial_channel_layouts"] = _unique_keep_order(layouts)
        if any("Atmos" in layout for layout in layouts):
            summary["auspatial_atmos_layout_active"] = True
    processors = [
        e.get("channel_processors")
        for e in events
        if e["hint"] == "auspatial_channel_processors" and e.get("channel_processors")
    ]
    if processors:
        summary["auspatial_channel_processors"] = _unique_keep_order(processors)
    if any(e["hint"] == "audioqueue_forced_atmos_714" for e in events):
        summary["audioqueue_forced_atmos_714"] = True

    # Allowed spatialization formats mask (AVCFPlayerItemSetAllowedAudioSpatializationFormats)
    mask_events = [e for e in events if e["hint"] == "allowed_spatialization_formats_mask"]
    if mask_events:
        masks = _unique_keep_order([e.get("mask") for e in mask_events if e.get("mask")])
        summary["allowed_spatialization_formats_masks"] = masks

    # SpatialMgr per-binding route capability. These three fields come from
    # the "Spatial info for binding" block emitted by SpatializationManager.
    # Together they answer: is the current route spatial-capable, and which
    # source identifier did AudioToolbox register for the app/binding?
    # On a non-spatial-capable route (e.g. wired headset) we see
    # max_spatializable_channels=0 and '?src' as the source — explains a
    # spatial=false verdict even when the app sets the allow mask correctly.
    max_ch_events = [e for e in events if e["hint"] == "max_spatializable_channels"]
    if max_ch_events:
        vals = [
            e.get("max_spatializable_channels")
            for e in max_ch_events
            if e.get("max_spatializable_channels") is not None
        ]
        if vals:
            summary["max_spatializable_channels"] = vals[-1]
            summary["route_spatial_capable"] = vals[-1] > 0
    sources_events = [e for e in events if e["hint"] == "spatial_audio_sources"]
    if sources_events:
        all_sources: list[str] = []
        for e in sources_events:
            for s in e.get("spatial_audio_sources") or []:
                if s and s not in all_sources:
                    all_sources.append(s)
        summary["spatial_audio_sources"] = all_sources
        summary["spatial_source_unknown"] = "?src" in all_sources
    app_events = [e for e in events if e["hint"] == "spatial_binding_app"]
    if app_events:
        apps = _unique_keep_order(
            [e.get("spatial_binding_app") for e in app_events if e.get("spatial_binding_app")]
        )
        summary["spatial_binding_apps"] = apps

    # Immersive rendering requested ([item requires immersive rendering yes|no])
    immersive_events = [e for e in events if e["hint"] == "immersive_rendering_requested"]
    if immersive_events:
        summary["immersive_rendering_requested"] = any(
            e.get("immersive_rendering_requested") is True for e in immersive_events
        )

    summary["spatial_rendering_changed_count"] = sum(
        1 for e in events if e["hint"] == "spatial_rendering_changed"
    )
    summary["lower_level_spatialization_active"] = any(
        summary.get(key) is True
        for key in (
            "spatial_power_active",
            "atmos_decoder_active",
            "oar_mode_active",
            "mixer_content_spatializable",
            "auspatial_mixer_active",
            "auspatial_atmos_layout_active",
            "audioqueue_forced_atmos_714",
        )
    )
    if summary.get("app_spatial_rendering_last_state") is True:
        summary["verdict"] = "app_spatial_rendering_active"
    elif (
        summary.get("app_spatial_rendering_ever_true") is True
        and summary.get("app_spatial_rendering_last_state") is False
    ):
        summary["verdict"] = "app_spatial_rendering_was_active"
    elif (
        summary.get("app_spatial_rendering_last_state") is False
        and summary["lower_level_spatialization_active"]
    ):
        summary["verdict"] = "lower_level_active_app_spatial_false"
        summary["verdict_note"] = (
            "Atmos decode and spatial mixer are active (lower-level CoreAudio machinery "
            "is running), but TV.app's app-level spatial-rendering flag is false. "
            "This is TV.app-specific behavior: it narrows allowedAudioSpatializationFormats "
            "to 0x5 (stripping Multichannel) for local items, via "
            "mpc_updateAVAudioSpatializationFormatsForPlayerAudioFormat:. Diagnostic "
            "AVFoundation controls and QuickTime render spatial=true on the same content/route. "
            "Not a codec downgrade — the Atmos pipeline is still engaged."
        )
    elif summary["lower_level_spatialization_active"]:
        summary["verdict"] = "lower_level_spatialization_active"
    else:
        summary["verdict"] = "no_spatial_renderer_activity"
    return summary


def _capture_quality(
    *,
    codec_events: list[dict[str, Any]],
    audio_events: list[dict[str, Any]],
    renderer_events: list[dict[str, Any]],
) -> dict[str, Any]:
    if codec_events and not audio_events and not renderer_events:
        return {
            "label": "likely missed audio init",
            "likely_missed_audio_init": True,
            "note": (
                "Decoded video events were captured, but no audio or renderer evidence was seen. "
                "Start capture before playback begins to catch TV.app initialization."
            ),
        }
    return {
        "label": "complete" if (audio_events or renderer_events) else "not captured",
        "likely_missed_audio_init": False,
        "note": None,
    }


def _route_capability_label(route: str) -> str:
    route_lower = route.lower()
    if "not capable" in route_lower or "not spatial" in route_lower:
        return "not capable of spatialization"
    if "built-in" in route_lower or "macbook" in route_lower:
        return "built-in speakers"
    if "airpods" in route_lower or "headphone" in route_lower:
        return "headphones"
    if "bluetooth" in route_lower:
        return "Bluetooth"
    if "wired" in route_lower or "hdmi" in route_lower:
        return "wired/headphone"
    return "unknown route capability"


def _unique_keep_order(values: list[Any]) -> list[Any]:
    unique: list[Any] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _unique_events(events: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for event in events:
        key = tuple(event.get(field) for field in fields)
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


__all__ = [
    "LogCapture",
    "PREDICATE",
    "LOCAL_PLAYER_PREDICATE",
    "summarize_log_file",
    "summarize_log_lines",
    "tvlog_summary_markdown",
]
