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

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .mux import StreamInfo


@dataclass
class PgsConversionResult:
    converted: int
    skipped: int
    reason: str | None = None


def have_bdsup2sub() -> bool:
    return shutil.which("bdsup2sub") is not None or shutil.which("bdsup2sub++") is not None


def _bdsup2sub_cmd() -> str:
    return shutil.which("bdsup2sub") or shutil.which("bdsup2sub++") or "bdsup2sub"


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
    if not have_bdsup2sub():
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
                "-y",
                "-i",
                str(source_mkv),
                "-map",
                f"0:{s.index}",
                "-c:s",
                "copy",
                str(sup_path),
            ]
            r = subprocess.run(extract_cmd, capture_output=True, text=True)
            if r.returncode != 0 or not sup_path.exists():
                continue
            idx_path = tmp / f"sub_{s.index}.idx"
            convert_cmd = [_bdsup2sub_cmd(), "-o", str(idx_path), str(sup_path)]
            r = subprocess.run(convert_cmd, capture_output=True, text=True)
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

        cmd: list[str] = ["ffmpeg", "-hide_banner", "-y", "-i", str(target_mp4)]
        for idx, _sub, _lang in idx_subs:
            cmd += ["-i", str(idx)]
        cmd += ["-map", "0"]
        for i, (_idx, _sub, lang) in enumerate(idx_subs, start=1):
            cmd += ["-map", f"{i}:s"]
            if lang:
                cmd += [f"-metadata:s:s:{existing_subs + i - 1}", f"language={lang}"]
        cmd += ["-c", "copy", "-movflags", "+faststart+use_metadata_tags", str(output)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return PgsConversionResult(
                converted=0,
                skipped=len(pgs_streams),
                reason=f"ffmpeg remux failed: {r.stderr.splitlines()[-3:]}",
            )

    return PgsConversionResult(converted=len(idx_subs), skipped=len(pgs_streams) - len(idx_subs))


def _count_subtitle_streams(mp4: Path) -> int:
    """Return how many subtitle streams already live in `mp4` (0 if probe fails)."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index", "-of", "csv=p=0", str(mp4)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return 0
    return sum(1 for line in r.stdout.splitlines() if line.strip())
