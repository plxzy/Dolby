"""Media analysis: ffprobe-based inspection (`inspect`) and multi-file ranking (`compare`)."""

from .compare import compare_files
from .inspect import InspectError, inspect_file
from .models import CompareResult, MediaSpec

__all__ = ["CompareResult", "InspectError", "MediaSpec", "compare_files", "inspect_file"]
