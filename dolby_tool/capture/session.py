"""Live capture: run macOS `log stream` and fold its output into a playback summary.

    with LogCapture(system=SystemSampler()) as capture:  # `log stream`, reader thread, sampler
        queue = capture.subscribe()  # parsed events, delivered on the asyncio loop
        ...
    summary = capture.stop()         # the structured summary of the finished capture

When `start()` is called from async code, events are broadcast to subscribers on the
running loop (or pass `loop=` explicitly); from sync code they are only summarized.
Each event is a dict with `kind`, `raw`, `ts` plus the fields parsed by `parser`.

`log stream` only runs for administrators. On a standard account the capture skips it
and relies on the optional `SystemSampler` (see `system`), whose events carry
`source: "system"` and whose findings land in the summary's `system` block.
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
from .system import SystemSampler, log_stream_access

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
        system: SystemSampler | None = None,
    ) -> None:
        self.predicate = predicate
        self._custom_command = command is not None
        self.command = list(command) if command is not None else log_stream_command(predicate)
        self.system = system
        self._raw_event_limit = raw_event_limit
        self._state = CaptureState(raw_event_limit)
        # The reader thread folds events into _state while callers may summarize it.
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._running = False
        self._log_access: dict[str, Any] | None = None
        self._system_event_count = 0
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
        """Spawn `log stream` (when this account may) and the system sampler; a no-op while running.

        Subscribers are fed on `loop`, by default the running loop when called from
        async code. Raises `ToolError` when neither source can run.
        """
        if self._running:
            return
        # A custom command (tests, replays) is trusted to run; `log stream` itself needs admin.
        access = {"available": True, "reason": "custom command"} if self._custom_command else log_stream_access()
        if not access["available"] and self.system is None:
            raise ToolError(f"could not start log stream: {access['reason']}", cmd=self.command)
        proc = None
        if access["available"]:
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
            self._system_event_count = 0
        self._log_access = access
        self._proc = proc
        self._running = True
        self._loop = loop or _running_loop()
        self._started_at = time.time()
        self._started_clock = time.monotonic()
        self._stopped_clock = None
        if proc is not None:
            self._reader = threading.Thread(target=self._read_lines, args=(proc.stdout,), daemon=True)
            self._reader.start()
        if self.system is not None:
            self.system.start(self._add_system_event)

    def stop(self) -> dict[str, Any]:
        """Stop `log stream` and the sampler, let the reader drain its output and return the summary.

        Calling it again (or on a capture that never started) returns the same summary.
        """
        if self._running:
            self._running = False
            self._stopped_clock = time.monotonic()
            proc, self._proc = self._proc, None
            if proc is not None:
                _terminate(proc)
                if self._reader is not None:
                    self._reader.join(timeout=STOP_TIMEOUT_S)
            if self.system is not None:
                self.system.stop()
        return self.summarize()

    @property
    def log_access(self) -> dict[str, Any]:
        """Whether `log stream` runs for this capture (decided at start) and why."""
        if self._log_access is not None:
            return self._log_access
        return {"available": True, "reason": "custom command"} if self._custom_command else log_stream_access()

    @property
    def capture_mode(self) -> str:
        """Which sources feed the capture: `log+system`, `log` or `system`."""
        sources = (("log", self.log_access["available"]), ("system", self.system is not None))
        return "+".join(name for name, on in sources if on)

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

    def _add_system_event(self, event: Event) -> None:
        """Sampler thread: the sampler keeps its own totals; count the event and pass it to subscribers."""
        with self._lock:
            self._system_event_count += 1
        self._broadcast(event)

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
        """The playback summary so far; safe to call while the capture is running.

        `playback` holds the log-derived verdicts (None without log events); `system`
        holds the sampler's findings (None without a sampler).
        """
        with self._lock:
            summary = build_summary(
                self._state,
                predicate=self.predicate,
                started_at=self._started_at,
                duration_s=self._duration(),
            )
            system_event_count = self._system_event_count
        summary["log_access"] = self.log_access
        summary["capture_mode"] = self.capture_mode
        summary["system_event_count"] = system_event_count
        if self.system is not None:
            summary["system"] = self.system.summary(log_available=self.log_access["available"])
        else:
            summary["system"] = None
        return summary

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
