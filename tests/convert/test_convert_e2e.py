"""End-to-end mux with real ffmpeg: an UHD-remux-like MKV becomes an Apple-friendly MP4."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from dolby_tool.common import ffprobe_json
from dolby_tool.convert import mux
from dolby_tool.convert.commands import main_convert

pytestmark = pytest.mark.requires_ffmpeg

SRT = "1\n00:00:00,000 --> 00:00:00,800\nHello\n"


@pytest.fixture
def remux_like_mkv(media_clip: Callable[..., Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    """HEVC + TrueHD + E-AC-3 + SRT, the shape of a typical Blu-ray remux."""
    srt = tmp_path_factory.mktemp("srt") / "subs.srt"
    srt.write_text(SRT)
    return media_clip(
        "remux_like.mkv",
        "-map", "0:v", "-map", "1:a", "-map", "1:a", "-map", "2:s",
        "-c:v", "libx265", "-x265-params", "log-level=none",
        "-c:a:0", "truehd", "-strict", "-2", "-metadata:s:a:0", "title=TrueHD",
        "-c:a:1", "eac3", "-metadata:s:a:1", "language=eng",
        "-c:s", "srt",
        inputs=(
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-i", str(srt),
        ),
    )  # fmt: skip


def streams_of(path: Path) -> list[dict]:
    return ffprobe_json(path, "-show_streams")["streams"]


def test_mux_produces_an_apple_friendly_mp4(remux_like_mkv: Path, tmp_path: Path) -> None:
    summary = mux.mux(remux_like_mkv, tmp_path / "out.m4v")

    video, *rest = streams_of(tmp_path / "out.m4v")
    assert (video["codec_name"], video["codec_tag_string"]) == ("hevc", "hvc1")
    assert [(s["codec_type"], s["codec_name"]) for s in rest] == [("audio", "eac3"), ("subtitle", "mov_text")]
    assert summary["video_tag"] == "hvc1"
    assert [s["codec"] for s in summary["audio_skipped"]] == ["truehd"]


def test_convert_command_cli_json(remux_like_mkv: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli.m4v"
    main_convert([str(remux_like_mkv), str(out), "--no-metadata", "--overwrite", "--json"])

    summary = json.loads(capsys.readouterr().out)
    assert summary["mux"]["audio_skipped"][0]["codec"] == "truehd"
    assert out.is_file()
