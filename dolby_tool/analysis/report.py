"""Markdown renderers for inspection specs and comparisons, for CLI output and reports."""

from collections.abc import Iterable

from .models import AudioTrack, CompareResult, CompareRow, MediaSpec, SubtitleTrack

_NONE = "-"


def spec_markdown(spec: MediaSpec) -> str:
    """Render one file's spec: a verdict line, then container, video, audio and subtitle sections."""
    video = spec.video
    lines = [f"# {spec.filename}\n", f"`{spec.path}`\n", f"**Verdict:** {_verdict(spec)}\n"]
    if video.fourcc_warning:
        lines.append(f"> **Warning:** {video.fourcc_warning}\n")

    container = spec.container
    lines += [
        "## Container\n",
        _bullets(
            ("format", container.format_long_name or container.format_name),
            ("duration", container.duration_pretty),
            ("size", container.size_pretty),
            ("overall bit rate", _mbps(container.overall_bit_rate_bps)),
        ),
    ]

    dolby_vision = video.dolby_vision
    lines += [
        "## Video\n",
        _bullets(
            ("codec", _join(video.codec, video.profile)),
            ("FourCC", video.fourcc),
            ("resolution", video.resolution),
            ("frame rate", f"{video.fps:g} fps" if video.fps else None),
            ("pixel format", video.pix_fmt),
            ("bit rate", f"{video.bit_rate_mbps:g} Mbit/s" if video.bit_rate_mbps else None),
            ("colour", _join(video.color_primaries, video.color_transfer, video.color_space, sep=" / ")),
            ("Dolby Vision", _dolby_vision_line(spec) if dolby_vision.present else "no"),
            ("HDR10", _hdr10_line(spec)),
            ("HDR10+", "yes" if video.hdr10_plus else "no"),
        ),
        "## Audio\n",
        _audio_table(spec.audio),
        "## Subtitles\n",
        _subtitle_table(spec.subtitles),
    ]
    return "\n".join(lines)


