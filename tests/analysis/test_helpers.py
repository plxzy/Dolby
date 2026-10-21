import pytest

from dolby_tool.analysis.inspect import (
    _avg_frame_rate,
    _detect_atmos,
    _dv_profile_label,
    _pretty_duration,
    _pretty_size,
)


@pytest.mark.parametrize(
    ("profile", "compat", "label"),
    [(8, 1, "8.1"), (8, 4, "8.4"), (8, None, "8"), (5, 0, "5"), (7, 6, "7"), (None, 1, None)],
)
def test_dv_profile_label(profile, compat, label):
    assert _dv_profile_label(profile, compat) == label


@pytest.mark.parametrize(
    ("fraction", "fps"),
    [("24000/1001", 23.976), ("25/1", 25.0), ("0/0", None), ("30", None), (None, None), ("a/b", None)],
)
def test_avg_frame_rate(fraction, fps):
    assert _avg_frame_rate(fraction) == fps


@pytest.mark.parametrize(
    ("size", "pretty"),
    [
        (None, None),
        (0, None),
        (512, "512.00 B"),
        (1536, "1.50 KB"),
        (5 * 1024**3, "5.00 GB"),
        (2 * 1024**5, "2.00 PB"),
    ],
)
def test_pretty_size(size, pretty):
    assert _pretty_size(size) == pretty


@pytest.mark.parametrize(
    ("seconds", "pretty"),
    [(None, None), (0, None), (59.9, "0m 59s"), (125, "2m 05s"), (3723, "1h 02m 03s")],
)
def test_pretty_duration(seconds, pretty):
    assert _pretty_duration(seconds) == pretty


@pytest.mark.parametrize(
    ("stream", "mediainfo", "expected"),
    [
        ({"codec_name": "eac3", "profile": "Dolby Digital Plus + Dolby Atmos"}, None, True),
        ({"codec_name": "eac3", "profile": "JOC"}, None, True),
        ({"codec_name": "eac3"}, {"Format_AdditionalFeatures": "JOC"}, True),
        ({"codec_name": "truehd"}, {"Format_Commercial_IfAny": "Dolby TrueHD with Dolby Atmos"}, True),
        ({"codec_name": "eac3"}, {"Format_Commercial_IfAny": "Dolby Digital Plus"}, False),
        ({"codec_name": "eac3", "channels": 8, "channel_layout": "7.1 (objects)"}, None, True),
        ({"codec_name": "eac3", "channels": 6, "channel_layout": "5.1(side)"}, None, None),
        ({"codec_name": "aac"}, None, None),
        ({"codec_name": "eac3"}, {}, None),
    ],
)
def test_detect_atmos(stream, mediainfo, expected):
    assert _detect_atmos(stream, mediainfo) is expected
