"""Inspect one file / compare several — the core analysis endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...analysis import CompareResult, MediaSpec, compare_files, inspect_file
from ..filesystem import normalize_path

router = APIRouter(prefix="/api", tags=["analysis"])


class InspectRequest(BaseModel):
    path: str = Field(description="Absolute path, `~` path or `file://` URI of a media file.")


class CompareRequest(BaseModel):
    paths: list[str] = Field(min_length=1, description="Media files to rank against each other.")
    weights: dict[str, float] | None = Field(
        default=None, description='Optional score weight overrides, e.g. `{"atmos": 30}`; unknown keys are ignored.'
    )


# Plain `def` handlers: ffprobe calls block, so FastAPI runs them in its threadpool.


@router.post("/inspect", response_model=MediaSpec, summary="Inspect a media file")
def inspect(body: InspectRequest) -> MediaSpec:
    """Run ffprobe (and mediainfo when installed) and return a structured Dolby Vision / HDR / Atmos spec."""
    return inspect_file(normalize_path(body.path))


@router.post("/compare", response_model=CompareResult, summary="Rank several media files")
def compare(body: CompareRequest) -> CompareResult:
    """Inspect every file, score it on DV/HDR/Atmos/bitrate/resolution/subtitles and mark per-dimension winners."""
    return compare_files([normalize_path(p) for p in body.paths], body.weights)
