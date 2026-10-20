"""Apple TV catalog client: response parsing against canned JSON (no network)."""

import httpx
import pytest

from dolby_tool.convert.metadata import apple_tv, storefronts


def hit(**overrides) -> apple_tv.SearchHit:
    fields = {
        "id": "umc.cmc.1",
        "type": "Movie",
        "title": "Arrival",
        "description": None,
        "release_date_ms": None,
        "images": {},
        "roles_summary": None,
        "rating_display": None,
    }
    return apple_tv.SearchHit(**(fields | overrides))


def test_release_date_from_epoch_ms() -> None:
    h = hit(release_date_ms=1_478_822_400_000)  # 2016-11-11T00:00:00Z
    assert h.year == 2016
    assert h.release_date_iso == "2016-11-11"


def test_missing_release_date() -> None:
    assert hit().year is None
    assert hit().release_date_iso is None


# --------------------------------------------------------------------- canned catalog responses

US = storefronts.resolve("US")

SEARCH = {
    "data": {
        "canvas": {
            "shelves": [
                {
                    "items": [
                        {
                            "id": "umc.cmc.movie1",
                            "type": "Movie",
                            "title": "Arrival",
                            "description": "Linguist meets aliens.",
                            "releaseDate": 1_478_822_400_000,
                            "url": "https://tv.apple.com/us/movie/arrival/umc.cmc.movie1",
                            "rating": {"displayName": "PG-13"},
                            "rolesSummary": {"cast": ["Amy Adams"], "directors": ["Denis Villeneuve"]},
                            "images": {
                                "coverArt16X9": {"url": "https://img/{w}x{h}.{f}", "width": 1920, "height": 1080},
                                "coverArt": {"url": "https://img2/{w}x{h}.{f}", "width": None, "height": None},
                            },
                        },
                        {"id": "umc.cmc.show1", "type": "Show", "title": "Severance"},
                        {"type": "Movie", "title": "No id, skipped"},
                    ]
                },
                {"items": None},
            ]
        }
    }
}

DETAILS = {
    "data": {
        "content": {
            "title": "Arrival",
            "description": "Short blurb.",
            "studio": "Paramount",
            "genres": [{"name": "Sci-Fi"}, {"name": None}, {}],
            "rating": {"displayName": "PG-13"},
        },
        "roles": [
            {"type": "Actor", "personName": "Amy Adams"},
            {"type": "Voice", "personName": "Jeremy Renner"},
            {"type": "Writer", "personName": "Eric Heisserer"},
            {"type": "Director", "personName": "Denis Villeneuve"},
            {"type": "Music", "personName": "Johann Johannsson"},
            {"type": "Actor"},
        ],
    }
}

SEASONS = {
    "data": {"seasonSummaries": [{"seasonNumber": 2, "episodeCount": 10}, {"seasonNumber": 1, "episodeCount": 9}]}
}

