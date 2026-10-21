"""AtomicParsley argument building and artwork handling, with the tool and network faked."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from dolby_tool.convert.metadata import apple_tv, artwork, ratings, tagger


@pytest.fixture
def atomicparsley(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run_tool(cmd: Sequence[str], *, timeout: float | None = None, check: bool = True):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(tagger, "run_tool", run_tool)
    monkeypatch.setattr(tagger, "which_any", lambda *names: "AtomicParsley")
    return calls


def movie(n_artworks: int) -> apple_tv.Metadata:
    arts = [artwork.Artwork(f"https://img/{i}.jpg", f"https://img/t{i}.jpg", "standard") for i in range(n_artworks)]
    return apple_tv.Metadata(
        media_kind="movie",
        title="Arrival",
        genres=["Sci-Fi"],
        rating=ratings.lookup(143441, ratings.MEDIA_MOVIE, "PG-13"),
        artworks=arts,
    )


def test_apply_reports_the_artwork_that_was_downloaded(atomicparsley, monkeypatch, tmp_path: Path) -> None:
    def download(url: str) -> bytes:
        if url.endswith("/1.jpg"):
            raise httpx.ConnectError("offline")
        return b"jpeg"

    monkeypatch.setattr(tagger, "download", download)
    result = tagger.apply(tmp_path / "movie.m4v", movie(3))

    assert result["applied"] is True
    assert result["artwork_count"] == 2
    assert result["rating"] == "mpaa|PG-13|300|"
    assert atomicparsley[0].count("--artwork") == 2
    assert {"--title", "--genre", "--stik", "iTunEXTC"} <= set(result["fields"])


def test_apply_with_no_artwork_reports_zero(atomicparsley, tmp_path: Path) -> None:
    assert tagger.apply(tmp_path / "movie.m4v", movie(0))["artwork_count"] == 0


def test_apply_without_atomicparsley(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tagger, "which_any", lambda *names: None)
    result = tagger.apply(tmp_path / "movie.m4v", movie(0))
    assert result["applied"] is False


@pytest.mark.parametrize(
    ("image", "size"),
    [
        ({"url": "https://i/{w}x{h}.{f}", "width": 1920, "height": 1080}, "rectangle"),
        ({"url": "https://i/{w}x{h}.{f}", "width": 800, "height": 800}, "square"),
        ({"url": "https://i/{w}x{h}.{f}", "width": 600, "height": 900}, "standard"),
        ({"url": "https://i/{w}x{h}.{f}", "width": None, "height": None}, "square"),
        ({"url": "https://i/{w}x{h}.{f}"}, "square"),
    ],
)
def test_artwork_size_from_image_json(image, size) -> None:
    art = artwork.from_image(image)
    assert art is not None
    assert art.size == size
    assert art.url.startswith("https://i/")


def test_artwork_requires_a_url() -> None:
    assert artwork.from_image(None) is None
    assert artwork.from_image({"width": 1}) is None


def test_replace_artwork_missing_image_does_not_modify_media(atomicparsley, tmp_path: Path) -> None:
    assert tagger.apply_artwork(tmp_path / "episode.m4v", tmp_path / "missing.jpg")["applied"] is False
    assert atomicparsley == []
