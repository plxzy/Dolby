import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dolby_tool.common import ToolError
from dolby_tool.hls.package import package_hls_from_file

DV_VIDEO = {
    "codec_type": "video",
    "codec_tag_string": "dvh1",
    "avg_frame_rate": "24000/1001",
    "color_transfer": "smpte2084",
    "color_primaries": "bt2020",
}
STEREO_AAC = {"codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a", "channels": 2}
ATMOS_EAC3 = {
    "codec_type": "audio",
    "codec_name": "eac3",
    "codec_tag_string": "ec-3",
    "profile": "Dolby Digital Plus + Dolby Atmos",
    "channels": 6,
    "tags": {"language": "eng"},
}


def dovi(profile: int, level: int, compatibility_id: int) -> dict[str, Any]:
    """ffprobe's side-data entry for a Dolby Vision configuration record."""
    return {
        "side_data_type": "DOVI configuration record",
        "dv_version_major": 1,
        "dv_version_minor": 0,
        "dv_profile": profile,
        "dv_level": level,
        "rpu_present_flag": 1,
        "el_present_flag": 0,
        "bl_present_flag": 1,
        "dv_bl_signal_compatibility_id": compatibility_id,
    }


@dataclass
class FakeTools:
    """Stands in for ffprobe and ffmpeg at the `subprocess.run` boundary used by `common.run_tool`.

    ffprobe answers with `streams`; ffmpeg writes a small split-audio HLS tree next to the
    playlist path it was given (or fails with `ffmpeg_stderr` when `ffmpeg_returncode` is set).
    """

    streams: list[dict[str, Any]]
    ffmpeg_returncode: int = 0
    ffmpeg_stderr: str = "ffmpeg stderr"
    calls: list[list[str]] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)

    def run(self, cmd: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        self.options.append(options)
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"streams": self.streams}), "")
        if self.ffmpeg_returncode:
            return subprocess.CompletedProcess(cmd, self.ffmpeg_returncode, "", self.ffmpeg_stderr)
        _write_split_hls(Path(cmd[-1]).parent)
        return subprocess.CompletedProcess(cmd, 0, "", self.ffmpeg_stderr)

    @property
    def ffmpeg_cmd(self) -> list[str]:
        return next(cmd for cmd in self.calls if cmd[0] == "ffmpeg")


def _write_split_hls(output: Path) -> None:
    output.mkdir(exist_ok=True)
    (output / "master.m3u8").write_text(
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="group_audio",URI="stream_English.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="group_audio"\nstream_video.m3u8\n',
        encoding="utf-8",
    )
    (output / "stream_video.m3u8").write_text(
        '#EXTM3U\n#EXT-X-MAP:URI="init_0.mp4"\nstream_video_0000.m4s\n',
        encoding="utf-8",
    )
    (output / "stream_English.m3u8").write_text(
        '#EXTM3U\n#EXT-X-MAP:URI="init_1.mp4"\nstream_English_0000.m4s\n',
        encoding="utf-8",
    )
    (output / "init_0.mp4").write_bytes(b"vinit")
    (output / "init_1.mp4").write_bytes(b"ainit")
    (output / "stream_video_0000.m4s").write_bytes(b"video")
    (output / "stream_English_0000.m4s").write_bytes(b"audio")


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    tools = FakeTools(streams=[DV_VIDEO, STEREO_AAC, ATMOS_EAC3])
    monkeypatch.setattr("dolby_tool.common.subprocess.run", tools.run)
    return tools


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "input.mp4"
    path.write_bytes(b"media")
    return path


def test_package_hls_from_file_invokes_ffmpeg_for_split_audio_group(tmp_path, source, fake_tools):
    output = tmp_path / "out"

    summary = package_hls_from_file(source, output, audio_stream=1, segment_time=4)

    cmd = fake_tools.ffmpeg_cmd
    assert "-map" in cmd
    assert "0:a:1" in cmd
    assert "-hls_segment_type" in cmd
    assert "fmp4" in cmd
    assert "v:0,agroup:audio,name:video a:0,agroup:audio,language:eng,name:English,default:yes" in cmd
    assert summary["split_audio_group"] is True
    assert summary["ffmpeg_stderr_tail"] == ["ffmpeg stderr"]
    assert summary["master_playlist_tags"] == {
        "audio_channels": "6/JOC",
        "codecs": "dvh1.05.06,ec-3",
        "frame_rate": "23.976",
        "language": "en",
        "video_range": "PQ",
    }
    assert (output / "master.m3u8").read_text(encoding="utf-8") == (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="group_audio",URI="stream_English.m3u8",'
        'LANGUAGE="en",NAME="English",AUTOSELECT=YES,CHANNELS="6/JOC"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="group_audio",'
        'FRAME-RATE=23.976,CODECS="dvh1.05.06,ec-3",VIDEO-RANGE=PQ\n'
        "stream_video.m3u8\n"
    )
    assert summary["playlists"] == [
        {"playlist": "master.m3u8", "segments": 0, "init_maps": 0, "bytes": 0},
        {"playlist": "stream_English.m3u8", "segments": 1, "init_maps": 1, "bytes": 10},
        {"playlist": "stream_video.m3u8", "segments": 1, "init_maps": 1, "bytes": 10},
    ]


