"""convert/tag command helpers: filename guessing, argument validation and summary output."""

import io
import json
from pathlib import Path

import pytest

from dolby_tool.convert import commands
from dolby_tool.convert.commands import guess_query

# --------------------------------------------------------------------- guess_query


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Severance.S02E03.1080p.mkv", ("tvShow", "Severance", 2, 3)),
        ("The Office (US) - s05e12 - Business Ethics.mkv", ("tvShow", "The Office (US)", 5, 12)),
        ("Show_Name_S1E10.mkv", ("tvShow", "Show Name", 1, 10)),
        ("Dune.Part.Two.2024.2160p.HDR.mkv", ("movie", "Dune Part Two", None, None)),
        ("Blade.Runner.2049.2017.2160p.mkv", ("movie", "Blade Runner 2049", None, None)),
        ("Blade Runner 2049 (2017).mkv", ("movie", "Blade Runner 2049", None, None)),
        ("2001.A.Space.Odyssey.1968.mkv", ("movie", "2001 A Space Odyssey", None, None)),
        ("1917.2019.1080p.mkv", ("movie", "1917", None, None)),
        ("Wall-E (2008).mkv", ("movie", "Wall-E", None, None)),
        ("Arrival.mkv", ("movie", "Arrival", None, None)),
    ],
)
def test_guess_query(filename, expected) -> None:
    assert guess_query(filename) == expected


@pytest.mark.parametrize("filename", ["Classes2e1.mkv", "Houses1e2.mkv", "Show.S01E021080p.mkv", "S01E02.mkv"])
def test_words_that_merely_contain_an_episode_marker_are_not_tv(filename) -> None:
    assert guess_query(filename)[0] == "movie"


def test_season_folder_layout_gives_show_season_and_episode() -> None:
    assert guess_query("TV Shows/Severance/Season 2/03 Who Is Alive.m4v") == ("tvShow", "Severance", 2, 3)
    assert guess_query("/library/TV Shows/Severance/Season 2/03 Who Is Alive.m4v") == ("tvShow", "Severance", 2, 3)


def test_season_folder_needs_an_episode_prefix() -> None:
    assert guess_query("Severance/Season 2/Who Is Alive.m4v")[0] == "movie"


def test_absolute_paths_are_detected_with_is_absolute(tmp_path: Path) -> None:
    show = tmp_path / "Show" / "Season 1"
    show.mkdir(parents=True)
    assert guess_query(show / "02 Pilot.m4v") == ("tvShow", "Show", 1, 2)


# --------------------------------------------------------------------- arguments


def test_unknown_storefront_is_a_clean_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        commands.main_tag(["movie.m4v", "--storefront", "ZZ"])
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "unknown storefront 'ZZ'" in err
    assert "Traceback" not in err


def test_storefront_is_case_insensitive() -> None:
    assert commands._storefront("gb") == "GB"


def test_convert_reports_mux_errors_without_a_traceback(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        commands.main_convert([str(tmp_path / "missing.mkv"), str(tmp_path / "out.m4v"), "--no-metadata"])
    assert "Input does not exist" in str(exit_info.value)


# --------------------------------------------------------------------- output


def test_interactive_prompt_goes_to_stderr(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
    assert commands._pick_interactive("Results:", ["a", "b"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Pick:" in captured.err


def test_directory_artwork_only_skips_lookup_and_nested_or_non_mp4_files(monkeypatch, tmp_path, capsys) -> None:
    for name in ["02.M4V", "01.mp4", "source.mkv"]:
        (tmp_path / name).touch()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "03.mp4").touch()
    image = tmp_path / "poster.jpg"
    image.write_bytes(b"jpeg")

    def lookup(*args, **kwargs):
        pytest.fail("artwork-only tagging must not look up metadata")

    monkeypatch.setattr(commands, "resolve_metadata", lookup)
    monkeypatch.setattr(commands.tagger, "_atomicparsley", lambda: None)
    commands.main_tag([str(tmp_path), "--season-artwork", str(image), "--json"])
    result = json.loads(capsys.readouterr().out)
    assert result["directory"] == str(tmp_path)
    assert [Path(entry["file"]).name for entry in result["files"]] == ["01.mp4", "02.M4V"]
    assert all("metadata" not in entry and entry["artwork"]["applied"] is False for entry in result["files"])
