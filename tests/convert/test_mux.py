"""ffmpeg command building in `mux`: stream selection, default audio, tags and subtitles.

ffprobe and ffmpeg are faked: `probe_streams` sets what the source contains and
the `ffmpeg` fixture records every command instead of running it.
"""

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from dolby_tool.common import ToolError
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


# --------------------------------------------------------------------- video tag


def hevc(dv_profile: int | None = None) -> dict[str, Any]:
    stream: dict[str, Any] = {"index": 0, "codec_type": "video", "codec_name": "hevc"}
    if dv_profile is not None:
        stream["side_data_list"] = [
            {"side_data_type": "Mastering display metadata"},
            {"side_data_type": "DOVI configuration record", "dv_profile": dv_profile, "dv_level": 6},
        ]
    return stream


@pytest.mark.parametrize(
    ("video", "fourcc", "strict", "dolby_vision"),
    [
        (VIDEO, None, None, None),
        (hevc(), "hvc1", None, None),
        (hevc(8), "dvh1", "unofficial", "kept (profile 8, dvh1)"),
        (hevc(5), "dvh1", "unofficial", "kept (profile 5, dvh1)"),
        (hevc(7), "hvc1", None, "dropped (profile 7 is not supported by Apple players)"),
    ],
    ids=["h264", "hevc", "dv-p8", "dv-p5", "dv-p7"],
)
def test_video_sample_entry_tag(video, fourcc, strict, dolby_vision, source, ffmpeg, probe_streams) -> None:
    probe_streams(video, audio(1, "eac3"))
    summary = run_mux(source)

    (cmd,) = ffmpeg
    assert values(cmd, "-tag:v") == ([fourcc] if fourcc else [])
    assert values(cmd, "-strict") == ([strict] if strict else [])
    assert summary["video_tag"] == fourcc
    assert summary["dolby_vision"] == dolby_vision


# --------------------------------------------------------------------- non-primary titles


@pytest.mark.parametrize(
    ("title", "non_primary"),
    [
        ("Director's Commentary", True),
        ("Commentary with the cast", True),
        ("English (AD)", True),
        ("English AD", True),
        ("Audio Description", True),
        ("English SDH", True),
        ("Cast & Crew", True),
        ("Director's Cut", False),
        ("Broadcast Mix", False),
        ("Cast Away 5.1", False),
        ("Dad Joke", False),
        ("Surround", False),
        (None, False),
    ],
)
def test_non_primary_title_matches_whole_words_only(title, non_primary) -> None:
    stream = mux.StreamInfo(index=1, codec="eac3", codec_type="audio", title=title)
    assert mux._is_non_primary(stream) is non_primary


def test_commentary_track_is_not_the_default_even_if_better_codec(source, ffmpeg, probe_streams) -> None:
    probe_streams(VIDEO, audio(1, "eac3", tags={"title": "Director's Commentary"}), audio(2, "aac"))
    assert run_mux(source)["default_audio_index"] == 1


def test_broadcast_title_does_not_demote_a_track(source, ffmpeg, probe_streams) -> None:
    probe_streams(VIDEO, audio(1, "eac3", tags={"title": "Broadcast Mix"}), audio(2, "aac"))
    assert run_mux(source)["default_audio_index"] == 0


# --------------------------------------------------------------------- subtitles


def test_only_convertible_subtitles_are_mapped(source, ffmpeg, probe_streams) -> None:
    probe_streams(
        VIDEO,
        audio(1, "eac3"),
        subtitle(2, "subrip"),
        subtitle(3, "ass"),
        subtitle(4, "hdmv_pgs_subtitle"),
        subtitle(5, "hdmv_text_subtitle"),
        subtitle(6, "dvb_subtitle"),
        subtitle(7, "dvd_subtitle"),
        subtitle(8, "eia_608"),
    )
    summary = run_mux(source)

    (cmd,) = ffmpeg
    assert values(cmd, "-map") == ["0:0", "0:1", "0:2?", "0:3?"]
    assert values(cmd, "-c:s:0") == values(cmd, "-c:s:1") == ["mov_text"]
    assert summary["text_subs_included"] == 2
    assert summary["bitmap_subs_dropped"] == 1
    assert [(s["index"], s["codec"]) for s in summary["subs_skipped"]] == [
        (5, "hdmv_text_subtitle"),
        (6, "dvb_subtitle"),
        (7, "dvd_subtitle"),
        (8, "eia_608"),
    ]
    assert all(s["reason"] for s in summary["subs_skipped"])


def test_pgs_passthrough_keeps_only_pgs(source, ffmpeg, probe_streams) -> None:
    probe_streams(VIDEO, audio(1, "eac3"), subtitle(2, "hdmv_pgs_subtitle"), subtitle(3, "dvb_subtitle"))
    summary = mux.mux(source, source.with_suffix(".m4v"), include_bitmap_subs_as_passthrough=True)

    assert values(ffmpeg[0], "-map") == ["0:0", "0:1", "0:2?"]
    assert values(ffmpeg[0], "-c:s:0") == ["copy"]
    assert summary["bitmap_subs_dropped"] == 0
    assert [s["index"] for s in summary["subs_skipped"]] == [3]


# --------------------------------------------------------------------- failure cleanup


def test_partial_output_is_deleted_when_ffmpeg_fails(source, probe_streams, monkeypatch) -> None:
    probe_streams(VIDEO, audio(1, "eac3"))
    output = source.with_suffix(".m4v")

    def failing_run_tool(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"half a file")
        raise ToolError("ffmpeg failed: boom", cmd=cmd, returncode=1)

    monkeypatch.setattr(mux, "run_tool", failing_run_tool)
    with pytest.raises(ToolError, match="boom"):
        mux.mux(source, output)
    assert not output.exists()


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_mux_rejects_input_aliases_before_running_tools(source, ffmpeg, alias) -> None:
    source.write_bytes(b"original media")
    output = source if alias == "same" else source.with_suffix(".m4v")
    if alias == "symlink":
        output.symlink_to(source)
    elif alias == "hardlink":
        output.hardlink_to(source)
    with pytest.raises(ValueError, match="different files"):
        mux.mux(source, output, overwrite=True)
    assert source.read_bytes() == b"original media"
    assert output.read_bytes() == b"original media"
    assert ffmpeg == []


def test_failure_cleanup_preserves_preexisting_output(tmp_path) -> None:
    output = tmp_path / "existing.m4v"
    output.write_bytes(b"previous conversion")
    with pytest.raises(ToolError), mux.discard_on_failure(output):
        raise ToolError("rejected before writing")
    assert output.read_bytes() == b"previous conversion"
