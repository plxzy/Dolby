"""Live capture: run macOS `log stream` and fold its output into a playback summary.

    with LogCapture() as capture:    # spawns `log stream` and a reader thread
        queue = capture.subscribe()  # parsed events, delivered on the asyncio loop
        ...
    summary = capture.stop()         # the structured summary of the finished capture

When `start()` is called from async code, events are broadcast to subscribers on the
running loop (or pass `loop=` explicitly); from sync code they are only summarized.
Each event is a dict with `kind`, `raw`, `ts` plus the fields parsed by `parser`.
"""

import asyncio
import contextlib
import logging
import signal
import subprocess
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import IO, Any, Self

from ..common import ToolError
from .parser import Event, parse_line
from .predicates import PREDICATE
from .summary import CaptureState, build_summary

logger = logging.getLogger(__name__)

# How long `log stream` gets to exit after SIGINT before it is killed, and how long
# stop() then waits for the reader thread to drain the pipe.
STOP_TIMEOUT_S = 2.0

SUBSCRIBER_QUEUE_SIZE = 1000


def log_stream_command(predicate: str) -> list[str]:
    """argv for `log stream` in the compact style `parse_line` understands."""
    return ["log", "stream", "--info", "--debug", "--style", "compact", "--predicate", predicate]


class LogCapture:
    """Runs `log stream` (or any line-printing `command`) and folds its output into a summary.

    A stopped capture can be started again; each start begins a fresh summary.
    """

    def __init__(
        self,
        predicate: str = PREDICATE,
        *,
        command: Sequence[str] | None = None,
        raw_event_limit: int = 5000,
    ) -> None:
        self.predicate = predicate
        self.command = list(command) if command is not None else log_stream_command(predicate)
        self._raw_event_limit = raw_event_limit
        self._state = CaptureState(raw_event_limit)
        # The reader thread folds events into _state while callers may summarize it.
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._started_at: float | None = None  # wall clock, reported in the summary
        self._started_clock: float | None = None  # monotonic, for duration_s
        self._stopped_clock: float | None = None
        self._listeners: list[asyncio.Queue[Event]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_events(cls, events: Iterable[Event], *, predicate: str = PREDICATE) -> Self:
        """A capture that never ran, with `events` replayed through `add_event` (tests, saved logs)."""
        capture = cls(predicate=predicate)
        for event in events:
            capture.add_event(event)
        return capture

    # -- lifecycle ---------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Spawn the command and start reading its output; a no-op while running.

        Subscribers are fed on `loop`, by default the running loop when called from
        async code. Raises `ToolError` when the command cannot be started.
        """
        if self._proc is not None:
            return
        try:
            # stderr is left attached to ours: `log` reports problems there, not log lines.
            proc = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as e:
            name = Path(self.command[0]).name
            raise ToolError(f"could not start {name}: {e.strerror or e}", cmd=self.command) from e
        with self._lock:
            self._state = CaptureState(self._raw_event_limit)
        self._proc = proc
        self._loop = loop or _running_loop()
        self._started_at = time.time()
        self._started_clock = time.monotonic()
        self._stopped_clock = None
        self._reader = threading.Thread(target=self._read_lines, args=(proc.stdout,), daemon=True)
        self._reader.start()

    def stop(self) -> dict[str, Any]:
        """Stop the command, let the reader drain its output and return the summary.

        Calling it again (or on a capture that never started) returns the same summary.
        """
        proc, self._proc = self._proc, None
        if proc is not None:
            self._stopped_clock = time.monotonic()
            _terminate(proc)
            if self._reader is not None:
                self._reader.join(timeout=STOP_TIMEOUT_S)
        return self.summarize()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- subscription ------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[Event]:
        """A queue that receives every event parsed from now on (bounded; a slow reader misses some)."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    # -- reader thread -----------------------------------------------------

    def _read_lines(self, stdout: IO[str]) -> None:
        """Parse the command's output until it exits, then close the pipe."""
        with stdout:
            for line in stdout:
                try:
                    if (event := parse_line(line)) is None:
                        continue
                    event["ts"] = time.time()
                    self.add_event(event)
                except Exception:
                    # One unexpected line must not end a capture that may run for hours.
                    logger.exception("skipped a log line that could not be processed: %r", line)
                    continue
                self._broadcast(event)

    def add_event(self, event: Event) -> None:
        """Fold one parsed event into the running totals."""
        with self._lock:
            self._state.add(event)

    def _broadcast(self, event: Event) -> None:
        """Reader thread → event loop: asyncio queues may only be touched on their loop."""
        if self._loop is None or not self._listeners:
            return
        with contextlib.suppress(RuntimeError):  # the loop has already been closed
            self._loop.call_soon_threadsafe(self._deliver, event)

    def _deliver(self, event: Event) -> None:
        for queue in tuple(self._listeners):
            # A subscriber that falls behind misses live events; the summary still has them.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    # -- summary -----------------------------------------------------------

    def summarize(self) -> dict[str, Any]:
        """The playback summary so far; safe to call while the capture is running."""
        with self._lock:
            return build_summary(
                self._state,
                predicate=self.predicate,
                started_at=self._started_at,
                duration_s=self._duration(),
            )

    def _duration(self) -> float:
        if self._started_clock is None:
            return 0
        end = self._stopped_clock if self._stopped_clock is not None else time.monotonic()
        return end - self._started_clock


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Interrupt `log stream` like Ctrl-C does, kill it if it lingers, and reap it."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:  # called from sync code
        return None
