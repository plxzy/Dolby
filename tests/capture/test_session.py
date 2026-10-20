"""`LogCapture`: running totals and the raw-event cap."""

from dolby_tool.capture import LogCapture, parse_line


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
