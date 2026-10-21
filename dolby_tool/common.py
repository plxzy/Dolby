"""Small helpers shared across the analysis, capture, HLS and convert packages.

Everything here is deliberately dependency-free and side-effect-free apart from
`run_tool`, the single place where external media tools (ffprobe, ffmpeg,
AtomicParsley, …) are executed.
"""

import json
import shutil
import subprocess
from collections.abc import Hashable, Iterable, Sequence
from pathlib import Path
from typing import Any

# Sample-entry FourCCs that make Apple players engage the Dolby Vision pipeline
# (HEVC: dvh1/dvhe, AVC: dvav/dva1). An `hvc1`/`hev1` tag on a DV stream means
# TV.app silently falls back to the HDR10/SDR base layer.
DV_FOURCCS: frozenset[str] = frozenset({"dvh1", "dvhe", "dvav", "dva1"})

# Container extensions the UI offers for inspection / comparison.
MEDIA_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".m4v", ".mkv", ".mov", ".ts"})


class ToolError(RuntimeError):
    """An external tool was missing, timed out, or exited non-zero."""

    def __init__(self, message: str, *, cmd: Sequence[str] = (), returncode: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr


# ---------------------------------------------------------------------------
# value coercion


def to_int(value: Any) -> int | None:
    """`int(value)`, or None when the value is missing or not numeric."""
    try:
        return int(value) if value is not None else None
    except TypeError, ValueError:
        return None


def to_float(value: Any) -> float | None:
    """`float(value)`, or None when the value is missing or not numeric."""
    try:
        return float(value) if value is not None else None
    except TypeError, ValueError:
        return None


def unique[T: Hashable](values: Iterable[T]) -> list[T]:
    """De-duplicate while keeping first-seen order."""
    return list(dict.fromkeys(values))


# ---------------------------------------------------------------------------
# external tools


def which_any(*names: str) -> str | None:
    """Absolute path of the first executable found on PATH among `names`."""
    for name in names:
        if found := shutil.which(name):
            return found
    return None


def run_tool(
    cmd: Sequence[str], *, timeout: float | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run an external tool and capture its output as text.

    stdin is detached (ffmpeg otherwise reads keystrokes from the terminal) and
    output is decoded leniently, because media tools happily echo non-UTF-8 tags
    and filenames. Failures raise `ToolError` carrying the last stderr line.
    """
    name = Path(cmd[0]).name
    try:
        result = subprocess.run(
            list(cmd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise ToolError(f"command not found: {name}", cmd=cmd) from e
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"{name} timed out after {timeout:g}s", cmd=cmd) from e
    if check and result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
        raise ToolError(f"{name} failed: {tail[0]}", cmd=cmd, returncode=result.returncode, stderr=result.stderr)
    return result


def ffprobe_json(path: str | Path, *args: str, timeout: float | None = 60) -> dict[str, Any]:
    """Run `ffprobe -v error -print_format json <args> <path>` and parse the result."""
    result = run_tool(["ffprobe", "-v", "error", "-print_format", "json", *args, str(path)], timeout=timeout)
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as e:
        raise ToolError(f"ffprobe returned invalid JSON for {path}", cmd=["ffprobe", str(path)]) from e
