"""AtomicParsley argument building and artwork handling, with the tool and network faked."""

import plistlib
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
    assert atomicparsley[0].count("--artwork") == 3  # REMOVE_ALL + the two that downloaded
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


def test_apply_writes_credits_plist_and_hd_flag(atomicparsley, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tagger, "is_hd", lambda path: True)
    meta = movie(0)
    meta.cast, meta.directors, meta.studio = ["Amy Adams"], ["Denis Villeneuve"], "Paramount"
    result = tagger.apply(tmp_path / "movie.m4v", meta)

    args = atomicparsley[0]
    assert {"iTunMOVI", "--hdvideo"} <= set(result["fields"])
    movi = args[args.index("name=iTunMOVI") - 1]
    credits = plistlib.loads(movi.encode())
    assert credits["cast"] == [{"name": "Amy Adams"}]
    assert credits["directors"] == [{"name": "Denis Villeneuve"}]
    assert credits["studio"] == "Paramount"
    assert args[args.index("--hdvideo") + 1] == "true"


def test_credits_plist_is_omitted_without_credits() -> None:
    assert tagger.itunmovi_plist(movie(0)) is None


def test_retagging_clears_old_artwork_before_adding_new(atomicparsley, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tagger, "download", lambda url: b"jpeg")
    tagger.apply(tmp_path / "movie.m4v", movie(2))
    args = atomicparsley[0]
    assert args.index("REMOVE_ALL") < args.index("--artwork", args.index("REMOVE_ALL") + 1)
    assert args[args.index("REMOVE_ALL") - 1] == "--artwork"


def test_retag_falls_back_to_a_full_copy_when_padding_is_too_small(monkeypatch, tmp_path: Path) -> None:
    media = tmp_path / "movie.m4v"
    media.write_bytes(b"old")
    calls: list[list[str]] = []

    def run_tool(cmd: Sequence[str], *, timeout: float | None = None, check: bool = True):
        calls.append(list(cmd))
        if "--overWrite" in cmd:
            return subprocess.CompletedProcess(list(cmd), 1, "AtomicParsley error: the insufficient space to retag", "")
        (tmp_path / "movie-temp-123.m4v").write_bytes(b"new")
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    monkeypatch.setattr(tagger, "run_tool", run_tool)
    monkeypatch.setattr(tagger, "which_any", lambda *names: "AtomicParsley")
    monkeypatch.setattr(tagger, "is_hd", lambda path: None)

    assert tagger.apply(media, movie(0))["applied"] is True
    assert media.read_bytes() == b"new"
    assert list(tmp_path.glob("*-temp-*")) == []
    assert "--overWrite" in calls[0] and "--overWrite" not in calls[1]