def test_package_hls_from_file_rejects_non_empty_output_without_overwrite(tmp_path, source, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    (output / "old.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("ffmpeg should not run")

    monkeypatch.setattr("dolby_tool.common.subprocess.run", fail_run)

    with pytest.raises(ValueError, match="Output directory is not empty"):
        package_hls_from_file(source, output)


def test_package_hls_from_file_runs_tools_detached_from_stdin(tmp_path, source, fake_tools):
    package_hls_from_file(source, tmp_path / "out")

    assert "-nostdin" in fake_tools.ffmpeg_cmd
    assert [cmd[0] for cmd in fake_tools.calls] == ["ffprobe", "ffmpeg"]
    assert all(options["stdin"] is subprocess.DEVNULL for options in fake_tools.options)


def test_package_hls_from_file_reports_ffmpeg_failures_readably(tmp_path, source, fake_tools):
    fake_tools.ffmpeg_returncode = 1
    fake_tools.ffmpeg_stderr = "Input #0, mov,mp4\n[hls @ 0x1] Invalid segment filename template\n"

    with pytest.raises(ToolError, match=r"ffmpeg failed: .*Invalid segment filename template") as excinfo:
        package_hls_from_file(source, tmp_path / "out")

    assert excinfo.value.returncode == 1


def _write_files(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"old")


def test_overwrite_refuses_a_folder_holding_unrelated_files(tmp_path, source, fake_tools):
    output = tmp_path / "Movies"
    _write_files(output, "master.m3u8", "stream_video_0000.m4s", "holiday.mov")
    (output / "Photos").mkdir()

    with pytest.raises(ValueError, match=r"does not create: Photos, holiday\.mov"):
        package_hls_from_file(source, output, overwrite=True)

    assert sorted(path.name for path in output.iterdir()) == [
        "Photos",
        "holiday.mov",
        "master.m3u8",
        "stream_video_0000.m4s",
    ]
    assert all(cmd[0] != "ffmpeg" for cmd in fake_tools.calls)


def test_overwrite_refuses_an_output_folder_containing_the_source(tmp_path, fake_tools):
    output = tmp_path / "out"
    _write_files(output, "input.mp4", "master.m3u8")

    with pytest.raises(ValueError, match="inside the output directory"):
        package_hls_from_file(output / "input.mp4", output, overwrite=True)

    assert (output / "input.mp4").read_bytes() == b"old"
    assert (output / "master.m3u8").exists()


def test_overwrite_keeps_earlier_output_when_probing_fails(tmp_path, source, fake_tools):
    output = tmp_path / "out"
    _write_files(output, "master.m3u8", "stream_video.m3u8", "init_0.mp4")
    fake_tools.streams = [STEREO_AAC]

    with pytest.raises(ValueError, match="No video stream"):
        package_hls_from_file(source, output, overwrite=True)

    assert sorted(path.name for path in output.iterdir()) == ["init_0.mp4", "master.m3u8", "stream_video.m3u8"]


def test_overwrite_replaces_only_generated_files(tmp_path, source, fake_tools):
    output = tmp_path / "out"
    # A previous `--combined` run, plus the metadata file Finder leaves behind.
    _write_files(output, "master.m3u8", "stream_main.m3u8", "stream_main_0000.m4s", "init.mp4", ".DS_Store")

    package_hls_from_file(source, output, overwrite=True)

    assert sorted(path.name for path in output.iterdir()) == [
        ".DS_Store",
        "init_0.mp4",
        "init_1.mp4",
        "master.m3u8",
        "stream_English.m3u8",
        "stream_English_0000.m4s",
        "stream_video.m3u8",
        "stream_video_0000.m4s",
    ]
    assert (output / ".DS_Store").read_bytes() == b"old"


@pytest.mark.requires_ffmpeg
def test_package_round_trip_with_real_ffmpeg(tmp_path, media_clip):
    clip = media_clip("hls_h264_aac.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    output = tmp_path / "hls"

    split = package_hls_from_file(clip, output, segment_time=1)
    with pytest.raises(ValueError, match="Output directory is not empty"):
        package_hls_from_file(clip, output)
    combined = package_hls_from_file(clip, output, overwrite=True, segment_time=1, split_audio_group=False)

    assert [row["playlist"] for row in split["playlists"]] == [
        "master.m3u8",
        "stream_English.m3u8",
        "stream_video.m3u8",
    ]
    assert [row["playlist"] for row in combined["playlists"]] == ["master.m3u8", "stream_main.m3u8"]
    # Nothing from the split run survives the combined one.
    segments = combined["playlists"][1]["segments"]
    assert {path.name for path in output.iterdir()} == {
        "init.mp4",
        "master.m3u8",
        "stream_main.m3u8",
        *(f"stream_main_{index:04d}.m4s" for index in range(segments)),
    }


@pytest.mark.parametrize(
    ("video", "expected"),
    [
        pytest.param({"color_transfer": "smpte2084", "color_primaries": "bt2020"}, "PQ", id="hdr10"),
        pytest.param({"color_transfer": "arib-std-b67", "color_primaries": "bt2020"}, "HLG", id="hlg"),
        pytest.param({"color_transfer": "bt709", "color_primaries": "bt2020"}, "SDR", id="sdr-wide-gamut"),
        pytest.param({}, "SDR", id="untagged"),
        pytest.param({"side_data_list": [dovi(5, 6, 0)]}, "PQ", id="dolby-vision-5"),
        pytest.param({"side_data_list": [dovi(8, 6, 4)]}, "HLG", id="dolby-vision-8.4"),
        pytest.param({"color_transfer": "smpte2084", "side_data_list": [dovi(8, 6, 1)]}, "PQ", id="dolby-vision-8.1"),
    ],
)
def test_video_range_follows_the_transfer_function(tmp_path, source, fake_tools, video, expected):
    fake_tools.streams = [{"codec_type": "video", "codec_tag_string": "hvc1", **video}, ATMOS_EAC3]
    output = tmp_path / "out"

    summary = package_hls_from_file(source, output)

    assert summary["master_playlist_tags"]["video_range"] == expected
    assert f"VIDEO-RANGE={expected}\n" in (output / "master.m3u8").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("video", "expected"),
    [
        pytest.param({"codec_tag_string": "dvh1", "side_data_list": [dovi(8, 6, 1)]}, "dvh1.08.06", id="dvh1-8.1"),
        pytest.param({"codec_tag_string": "dvhe", "side_data_list": [dovi(5, 9, 0)]}, "dvhe.05.09", id="dvhe-5"),
        pytest.param({"codec_tag_string": "dvh1"}, "dvh1.05.06", id="no-record-falls-back"),
        pytest.param({"codec_tag_string": "hvc1", "side_data_list": [dovi(8, 6, 1)]}, "hvc1", id="hvc1-tagged"),
    ],
)
def test_dolby_vision_codec_comes_from_the_configuration_record(tmp_path, source, fake_tools, video, expected):
    fake_tools.streams = [{"codec_type": "video", **video}, ATMOS_EAC3]

    summary = package_hls_from_file(source, tmp_path / "out")

    assert summary["master_playlist_tags"]["codecs"] == f"{expected},ec-3"


def test_cover_art_is_not_taken_for_the_video_stream(tmp_path, source, fake_tools):
    cover = {
        "codec_type": "video",
        "codec_name": "mjpeg",
        "codec_tag_string": "[0][0][0][0]",
        "r_frame_rate": "90000/1",
        "disposition": {"default": 0, "attached_pic": 1},
    }
    fake_tools.streams = [cover, DV_VIDEO, ATMOS_EAC3]

    summary = package_hls_from_file(source, tmp_path / "out")

    assert summary["master_playlist_tags"]["codecs"] == "dvh1.05.06,ec-3"
    assert summary["master_playlist_tags"]["frame_rate"] == "23.976"
    cmd = fake_tools.ffmpeg_cmd
    assert cmd[cmd.index("-map") + 1] == "0:V:0"


@pytest.mark.requires_ffmpeg
def test_hlg_clip_is_labelled_hlg(tmp_path, media_clip):
    clip = media_clip(
        "hls_hevc_hlg.mp4",
        "-c:v", "libx265", "-x265-params", "log-level=error", "-tag:v", "hvc1",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
        "-c:a", "eac3",
    )  # fmt: skip

    summary = package_hls_from_file(clip, tmp_path / "hls", segment_time=1)

    assert summary["master_playlist_tags"]["video_range"] == "HLG"
    assert summary["master_playlist_tags"]["codecs"] == "hvc1,ec-3"
