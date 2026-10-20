"""Live capture: run macOS `log stream` and fold its output into a playback summary.

    capture = LogCapture()
    capture.start()              # spawns `log stream` and a reader thread
    queue = capture.subscribe()  # parsed events, delivered on the asyncio loop given to start()
    summary = capture.stop()     # collapses the running totals into a structured summary

Each event is a dict with `kind`, `raw`, `ts` plus the fields parsed by `parser`.
"""

import asyncio
import signal
import subprocess
import threading
import time
from collections.abc import Iterable
from typing import Any, Self

from .parser import Event, parse_line
from .predicates import PREDICATE
from .summary import CaptureState, build_summary


class LogCapture:
    """Spawns `log stream` and accumulates parsed events until stopped."""

    def __init__(self, predicate: str = PREDICATE, *, raw_event_limit: int = 5000) -> None:
        self.predicate = predicate
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
        if self._proc is not None:
            return
        self._loop = loop
        self._started_at = time.time()
        self._proc = subprocess.Popen(
            [
                "log",
                "stream",
                "--info",
                "--debug",
                "--style",
                "compact",
                "--predicate",
                self.predicate,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
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
