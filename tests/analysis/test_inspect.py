import subprocess

import pytest

from dolby_tool.analysis.inspect import InspectError, inspect_file
from dolby_tool.common import ToolError


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\0")
    return str(path)


def test_inspect_missing_file_raises():
    with pytest.raises(InspectError, match="not a file"):
        inspect_file("/nonexistent/clip.mp4")


def test_ffprobe_timeout_raises_inspect_error(media_file, monkeypatch):
    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr("dolby_tool.common.subprocess.run", hang)
    with pytest.raises(InspectError, match="ffprobe timed out") as excinfo:
        inspect_file(media_file)
    assert isinstance(excinfo.value.__cause__, ToolError)


def test_ffprobe_garbage_output_raises_inspect_error(media_file, monkeypatch):
    def garbage(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    monkeypatch.setattr("dolby_tool.common.subprocess.run", garbage)
    with pytest.raises(InspectError, match="invalid JSON"):
        inspect_file(media_file)
