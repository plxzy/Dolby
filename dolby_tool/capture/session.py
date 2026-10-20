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
import signal
import subprocess
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Self

from ..common import ToolError
from .parser import Event, parse_line
from .predicates import PREDICATE
from .summary import CaptureState, build_summary


def log_stream_command(predicate: str) -> list[str]:
    """argv for `log stream` in the compact style `parse_line` understands."""
    return ["log", "stream", "--info", "--debug", "--style", "compact", "--predicate", predicate]


class LogCapture:
    """Runs `log stream` (or any line-printing `command`) and folds its output into a summary."""

    def __init__(
        self,
        predicate: str = PREDICATE,
        *,
        command: Sequence[str] | None = None,
        raw_event_limit: int = 5000,
    ) -> None:
        self.predicate = predicate
        self.command = list(command) if command is not None else log_stream_command(predicate)
        self._state = CaptureState(raw_event_limit)
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._started_at: float | None = None
        self._listeners: list[asyncio.Queue] = []
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
            self._proc = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            name = Path(self.command[0]).name
            raise ToolError(f"could not start {name}: {e.strerror or e}", cmd=self.command) from e
        self._loop = loop or _running_loop()
        self._started_at = time.time()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=2)
        self._proc = None
        self._reader = None
        return self.summarize()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- subscription ------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._listeners:
            self._listeners.remove(q)

    # -- reader thread -----------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            if self._stop.is_set():
                break
            event = parse_line(line)
            if not event:
                continue
            event["ts"] = time.time()
            self.add_event(event)
            self._broadcast(event)

    def add_event(self, event: Event) -> None:
        self._state.add(event)

    def _broadcast(self, event: Event) -> None:
        loop = self._loop
        for q in list(self._listeners):
            if loop and loop.is_running():
                try:
                    loop.call_soon_threadsafe(q.put_nowait, event)
                except RuntimeError, asyncio.QueueFull:
                    pass

    # -- summary -----------------------------------------------------------

    def summarize(self) -> dict[str, Any]:
        return build_summary(
            self._state,
            predicate=self.predicate,
            started_at=self._started_at,
            duration_s=time.time() - self._started_at if self._started_at else 0,
        )


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:  # called from sync code
        return None
