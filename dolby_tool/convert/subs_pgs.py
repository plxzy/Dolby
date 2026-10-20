"""PGS (Blu-ray bitmap subtitle) → VobSub conversion + re-mux.

ffmpeg cannot transcode PGS to VobSub natively. We shell out to `bdsup2sub`
(BDSup2Sub++) when it's installed; otherwise we warn and skip.

Pipeline:
  1. Extract each PGS track from the source MKV with `ffmpeg -c:s copy` to a
     temporary `.sup` file.
  2. Run `bdsup2sub` to produce `.idx`/`.sub` pairs.
  3. Re-mux the existing MP4 + the new VobSub tracks into a new MP4
     (`ffmpeg -c copy` again — VobSub is supported by the MP4 muxer as
     `mov_text`? No — actually MP4 has no standard for VobSub; we add it as
     a subtitle track with `-c:s copy` and rely on Apple's VobSub support).

This is the one piece of Subler we don't try to faithfully replicate
in-process — bdsup2sub already does the palette/timing work well.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..common import run_tool, which_any
from .mux import StreamInfo, probe, video_tag


@dataclass
class PgsConversionResult:
    converted: int
    skipped: int
    reason: str | None = None


def _bdsup2sub() -> str | None:
    return which_any("bdsup2sub", "bdsup2sub++")


def have_bdsup2sub() -> bool:
    return _bdsup2sub() is not None


def convert_and_remux(
    source_mkv: Path,
    target_mp4: Path,
    pgs_streams: list[StreamInfo],
    *,
    output: Path | None = None,
) -> PgsConversionResult:
    """Convert PGS → VobSub and remux into a new MP4 alongside `target_mp4`."""
    if not pgs_streams:
        return PgsConversionResult(converted=0, skipped=0, reason="no PGS streams")
    bdsup2sub = _bdsup2sub()
    if bdsup2sub is None:
        return PgsConversionResult(
            converted=0,
            skipped=len(pgs_streams),
            reason="bdsup2sub not installed (brew install bdsup2sub++); PGS dropped",
        )

    output = output or target_mp4
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        idx_subs: list[tuple[Path, Path, str | None]] = []
        for s in pgs_streams:
            sup_path = tmp / f"sub_{s.index}.sup"
            extract_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(source_mkv),
                "-map",
                f"0:{s.index}",
                "-c:s",
                "copy",
                str(sup_path),
            ]
            r = run_tool(extract_cmd, check=False)
            if r.returncode != 0 or not sup_path.exists():
                continue
            idx_path = tmp / f"sub_{s.index}.idx"
            r = run_tool([bdsup2sub, "-o", str(idx_path), str(sup_path)], check=False)
            sub_path = idx_path.with_suffix(".sub")
            if r.returncode != 0 or not idx_path.exists() or not sub_path.exists():
                continue
            idx_subs.append((idx_path, sub_path, s.language))

        if not idx_subs:
            return PgsConversionResult(converted=0, skipped=len(pgs_streams), reason="bdsup2sub failed on all tracks")

        # `-map 0` re-emits every stream already in target_mp4 — including any
        # existing subtitle tracks — so the new VobSub outputs land at indices
        # offset by the count of subtitles already present.
        existing_subs = _count_subtitle_streams(target_mp4)
        # A plain `-c copy` would fall back to `hev1` and drop the Dolby Vision
        # config box, so re-apply the first pass's sample-entry tag.
        tag = video_tag(probe(target_mp4).video)

        cmd: list[str] = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(target_mp4)]
        for idx, _sub, _lang in idx_subs:
            cmd += ["-i", str(idx)]
        cmd += ["-map", "0"]
        for i, (_idx, _sub, lang) in enumerate(idx_subs, start=1):
            cmd += ["-map", f"{i}:s"]
            if lang:
                cmd += [f"-metadata:s:s:{existing_subs + i - 1}", f"language={lang}"]
        cmd += ["-c", "copy", *tag.ffmpeg_args, "-movflags", "+faststart+use_metadata_tags", str(output)]
        r = run_tool(cmd, check=False)
        if r.returncode != 0:
            return PgsConversionResult(
                converted=0,
                skipped=len(pgs_streams),
                reason=f"ffmpeg remux failed: {r.stderr.splitlines()[-3:]}",
            )

    return PgsConversionResult(converted=len(idx_subs), skipped=len(pgs_streams) - len(idx_subs))


def _count_subtitle_streams(mp4: Path) -> int:
    """Return how many subtitle streams already live in `mp4` (0 if probe fails)."""
    r = run_tool(
        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index", "-of", "csv=p=0", str(mp4)],
        check=False,
    )
    if r.returncode != 0:
        return 0
    return sum(1 for line in r.stdout.splitlines() if line.strip())