EPISODES = {
    "data": {
        "episodes": [
            {
                "id": "umc.cmc.ep1",
                "title": "Hello, Ms. Cobel",
                "seasonNumber": 2,
                "episodeNumber": 1,
                "releaseDate": 1_736_467_200_000,
                "description": "Back to work.",
                "rating": {"displayName": "TV-MA"},
                "images": {"previewFrame": {"url": "https://ep/{w}x{h}.{f}", "width": 1920, "height": 1080}},
            }
        ]
    }
}


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer catalog URLs with canned JSON (by endpoint) and record every request."""
    requested: list[str] = []

    def fake_get(url: str) -> dict | None:
        requested.append(url)
        if "/search/incremental" in url:
            return SEARCH
        if "/view/product/" in url:
            return DETAILS
        if "/episodes?skip=" in url:
            return EPISODES
        if "/episodes?" in url:
            return SEASONS
        return None

    monkeypatch.setattr(apple_tv, "_get", fake_get)
    return requested


def test_search_returns_only_the_requested_kind_with_ids(catalog) -> None:
    movies = apple_tv.search_movies("Arrival (Dubbed)", US)
    assert [(h.id, h.title, h.year, h.rating_display) for h in movies] == [("umc.cmc.movie1", "Arrival", 2016, "PG-13")]
    assert [h.id for h in apple_tv.search_shows("Severance", US)] == ["umc.cmc.show1"]
    assert "&q=Arrival" in catalog[0]
    assert "Dubbed" not in catalog[0]
    assert f"sf={US.store_code}" in catalog[0]


def test_fetch_movie_merges_search_hit_and_details(catalog) -> None:
    (hit,) = apple_tv.search_movies("Arrival", US)
    meta = apple_tv.fetch_movie(hit, US)

    assert (meta.media_kind, meta.title, meta.release_date) == ("movie", "Arrival", "2016-11-11")
    assert meta.genres == ["Sci-Fi"]
    assert meta.studio == "Paramount"
    assert meta.cast == ["Amy Adams", "Jeremy Renner"]
    assert (meta.directors, meta.screenwriters, meta.composers) == (
        ["Denis Villeneuve"],
        ["Eric Heisserer"],
        ["Johann Johannsson"],
    )
    assert meta.rating is not None
    assert meta.rating.itunes_extc() == "mpaa|PG-13|300|"
    assert meta.short_description == "Short blurb."
    # 16:9 art first, then the poster whose null width/height must not crash
    assert [a.size for a in meta.artworks] == ["rectangle", "square"]


def test_fetch_movie_by_id_backfills_the_title(catalog) -> None:
    bare = apple_tv.SearchHit("umc.cmc.movie1", "Movie", None, None, None, {}, None, None)
    assert apple_tv.fetch_movie(bare, US).title == "Arrival"


def test_fetch_episode_walks_seasons_and_pages(catalog) -> None:
    show = apple_tv.search_shows("Severance", US)[0]
    meta = apple_tv.fetch_episode(show, 2, 1, US)

    assert meta is not None
    assert (meta.media_kind, meta.title, meta.series_name) == ("tvShow", "Hello, Ms. Cobel", "Severance")
    assert (meta.season, meta.episode_number, meta.episode_id) == (2, 1, "201")
    assert meta.release_date == "2025-01-10"
    assert meta.series_description == "Short blurb."
    # Season 2 starts after season 1's 9 episodes.
    assert any("skip=9&count=10" in url for url in catalog)
    assert meta.rating is not None
    assert meta.rating.itunes_extc() == "us-tv|TV-MA|600|"


def test_fetch_episode_unknown_season_or_episode(catalog) -> None:
    show = apple_tv.search_shows("Severance", US)[0]
    assert apple_tv.fetch_episode(show, 9, 1, US) is None
    assert apple_tv.fetch_episode(show, 2, 99, US) is None


def test_seasons_fall_back_to_available_channels(monkeypatch) -> None:
    payload = {
        "data": {"seasonSummaries": None, "availableChannels": [{"seasonNumbers": [2, 1]}, {"seasonNumbers": None}]}
    }
    monkeypatch.setattr(apple_tv, "_get", lambda url: payload)
    assert apple_tv.fetch_seasons("umc.cmc.show1", US) == [(1, 0), (2, 0)]


def test_missing_or_null_payloads_degrade_to_empty(monkeypatch) -> None:
    monkeypatch.setattr(apple_tv, "_get", lambda url: None)
    assert apple_tv.search_movies("x", US) == []
    assert apple_tv.fetch_seasons("id", US) == []
    assert apple_tv.fetch_episodes("id", US, skip=0, count=1) == []
    monkeypatch.setattr(apple_tv, "_get", lambda url: {"data": {"canvas": {"shelves": None}}})
    assert apple_tv.search_movies("x", US) == []


# --------------------------------------------------------------------- HTTP layer


def test_get_returns_json_and_swallows_transport_errors(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/ok":
                return httpx.Response(200, json={"a": 1})
            case "/list":
                return httpx.Response(200, json=[1])
            case "/html":
                return httpx.Response(200, text="<html>")
            case "/missing":
                return httpx.Response(404)
            case _:
                raise httpx.ConnectTimeout("slow", request=request)

    real_client = httpx.Client
    monkeypatch.setattr(
        apple_tv.httpx, "Client", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler))
    )
    base = "https://uts.test"
    assert apple_tv._get(f"{base}/ok") == {"a": 1}
    assert apple_tv._get(f"{base}/list") is None
    assert apple_tv._get(f"{base}/html") is None
    assert apple_tv._get(f"{base}/missing") is None
    assert apple_tv._get(f"{base}/timeout") is None


def test_one_client_serves_a_whole_lookup(monkeypatch) -> None:
    clients: list[httpx.Client] = []
    real_client = httpx.Client

    def make_client(**kwargs) -> httpx.Client:
        clients.append(real_client(**kwargs, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
        return clients[-1]

    monkeypatch.setattr(apple_tv.httpx, "Client", make_client)
    with apple_tv.session():
        apple_tv.fetch_seasons("a", US)
        apple_tv.fetch_episodes("a", US, skip=0, count=1)
        apple_tv.search_movies("x", US)
    assert len(clients) == 1
    apple_tv.fetch_seasons("a", US)
    assert len(clients) == 2
