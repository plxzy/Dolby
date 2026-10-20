"""Pydantic models for inspection specs (`inspect_file`) and comparisons (`compare_files`).

`model_dump(mode="json")` reproduces the JSON the web UI has always consumed: the same keys
in the same order, None-valued ones included. The field descriptions double as the
reference on the FastAPI /docs page.
"""

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from ..common import DV_FOURCCS

# Weights and score components keep whole numbers as ints (`25`, not `25.0`), so the JSON
# stays exactly what it has always been; a plain `float` field would coerce them.
type Number = int | float


class _Model(BaseModel):
    # extra="forbid" turns a mistyped keyword into an error instead of a silently dropped field.
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# inspect


class Container(_Model):
    """File-level facts from ffprobe's `format` section."""

    format_name: str | None = Field(None, description="ffprobe short format name(s), e.g. `mov,mp4,m4a,3gp,3g2,mj2`.")
    format_long_name: str | None = Field(None, description="Human-readable container name, e.g. `QuickTime / MOV`.")
    duration_s: float | None = Field(
        None, description="Duration in seconds: the video stream's own duration, else the container's."
    )
    duration_pretty: str | None = Field(None, description="Duration as `1h 02m 03s` or `2m 03s`.")
    size_bytes: int | None = Field(None, description="File size in bytes.")
    size_pretty: str | None = Field(None, description="File size in 1024-based units, e.g. `1.20 GB`.")
    overall_bit_rate_bps: int | None = Field(None, description="Bit rate of all streams together, in bits/s.")


class DolbyVision(_Model):
    """The Dolby Vision configuration record (`dvcC`/`dvvC`) of the video stream.

    Serialises to just `{"present": false}` when the stream carries no Dolby Vision.
    """

    present: bool = Field(False, description="A Dolby Vision configuration record was found.")
    profile_raw: int | None = Field(None, description="Numeric profile as stored in the record (5, 7, 8, …).")
    compatibility_id: int | None = Field(
        None,
        description="Base-layer signal compatibility ID: 0 none, 1 HDR10, 2 SDR, 4 HLG, 6 Blu-ray HDR10.",
    )
    compatibility_name: str | None = Field(None, description="Readable name of `compatibility_id`.")
    profile: str | None = Field(
        None, description="Display profile: `8.<compatibility_id>` for profile 8 (e.g. `8.1`), else the bare number."
    )
    level: int | None = Field(None, description="Dolby Vision level, i.e. the resolution and frame-rate tier.")
    rpu_present: bool = Field(False, description="The stream carries an RPU (dynamic metadata) layer.")
    bl_present: bool = Field(False, description="The stream carries a base layer.")
    el_present: bool = Field(False, description="The stream carries an enhancement layer (dual-layer profile 7).")

    # No return annotation on purpose: pydantic would document the annotated `dict` instead of
    # the fields above in the OpenAPI schema.
    @model_serializer(mode="wrap")
    def _absent_as_flag_only(self, handler: SerializerFunctionWrapHandler):
        return handler(self) if self.present else {"present": False}


class Hdr10(_Model):
    """Static HDR10 metadata found on the video stream or its first frame."""

    mdcv: bool = Field(False, description="Mastering display colour volume (SMPTE ST 2086) is present.")
    cll: bool = Field(False, description="Content light level (MaxCLL / MaxFALL) is present.")

    @property
    def present(self) -> bool:
        """Either piece of HDR10 static metadata is present."""
        return self.mdcv or self.cll


