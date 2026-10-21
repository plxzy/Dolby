"""Local conversion and metadata/artwork tagging, run in FastAPI's worker pool."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...convert import mux, subs_pgs
from ...convert.commands import resolve_metadata
from ...convert.metadata import tagger
from ..filesystem import normalize_path

router = APIRouter(prefix="/api", tags=["convert"])


class ConvertPayload(BaseModel):
    input: str
    output: str
    overwrite: bool = False
    no_metadata: bool = True
    metadata_id: str | None = None
    storefront: str = "US"
    language: str | None = None
    show: str | None = None
    season: int | None = None
    episode: int | None = None
    skip_pgs: bool = False
    season_artwork: str | None = None


class TagPayload(BaseModel):
    file: str
    metadata_id: str | None = None
    storefront: str = "US"
    language: str | None = None
    show: str | None = None
    season: int | None = None
    episode: int | None = None
    season_artwork: str | None = None


def _metadata(path: Path, body: ConvertPayload | TagPayload, *, output: Path | None = None) -> dict[str, Any]:
    try:
        meta = resolve_metadata(
            str(path),
            storefront=body.storefront,
            language=body.language,
            content_id=body.metadata_id,
            interactive=False,
            show_override=body.show,
            season_override=body.season,
            episode_override=body.episode,
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta is None:
        return {"metadata": {"applied": False, "reason": "no match"}}
    return {"matched_title": meta.title, "metadata": tagger.apply(output if output is not None else path, meta)}


# Synchronous handlers run blocking ffmpeg, catalog and AtomicParsley work off the event loop.
@router.post("/convert", summary="Convert a local media file")
def convert(body: ConvertPayload) -> dict[str, Any]:
    src = Path(normalize_path(body.input))
    dst = Path(normalize_path(body.output))
    if not src.is_file():
        raise HTTPException(status_code=400, detail=f"input not found: {src}")
    summary: dict[str, Any] = {"input": str(src), "output": str(dst)}
    try:
        mux.require_distinct_output(src, dst)
        summary["mux"] = mux.mux(src, dst, overwrite=body.overwrite)
        if not body.skip_pgs:
            info = mux.probe(src)
            if info.bitmap_subs:
                pgs_out = dst.with_suffix(".pgs.m4v")
                result = subs_pgs.convert_and_remux(src, dst, info.bitmap_subs, output=pgs_out)
                summary["pgs"] = {
                    "converted": result.converted,
                    "skipped": result.skipped,
                    "reason": result.reason,
                }
                if result.converted:
                    pgs_out.replace(dst)
        if not body.no_metadata:
            summary.update(_metadata(src, body, output=dst))
        if body.season_artwork:
            summary["artwork"] = tagger.apply_artwork(dst, Path(normalize_path(body.season_artwork)))
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return summary


@router.post("/tag", summary="Tag a local media file or directory")
def tag(body: TagPayload) -> dict[str, Any]:
    target = Path(normalize_path(body.file))
    if target.is_dir():
        files = sorted(p for p in target.iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".m4v"})
    elif target.is_file():
        files = [target]
    else:
        raise HTTPException(status_code=400, detail=f"not found: {target}")
    art = Path(normalize_path(body.season_artwork)) if body.season_artwork else None
    art_only = art is not None and not (body.metadata_id or body.show)
    results: list[dict[str, Any]] = []
    try:
        for path in files:
            entry: dict[str, Any] = {"file": str(path)}
            if not art_only:
                entry.update(_metadata(path, body))
            if art is not None:
                entry["artwork"] = tagger.apply_artwork(path, art)
            results.append(entry)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return results[0] if len(results) == 1 else {"directory": str(target), "files": results}
