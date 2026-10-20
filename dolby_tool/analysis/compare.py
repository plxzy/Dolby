"""Compare multiple inspected specs side-by-side and pick a winner."""

from .inspect import inspect_file
from .models import CompareError, CompareResult, CompareRow, MediaSpec, Number, Score

# Score weights — tunable. Higher = more impactful on overall score.
WEIGHTS: dict[str, Number] = {
    "dv_present": 25,
    "dv_fourcc_ok": 25,  # huge — without this DV silently falls back in TV.app
    "hdr10_plus": 10,
    "atmos": 15,
    "bit_rate_mbps": 20,  # scaled 0..20 (capped at 80 Mbps)
    "resolution": 10,  # 0=SD, 5=1080p, 10=2160p+
    "subtitles": 5,  # 0..5 by count, capped at 5 tracks
}

# Columns of the comparison table that can be won: by the highest value, or by every file
# that has the flag set.
_HIGHEST_WINS = ("video_bitrate_mbps", "audio_bitrate_kbps", "audio_channels", "subtitle_count")
_FLAG_WINS = ("fourcc_ok", "dv_profile", "hdr10_plus", "audio_atmos")


def compare_files(paths: list[str], weights: dict[str, float] | None = None) -> CompareResult:
    """Inspect `paths` in order, then score and rank them; unreadable files land in `errors`."""
    specs: list[MediaSpec] = []
    errors: list[CompareError] = []
    for path in paths:
        try:
            specs.append(inspect_file(path))
        except Exception as e:  # one unreadable file must not sink the whole comparison
            errors.append(CompareError(path=path, error=str(e)))

    effective_weights = _effective_weights(weights)
    rows = [_summary_row(s) for s in specs]
    return CompareResult(
        rows=rows,
        scores=_scores(specs, effective_weights),
        winners=_winners(rows),
        specs=specs,
        errors=errors,
        weights=effective_weights,
    )


def _effective_weights(custom: dict[str, float] | None) -> dict[str, Number]:
    """The default weights, overridden by the known keys of `custom` (unknown keys are ignored)."""
    weights = dict(WEIGHTS)
    for key, value in (custom or {}).items():
        if key in weights:
            weights[key] = float(value)
    return weights


# ---------------------------------------------------------------------------
# scoring


def _scores(specs: list[MediaSpec], weights: dict[str, Number]) -> list[Score]:
    components = [_components(spec, weights) for spec in specs]
    totals = [round(sum(c.values()), 1) for c in components]
    best_total = max(totals, default=None)
    return [
        Score(total=total, components=parts, max=sum(WEIGHTS.values()), is_best=total == best_total)
        for parts, total in zip(components, totals, strict=True)
    ]


def _components(spec: MediaSpec, weights: dict[str, Number]) -> dict[str, Number]:
    video = spec.video
    dv_present = video.dolby_vision.present
    bit_rate = min((video.bit_rate_mbps or 0) / 80.0, 1.0) * weights["bit_rate_mbps"]
    subtitles = min(len(spec.subtitles) / 5.0, 1.0) * weights["subtitles"]
    return {
        "dv_present": weights["dv_present"] if dv_present else 0,
        "dv_fourcc_ok": weights["dv_fourcc_ok"] if dv_present and video.has_dv_fourcc else 0,
        "hdr10_plus": weights["hdr10_plus"] if video.hdr10_plus else 0,
        "atmos": weights["atmos"] if spec.atmos_track else 0,
        "bit_rate_mbps": round(bit_rate, 1),
        "resolution": round(_resolution_points(video.height, weights["resolution"]), 1),
        "subtitles": round(subtitles, 1),
    }


def _resolution_points(height: int | None, weight: Number) -> Number:
    """Full weight for 2160p and up, half for 1080p, a fifth for anything smaller."""
    height = height or 0
    if height >= 2000:
        return weight
    if height >= 1000:
        return weight * 0.5
    if height > 0:
        return weight * 0.2
    return 0


# ---------------------------------------------------------------------------
# table rows and winners


def _summary_row(spec: MediaSpec) -> CompareRow:
    """Flatten to a row suitable for table display."""
    video = spec.video
    audio = spec.atmos_track or (spec.audio[0] if spec.audio else None)
    return CompareRow(
        filename=spec.filename,
        path=spec.path,
        size=spec.container.size_pretty,
        duration=spec.container.duration_pretty,
        resolution=video.resolution,
        fps=video.fps,
        video_codec=video.codec,
        fourcc=video.fourcc,
        fourcc_ok=video.has_dv_fourcc,
        video_bitrate_mbps=video.bit_rate_mbps,
        dv_profile=video.dolby_vision.profile,
        dv_compat=video.dolby_vision.compatibility_name,
        hdr10=video.hdr10.present,
        hdr10_plus=video.hdr10_plus,
        audio_codec=audio.codec if audio else None,
        audio_channels=audio.channels if audio else None,
        audio_atmos=audio.atmos if audio else None,
        audio_bitrate_kbps=audio.bit_rate_kbps if audio else None,
        subtitle_count=len(spec.subtitles),
        warnings=[video.fourcc_warning] if video.fourcc_warning else [],
    )


def _winners(rows: list[CompareRow]) -> dict[str, list[int]]:
    """For each comparable column, the indexes of the rows that win it (ties included)."""
    if not rows:
        return {}
    winners = {key: _highest(rows, key) for key in _HIGHEST_WINS}
    winners |= {key: [i for i, row in enumerate(rows) if getattr(row, key)] for key in _FLAG_WINS}
    return winners


def _highest(rows: list[CompareRow], key: str) -> list[int]:
    """Rows sharing the highest value of `key`; missing and zero values never win."""
    values = {i: value for i, row in enumerate(rows) if (value := getattr(row, key))}
    top = max(values.values(), default=None)
    return [i for i, value in values.items() if value == top]
