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


def _fake_ffprobe(streams: list[dict], frame_side_data: list[dict] | None = None):
    """Stand in for `ffprobe_json`, recording each call's arguments in `.calls`."""

    def fake(path, *args, timeout=60):
        fake.calls.append(args)
        if "-show_frames" in args:
            return {"frames": [{"side_data_list": frame_side_data or []}]}
        return {"format": {}, "streams": streams}

    fake.calls = []
    return fake


def test_cover_art_is_not_the_primary_video(media_file, monkeypatch):
    cover = {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}
    video = {"index": 1, "codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160}
    fake = _fake_ffprobe([cover, video])
    monkeypatch.setattr("dolby_tool.analysis.inspect.ffprobe_json", fake)

    spec = inspect_file(media_file)

    assert spec.video.codec == "hevc"
    assert spec.video.resolution == "3840x2160"
    frame_probe = next(args for args in fake.calls if "-show_frames" in args)
    assert frame_probe[frame_probe.index("-select_streams") + 1] == "1"


def test_file_with_only_cover_art_has_no_video(media_file, monkeypatch):
    cover = {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}}
    monkeypatch.setattr("dolby_tool.analysis.inspect.ffprobe_json", _fake_ffprobe([cover]))
    with pytest.raises(InspectError, match="no video stream"):
        inspect_file(media_file)


@pytest.mark.requires_ffmpeg
def test_mkv_with_attached_cover_inspects_the_real_video(media_clip, tmp_path):
    cover = tmp_path / "cover.jpg"
    cover_cmd = ["ffmpeg", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=red:size=300x300"]
    subprocess.run([*cover_cmd, "-frames:v", "1", str(cover)], check=True)
    clip = media_clip(
        "with_cover.mkv",
        "-c:v", "libx264", "-c:a", "aac",
        "-attach", str(cover), "-metadata:s:t", "mimetype=image/jpeg", "-metadata:s:t", "filename=cover.jpg",
    )  # fmt: skip

    spec = inspect_file(str(clip))

    assert (spec.video.codec, spec.video.resolution) == ("h264", "640x360")


_MDCV = {"side_data_type": "Mastering display metadata"}
_CLL = {"side_data_type": "Content light level metadata"}
_VIDEO = {"index": 0, "codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160}


def test_hdr10_found_in_stream_side_data(media_file, monkeypatch):
    # Container-level mdcv/clli boxes (MP4/MKV) are listed on the stream, not on any frame.
    stream = {**_VIDEO, "side_data_list": [_MDCV, _CLL]}
    monkeypatch.setattr("dolby_tool.analysis.inspect.ffprobe_json", _fake_ffprobe([stream]))

    hdr10 = inspect_file(media_file).video.hdr10

    assert (hdr10.mdcv, hdr10.cll) == (True, True)


def test_hdr10_found_in_frame_side_data(media_file, monkeypatch):
    monkeypatch.setattr("dolby_tool.analysis.inspect.ffprobe_json", _fake_ffprobe([_VIDEO], [_CLL]))

    hdr10 = inspect_file(media_file).video.hdr10

    assert (hdr10.mdcv, hdr10.cll) == (False, True)
    assert hdr10.present


def test_sdr_has_no_hdr10(media_file, monkeypatch):
    monkeypatch.setattr("dolby_tool.analysis.inspect.ffprobe_json", _fake_ffprobe([_VIDEO]))
    assert not inspect_file(media_file).video.hdr10.present
