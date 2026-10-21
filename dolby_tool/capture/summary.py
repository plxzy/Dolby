"""Fold parsed log events into a playback summary and render it as Markdown.

`CaptureState` keeps running totals (latest HLS variant, decoder, audio, renderer
evidence, …) so a capture can run for hours while only the newest raw events are
retained. `build_summary` turns that state into the JSON-friendly dict shared by
live captures, saved-log parsing and the web UI.
"""

from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..common import unique
from .parser import Event, parse_line
from .predicates import PREDICATE

# Dolby Digital Plus sample-entry/codec names as TV.app and CoreAudio report them.
# `qc+3` is Apple's private CoreMedia variant of E-AC-3.
DOLBY_AUDIO_FORMATS = frozenset({"ec+3", "ec-3", "ec3", "qc+3"})

# Most distinct renderer events kept as evidence; a long capture repeats the same few.
SUMMARY_RENDERER_EVENT_LIMIT = 1000


class CaptureState:
    """Running totals folded from parsed events: everything a summary is built from."""

    def __init__(self, raw_event_limit: int = 5000) -> None:
        self.raw_event_limit = max(0, raw_event_limit)
        self.events: deque[Event] = deque(maxlen=self.raw_event_limit)
        self.event_count = 0
        self.last_hls_variant: Event | None = None
        self.last_codec: Event | None = None
        self.last_audio: Event | None = None
        self.best_audio: Event | None = None
        # Insertion-ordered: one audio summary (with a count) per distinct audio report.
        self.observed_audio: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.last_file: Event | None = None
        self.renderer_events: list[Event] = []
        self._renderer_keys: set[tuple[tuple[str, str], ...]] = set()
        # The de-duplicated list cannot say which value is current (A → B → A lists A, B).
        self.latest_renderer_hints: dict[str, Event] = {}
        self.spatial_rendering_changed_count = 0
        self.pipeline_engines: list[str] = []

    def add(self, event: Event) -> None:
        """Fold one parsed event into the totals."""
        self.event_count += 1
        self.events.append(event)
        engine = event.get("pipeline_engine")
        if engine and engine not in self.pipeline_engines:
            self.pipeline_engines.append(engine)
        match event.get("kind"):
            case "hls_variant":
                self.last_hls_variant = event
            case "codec_type":
                self.last_codec = event
            case "audio_format":
                self._add_audio(event)
            case "file_player":
                self.last_file = event
            case "renderer_hint":
                self._add_renderer_hint(event)

    def _add_audio(self, event: Event) -> None:
        self.last_audio = event
        if self.best_audio is None or _audio_rank(event) > _audio_rank(self.best_audio):
            self.best_audio = event
        key = _audio_key(event)
        if key not in self.observed_audio:
            self.observed_audio[key] = {**_audio_summary(event), "count": 0}
        self.observed_audio[key]["count"] += 1

    def _add_renderer_hint(self, event: Event) -> None:
        self.latest_renderer_hints[event["hint"]] = event
        if event["hint"] == "spatial_rendering_changed":
            self.spatial_rendering_changed_count += 1
        if len(self.renderer_events) >= SUMMARY_RENDERER_EVENT_LIMIT:
            return
        # De-duplicate by content: the same hint repeats with only `raw`/`ts` changing.
        key = tuple(sorted((k, repr(v)) for k, v in event.items() if k not in {"raw", "ts"}))
        if key not in self._renderer_keys:
            self._renderer_keys.add(key)
            self.renderer_events.append(event)


# ---------------------------------------------------------------------------
# summary


def build_summary(
    state: CaptureState,
    *,
    predicate: str,
    started_at: float | None = None,
    duration_s: float = 0,
) -> dict[str, Any]:
    """The capture summary: counters, the retained raw events and the `playback` verdicts."""
    events = list(state.events)
    return {
        "started_at": started_at,
        "duration_s": duration_s,
        "event_count": state.event_count,
        "events_returned": len(events),
        "events_dropped": state.event_count - len(events),
        "raw_event_limit": state.raw_event_limit,
        "playback": _playback_summary(state, started_at) if state.event_count else None,
        "events": events,
        "predicate": predicate,
    }


def summarize_log_lines(
    lines: Iterable[str],
    predicate: str = PREDICATE,
    raw_event_limit: int = 5000,
) -> dict[str, Any]:
    """Parse saved `log stream --style compact` lines into a capture summary."""
    state = CaptureState(raw_event_limit)
    for line in lines:
        if event := parse_line(line):
            state.add(event)
    return build_summary(state, predicate=predicate)


