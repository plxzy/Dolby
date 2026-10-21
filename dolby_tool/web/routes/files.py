"""File-picking helpers for the UI: native dialog, Finder drag-and-drop resolution, folder listings."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...common import ToolError
from .. import filesystem

router = APIRouter(prefix="/api", tags=["files"])


class PickedPaths(BaseModel):
    paths: list[str]


class FoundPaths(BaseModel):
    name: str
    paths: list[str]


class FileEntry(BaseModel):
    path: str
    name: str
    size: int | None


class DirectoryListing(BaseModel):
    path: str
    files: list[FileEntry]


@router.get("/pick", response_model=PickedPaths, summary="Native macOS file or folder dialog")
def pick(multi: bool = False, directory: bool = False) -> PickedPaths:
    """Show the macOS file/folder dialog. Returns no paths when the user cancels."""
    try:
        return PickedPaths(paths=filesystem.pick_files(multiple=multi, directory=directory))
    except ToolError as e:
        raise HTTPException(status_code=501, detail="The native file picker needs macOS (osascript).") from e


@router.get("/find", response_model=FoundPaths, summary="Locate a dropped file by name")
def find(name: str, hint: str = "") -> FoundPaths:
    """Resolve a drag-and-drop file name to candidate paths (Spotlight, then find(1) over common media roots)."""
    return FoundPaths(name=name, paths=filesystem.find_media_by_name(name, hint))


@router.get("/list", response_model=DirectoryListing, summary="List media files in a folder")
def list_directory(path: str) -> DirectoryListing:
    """Media files (.mp4, .m4v, .mkv, .mov, .ts) directly inside a folder — used by Compare's "Add folder"."""
    directory = filesystem.normalize_path(path)
    if not Path(directory).is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {directory}")
    return DirectoryListing(path=directory, files=filesystem.list_media(directory))
