"""Media analysis: ffprobe-based inspection (`inspect`), multi-file ranking (`compare`) and Markdown reports."""

from .compare import compare_files
from .inspect import InspectError, inspect_file
from .models import CompareResult, MediaSpec
from .report import compare_markdown, spec_markdown

__all__ = [
    "CompareResult",
    "InspectError",
    "MediaSpec",
    "compare_files",
    "compare_markdown",
    "inspect_file",
    "spec_markdown",
]