def summarize_log_file(path: str | Path, predicate: str = PREDICATE) -> dict[str, Any]:
    """Parse a saved raw log file into the same shape as a live capture."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return summarize_log_lines(text.splitlines(), predicate=predicate)


def _playback_summary(state: CaptureState, started_at: float | None) -> dict[str, Any]:
    variant, codec, file_player = state.last_hls_variant, state.last_codec, state.last_file
    observed_audio = [dict(item) for item in state.observed_audio.values()]
    playback: dict[str, Any] = {
        "source": "hls" if variant else ("local_file" if file_player else "unknown"),
    }
    if variant:
        playback |= _hls_variant_fields(variant)
    if codec:
        playback["decoded_fourcc"] = codec.get("fourcc")
        playback["decoder"] = codec.get("decoder")
        if codec.get("width"):
            playback["decoded_resolution"] = f"{codec['width']}x{codec['height']}"
    if state.last_audio:
        playback["audio"] = _audio_summary(state.last_audio)
    if state.best_audio:
        playback["best_audio"] = _audio_summary(state.best_audio)
    if observed_audio:
        playback["observed_audio"] = observed_audio
    if variant and (
        verdict := _movpkg_hls_verdict(
            selected_group=variant.get("audio_group"),
            audio_codec=playback["audio_codec"],
            observed_audio=observed_audio,
            renderer_events=state.renderer_events,
        )
    ):
        playback["downloaded_hls_verdict"] = verdict
    if file_player:
        playback["file_player"] = file_player
    if engines := state.pipeline_engines:
        playback["pipeline_engine"] = _choose_pipeline_engine(playback["source"], engines)
        playback["pipeline_engines_observed"] = list(engines)
    if state.renderer_events:
        playback["renderer_evidence"] = _renderer_summary(
            state.renderer_events,
            state.latest_renderer_hints,
            started_at=started_at,
            spatial_rendering_changed_count=state.spatial_rendering_changed_count,
        )
    playback["capture_quality"] = _capture_quality(
        saw_codec=codec is not None,
        saw_audio=state.last_audio is not None,
        saw_renderer=bool(state.renderer_events),
    )
    playback |= _dolby_vision_verdict(playback.get("decoded_fourcc"), playback["source"])
    return playback


def _hls_variant_fields(variant: Event) -> dict[str, Any]:
    # CODECS attribute such as "dvh1.05.06,ec-3": video first, then audio.
    codecs = (variant.get("codecs") or "").split(",")
    audio_codec = codecs[1] if len(codecs) > 1 else None
    audio_group = variant.get("audio_group")
    return {
        "hls_variant": variant,
        "video_fourcc": codecs[0].split(".")[0] if codecs[0] else None,
        "audio_codec": audio_codec,
        "selected_hls_audio_group": audio_group,
        "selected_hls_audio_group_kind": _hls_audio_group_kind(audio_group, audio_codec),
        "hls_delivery": _hls_delivery_label(audio_group),
        "peak_mbps": _mbps(variant.get("peak_bps")),
        "avg_mbps": _mbps(variant.get("avg_bps")),
    }


def _mbps(bps: int | None) -> float | None:
    return round(bps / 1_000_000, 2) if bps else None


def _dolby_vision_verdict(decoded_fourcc: str | None, source: str) -> dict[str, Any]:
    match (decoded_fourcc or "").lower():
        case "dvh1" | "dvhe":
            return {"dolby_vision_active": True}
        case "hvc1" if source == "local_file":
            return {
                "dolby_vision_active": False,
                "dv_diagnosis": (
                    "Local file decoded as 'hvc1' — TV.app did NOT engage DV pipeline. "
                    "Re-tag sample-entry to 'dvh1' to fix."
                ),
            }
        case "qdh1" if source == "hls":
            return {
                "dolby_vision_active": None,
                "dv_label": "Apple HLS private HDR/DV path (qdh1)",
                "dv_diagnosis": (
                    "Decoded as 'qdh1' on the HLS path. This is Apple's private CoreMedia "
                    "HDR/Dolby Vision identifier for streaming content — it is the expected "
                    "fourcc for Apple TV+ HLS with DV video, not a fallback or degraded path."
                ),
            }
        case "qdh1":
            return {
                "dolby_vision_active": None,
                "dv_label": "Apple private HDR path (qdh1) — source unknown",
                "dv_diagnosis": (
                    "Decoded as 'qdh1' via hardware decoder. Not the hvc1 SDR fallback, "
                    "but also not the standard dvh1/dvhe open-DV marker. Likely Apple's "
                    "private CoreMedia HDR/DV path. Correlate with display output to confirm."
                ),
            }
        case _:
            return {"dolby_vision_active": None}


def _capture_quality(*, saw_codec: bool, saw_audio: bool, saw_renderer: bool) -> dict[str, Any]:
    if saw_codec and not saw_audio and not saw_renderer:
        return {
            "label": "likely missed audio init",
            "likely_missed_audio_init": True,
            "note": (
                "Decoded video events were captured, but no audio or renderer evidence was seen. "
                "Start capture before playback begins to catch TV.app initialization."
            ),
        }
    return {
        "label": "complete" if (saw_audio or saw_renderer) else "not captured",
        "likely_missed_audio_init": False,
        "note": None,
    }


# ---------------------------------------------------------------------------
# audio


def _audio_summary(event: Event) -> dict[str, Any]:
    audio_format = (event.get("format") or "").lower()
    summary = {
        "format": event.get("format"),
        "channels": event.get("channels"),
        "sample_rate": event.get("sample_rate"),
        "spatialization": event.get("spatialization"),
        "spatialization_eligible": event.get("spatialization_eligible"),
        "decodable": event.get("decodable"),
        "is_atmos": audio_format in DOLBY_AUDIO_FORMATS and event.get("spatialization") == "yes",
    }
    if audio_format == "qc+3":
        summary["diagnosis"] = (
            "Audio format 'qc+3' was reported as decodable by TV.app. "
            "Preserving it as an Apple/CoreMedia Dolby-like runtime path, not confirmed Atmos."
        )
    return summary


def _audio_key(event: Event) -> tuple[Any, ...]:
    return (
        event.get("format"),
        event.get("channels"),
        event.get("sample_rate"),
        event.get("spatialization"),
        event.get("spatialization_eligible"),
        event.get("decodable"),
    )


def _audio_rank(event: Event) -> tuple[int, int, int]:
    """Sort key for the "best" audio seen: Atmos-capable Dolby first, then channels, then decodable."""
    audio_format = (event.get("format") or "").lower()
    channels = event.get("channels") or 0
    is_dolby = audio_format in DOLBY_AUDIO_FORMATS
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


def _is_dolby_16ch(audio: dict[str, Any]) -> bool:
    """16-channel Dolby (E-AC-3 JOC) audio, i.e. the Atmos rendition of an HLS title."""
    return (audio.get("format") or "").lower() in DOLBY_AUDIO_FORMATS and (audio.get("channels") or 0) >= 16


# ---------------------------------------------------------------------------
# HLS


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
    renderer_events: list[Event],
) -> str | None:
    """Which audio rendition a downloaded .movpkg played, and whether Atmos was available."""
    if _hls_delivery_label(selected_group) != "downloaded_movpkg":
        return None
    match _hls_audio_group_kind(selected_group, audio_codec):
        case "atmos":
            return "movpkg_atmos_variant_selected"
        case "stereo" if _atmos_seen(observed_audio, renderer_events):
            return "movpkg_atmos_variant_present_but_not_selected"
        case "stereo":
            return "movpkg_figstreamplayer_selected_stereo"
    return None


def _atmos_seen(observed_audio: list[dict[str, Any]], renderer_events: list[Event]) -> bool:
    return any(_is_dolby_16ch(audio) for audio in observed_audio) or any(
        e.get("hint") == "mixer_spatial_status" and _is_dolby_16ch(e) and e.get("content_spatializable") is True
        for e in renderer_events
    )


# ---------------------------------------------------------------------------
# renderer evidence

# Signals that CoreAudio's spatial/Atmos machinery is running, whatever the app reports.
_LOWER_LEVEL_SIGNALS = (
    "spatial_power_active",
    "atmos_decoder_active",
    "oar_mode_active",
    "mixer_content_spatializable",
    "auspatial_mixer_active",
    "auspatial_atmos_layout_active",
    "audioqueue_forced_atmos_714",
)

_TV_APP_SPATIAL_FALSE_NOTE = (
    "Atmos decode and spatial mixer are active (lower-level CoreAudio machinery "
    "is running), but TV.app's app-level spatial-rendering flag is false. "
    "This is TV.app-specific behavior: it narrows allowedAudioSpatializationFormats "
    "to 0x5 (stripping Multichannel) for local items, via "
    "mpc_updateAVAudioSpatializationFormatsForPlayerAudioFormat:. Diagnostic "
    "AVFoundation controls and QuickTime render spatial=true on the same content/route. "
    "Not a codec downgrade — the Atmos pipeline is still engaged."
)


def _renderer_summary(
    events: list[Event],
    latest: dict[str, Event],
    *,
    started_at: float | None,
    spatial_rendering_changed_count: int,
) -> dict[str, Any]:
    """App-level vs CoreAudio-level spatial rendering evidence, ending in a `verdict`.

    `events` is the de-duplicated evidence; `latest` maps each hint to its most
    recent event and supplies the "current" values.
    """
    by_hint: dict[str, list[Event]] = {}
    for event in events:
        by_hint.setdefault(event["hint"], []).append(event)

    summary: dict[str, Any] = {"observed_hints": sorted(by_hint)}
    summary |= _route_evidence(by_hint.get("route", []), latest.get("route"))
    summary |= _app_spatial_evidence(by_hint.get("media_formatinfo", []), latest.get("media_formatinfo"), started_at)
    summary |= _spatial_power_evidence(by_hint.get("spatial_power", []), by_hint.get("head_tracking_preference", []))
    summary |= _atmos_decoder_evidence(by_hint.get("atmos_decoder_state", []), by_hint.get("atmos_decoder_subtype", []))
    summary |= _mixer_evidence(by_hint.get("mixer_spatial_status", []))
    summary |= _auspatial_evidence(by_hint)
    if masks := _distinct_values(by_hint.get("allowed_spatialization_formats_mask", []), "mask"):
        summary["allowed_spatialization_formats_masks"] = masks
    summary |= _spatial_binding_evidence(by_hint, latest.get("max_spatializable_channels"))
    if immersive := by_hint.get("immersive_rendering_requested"):
        summary["immersive_rendering_requested"] = any(
            e.get("immersive_rendering_requested") is True for e in immersive
        )
    summary["spatial_rendering_changed_count"] = spatial_rendering_changed_count
    summary["lower_level_spatialization_active"] = any(summary.get(key) is True for key in _LOWER_LEVEL_SIGNALS)
    summary |= _renderer_verdict(summary)
    return summary


def _route_evidence(route_events: list[Event], latest: Event | None) -> dict[str, Any]:
    routes = _distinct_values(route_events, "route")
    if not routes:
        return {}
    # Label the route in use now; `routes` lists each route once, in first-seen order.
    current = (latest or {}).get("route") or routes[-1]
    return {"routes": routes, "route_capability_label": _route_capability_label(current)}


def _app_spatial_evidence(media_events: list[Event], latest: Event | None, started_at: float | None) -> dict[str, Any]:
    """TV.app's own `mediaFormatinfo … is [not] rendering spatial audio` reports."""
    if not media_events:
        return {}
    last_state = (latest or {}).get("rendering_spatial_audio")
    ever_true = last_state is True or any(e.get("rendering_spatial_audio") is True for e in media_events)
    evidence: dict[str, Any] = {
        "app_spatial_rendering_ever_true": ever_true,
        "app_spatial_rendering_last_state": last_state,
    }
    if started_at is not None:
        first_true = next(
            (e["ts"] for e in media_events if e.get("rendering_spatial_audio") is True and e.get("ts") is not None),
            None,
        )
        if first_true is not None:
            evidence["first_true_at_seconds"] = round(first_true - started_at, 2)
    evidence["media_formatinfo"] = _distinct_views(
        media_events,
        ("format", "label", "channels", "sample_rate", "rendering_spatial_audio"),
        key=("format", "channels", "rendering_spatial_audio"),
    )
    return evidence


