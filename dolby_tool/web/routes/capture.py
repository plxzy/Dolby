"""TV.app playback capture: a live `log stream` over WebSocket, plus parsing of saved logs."""

import asyncio
import contextlib
from typing import Any, Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ...capture import LOCAL_PLAYER_PREDICATE, PREDICATE, LogCapture, summarize_log_lines
from ...common import ToolError

router = APIRouter(tags=["capture"])

Profile = Literal["tv", "local-player"]
_PREDICATES: dict[str, str] = {"tv": PREDICATE, "local-player": LOCAL_PLAYER_PREDICATE}


class CaptureHub:
    """Owns the single live capture (the tool is single-user by design).

    Starting a capture replaces any running one; only the connection that
    started a capture can stop it or receive its summary, so a second browser
    tab cannot steal another tab's results.
    """

    def __init__(self) -> None:
        self._capture: LogCapture | None = None
        self._owner: object | None = None
        self._lock = asyncio.Lock()

    async def start(self, owner: object, predicate: str) -> asyncio.Queue[dict[str, Any]]:
        async with self._lock:
            await self._stop_locked()
            capture = LogCapture(predicate=predicate)
            capture.start(asyncio.get_running_loop())
            queue = capture.subscribe()
            self._capture, self._owner = capture, owner
            return queue

    async def stop(self, owner: object | None = None) -> dict[str, Any] | None:
        """Stop the capture (only if `owner` started it) and return its summary."""
        async with self._lock:
            if self._capture is None or (owner is not None and owner is not self._owner):
                return None
            return await self._stop_locked()

    async def _stop_locked(self) -> dict[str, Any] | None:
        capture, self._capture, self._owner = self._capture, None, None
        if capture is None:
            return None
        # stop() waits for `log` to exit and joins the reader thread — keep it off the event loop.
        return await asyncio.to_thread(capture.stop)


@router.websocket("/ws/tvlog")
async def tvlog_socket(ws: WebSocket) -> None:
    """Commands: `{"cmd": "start" | "stop" | "ping"}`.

    Replies: `started` (with the predicate), streamed `event`s, the final `summary`, `pong`, or `error`.
    """
    hub: CaptureHub = ws.app.state.capture_hub
    await ws.accept()
    pump: asyncio.Task[None] | None = None
    try:
        while True:
            try:
                message = await ws.receive_json()
            except ValueError:
                await ws.send_json({"type": "error", "message": "messages must be JSON objects"})
                continue
            match message.get("cmd") if isinstance(message, dict) else None:
                case "start":
                    if pump is not None:
                        pump.cancel()
                    profile = message.get("profile", "tv")
                    predicate = _PREDICATES.get(profile, PREDICATE)
                    try:
                        queue = await hub.start(ws, predicate)
                    except (OSError, ToolError) as e:
                        await ws.send_json({"type": "error", "message": _start_error(e)})
                        continue
                    await ws.send_json({"type": "started", "predicate": predicate})
                    pump = asyncio.create_task(_pump(ws, queue))
                case "stop":
                    if pump is not None:
                        pump.cancel()
                        pump = None
                    summary = await hub.stop(owner=ws)
                    if summary is None:
                        await ws.send_json({"type": "error", "message": "no capture running"})
                    else:
                        await ws.send_json({"type": "summary", "summary": summary})
                case "ping":
                    await ws.send_json({"type": "pong"})
                case other:
                    await ws.send_json({"type": "error", "message": f"unknown cmd: {other}"})
    except WebSocketDisconnect:
        pass
    finally:
        if pump is not None:
            pump.cancel()
        await hub.stop(owner=ws)


async def _pump(ws: WebSocket, queue: asyncio.Queue[dict[str, Any]]) -> None:
    with contextlib.suppress(WebSocketDisconnect, RuntimeError, asyncio.QueueShutDown):
        while True:
            event = await queue.get()
            await ws.send_json({"type": "event", "event": event})


def _start_error(error: Exception) -> str:
    # LogCapture reports a missing binary as ToolError chained from FileNotFoundError.
    if isinstance(error, FileNotFoundError) or isinstance(error.__cause__, FileNotFoundError):
        return "`log stream` is not available here — live capture needs macOS. Use “Import saved log” instead."
    return f"could not start capture: {error}"


class ParseLogRequest(BaseModel):
    text: str = Field(description="Contents of a saved `log stream --style compact` capture.")
    profile: Profile = Field(default="tv", description="Predicate profile the log was captured with.")


class ParseLogResponse(BaseModel):
    summary: dict[str, Any]


@router.post("/api/tvlog/parse", response_model=ParseLogResponse, summary="Summarise a saved log capture")
def parse_saved_log(body: ParseLogRequest) -> ParseLogResponse:
    """Same summary as a live capture, computed from saved log text — works on any OS."""
    lines = body.text.splitlines()
    return ParseLogResponse(summary=summarize_log_lines(lines, predicate=_PREDICATES[body.profile]))
