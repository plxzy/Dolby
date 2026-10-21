"""MKV → Apple-TV-compatible .m4v stream-copy mux via ffmpeg.

Design: do as little as possible during mux. We `-c copy` video and the audio
tracks Apple players can decode, so HEVC/H.264 + AC-3/EAC-3/AC-4 passthrough
is bit-exact; audio Apple can't play from MP4 (TrueHD, DTS, PCM, …) is skipped
and reported. HEVC is labelled `hvc1` (or `dvh1` for Dolby Vision profiles
5/8), the sample entries Apple players accept. Text subtitles are converted
to `mov_text` (the only sub codec MP4 natively supports), and PGS bitmap
subtitles are stripped here and re-injected by `subs_pgs.py` in a second pass.

Optional `atoms.py` post-mux patches are available but NOT required for TV.app
spatial playback on current builds — the 2026-05-23 N2-probe baseline (see
reports/01_gate_model.md caveat) shows TV.app local playback spatializing
without dec3/colr patching. Treat the patchers as last-resort knobs for
specific bitstream-mismatch cases, not as a default pipeline step.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..common import ToolError, ffprobe_json, run_tool, to_int

# Audio codecs Apple players decode from MP4. Everything else (TrueHD/MLP, DTS,
# PCM, Vorbis, …) is skipped: ffmpeg refuses or only experimentally supports
# muxing it into MP4, and TV.app could not play it anyway.
APPLE_AUDIO_CODECS = frozenset({"aac", "ac3", "eac3", "ac4", "alac", "flac", "mp3", "opus"})

# Text subtitle codecs ffmpeg can convert to `mov_text`. Everything else is
# skipped, except PGS, which `subs_pgs.py` turns into VobSub in a second pass.
TEXT_SUB_CODECS = frozenset({"subrip", "ass", "ssa", "mov_text", "webvtt", "text"})
PGS_CODEC = "hdmv_pgs_subtitle"


@dataclass
class StreamInfo:
    index: int
    codec: str
    codec_type: str
    language: str | None = None
    title: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkippedStream:
    """A source stream the mux leaves out, and why."""

    index: int
    codec: str
    reason: str


@dataclass
class ProbeResult:
    video: StreamInfo
    audios: list[StreamInfo]  # only codecs Apple players can play from MP4
    text_subs: list[StreamInfo]  # convertible to mov_text
    bitmap_subs: list[StreamInfo]  # PGS only
    has_chapters: bool
    raw: dict[str, Any]
    skipped_audio: list[SkippedStream] = field(default_factory=list)
    skipped_subs: list[SkippedStream] = field(default_factory=list)


def probe(source: Path) -> ProbeResult:
    """Run ffprobe and classify streams."""
    raw = ffprobe_json(source, "-show_streams", "-show_chapters", "-show_format")

    video: StreamInfo | None = None
    audios: list[StreamInfo] = []
    skipped_audio: list[SkippedStream] = []
    skipped_subs: list[SkippedStream] = []
    text_subs: list[StreamInfo] = []
    bitmap_subs: list[StreamInfo] = []
    for s in raw.get("streams", []):
        info = StreamInfo(
            index=int(s["index"]),
            codec=s.get("codec_name", ""),
            codec_type=s.get("codec_type", ""),
            language=(s.get("tags") or {}).get("language"),
            title=(s.get("tags") or {}).get("title"),
            raw=s,
        )
        if info.codec_type == "video" and video is None and info.codec != "mjpeg":
            video = info
        elif info.codec_type == "audio" and info.codec in APPLE_AUDIO_CODECS:
            audios.append(info)
        elif info.codec_type == "audio":
            skipped_audio.append(SkippedStream(info.index, info.codec, "not playable from MP4 by Apple players"))
        elif info.codec_type == "subtitle":
            if info.codec in TEXT_SUB_CODECS:
                text_subs.append(info)
            elif info.codec == PGS_CODEC:
                bitmap_subs.append(info)
            else:
                skipped_subs.append(
                    SkippedStream(info.index, info.codec, "cannot be converted to an MP4 subtitle track")
                )
    if video is None:
        raise ValueError(f"No video stream found in {source}")
    return ProbeResult(
        video=video,
        audios=audios,
        text_subs=text_subs,
        bitmap_subs=bitmap_subs,
        has_chapters=bool(raw.get("chapters")),
        raw=raw,
        skipped_audio=skipped_audio,
        skipped_subs=skipped_subs,
    )


# Preference among the Apple-playable codecs for the default track: Atmos-capable
# first (E-AC-3 carries Atmos/JOC on every Apple device; AC-4 only on recent
# ones), then lossless, then lossy multichannel, then stereo lossy.
_CODEC_SCORE = {"eac3": 100, "ac4": 90, "alac": 80, "flac": 80, "ac3": 70, "opus": 50, "aac": 40, "mp3": 20}

# Track-title patterns that indicate a non-primary track we should NOT pick as
# the default (commentary, audio description, SDH, …). Whole words only, so
# "Broadcast" and "Director's Cut" don't match.
_NON_PRIMARY_RE = re.compile(
    r"\b(?:"
    r"commentary|comments?|audio[ -]description|descriptive|described|visually[ -]impaired"
    r"|ad|sdh|vi"
    r"|cast\s*(?:&|and)\s*crew|(?:director|cast)(?:['\u2019]s)?\s+(?:track|audio|talk)"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VideoTag:
    """The sample-entry fourcc to write for the video stream, and a Dolby Vision note."""

    fourcc: str | None = None  # None keeps ffmpeg's default
    dolby_vision: str | None = None

    @property
    def ffmpeg_args(self) -> list[str]:
        if self.fourcc is None:
            return []
        args = ["-tag:v", self.fourcc]
        if self.fourcc == "dvh1":
            # The MP4 muxer only writes the dvcC/dvvC configuration box at this strictness.
            args += ["-strict", "unofficial"]
        return args


def video_tag(video: StreamInfo) -> VideoTag:
    """Choose the sample entry Apple players need for a stream-copied video track.

    ffmpeg writes HEVC as `hev1`, which Apple players reject; `hvc1` is the
    accepted form. Dolby Vision profiles 5 and 8 become `dvh1`. Apple players
    can't decode other profiles (7 is dual-layer), so those keep only their
    HDR10/SDR base layer as plain `hvc1`.
    """
    if video.codec != "hevc":
        return VideoTag()
    profile = _dovi_profile(video)
    if profile is None:
        return VideoTag("hvc1")
    if profile in (5, 8):
        return VideoTag("dvh1", f"kept (profile {profile}, dvh1)")
    return VideoTag("hvc1", f"dropped (profile {profile} is not supported by Apple players)")


def _dovi_profile(video: StreamInfo) -> int | None:
    """Dolby Vision profile from the stream-level DOVI configuration record, if any."""
    for side_data in video.raw.get("side_data_list") or []:
        if side_data.get("side_data_type") == "DOVI configuration record":
            return to_int(side_data.get("dv_profile"))
    return None


def _is_non_primary(a: StreamInfo) -> bool:
    if _NON_PRIMARY_RE.search(a.title or ""):
        return True
    disp = a.raw.get("disposition") or {}
    return bool(disp.get("comment") or disp.get("descriptions") or disp.get("visual_impaired"))


def _channels(a: StreamInfo) -> int:
    return to_int(a.raw.get("channels")) or 0


def _default_audio_index(audios: list[StreamInfo]) -> int:
    """Pick the best primary audio track.

    Ranking (high→low): not commentary/AD, English language, codec quality
    (E-AC-3 > AC-4 > ALAC/FLAC > AC-3 > Opus > AAC > MP3), channel count,
    source `default` disposition, original index as tiebreaker.
    """
    if not audios:
        return 0

    def score(idx_and_a: tuple[int, StreamInfo]) -> tuple:
        i, a = idx_and_a
        lang = (a.language or "").lower()
        disp = a.raw.get("disposition") or {}
        return (
            0 if _is_non_primary(a) else 1,
            1 if lang in ("eng", "en") else 0,
            _CODEC_SCORE.get(a.codec.lower(), 0),
            _channels(a),
            1 if disp.get("default") else 0,
            -i,  # earlier index breaks ties
        )

    best = max(enumerate(audios), key=score)
    return best[0]


@contextmanager
def discard_on_failure(output: Path) -> Iterator[None]:
    """Delete a newly created partial output, preserving files present before the attempt."""
    existed = output.exists() or output.is_symlink()
    try:
        yield
    except ToolError, KeyboardInterrupt:
        if not existed:
            output.unlink(missing_ok=True)
        raise


def require_distinct_output(source: Path, output: Path) -> None:
    """Reject in-place conversion, including symlink and hard-link aliases."""
    if source.resolve() == output.resolve() or (output.exists() and source.exists() and source.samefile(output)):
        raise ValueError("Input and output must be different files")


def mux(
    source: Path,
    output: Path,
    *,
    overwrite: bool = False,
    include_bitmap_subs_as_passthrough: bool = False,
) -> dict[str, Any]:
    """Run the ffmpeg passthrough mux and return a summary dict.

    `include_bitmap_subs_as_passthrough=True` keeps PGS as a raw stream in the
    MP4 (broken on TV.app, fine on Infuse/VLC). Default: drop them — the
    PGS→VobSub step in `subs_pgs.py` re-adds them in a second pass.
    """
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Input does not exist: {source}")
    require_distinct_output(source, output)
    if output.exists() and not overwrite:
        raise ValueError(f"Output already exists (pass overwrite=True): {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    info = probe(source)
    if info.skipped_audio and not info.audios:
        skipped = ", ".join(f"#{s.index} {s.codec}" for s in info.skipped_audio)
        raise ValueError(f"No audio track Apple players can play from MP4 in {source.name} (skipped: {skipped})")
    default_audio = _default_audio_index(info.audios)
    tag = video_tag(info.video)

    cmd: list[str] = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y" if overwrite else "-n",
        # Tolerate partial/truncated MKVs: drop corrupt frames rather than abort.
        "-fflags",
        "+discardcorrupt+genpts",
        "-err_detect",
        "ignore_err",
        "-i",
        str(source),
        "-map",
        f"0:{info.video.index}",
    ]

    # Playable audio tracks, in source order.
    for a in info.audios:
        cmd += ["-map", f"0:{a.index}"]

    # Map subtitles by absolute input stream index so codec-class splitting
    # doesn't reorder them (a `0:s:N` index refers to subtitle-only ordering
    # in the input — using the original absolute index avoids the ambiguity
    # entirely and lets us set per-output-stream codec choices below).
    sub_codecs: list[str] = []  # codec per output subtitle stream, in order
    for s in info.text_subs:
        cmd += ["-map", f"0:{s.index}?"]
        sub_codecs.append("mov_text")
    if include_bitmap_subs_as_passthrough:
        for s in info.bitmap_subs:
            cmd += ["-map", f"0:{s.index}?"]
            sub_codecs.append("copy")

    cmd += ["-c:v", "copy", *tag.ffmpeg_args, "-c:a", "copy"]
    for i, codec in enumerate(sub_codecs):
        cmd += [f"-c:s:{i}", codec]

    # Default audio track flag
    if info.audios:
        for i, _ in enumerate(info.audios):
            flag = "default" if i == default_audio else "0"
            cmd += [f"-disposition:a:{i}", flag]

    if info.has_chapters:
        cmd += ["-map_chapters", "0"]

    cmd += [
        "-movflags",
        "+faststart+use_metadata_tags",
        "-f",
        "mp4",
        str(output),
    ]

    # No timeout: a stream copy of a full-length film legitimately takes minutes.
    with discard_on_failure(output):
        result = run_tool(cmd, timeout=None)

    return {
        "source": str(source),
        "output": str(output),
        "video_codec": info.video.codec,
        "video_tag": tag.fourcc,
        "dolby_vision": tag.dolby_vision,
        "audio_streams": [{"codec": a.codec, "language": a.language} for a in info.audios],
        "default_audio_index": default_audio,
        "audio_skipped": [asdict(s) for s in info.skipped_audio],
        "text_subs_included": len(info.text_subs),
        "subs_skipped": [asdict(s) for s in info.skipped_subs],
        "bitmap_subs_dropped": 0 if include_bitmap_subs_as_passthrough else len(info.bitmap_subs),
        "chapters": info.has_chapters,
        "ffmpeg_stderr_tail": result.stderr.splitlines()[-20:],
    }
