"""Line parser for `log stream --style compact` output.

`parse_line` turns one log line into a flat event dict: a `kind` (renderer evidence
also carries a `hint`), the stripped `raw` line and the fields parsed from it, or
None when the line carries no playback signal. Order matters: the first matching
pattern wins.
"""

import re
from typing import Any

from ..common import to_float, to_int

type Event = dict[str, Any]


# ---------------------------------------------------------------------------
# regexes — kept loose, errors of omission are fine, errors of commission are not


# FigAlternate(504) [Peak/Avg 30570719/24765202] [3840x1606] [dvh1.05.06,ec-3]
# <FigAlternate(504):[0x...] [Peak/Avg 30570719/24765202] [3840x1606] [AudioGroup ...] [dvh1.05.06,ec-3]
# [VideoRange PQ] [HDCP Type1] [FrameRate 23.976]
RE_FIG_ALT = re.compile(r"FigAlternate\(\s*(?P<id>\d+)\s*\)")
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
RE_MAX_SPAT_CHANNELS = re.compile(r"maxSpatializableChannels\s*=\s*(?P<channels>\d+)", re.IGNORECASE)
RE_SPATIAL_AUDIO_SOURCES = re.compile(r"spatialAudioSources\s*=\s*\[\s*(?P<sources>[^\]]*?)\s*\]", re.IGNORECASE)
RE_SPATIAL_BINDING_APP = re.compile(r"^\s*App\s*=\s*(?P<app>[^\s{}][^\n]*?)\s*$", re.IGNORECASE | re.MULTILINE)
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

NOISE_AUDIO_FORMATS = frozenset({"table:"})


# ---------------------------------------------------------------------------
# line → event


