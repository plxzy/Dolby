"""Filesystem helpers behind the file-picking endpoints (path normalising, Finder drops, folder listings)."""

import re
from pathlib import Path
from urllib.parse import unquote

from ..common import MEDIA_EXTENSIONS, ToolError, run_tool, unique

# Roots searched by find(1) when Spotlight doesn't know a dropped file
# (external/network volumes are often excluded from indexing).
_FIND_ROOTS = ("/Volumes", "~/Movies", "~/Desktop", "~/Downloads")
_FIND_LIMIT = 20
_GLOB_CHARS = re.compile(r"([*?\[\]\\])")

_PICK_ONE = 'POSIX path of (choose file with prompt "Pick a file")'
_PICK_MANY = "\n".join(
    [
        'set theFiles to (choose file with prompt "Pick file(s)" with multiple selections allowed)',
        'set out to ""',
        "repeat with f in theFiles",
        '  set out to out & POSIX path of f & "\\n"',
        "end repeat",
        "return out",
    ]
)


def normalize_path(raw: str) -> str:
    """Accept plain paths, `~` paths and the `file://` URIs browsers produce for Finder drops."""
    path = raw.strip()
    if path.startswith("file://"):
        path = unquote(path.removeprefix("file://"))
    return str(Path(path).expanduser())


def is_media(path: str | Path) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTENSIONS


def pick_files(*, multiple: bool) -> list[str]:
    """Show the native macOS "choose file" dialog; an empty list means the user cancelled."""
    result = run_tool(["osascript", "-e", _PICK_MANY if multiple else _PICK_ONE], timeout=300, check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def find_media_by_name(name: str, hint: str = "") -> list[str]:
    """Resolve a dropped file name to absolute path candidates.

    Safari hands drag-and-drop `File` objects to the page without a path, so the
    UI asks us to locate the name: Spotlight first (fast, indexed volumes), then
    find(1) over common media roots plus the directory the user last used.
    """
    paths = [p for p in _spotlight(name) if Path(p).name == name and is_media(p)]
    if not paths:
        paths = _find(name, hint)
    return unique(paths)[:_FIND_LIMIT]


def list_media(directory: str) -> list[dict[str, object]]:
    """Media files directly inside `directory`, sorted by name."""
    entries = []
    for child in sorted(Path(directory).iterdir(), key=lambda p: p.name):
        if not (child.is_file() and is_media(child)):
            continue
        try:
            size: int | None = child.stat().st_size
        except OSError:
            size = None
        entries.append({"path": str(child), "name": child.name, "size": size})
    return entries


def _spotlight(name: str) -> list[str]:
    try:
        result = run_tool(["mdfind", "-name", name], timeout=5, check=False)
    except ToolError:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _find(name: str, hint: str) -> list[str]:
    roots = [Path(root).expanduser() for root in _FIND_ROOTS]
    if hint and (hint_dir := Path(hint).expanduser()).is_dir():
        roots.insert(0, hint_dir)
    pattern = _GLOB_CHARS.sub(r"\\\1", name)  # match the name literally, not as a glob
    found: list[str] = []
    for root in unique(roots):
        if not root.exists():
            continue
        try:
            result = run_tool(["find", str(root), "-maxdepth", "8", "-name", pattern], timeout=10, check=False)
        except ToolError:
            continue
        found.extend(line.strip() for line in result.stdout.splitlines() if line.strip() and is_media(line.strip()))
        if len(found) >= _FIND_LIMIT:
            break
    return found
