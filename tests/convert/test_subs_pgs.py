"""PGS → VobSub second pass: command building and failure handling, with every tool faked."""

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from dolby_tool.common import ToolError
from dolby_tool.convert import mux, subs_pgs

PGS = [mux.StreamInfo(index=3, codec="hdmv_pgs_subtitle", codec_type="subtitle", language="eng")]


def first_pass_probe(*, dv_profile: int | None = 8) -> mux.ProbeResult:
    video: dict[str, Any] = {"index": 0, "codec_type": "video", "codec_name": "hevc"}
    if dv_profile is not None:
        video["side_data_list"] = [{"side_data_type": "DOVI configuration record", "dv_profile": dv_profile}]
    streams = [video, {"index": 1, "codec_type": "audio", "codec_name": "eac3"}, {"index": 2, "codec_type": "subtitle"}]
    infos = [mux.StreamInfo(s["index"], s.get("codec_name", ""), s["codec_type"], raw=s) for s in streams]
    return mux.ProbeResult(
        video=infos[0],
        audios=[infos[1]],
        text_subs=[infos[2]],
        bitmap_subs=[],
        has_chapters=False,
        raw={"streams": streams},
    )


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    source, target = tmp_path / "movie.mkv", tmp_path / "movie.m4v"
    source.write_bytes(b"")
    target.write_bytes(b"first pass")
    return source, target, tmp_path / "movie.pgs.m4v"


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fake bdsup2sub and ffmpeg: each command is recorded and 'writes' its output."""
    calls: list[list[str]] = []

    def run_tool(cmd: Sequence[str], *, timeout: float | None = None, check: bool = True):
        calls.append(list(cmd))
        if cmd[0] == "bdsup2sub":
            idx = Path(cmd[2])
            idx.write_text("idx")
            idx.with_suffix(".sub").write_text("sub")
        else:
            Path(cmd[-1]).write_bytes(b"data")
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(subs_pgs, "run_tool", run_tool)
    monkeypatch.setattr(subs_pgs, "which_any", lambda *names: "bdsup2sub")
    monkeypatch.setattr(subs_pgs, "probe", lambda path: first_pass_probe())
    return calls


def test_output_is_required_and_must_differ_from_target(files, tools) -> None:
    source, target, _ = files
    with pytest.raises(TypeError):
        subs_pgs.convert_and_remux(source, target, PGS)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="different file"):
        subs_pgs.convert_and_remux(source, target, PGS, output=target)
    assert tools == []


def test_remux_keeps_the_dolby_vision_tag_and_offsets_language_metadata(files, tools) -> None:
    source, target, output = files
    result = subs_pgs.convert_and_remux(source, target, PGS, output=output)

    assert (result.converted, result.skipped, result.reason) == (1, 0, None)
    remux = tools[-1]
    assert remux[remux.index("-tag:v") + 1] == "dvh1"
    assert remux[remux.index("-strict") + 1] == "unofficial"
    # one subtitle already lives in the first-pass file, so the new track is s:1
    assert "-metadata:s:s:1" in remux
    assert output.exists()


def test_probe_failure_is_reported_instead_of_guessing_indices(files, tools, monkeypatch) -> None:
    source, target, output = files

    def broken_probe(path: Path) -> mux.ProbeResult:
        raise ToolError("ffprobe failed: moov atom not found")

    monkeypatch.setattr(subs_pgs, "probe", broken_probe)
    result = subs_pgs.convert_and_remux(source, target, PGS, output=output)

    assert result.converted == 0
    assert result.skipped == 1
    assert "moov atom not found" in (result.reason or "")
    assert not any("-metadata:s:s:0" in cmd for cmd in tools)
    assert not output.exists()


def test_failed_remux_leaves_no_partial_output(files, tools, monkeypatch) -> None:
    source, target, output = files
    run_tool = subs_pgs.run_tool

    def failing_remux(cmd: Sequence[str], **kwargs: Any):
        if cmd[0] == "ffmpeg" and Path(cmd[-1]) == output:
            Path(cmd[-1]).write_bytes(b"partial")
            raise ToolError("ffmpeg failed: Invalid data", cmd=cmd, returncode=1)
        return run_tool(cmd, **kwargs)

    monkeypatch.setattr(subs_pgs, "run_tool", failing_remux)
    result = subs_pgs.convert_and_remux(source, target, PGS, output=output)

    assert result.converted == 0
    assert "Invalid data" in (result.reason or "")
    assert not output.exists()


def test_missing_bdsup2sub_skips_everything(files, monkeypatch) -> None:
    source, target, output = files
    monkeypatch.setattr(subs_pgs, "which_any", lambda *names: None)
    result = subs_pgs.convert_and_remux(source, target, PGS, output=output)
    assert (result.converted, result.skipped) == (0, 1)
    assert "bdsup2sub" in (result.reason or "")