def _parse_line(line: str) -> Event | None:
    """Parse one log line into an event, or None when it carries no playback signal."""
    if m := RE_FIG_ALT.search(line):
        return _hls_variant_event(m, line)
    if m := RE_HLS_VARIANT_SUMMARY.search(line):
        return {
            "kind": "hls_variant",
            "raw": line.rstrip(),
            "id": None,
            "peak_bps": None,
            "avg_bps": None,
            "width": to_int(m.group("width")),
            "height": to_int(m.group("height")),
            "codecs": None,
            "video_range": None,
            "hdcp": None,
            "fps": None,
        }
    if m := RE_CODEC_TYPE.search(line):
        d = m.groupdict()
        return {
            "kind": "codec_type",
            "raw": line.rstrip(),
            "fourcc": d["fourcc"],
            "decoder": d["decoder"],
            "width": to_int(d["width"] or _search_group(RE_RESOLUTION, line, "width")),
            "height": to_int(d["height"] or _search_group(RE_RESOLUTION, line, "height")),
        }
    if m := RE_AUDIO_FORMAT.search(line):
        return _audio_format_event(m, line)
    if m := RE_COREAUDIO_INPUT_FORMAT.search(line):
        return {
            "kind": "audio_format",
            "raw": line.rstrip(),
            "format": m.group("format"),
            "channels": to_int(m.group("channels")),
            "sample_rate": to_int(m.group("sample_rate")),
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
    if (m := RE_FILE_PLAYER.search(line)) and m.group("codec"):
        return {
            "kind": "file_player",
            "raw": line.rstrip(),
            "codec": m.group("codec"),
            "encryption_scheme": to_int(m.group("enc")),
            "width": to_int(m.group("width")),
            "height": to_int(m.group("height")),
        }
    if m := RE_LUMA.search(line):
        return {
            "kind": "luma_chroma",
            "raw": line.rstrip(),
            "luma_depth": to_int(m.group("luma")),
            "chroma_format": to_int(m.group("chroma")),
        }
    return None


parse_line = _parse_line


def _hls_variant_event(m: re.Match[str], line: str) -> Event:
    return {
        "kind": "hls_variant",
        "raw": line.rstrip(),
        "id": to_int(m.group("id")),
        "peak_bps": to_int(_search_group(RE_FIG_ALT_PEAK, line, "peak")),
        "avg_bps": to_int(_search_group(RE_FIG_ALT_PEAK, line, "avg")),
        "width": to_int(_search_group(RE_FIG_ALT_RES, line, "width")),
        "height": to_int(_search_group(RE_FIG_ALT_RES, line, "height")),
        "codecs": _search_group(RE_FIG_ALT_CODECS, line, "codecs"),
        "audio_group": _search_group(RE_FIG_ALT_AUDIO_GROUP, line, "audio_group"),
        "video_range": _search_group(RE_FIG_ALT_RANGE, line, "range"),
        "hdcp": _search_group(RE_FIG_ALT_HDCP, line, "hdcp"),
        "fps": to_float(_search_group(RE_FIG_ALT_FPS, line, "fps")),
        "pipeline_engine": _pipeline_engine_from_line(line),
    }


def _audio_format_event(m: re.Match[str], line: str) -> Event | None:
    d = m.groupdict()
    fmt = d["bracket_format"] or d["summary_format"]
    channels = d["bracket_channels"] or d["summary_channels"]
    decodable = d["summary_decodable"] == "decodable" if d["summary_decodable"] else None
    fmt, channels, decodable = _normalize_audio_fields(fmt, channels, decodable)
    bracket_fields = _audio_bracket_fields(line)
    channels = channels or bracket_fields.get("audiochannels")
    if not fmt:
        return None
    event: Event = {
        "kind": "audio_format",
        "raw": line.rstrip(),
        "format": fmt,
        "channels": to_int(channels),
        "sample_rate": to_int(d["bracket_rate"] or bracket_fields.get("samplerate")),
        "spatialization_eligible": d["bracket_spat_elig"] or bracket_fields.get("spatializationeligible"),
        "spatialization": d["bracket_spat"] or bracket_fields.get("spatialization"),
        "decodable": decodable,
    }
    # The reporting function and the immersive-rendering flag share the audio report's line.
    if (pipeline_engine := _pipeline_engine_from_line(line)) is not None:
        event["pipeline_engine"] = pipeline_engine
    if (immersive := _search_group(RE_IMMERSIVE_RENDERING, line, "value")) is not None:
        event["immersive_rendering_requested"] = immersive.lower() == "yes"
    return event


def _parse_renderer_line(line: str) -> Event | None:
    if m := RE_MEDIA_FORMATINFO.search(line):
        d = m.groupdict()
        sample_rate_khz = to_float(d["sample_rate"])
        return {
            "kind": "renderer_hint",
            "hint": "media_formatinfo",
            "raw": line.rstrip(),
            "format": d["format"].strip(),
            "label": d["label"].strip(),
            "channels": to_int(d["channels"]),
            "sample_rate": sample_rate_khz * 1000 if sample_rate_khz else None,
            "rendering_spatial_audio": d["not_rendering"] is None,
        }
    if m := RE_SPATIAL_POWER.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "spatial_power",
            "raw": line.rstrip(),
            "spatialization": _flag(m.group("spatialization")),
            "stereo_upmix": _flag(m.group("stereo_upmix")),
            "head_tracking": _flag(m.group("head_tracking")),
        }
    if m := RE_HEAD_TRACKING_PREF.search(line):
        return {
            "kind": "renderer_hint",
            "hint": "head_tracking_preference",
            "raw": line.rstrip(),
            "prefers_head_tracked_spatialization": _flag(m.group("head_tracking")),
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
            "max_spatializable_channels": to_int(m.group("channels")),
        }
    if m := RE_SPATIAL_AUDIO_SOURCES.search(line):
        # Sources look like "'mlti'", "'?src'" or "'mlti', 'atms'". '?src' is the
        # sentinel for an unrecognized source (e.g. a wired headset route).
        sources = [s.strip().strip("'\"") for s in m.group("sources").split(",") if s.strip()]
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
        return {
            "kind": "renderer_hint",
            "hint": "atmos_decoder_state",
            "raw": line.rstrip(),
            "decoder_is_atmos": _flag(m.group("is_atmos")),
            "decoder_is_tvos": _flag(m.group("is_tvos")),
            "decoder_oar_mode": _flag(m.group("oar_mode")),
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
            "channel_processors": to_int(m.group("channels")),
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
            "channels": to_int(d["channels"]),
            "best_available_content_type": to_int(d["content_type"]),
            "content_spatializable": _flag(d["content_spatializable"]),
            "spatialization_status": to_int(d["spatialization_status"]),
            "err": to_int(d["err"]),
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


# ---------------------------------------------------------------------------
# helpers


def _search_group(rx: re.Pattern[str], line: str, name: str) -> str | None:
    """Group `name` of the first `rx` match in `line`, or None when it does not match."""
    m = rx.search(line)
    return m.group(name) if m else None


def _flag(value: str) -> bool:
    """A `0`/`1` log field as a bool (the regexes only ever capture `[01]`)."""
    return value == "1"


def _pipeline_engine_from_line(line: str) -> str | None:
    if pm := RE_PIPELINE_ENGINE.search(line):
        fn = pm.group("engine").lower()
        return "FigFilePlayer" if fn.startswith("itemfig") else "FigStreamPlayer"
    if pm := RE_PIPELINE_MARKER.search(line):
        engine = pm.group("engine")
        return "FigFilePlayer" if engine.lower() == "figfileplayer" else "FigStreamPlayer"
    return None


def _audio_bracket_fields(line: str) -> dict[str, str]:
    """`[Key Words value]` fields of an audio report, keyed by lower-cased key without spaces."""
    return {m.group("key").replace(" ", "").lower(): m.group("value").strip() for m in RE_BRACKET_FIELD.finditer(line)}


def _normalize_audio_fields(
    audio_format: Any,
    channels: Any,
    decodable: bool | None,
) -> tuple[str | None, Any, bool | None]:
    """Split status text such as `qc+3 is decodable ch=16` out of the format; drop table noise."""
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
