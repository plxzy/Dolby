"""Round-trip tests for storefront / ratings lookups and filename guessing.

No network — these only exercise the JSON data + parsing code.
"""

import pytest

from dolby_tool.convert.commands import guess_query
from dolby_tool.convert.metadata import ratings, storefronts


def test_us_storefront() -> None:
    s = storefronts.resolve("US")
    assert s.store_code == 143441
    assert s.locale.startswith("en")


def test_gb_storefront_with_language() -> None:
    s = storefronts.resolve("GB", "en")
    assert s.store_code == 143444
    assert s.locale == "en_GB"


def test_au_storefront_known_code() -> None:
    assert storefronts.resolve("AU").store_code == 143460


def test_unknown_country_raises() -> None:
    with pytest.raises(KeyError):
        storefronts.resolve("ZZ")


def test_us_movie_pg13_rating() -> None:
    r = ratings.lookup(143441, ratings.MEDIA_MOVIE, "PG-13")
    assert r is not None
    assert r.prefix == "mpaa"
    assert r.itunes_value == "300"
    assert r.itunes_extc() == "mpaa|PG-13|300|"


def test_unknown_rating_returns_none() -> None:
    assert ratings.lookup(143441, ratings.MEDIA_MOVIE, "NOPE") is None


def test_guess_query_tv_show() -> None:
    kind, title, season, episode = guess_query("Severance.S02E03.1080p.mkv")
    assert kind == "tvShow"
    assert title == "Severance"
    assert season == 2
    assert episode == 3


def test_guess_query_movie_with_year() -> None:
    kind, title, season, episode = guess_query("Dune.Part.Two.2024.2160p.HDR.mkv")
    assert kind == "movie"
    assert title.startswith("Dune")
    assert season is None and episode is None


@pytest.mark.parametrize("media", [ratings.MEDIA_MOVIE, ratings.MEDIA_TV])
def test_gb_shared_movie_and_tv_ratings_match_both_kinds(media: str) -> None:
    r = ratings.lookup(143444, media, "12A")
    assert r is not None
    assert r.media == ratings.MEDIA_BOTH
    assert r.itunes_extc() == "uk-movie|12A|325|"


def test_gb_tv_only_rating_does_not_match_movies() -> None:
    assert ratings.lookup(143444, ratings.MEDIA_TV, "Caution") is not None
    assert ratings.lookup(143444, ratings.MEDIA_MOVIE, "Caution") is None


def test_exact_media_wins_over_shared_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def entry(media: str, value: str) -> dict:
        return {"media": media, "prefix": "x", "itunes-code": "PG", "itunes-value": value, "description": ""}

    table = [
        {"country": "Testland", "storeCode": 1, "ratings": [entry("Movie & TV", "shared"), entry("Movie", "movie")]}
    ]
    monkeypatch.setattr(ratings, "_table", lambda: table)

    assert ratings.lookup(1, ratings.MEDIA_MOVIE, "PG").itunes_value == "movie"
    assert ratings.lookup(1, ratings.MEDIA_TV, "PG").itunes_value == "shared"
