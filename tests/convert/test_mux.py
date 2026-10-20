"""ffmpeg command building in `mux`: stream selection, default audio, tags and subtitles.

ffprobe and ffmpeg are faked: `probe_streams` sets what the source contains and
the `ffmpeg` fixture records every command instead of running it.
"""

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from dolby_tool.convert import mux

VIDEO = {"index": 0, "codec_type": "video", "codec_name": "h264"}


def audio(index: int, codec: str, *, channels: int = 6, language: str = "eng", **extra: Any) -> dict[str, Any]:
    return {
        "index": index,
        "codec_type": "audio",
        "codec_name": codec,
        "channels": channels,
        "tags": {"language": language, **extra.pop("tags", {})},
        **extra,
    }


def subtitle(index: int, codec: str) -> dict[str, Any]:
    return {"index": index, "codec_type": "subtitle", "codec_name": codec}


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"")
    return path


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record ffmpeg commands; each run 'writes' its output file."""
    calls: list[list[str]] = []

    def run_tool(cmd: Sequence[str], *, timeout: float | None = None, check: bool = True):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"fake mp4")
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(mux, "run_tool", run_tool)
    return calls


@pytest.fixture
def probe_streams(monkeypatch: pytest.MonkeyPatch):
    def set_streams(*streams: dict[str, Any]) -> None:
        payload = {"streams": list(streams), "chapters": [], "format": {}}
        monkeypatch.setattr(mux, "ffprobe_json", lambda path, *args, **kwargs: payload)

    return set_streams


def values(cmd: list[str], flag: str) -> list[str]:
    """Every value passed after `flag` in an ffmpeg command."""
    return [cmd[i + 1] for i, arg in enumerate(cmd[:-1]) if arg == flag]


def run_mux(source: Path) -> dict[str, Any]:
    return mux.mux(source, source.with_suffix(".m4v"))


# --------------------------------------------------------------------- audio


def test_skips_audio_apple_cannot_play(source, ffmpeg, probe_streams) -> None:
    probe_streams(
        VIDEO,
        audio(1, "truehd", channels=8, disposition={"default": 1}),
        audio(2, "eac3"),
        audio(3, "dts"),
        audio(4, "pcm_s24le"),
    )
    summary = run_mux(source)

    (cmd,) = ffmpeg
    assert values(cmd, "-map") == ["0:0", "0:2"]
    assert summary["audio_streams"] == [{"codec": "eac3", "language": "eng"}]
    assert summary["default_audio_index"] == 0
    assert [(s["index"], s["codec"]) for s in summary["audio_skipped"]] == [(1, "truehd"), (3, "dts"), (4, "pcm_s24le")]
    assert all(s["reason"] for s in summary["audio_skipped"])


def test_refuses_when_no_playable_audio_remains(source, ffmpeg, probe_streams) -> None:
    probe_streams(VIDEO, audio(1, "truehd"), audio(2, "dts"))
    with pytest.raises(ValueError, match=r"#1 truehd, #2 dts"):
        run_mux(source)
    assert ffmpeg == []


def test_video_only_source_still_muxes(source, ffmpeg, probe_streams) -> None:
    probe_streams(VIDEO)
    summary = run_mux(source)
    assert values(ffmpeg[0], "-map") == ["0:0"]
    assert summary["audio_streams"] == []


@pytest.mark.parametrize(
    ("codecs", "best"),
    [
        (["aac", "ac3", "eac3"], "eac3"),
        (["ac4", "eac3"], "eac3"),
        (["ac3", "flac"], "flac"),
        (["aac", "ac3"], "ac3"),
    ],
)
def test_default_audio_prefers_atmos_capable_codecs(codecs, best, source, ffmpeg, probe_streams) -> None:
    probe_streams(VIDEO, *(audio(i, codec) for i, codec in enumerate(codecs, start=1)))
    summary = run_mux(source)
    assert codecs[summary["default_audio_index"]] == best
    dispositions = values(ffmpeg[0], f"-disposition:a:{summary['default_audio_index']}")
    assert dispositions == ["default"]
