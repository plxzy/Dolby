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
