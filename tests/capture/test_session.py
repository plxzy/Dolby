"""`LogCapture`: running totals, the raw-event cap and the subprocess lifecycle.

Lifecycle tests replace macOS `log stream` with a small Python process that prints
log lines, so they run anywhere.
"""

import asyncio
import sys
import time
from collections.abc import Callable

import pytest

from dolby_tool.capture import PREDICATE, LogCapture, parse_line
from dolby_tool.common import ToolError

LINES = (
    "2026-05-24 20:14:05.311 Df TV[1287:2a4f1] [com.apple.coremedia:player] <<<< FigStreamPlayer >>>> "
    "fpfs_ReportVariantSwitchStart: to [<FigAlternate(441):[0x7b2c4d000] [Peak/Avg 23415872/15234567] "
    "[3840x1606] [AudioGroup audio-atmos_download-ap-aoc.tv.apple.com] [dvh1.05.06,ec-3] [VideoRange PQ]>]",
    "2026-05-24 20:14:05.902 Df VTDecoderXPCService[1290:2a5c3] CodecType: dvh1 (HW decoder), 3840 x 1606",
    "2026-05-24 20:14:06.120 Df TV[1287:2a4f9] AUDIO_FORMAT ec+3 is decodable ch=16",
)


def _printing(*lines: str) -> list[str]:
    """A stand-in for `log stream` that prints `lines` and exits."""
    return [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", "".join(f"{line}\n" for line in lines)]


def _wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for the capture"
        time.sleep(0.01)


def _event_count(capture: LogCapture) -> int:
    return capture.summarize()["event_count"]


# ---------------------------------------------------------------------------
# running totals


def _audio_capture_summary(total: int, cap: int):
    capture = LogCapture(raw_event_limit=cap)
    for idx in range(total):
        event = parse_line(f"AUDIO_FORMAT qaac is decodable ch={idx + 1}")
        assert event is not None
        capture.add_event(event)
    return capture.summarize()


def test_log_capture_raw_event_cap_under_limit_returns_all_events():
    summary = _audio_capture_summary(total=3, cap=5)

    assert summary["event_count"] == 3
    assert summary["events_returned"] == 3
    assert summary["events_dropped"] == 0
    assert summary["raw_event_limit"] == 5
    assert [event["channels"] for event in summary["events"]] == [1, 2, 3]


def test_log_capture_raw_event_cap_at_limit_returns_all_events():
    summary = _audio_capture_summary(total=5, cap=5)

    assert summary["event_count"] == 5
    assert summary["events_returned"] == 5
    assert summary["events_dropped"] == 0
    assert summary["raw_event_limit"] == 5
    assert [event["channels"] for event in summary["events"]] == [1, 2, 3, 4, 5]


def test_log_capture_raw_event_cap_over_limit_keeps_newest_events():
    summary = _audio_capture_summary(total=7, cap=5)

    assert summary["event_count"] == 7
    assert summary["events_returned"] == 5
    assert summary["events_dropped"] == 2
    assert summary["raw_event_limit"] == 5
    assert [event["channels"] for event in summary["events"]] == [3, 4, 5, 6, 7]


def test_log_capture_summary_keeps_best_audio_after_raw_event_drop():
    capture = LogCapture(raw_event_limit=2)
    for line in [
        "AUDIO_FORMAT ec+3 is decodable ch=16",
        "AUDIO_FORMAT qaac is decodable ch=2",
        "AUDIO_FORMAT qaac is decodable ch=2",
    ]:
        event = parse_line(line)
        assert event is not None
        capture.add_event(event)

    summary = capture.summarize()
    playback = summary["playback"]

    assert summary["event_count"] == 3
    assert summary["events_returned"] == 2
    assert summary["events_dropped"] == 1
    assert [event["channels"] for event in summary["events"]] == [2, 2]
    assert playback["audio"]["format"] == "qaac"
    assert playback["best_audio"]["format"] == "ec+3"
    assert playback["best_audio"]["channels"] == 16
    assert [audio["format"] for audio in playback["observed_audio"]] == ["ec+3", "qaac"]


def test_from_events_replays_like_a_live_capture():
    events = [parse_line(line) for line in LINES]
    capture = LogCapture.from_events(events, predicate='process == "TV"')
    summary = capture.summarize()

    assert summary["event_count"] == len(LINES)
    assert summary["predicate"] == 'process == "TV"'
    assert summary["started_at"] is None
    assert summary["playback"]["dolby_vision_active"] is True


# ---------------------------------------------------------------------------
# subprocess lifecycle


def test_default_command_is_log_stream_with_the_predicate():
    assert LogCapture().command == [
        "log",
        "stream",
        "--info",
        "--debug",
        "--style",
        "compact",
        "--predicate",
        PREDICATE,
    ]


def test_start_reports_a_missing_command_as_tool_error():
    capture = LogCapture(command=["/nonexistent/log-stream"])

    with pytest.raises(ToolError, match="could not start log-stream"):
        capture.start()


def test_events_reach_subscribers_on_the_running_loop():
    async def scenario():
        capture = LogCapture(command=_printing(*LINES))
        queue = capture.subscribe()
        capture.start()  # no loop given: called from async code, so the running loop is used
        events = [await asyncio.wait_for(queue.get(), timeout=10) for _ in LINES]
        return events, await asyncio.to_thread(capture.stop)

    events, summary = asyncio.run(scenario())

    assert [event["kind"] for event in events] == ["hls_variant", "codec_type", "audio_format"]
    assert all(isinstance(event["ts"], float) for event in events)
    assert summary["event_count"] == len(LINES)
    assert summary["playback"]["selected_hls_audio_group_kind"] == "atmos"
    assert summary["playback"]["dolby_vision_active"] is True


def test_sync_start_summarizes_without_broadcasting():
    capture = LogCapture(command=_printing(*LINES))
    queue = capture.subscribe()
    capture.start()
    _wait_until(lambda: _event_count(capture) == len(LINES))
    summary = capture.stop()

    assert queue.empty()
    assert summary["event_count"] == len(LINES)


def test_context_manager_starts_and_stops_the_capture():
    with LogCapture(command=_printing(*LINES)) as capture:
        _wait_until(lambda: _event_count(capture) == len(LINES))

    assert capture._proc is None
    assert capture.stop()["event_count"] == len(LINES)
