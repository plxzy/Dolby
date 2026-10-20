"""Live playback capture: macOS unified-log (`log stream`) parsing and summaries for TV.app and local players.

- `predicates` — which processes `log stream` should follow
- `parser`     — one log line → one event dict
- `summary`    — running totals → playback summary (+ Markdown)
- `session`    — `LogCapture`: the `log stream` subprocess, reader thread and subscribers
"""

from .parser import parse_line
from .predicates import LOCAL_PLAYER_PREDICATE, PREDICATE
from .session import LogCapture
from .summary import summarize_log_file, summarize_log_lines, tvlog_summary_markdown

__all__ = [
    "LOCAL_PLAYER_PREDICATE",
    "PREDICATE",
    "LogCapture",
    "parse_line",
    "summarize_log_file",
    "summarize_log_lines",
    "tvlog_summary_markdown",
]
