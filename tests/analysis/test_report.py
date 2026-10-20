from dolby_tool.analysis import compare_files, compare_markdown, spec_markdown
from dolby_tool.analysis.models import AudioTrack, SubtitleTrack


def _dv_spec(make_spec, **kwargs):
    spec = make_spec("movie|dv.mkv", dv="8.1", fourcc="dvh1", hdr10=True, hdr10_plus=True, atmos=True, **kwargs)
    return spec.model_copy(
        update={
            "audio": [AudioTrack(index=1, codec="eac3", channels=6, channel_layout="5.1(side)", atmos=True)],
            "subtitles": [SubtitleTrack(index=2, codec="subrip", language="eng", forced=True)],
        }
    )


def test_spec_markdown_verdict_and_sections(make_spec):
    text = spec_markdown(_dv_spec(make_spec))

    assert text.startswith("# movie|dv.mkv\n")
    assert "**Verdict:** Dolby Vision profile 8.1 (FourCC ok), HDR10, HDR10+, Dolby Atmos" in text
    for heading in ("## Container", "## Video", "## Audio", "## Subtitles"):
        assert heading in text
    assert "- Dolby Vision: `profile 8.1, HDR10 compatible" in text
    assert "| 1 | eac3 | 5.1(side) |" in text
    assert "| 2 | subrip | eng | - | no | yes | no |" in text
    assert "Warning" not in text
    assert text.endswith("\n")


def test_spec_markdown_flags_a_broken_fourcc(make_spec):
    spec = make_spec("broken.mp4", dv="8.1", fourcc="hvc1")
    spec = spec.model_copy(
        update={"video": spec.video.model_copy(update={"fourcc_warning": "FourCC 'hvc1' will NOT engage DV"})}
    )

    text = spec_markdown(spec)

    assert "FourCC `hvc1` will not engage DV" in text.split("## Container")[0]
    assert "> **Warning:** FourCC 'hvc1' will NOT engage DV" in text


def test_spec_markdown_for_a_plain_file(make_spec):
    text = spec_markdown(make_spec("plain.mp4", audio=False))

    assert "no Dolby Vision, HDR10(+) or Atmos detected" in text
    assert "- Dolby Vision: `no`" in text
    assert "No audio streams." in text
    assert "No subtitle streams." in text


def test_compare_markdown_ranks_best_first_and_marks_the_winner(make_spec, monkeypatch):
    specs = {
        "/media/plain.mp4": make_spec("plain.mp4"),
        "/media/dv.mkv": make_spec("dv.mkv", dv="8.1", fourcc="dvh1", atmos=True, height=2160),
    }
    monkeypatch.setattr("dolby_tool.analysis.compare.inspect_file", specs.__getitem__)
    result = compare_files(["/media/plain.mp4", "/media/dv.mkv"])

    text = compare_markdown(result)

    lines = [line for line in text.splitlines() if line.startswith("| ") and "---" not in line]
    assert lines[0].startswith("| rank | file | score")
    assert lines[1].startswith("| 1 | **dv.mkv** (best) |")
    assert lines[2].startswith("| 2 | plain.mp4 |")
    assert f"{result.scores[1].total:g} / 110" in lines[1]
    assert "8.1 (HDR10)" in lines[1]
    assert "`dvh1` ok" in lines[1]
    assert "`hvc1` not DV" in lines[2]


def test_compare_markdown_ties_share_a_rank(make_spec, monkeypatch):
    monkeypatch.setattr("dolby_tool.analysis.compare.inspect_file", lambda path: make_spec(path.rsplit("/")[-1]))

    text = compare_markdown(compare_files(["/media/a.mp4", "/media/b.mp4"]))

    assert "| 1 | **a.mp4** (best) |" in text
    assert "| 1 | **b.mp4** (best) |" in text


def test_compare_markdown_lists_warnings_and_errors(make_spec, monkeypatch):
    def fake_inspect(path):
        if path == "/media/missing.mp4":
            raise RuntimeError("not a file: /media/missing.mp4")
        spec = make_spec("a.mp4", dv="8.1")
        return spec.model_copy(update={"video": spec.video.model_copy(update={"fourcc_warning": "bad fourcc"})})

    monkeypatch.setattr("dolby_tool.analysis.compare.inspect_file", fake_inspect)

    text = compare_markdown(compare_files(["/media/a.mp4", "/media/missing.mp4"]))

    assert "## Warnings" in text
    assert "- `a.mp4`: bad fourcc" in text
    assert "## Errors" in text
    assert "- `/media/missing.mp4`: not a file: /media/missing.mp4" in text


def test_compare_markdown_without_any_file():
    text = compare_markdown(compare_files([]))

    assert "No file could be inspected." in text
