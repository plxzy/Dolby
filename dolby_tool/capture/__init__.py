"""Live playback capture: macOS unified-log (`log stream`) parsing and summaries for TV.app and local players.

- `predicates` — which processes `log stream` should follow
- `parser`     — one log line → one event dict
- `summary`    — running totals → playback summary (+ Markdown)
- `session`    — `LogCapture`: the `log stream` subprocess, reader thread and subscribers
- `system`     — `SystemSampler`: evidence a non-admin account can read (decoder, display, network, …)
"""

from .parser import parse_line
from .predicates import LOCAL_PLAYER_PREDICATE, PREDICATE
from .session import LogCapture
from .summary import summarize_log_file, summarize_log_lines, tvlog_summary_markdown
from .system import PLAYER_PROCESSES, SystemSampler, log_stream_access

__all__ = [
    "LOCAL_PLAYER_PREDICATE",
    "PLAYER_PROCESSES",
    "PREDICATE",
    "LogCapture",
    "SystemSampler",
    "log_stream_access",
    "parse_line",
    "summarize_log_file",
    "summarize_log_lines",
    "tvlog_summary_markdown",
]
