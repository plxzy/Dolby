"""Media analysis: ffprobe-based inspection (`inspect`) and multi-file ranking (`compare`)."""

from .compare import compare_files
from .inspect import InspectError, inspect_file

__all__ = ["InspectError", "compare_files", "inspect_file"]