def compare_markdown(result: CompareResult) -> str:
    """Render a ranked comparison table (best first; ties share a rank), then warnings and errors."""
    lines = ["# Comparison\n"]
    if result.rows:
        weights = ", ".join(f"{name} {weight:g}" for name, weight in result.weights.items())
        lines += [f"- files compared: {len(result.rows)}", f"- score weights: {weights}\n"]
        lines += [
            "| rank | file | score | resolution | Dolby Vision | HDR10 | HDR10+ | FourCC | video | audio | subs |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for rank, index in _ranking(result):
            row, score = result.rows[index], result.scores[index]
            name = f"**{_cell(row.filename)}** (best)" if score.is_best else _cell(row.filename)
            lines.append(
                f"| {rank} | {name} | {score.total:g} / {score.max:g} | {row.resolution or _NONE} | "
                f"{_dv_cell(row.dv_profile, row.dv_compat)} | {_yes(row.hdr10)} | {_yes(row.hdr10_plus)} | "
                f"`{row.fourcc}` {'ok' if row.fourcc_ok else 'not DV'} | "
                f"{_join(row.video_codec, _unit(row.video_bitrate_mbps, 'Mbit/s'))} | {_audio_cell(row)} | "
                f"{row.subtitle_count} |"
            )
        lines.append("")
    else:
        lines.append("No file could be inspected.\n")

    warnings = [(row.filename, warning) for row in result.rows for warning in row.warnings]
    if warnings:
        lines += ["## Warnings\n", *(f"- `{name}`: {warning}" for name, warning in warnings), ""]
    if result.errors:
        lines += ["## Errors\n", *(f"- `{error.path}`: {error.error}" for error in result.errors), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# spec sections


def _verdict(spec: MediaSpec) -> str:
    video = spec.video
    parts = []
    if video.dolby_vision.present:
        state = "FourCC ok" if video.has_dv_fourcc else f"FourCC `{video.fourcc}` will not engage DV"
        parts.append(f"Dolby Vision profile {video.dolby_vision.profile} ({state})")
    if video.hdr10.present:
        parts.append("HDR10")
    if video.hdr10_plus:
        parts.append("HDR10+")
    if spec.atmos_track:
        parts.append("Dolby Atmos")
    return ", ".join(parts) or "no Dolby Vision, HDR10(+) or Atmos detected"


def _dolby_vision_line(spec: MediaSpec) -> str:
    dolby_vision = spec.video.dolby_vision
    layers = [name for name, on in (("BL", dolby_vision.bl_present), ("EL", dolby_vision.el_present)) if on]
    if dolby_vision.rpu_present:
        layers.append("RPU")
    return (
        f"profile {dolby_vision.profile}, {dolby_vision.compatibility_name or 'unknown'} compatible, "
        f"level {dolby_vision.level if dolby_vision.level is not None else _NONE}, layers {'+'.join(layers) or _NONE}"
    )


def _hdr10_line(spec: MediaSpec) -> str:
    hdr10 = spec.video.hdr10
    if not hdr10.present:
        return "no"
    present = [name for name, on in (("mastering display", hdr10.mdcv), ("content light level", hdr10.cll)) if on]
    return "yes (" + ", ".join(present) + ")"


def _audio_table(tracks: list[AudioTrack]) -> str:
    if not tracks:
        return "No audio streams.\n"
    rows = [
        (
            track.index,
            _join(track.codec, track.profile),
            track.channel_layout or track.channels,
            f"{track.sample_rate_hz} Hz" if track.sample_rate_hz else None,
            _unit(track.bit_rate_kbps, "kbit/s"),
            track.language,
            {True: "yes", False: "no"}.get(track.atmos, "unknown"),
            _yes(track.default),
            track.title,
        )
        for track in tracks
    ]
    return _table(("#", "codec", "channels", "sample rate", "bit rate", "language", "Atmos", "default", "title"), rows)


def _subtitle_table(tracks: list[SubtitleTrack]) -> str:
    if not tracks:
        return "No subtitle streams.\n"
    rows = [
        (
            track.index,
            track.codec,
            track.language,
            track.title,
            _yes(track.default),
            _yes(track.forced),
            _yes(track.hearing_impaired),
        )
        for track in tracks
    ]
    return _table(("#", "codec", "language", "title", "default", "forced", "SDH"), rows)


# ---------------------------------------------------------------------------
# comparison cells


def _ranking(result: CompareResult) -> list[tuple[int, int]]:
    """(rank, row index) best first; files with an equal total share the lower rank number."""
    totals = [score.total for score in result.scores]
    order = sorted(range(len(totals)), key=lambda i: -totals[i])
    return [(1 + sum(total > totals[i] for total in totals), i) for i in order]


def _dv_cell(profile: str | None, compat: str | None) -> str:
    if profile is None:
        return _NONE
    return f"{profile} ({compat})" if compat else profile


def _audio_cell(row: CompareRow) -> str:
    if row.audio_codec is None:
        return _NONE
    channels = f"{row.audio_channels}ch" if row.audio_channels else None
    return _join(
        row.audio_codec, channels, "Atmos" if row.audio_atmos else None, _unit(row.audio_bitrate_kbps, "kbit/s")
    )


# ---------------------------------------------------------------------------
# formatting helpers


def _bullets(*items: tuple[str, object]) -> str:
    """A bullet per item whose value is set; empty values are left out."""
    return "\n".join(f"- {label}: `{value}`" for label, value in items if value not in (None, "")) + "\n"


def _table(headers: tuple[str, ...], rows: Iterable[tuple[object, ...]]) -> str:
    lines = [f"| {' | '.join(headers)} |", f"|{'---|' * len(headers)}"]
    lines += [f"| {' | '.join(_cell(value) for value in row)} |" for row in rows]
    return "\n".join(lines) + "\n"


def _cell(value: object) -> str:
    text = _NONE if value in (None, "") else str(value)
    return text.replace("|", "\\|")


def _join(*parts: object, sep: str = " ") -> str | None:
    return sep.join(str(part) for part in parts if part not in (None, "")) or None


def _unit(value: float | None, unit: str) -> str | None:
    return f"{value:g} {unit}" if value else None


def _mbps(bits_per_second: int | None) -> str | None:
    return f"{bits_per_second / 1_000_000:.2f} Mbit/s" if bits_per_second else None


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"
