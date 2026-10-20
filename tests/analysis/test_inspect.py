import subprocess

import pytest

from dolby_tool.analysis.inspect import InspectError, _dolby_vision, _fourcc_warning, inspect_file
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


def _dovi(profile: int, compat: int) -> dict:
    return {
        "side_data_type": "DOVI configuration record",
        "dv_profile": profile,
        "dv_bl_signal_compatibility_id": compat,
        "rpu_present_flag": 1,
        "bl_present_flag": 1,
    }


@pytest.mark.parametrize("profile", [5, 8])
def test_fourcc_warning_for_single_layer_profiles_suggests_dvh1_remux(profile):
    warning = _fourcc_warning("hvc1", _dolby_vision([_dovi(profile, 1)]))
    assert warning is not None
    assert "'hvc1'" in warning
    assert "-tag:v dvh1 -strict unofficial" in warning


def test_fourcc_warning_for_profile_7_asks_for_conversion_first():
    warning = _fourcc_warning("hvc1", _dolby_vision([_dovi(7, 6)]))
    assert warning is not None
    assert "profile 7" in warning
    assert "8.1" in warning
    assert "-strict unofficial" not in warning


@pytest.mark.parametrize(
    ("fourcc", "side_data"),
    [("dvh1", [_dovi(8, 1)]), ("hvc1", []), ("dvh1", [])],
    ids=["dv-fourcc", "no-dv", "dv-fourcc-without-dv"],
)
def test_no_fourcc_warning_when_nothing_is_broken(fourcc, side_data):
    assert _fourcc_warning(fourcc, _dolby_vision(side_data)) is None


def test_inspect_surfaces_the_warning(media_file, monkeypatch):
    stream = {**_VIDEO, "codec_tag_string": "hvc1", "side_data_list": [_dovi(7, 6)]}
    monkeypatch.setattr("dolby_tool.analysis.inspect.ffprobe_json", _fake_ffprobe([stream]))

    video = inspect_file(media_file).video

    assert video.dolby_vision.profile == "7"
    assert video.fourcc_warning is not None
    assert "profile 7" in video.fourcc_warning


_HDR10_X265 = (
    "hdr10=1:repeat-headers=1:log-level=error:max-cll=1000,400:"
    "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
)


@pytest.mark.requires_ffmpeg
def test_hdr10_hevc_with_eac3_clip(media_clip):
    clip = media_clip(
        "hdr10.mp4",
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
        "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
        "-x265-params", _HDR10_X265,
        "-c:a", "eac3", "-b:a", "640k", "-ac", "6",
    )  # fmt: skip

    spec = inspect_file(str(clip))

    assert spec.filename == "hdr10.mp4"
    video = spec.video
    assert (video.codec, video.fourcc, video.pix_fmt) == ("hevc", "hvc1", "yuv420p10le")
    assert (video.color_primaries, video.color_transfer, video.color_space) == ("bt2020", "smpte2084", "bt2020nc")
    assert (video.hdr10.mdcv, video.hdr10.cll) == (True, True)
    assert video.dolby_vision.model_dump() == {"present": False}
    assert not video.hdr10_plus
    assert video.fourcc_warning is None
    assert (video.resolution, video.fps) == ("640x360", 24.0)

    [audio] = spec.audio
    assert (audio.codec, audio.channels, audio.sample_rate_hz) == ("eac3", 6, 48000)
    assert audio.default
    assert audio.atmos in (False, None)  # plain E-AC-3: never Atmos, "unknown" without mediainfo
    assert spec.subtitles == []


@pytest.mark.requires_ffmpeg
def test_h264_aac_clip_with_a_subtitle_track(media_clip, tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,800\nHello\n", encoding="utf-8")
    clip = media_clip(
        "subs.mp4",
        "-map", "0:v", "-map", "1:a", "-map", "2:s",
        "-c:v", "libx264", "-c:a", "aac", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=eng",
        "-disposition:s:0", "default+hearing_impaired",
        inputs=(
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-i", str(srt),
        ),
    )  # fmt: skip

    spec = inspect_file(str(clip))

    assert (spec.video.codec, spec.video.fourcc) == ("h264", "avc1")
    assert not spec.video.hdr10.present
    assert spec.audio[0].codec == "aac"
    [subtitle] = spec.subtitles
    assert (subtitle.codec, subtitle.language) == ("mov_text", "eng")
    assert (subtitle.default, subtitle.hearing_impaired, subtitle.forced) == (True, True, False)
    assert subtitle.index == spec.audio[0].index + 1


@pytest.mark.requires_ffmpeg
def test_hevc_tagged_hvc1_is_not_reported_as_dolby_vision(media_clip):
    spec = inspect_file(str(media_clip("sdr_hevc.mp4", "-c:v", "libx265", "-tag:v", "hvc1", "-c:a", "aac")))

    assert spec.video.fourcc == "hvc1"
    assert spec.video.dolby_vision.present is False
    assert spec.video.fourcc_warning is None


@pytest.mark.requires_ffmpeg
def test_file_without_video_raises(media_clip):
    clip = media_clip("audio_only.m4a", "-vn", "-c:a", "aac")

    with pytest.raises(InspectError, match="no video stream"):
        inspect_file(str(clip))