class VideoSpec(_Model):
    """The primary video stream."""

    codec: str | None = Field(None, description="ffprobe codec name, e.g. `hevc` or `h264`.")
    profile: str | None = Field(None, description="Codec profile, e.g. `Main 10`.")
    fourcc: str = Field(
        "",
        description=(
            "Sample-entry FourCC, lower-cased (e.g. `dvh1`, `hvc1`). Apple players only engage "
            "Dolby Vision for `dvh1`, `dvhe`, `dvav` and `dva1`."
        ),
    )
    fourcc_warning: str | None = Field(
        None, description="Set when Dolby Vision will not engage in TV.app with this FourCC; says how to fix it."
    )
    width: int | None = Field(None, description="Coded width in pixels.")
    height: int | None = Field(None, description="Coded height in pixels.")
    resolution: str | None = Field(None, description="`WIDTHxHEIGHT`, e.g. `3840x2160`.")
    pix_fmt: str | None = Field(None, description="Pixel format, e.g. `yuv420p10le`.")
    fps: float | None = Field(None, description="Average frame rate, rounded to 3 decimals.")
    bit_rate_bps: int | None = Field(
        None, description="Video bit rate in bits/s; the container's overall rate when the stream reports none."
    )
    bit_rate_mbps: float | None = Field(None, description="`bit_rate_bps` in Mbit/s, rounded to 2 decimals.")
    color_transfer: str | None = Field(
        None, description="Transfer characteristics, e.g. `smpte2084` (PQ) or `arib-std-b67` (HLG)."
    )
    color_primaries: str | None = Field(None, description="Colour primaries, e.g. `bt2020`.")
    color_space: str | None = Field(None, description="Matrix coefficients, e.g. `bt2020nc`.")
    dolby_vision: DolbyVision = Field(default_factory=DolbyVision, description="Dolby Vision configuration.")
    hdr10: Hdr10 = Field(default_factory=Hdr10, description="HDR10 static metadata.")
    hdr10_plus: bool = Field(False, description="HDR10+ (SMPTE ST 2094-40) dynamic metadata is present.")

    @property
    def has_dv_fourcc(self) -> bool:
        """The FourCC is one Apple players treat as Dolby Vision (whether or not DV is present)."""
        return self.fourcc in DV_FOURCCS


class AudioTrack(_Model):
    """One audio stream."""

    index: int = Field(description="Absolute ffprobe stream index.")
    codec: str | None = Field(None, description="ffprobe codec name, e.g. `eac3`, `truehd` or `aac`.")
    codec_tag: str | None = Field(None, description="Sample-entry FourCC, lower-cased (e.g. `ec-3`); null when unset.")
    profile: str | None = Field(None, description="Codec profile as reported by ffprobe, e.g. `LC`.")
    channels: int | None = Field(None, description="Channel count.")
    channel_layout: str | None = Field(None, description="Channel layout, e.g. `5.1(side)`.")
    sample_rate_hz: int | None = Field(None, description="Sample rate in Hz.")
    bit_rate_bps: int | None = Field(None, description="Bit rate in bits/s, when the container reports one.")
    bit_rate_kbps: int | None = Field(None, description="`bit_rate_bps` in kbit/s, rounded.")
    language: str | None = Field(None, description="Language tag, usually ISO 639-2 (e.g. `eng`).")
    title: str | None = Field(None, description="Track title tag.")
    atmos: bool | None = Field(
        None,
        description=(
            "Dolby Atmos (E-AC-3 JOC or TrueHD Atmos): true, false, or null when it cannot be told. "
            "Detection is only conclusive when `mediainfo` is installed."
        ),
    )
    default: bool = Field(False, description="The track has the default disposition.")


class SubtitleTrack(_Model):
    """One subtitle stream."""

    index: int = Field(description="Absolute ffprobe stream index.")
    codec: str | None = Field(None, description="ffprobe codec name, e.g. `mov_text`, `subrip` or `hdmv_pgs_subtitle`.")
    language: str | None = Field(None, description="Language tag, usually ISO 639-2 (e.g. `eng`).")
    title: str | None = Field(None, description="Track title tag, falling back to the handler name.")
    default: bool = Field(False, description="The track has the default disposition.")
    forced: bool = Field(False, description="The track is flagged forced.")
    hearing_impaired: bool = Field(False, description="The track is flagged for the hearing impaired (SDH).")


