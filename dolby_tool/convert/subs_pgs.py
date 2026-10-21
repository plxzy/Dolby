"""PGS (Blu-ray bitmap subtitle) → VobSub conversion + re-mux.

ffmpeg cannot transcode PGS to VobSub natively. We shell out to `bdsup2sub`
(BDSup2Sub++) when it's installed; otherwise we warn and skip.

Pipeline:
  1. Extract each PGS track from the source MKV with `ffmpeg -c:s copy` to a
     temporary `.sup` file.
  2. Run `bdsup2sub` to produce `.idx`/`.sub` pairs.
  3. Re-mux the first-pass MP4 plus the new VobSub tracks into a second MP4
     (`ffmpeg -c copy`). MP4 has no standard VobSub mapping, so the tracks are
     stream-copied as-is and rely on the player's VobSub support.

This is the one piece of Subler we don't try to faithfully replicate
in-process — bdsup2sub already does the palette/timing work well.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..common import ToolError, run_tool, which_any
from .mux import StreamInfo, discard_on_failure, probe, video_tag


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
    output: Path,
) -> PgsConversionResult:
    """Convert PGS → VobSub and remux `target_mp4` plus the new tracks into `output`.

    `output` must differ from `target_mp4`: ffmpeg can't read and write one file.
    A failed remux leaves no partial `output` behind.
    """
    if output.resolve() == target_mp4.resolve():
        raise ValueError("output must be a different file from target_mp4")
    if not pgs_streams:
        return PgsConversionResult(converted=0, skipped=0, reason="no PGS streams")
    bdsup2sub = _bdsup2sub()
    if bdsup2sub is None:
        return PgsConversionResult(
            converted=0,
            skipped=len(pgs_streams),
            reason="bdsup2sub not installed (brew install bdsup2sub++); PGS dropped",
        )

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        idx_subs: list[tuple[Path, str | None]] = []
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
            if r.returncode != 0 or not idx_path.exists() or not idx_path.with_suffix(".sub").exists():
                continue
            idx_subs.append((idx_path, s.language))

        if not idx_subs:
            return PgsConversionResult(converted=0, skipped=len(pgs_streams), reason="bdsup2sub failed on all tracks")

        try:
            existing = probe(target_mp4)
        except (ToolError, ValueError) as e:
            return PgsConversionResult(
                converted=0, skipped=len(pgs_streams), reason=f"could not probe {target_mp4.name}: {e}"
            )
        # A plain `-c copy` would fall back to `hev1` and drop the Dolby Vision
        # config box, so re-apply the first pass's sample-entry tag.
        tag = video_tag(existing.video)
        # `-map 0` re-emits every stream already in target_mp4 — including any
        # existing subtitle tracks — so the new VobSub outputs land at indices
        # offset by the count of subtitles already present.
        existing_subs = sum(1 for s in existing.raw.get("streams", []) if s.get("codec_type") == "subtitle")

        cmd: list[str] = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(target_mp4)]
        for idx, _lang in idx_subs:
            cmd += ["-i", str(idx)]
        cmd += ["-map", "0"]
        for i, (_idx, lang) in enumerate(idx_subs, start=1):
            cmd += ["-map", f"{i}:s"]
            if lang:
                cmd += [f"-metadata:s:s:{existing_subs + i - 1}", f"language={lang}"]
        cmd += ["-c", "copy", *tag.ffmpeg_args, "-movflags", "+faststart+use_metadata_tags", str(output)]
        try:
            with discard_on_failure(output):
                run_tool(cmd)
        except ToolError as e:
            return PgsConversionResult(converted=0, skipped=len(pgs_streams), reason=f"ffmpeg remux failed: {e}")

    return PgsConversionResult(converted=len(idx_subs), skipped=len(pgs_streams) - len(idx_subs))
