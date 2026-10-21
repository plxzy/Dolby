"""Environment introspection: which external tools are available, and a health probe."""

import platform
import shutil

from fastapi import APIRouter
from pydantic import BaseModel

from ... import __version__

router = APIRouter(prefix="/api", tags=["system"])

_TOOLS = ("ffprobe", "mediainfo", "osascript", "log", "mdfind")


class Capabilities(BaseModel):
    platform: str
    tools: dict[str, bool]


class Health(BaseModel):
    status: str
    version: str


@router.get("/capabilities", response_model=Capabilities, summary="Available external tools")
def capabilities() -> Capabilities:
    """Lets the UI explain (instead of failing) when e.g. live capture or the native picker need macOS."""
    return Capabilities(platform=platform.system(), tools={tool: shutil.which(tool) is not None for tool in _TOOLS})


@router.get("/health", response_model=Health, summary="Liveness probe")
def health() -> Health:
    return Health(status="ok", version=__version__)
