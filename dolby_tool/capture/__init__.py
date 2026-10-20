"""Live playback capture: macOS unified-log (`log stream`) parsing and summaries for TV.app and local players."""

from .tvlog import (
    LOCAL_PLAYER_PREDICATE,
    PREDICATE,
    LogCapture,
    summarize_log_file,
    summarize_log_lines,
    tvlog_summary_markdown,
)

__all__ = [
    "LOCAL_PLAYER_PREDICATE",
    "PREDICATE",
    "LogCapture",
    "summarize_log_file",
    "summarize_log_lines",
    "tvlog_summary_markdown",
]
