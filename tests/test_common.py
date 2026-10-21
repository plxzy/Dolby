import sys

import pytest

from dolby_tool.common import ToolError, ffprobe_json, run_tool, to_float, to_int, unique, which_any


@pytest.mark.parametrize(("value", "expected"), [("42", 42), (7, 7), (None, None), ("n/a", None), ([], None)])
def test_to_int(value, expected):
    assert to_int(value) == expected


@pytest.mark.parametrize(("value", "expected"), [("23.976", 23.976), (2, 2.0), (None, None), ("x", None)])
def test_to_float(value, expected):
    assert to_float(value) == expected


def test_unique_keeps_first_seen_order():
    assert unique(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_which_any_returns_first_available():
    assert which_any("definitely-not-a-real-binary", "python3", "sh") is not None
    assert which_any("definitely-not-a-real-binary") is None


def test_run_tool_captures_output_and_detaches_stdin():
    result = run_tool([sys.executable, "-c", "import sys; print(sys.stdin.read() == '')"])
    assert result.stdout.strip() == "True"


def test_run_tool_reports_last_stderr_line():
    script = "import sys; sys.stderr.write('first\\nboom: bad input\\n'); sys.exit(3)"
    with pytest.raises(ToolError, match="failed: boom: bad input") as exc:
        run_tool([sys.executable, "-c", script])
    assert exc.value.returncode == 3


def test_run_tool_decodes_invalid_utf8_leniently():
    result = run_tool([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff')"])
    assert result.stdout.startswith("ok ")


def test_run_tool_missing_binary_and_timeout():
    with pytest.raises(ToolError, match="command not found"):
        run_tool(["definitely-not-a-real-binary"])
    with pytest.raises(ToolError, match="timed out"):
        run_tool([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)


@pytest.mark.requires_ffmpeg
def test_ffprobe_json_reads_streams(media_clip):
    clip = media_clip("probe.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    data = ffprobe_json(clip, "-show_streams")
    assert [s["codec_type"] for s in data["streams"]] == ["video", "audio"]