class MediaSpec(_Model):
    """Everything `inspect_file` learned about one media file."""

    path: str = Field(description="Path of the inspected file, as given.")
    filename: str = Field(description="Base name of `path`.")
    container: Container = Field(default_factory=Container, description="Container-level facts.")
    video: VideoSpec = Field(default_factory=VideoSpec, description="The primary video stream.")
    audio: list[AudioTrack] = Field(default_factory=list, description="Audio streams in file order.")
    subtitles: list[SubtitleTrack] = Field(default_factory=list, description="Subtitle streams in file order.")

    @property
    def atmos_track(self) -> AudioTrack | None:
        """The first audio track known to carry Dolby Atmos."""
        return next((track for track in self.audio if track.atmos is True), None)


# ---------------------------------------------------------------------------
# compare


class CompareRow(_Model):
    """One file flattened to the facts shown in the comparison table."""

    filename: str = Field(description="Base name of the file.")
    path: str = Field(description="Path of the file, as given.")
    size: str | None = Field(None, description="File size, human-readable.")
    duration: str | None = Field(None, description="Duration, human-readable.")
    resolution: str | None = Field(None, description="`WIDTHxHEIGHT` of the primary video stream.")
    fps: float | None = Field(None, description="Average frame rate.")
    video_codec: str | None = Field(None, description="Video codec name.")
    fourcc: str = Field("", description="Video sample-entry FourCC.")
    fourcc_ok: bool = Field(
        False, description="The FourCC is a Dolby Vision sample entry (`dvh1`/`dvhe`/`dvav`/`dva1`)."
    )
    video_bitrate_mbps: float | None = Field(None, description="Video bit rate in Mbit/s.")
    dv_profile: str | None = Field(None, description="Dolby Vision profile, e.g. `8.1`; null without Dolby Vision.")
    dv_compat: str | None = Field(None, description="Dolby Vision base-layer compatibility, e.g. `HDR10`.")
    hdr10: bool = Field(
        False, description="HDR10 static metadata (mastering display or content light level) is present."
    )
    hdr10_plus: bool = Field(False, description="HDR10+ dynamic metadata is present.")
    audio_codec: str | None = Field(None, description="Codec of the first Atmos track, else of the first audio track.")
    audio_channels: int | None = Field(None, description="Channel count of that audio track.")
    audio_atmos: bool | None = Field(None, description="Atmos flag of that audio track.")
    audio_bitrate_kbps: int | None = Field(None, description="Bit rate of that audio track in kbit/s.")
    subtitle_count: int = Field(0, description="Number of subtitle tracks.")
    warnings: list[str] = Field(default_factory=list, description="Playback warnings, e.g. a FourCC that breaks DV.")


class Score(_Model):
    """The weighted score of one file."""

    total: float = Field(description="Sum of `components`, rounded to one decimal.")
    components: dict[str, Number] = Field(
        description="Points earned per weighted dimension; the keys match `CompareResult.weights`."
    )
    max: Number = Field(description="Highest total any file could reach under the weights used.")
    is_best: bool = Field(description="This file has the highest total; tied files all win.")


class CompareError(_Model):
    """A file that could not be inspected and so takes no part in the comparison."""

    path: str = Field(description="Path of the file, as given.")
    error: str = Field(description="Why inspection failed.")


class CompareResult(_Model):
    """Side-by-side comparison of several files.

    `rows`, `scores` and `specs` are parallel: index *i* in each describes the same file.
    """

    rows: list[CompareRow] = Field(description="One flattened summary row per inspected file, in input order.")
    scores: list[Score] = Field(description="The weighted score of each row.")
    winners: dict[str, list[int]] = Field(
        description="For each comparable column, the indexes of the rows that win it (ties included)."
    )
    specs: list[MediaSpec] = Field(description="The full inspection spec of each row, for drill-down.")
    errors: list[CompareError] = Field(description="Files that could not be inspected.")
    weights: dict[str, Number] = Field(
        description="The effective score weights: the defaults, overridden by any custom values supplied."
    )