def _spatial_power_evidence(power_events: list[Event], preference_events: list[Event]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if power_events:
        evidence["spatial_power_active"] = any(e.get("spatialization") is True for e in power_events)
        evidence["head_tracking_active"] = any(e.get("head_tracking") is True for e in power_events)
        evidence["spatial_power"] = _distinct_views(power_events, ("spatialization", "stereo_upmix", "head_tracking"))
    if preference_events:
        evidence["prefers_head_tracked_spatialization"] = unique(
            e.get("prefers_head_tracked_spatialization") for e in preference_events
        )
    return evidence


def _atmos_decoder_evidence(state_events: list[Event], subtype_events: list[Event]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    if state_events:
        evidence["atmos_decoder_active"] = any(e.get("decoder_is_atmos") is True for e in state_events)
        evidence["oar_mode_active"] = any(e.get("decoder_oar_mode") is True for e in state_events)
        evidence["atmos_decoder_states"] = _distinct_views(state_events, ("decoder_is_atmos", "decoder_oar_mode"))
    if subtypes := _distinct_values(subtype_events, "decoder_subtype"):
        evidence["decoder_subtypes"] = subtypes
    return evidence


def _mixer_evidence(mixer_events: list[Event]) -> dict[str, Any]:
    if not mixer_events:
        return {}
    return {
        "mixer_content_spatializable": any(e.get("content_spatializable") is True for e in mixer_events),
        "mixer_spatialization_statuses": unique(
            e["spatialization_status"] for e in mixer_events if e.get("spatialization_status") is not None
        ),
        "mixer_spatial_status": _distinct_views(
            mixer_events, ("format", "channels", "content_spatializable", "spatialization_status")
        ),
    }


def _auspatial_evidence(by_hint: dict[str, list[Event]]) -> dict[str, Any]:
    """AUSpatialMixerV2 (the Atmos/spatial renderer) and AudioQueue's forced 7.1.4 Atmos decode."""
    evidence: dict[str, Any] = {}
    if any(hint.startswith("auspatial_") for hint in by_hint):
        evidence["auspatial_mixer_active"] = True
    if layouts := _distinct_values(by_hint.get("auspatial_channel_layout", []), "channel_layout"):
        evidence["auspatial_channel_layouts"] = layouts
        if any("Atmos" in layout for layout in layouts):
            evidence["auspatial_atmos_layout_active"] = True
    if processors := _distinct_values(by_hint.get("auspatial_channel_processors", []), "channel_processors"):
        evidence["auspatial_channel_processors"] = processors
    if "audioqueue_forced_atmos_714" in by_hint:
        evidence["audioqueue_forced_atmos_714"] = True
    return evidence


def _spatial_binding_evidence(by_hint: dict[str, list[Event]], latest_capability: Event | None) -> dict[str, Any]:
    """SpatializationManager's per-binding block: is the route spatial-capable, and for which source?

    On a non-capable route (e.g. a wired headset) it reports maxSpatializableChannels = 0
    and the '?src' source, which explains spatial=false even when the app sets the
    allowed-formats mask correctly.
    """
    evidence: dict[str, Any] = {}
    if (channels := (latest_capability or {}).get("max_spatializable_channels")) is not None:
        evidence["max_spatializable_channels"] = channels
        evidence["route_spatial_capable"] = channels > 0
    if source_events := by_hint.get("spatial_audio_sources"):
        sources = unique(s for e in source_events for s in e.get("spatial_audio_sources") or [] if s)
        evidence["spatial_audio_sources"] = sources
        evidence["spatial_source_unknown"] = "?src" in sources
    if apps := _distinct_values(by_hint.get("spatial_binding_app", []), "spatial_binding_app"):
        evidence["spatial_binding_apps"] = apps
    return evidence


def _renderer_verdict(summary: dict[str, Any]) -> dict[str, str]:
    last_state = summary.get("app_spatial_rendering_last_state")
    lower_level = summary["lower_level_spatialization_active"]
    if last_state is True:
        return {"verdict": "app_spatial_rendering_active"}
    if last_state is False and summary.get("app_spatial_rendering_ever_true") is True:
        return {"verdict": "app_spatial_rendering_was_active"}
    if last_state is False and lower_level:
        return {"verdict": "lower_level_active_app_spatial_false", "verdict_note": _TV_APP_SPATIAL_FALSE_NOTE}
    if lower_level:
        return {"verdict": "lower_level_spatialization_active"}
    return {"verdict": "no_spatial_renderer_activity"}


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


def _distinct_values(events: list[Event], field: str) -> list[Any]:
    """Distinct truthy values of `field`, in first-seen order."""
    return unique(e[field] for e in events if e.get(field))


def _distinct_views(
    events: list[Event], fields: tuple[str, ...], *, key: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    """`fields` of each event, skipping events whose `key` fields (default: `fields`) repeat."""
    seen: set[tuple[Any, ...]] = set()
    views = []
    for event in events:
        identity = tuple(event.get(field) for field in key or fields)
        if identity not in seen:
            seen.add(identity)
            views.append({field: event.get(field) for field in fields})
    return views


# ---------------------------------------------------------------------------
# Markdown

# (key, label) pairs rendered as "- label: `value`" when the value is truthy.
_PLAYBACK_LINES = (
    ("pipeline_engine", "pipeline engine"),
    ("selected_hls_audio_group", "selected HLS AudioGroup"),
    ("selected_hls_audio_group_kind", "HLS AudioGroup kind"),
    ("audio_codec", "HLS audio codec"),
    ("hls_delivery", "HLS delivery"),
    ("downloaded_hls_verdict", "downloaded HLS verdict"),
)
_AUDIO_LINES = (
    ("audio", "current audio"),
    ("best_audio", "best observed audio"),
)
# Renderer flags are rendered whenever present, so an explicit False shows up too.
_RENDERER_LINES = (
    ("app_spatial_rendering_ever_true", "TV.app app spatial ever true"),
    ("lower_level_spatialization_active", "lower-level spatialization active"),
    ("atmos_decoder_active", "Atmos decoder active"),
    ("oar_mode_active", "OAR mode active"),
    ("audioqueue_forced_atmos_714", "AudioQueue forced 7.1.4 Atmos"),
)


def tvlog_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a compact Markdown summary for CLI use."""
    playback = summary.get("playback") or {}
    renderer = playback.get("renderer_evidence") or {}
    lines = ["# TV/log playback summary\n"]
    if (access := summary.get("log_access")) and not access.get("available"):
        lines.append(f"- log capture: `skipped` — {access.get('reason')}")
    lines += [
        f"- events parsed: `{summary.get('event_count', 0)}`",
        f"- source: `{playback.get('source')}`",
    ]
    lines += [f"- {label}: `{playback[key]}`" for key, label in _PLAYBACK_LINES if playback.get(key)]
    lines += [
        f"- {label}: `{_format_audio_summary(playback[key])}`" for key, label in _AUDIO_LINES if playback.get(key)
    ]
    if renderer:
        lines.append(f"- renderer verdict: `{renderer.get('verdict')}`")
        lines += [f"- {label}: `{renderer[key]}`" for key, label in _RENDERER_LINES if renderer.get(key) is not None]
        if layouts := renderer.get("auspatial_channel_layouts"):
            lines.append("- AUSpatialMixer layouts: " + ", ".join(f"`{layout}`" for layout in layouts))
    if system := summary.get("system"):
        lines += _system_markdown(system)
    return "\n".join(lines) + "\n"


def _system_markdown(system: dict[str, Any]) -> list[str]:
    """The non-admin `system` block (see `capture.system`)."""
    lines = ["\n## System evidence (no admin needed)\n", f"- verdict: `{system['verdict']['label']}`"]
    if video := system.get("video"):
        lines.append(f"- video decode: `{video['description']}`")
    display = system.get("display") or {}
    lines.append(
        f"- HDR presented: `{display.get('hdr_presented')}` (max EDR headroom `{display.get('max_edr_headroom')}`, "
        f"panel cap `{display.get('max_panel_nits_cap')}` nits)"
    )
    if outputs := (system.get("audio") or {}).get("outputs"):
        lines.append("- audio output: " + ", ".join(f"`{o}`" for o in outputs))
    if network := system.get("network"):
        lines.append(
            f"- download: `{network['avg_mbps']}` Mbit/s avg, `{network['peak_mbps']}` peak ({network['process']})"
        )
    if now := system.get("now_playing"):
        title = " — ".join(str(part) for part in (now.get("album"), now.get("title")) if part)
        lines.append(f"- now playing: `{title}`")
    lines += [f"- opened file: `{path}`" for path in system.get("media_files") or []]
    lines += [f"- note: {note}" for note in system["verdict"]["notes"]]
    lines += [f"- needs admin log: {item}" for item in system.get("not_observable") or []]
    return lines


def _format_audio_summary(audio: dict[str, Any]) -> str:
    parts = [str(audio.get("format"))]
    if audio.get("channels") is not None:
        parts.append(f"ch={audio.get('channels')}")
    if audio.get("sample_rate") is not None:
        parts.append(f"{audio.get('sample_rate')} Hz")
    if audio.get("spatialization") is not None:
        parts.append(f"spatialization={audio.get('spatialization')}")
    return " ".join(parts)
