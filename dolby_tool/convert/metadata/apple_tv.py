"""Apple TV / iTunes Store metadata client (uts-api).

Direct port of Subler's `Classes/MetadataImporters/AppleTV.swift` query/parse
logic, scoped to the endpoints we actually use:

- `search/incremental` — title search
- `view/product/{id}` — full movie/show details
- `view/show/{id}/episodes` — TV episode list (with skip/count paging)
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import httpx

from ...common import to_int
from .artwork import Artwork, from_image
from .ratings import MEDIA_MOVIE, MEDIA_TV, Rating, lookup
from .storefronts import Store

_BASE = "https://uts-api.itunes.apple.com/uts/v2"
_SEARCH = f"{_BASE}/search/incremental"
_DETAILS = f"{_BASE}/view/product/"
_EPISODES = f"{_BASE}/view/show/"
_OPTIONS = "&utsk=0&caller=wta&v=58&pfm=appletv"


def _normalize(term: str) -> str:
    return term.replace(" (Dubbed)", "").replace(" (Subtitled)", "").replace(" (Ex-tended Edition)", "")


_client: ContextVar[httpx.Client | None] = ContextVar("apple_tv_client", default=None)


@contextmanager
def session(timeout: float = 20.0) -> Iterator[httpx.Client]:
    """Share one HTTP client (and its connection) across all requests of a lookup.

    Nested sessions reuse the outer client, so the public search/fetch functions
    can open one themselves and a caller can still wrap several of them.
    """
    if (current := _client.get()) is not None:
        yield current
        return
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "dolby-tool/0.1"}) as client:
        token = _client.set(client)
        try:
            yield client
        finally:
            _client.reset(token)


def _get(url: str) -> dict[str, Any] | None:
    """GET a catalog URL and return its JSON object; None on any HTTP, transport or decoding failure."""
    with session() as client:
        try:
            r = client.get(url)
            if r.status_code != 200:
                return None
            payload = r.json()
        except httpx.HTTPError, ValueError:
            return None
    return payload if isinstance(payload, dict) else None


@dataclass
class SearchHit:
    id: str
    type: str  # "Movie" or "Show"
    title: str | None
    description: str | None
    release_date_ms: float | None
    images: dict[str, Any]
    roles_summary: dict[str, Any] | None
    rating_display: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def release_date(self) -> date | None:
        return _date_from_ms(self.release_date_ms)

    @property
    def year(self) -> int | None:
        return d.year if (d := self.release_date) else None

    @property
    def release_date_iso(self) -> str | None:
        return d.isoformat() if (d := self.release_date) else None


@dataclass
class Metadata:
    """Resolved metadata for one title (movie or TV episode), ready to write to MP4."""

    media_kind: str  # "movie" | "tvShow"
    title: str | None = None
    release_date: str | None = None  # YYYY-MM-DD
    long_description: str | None = None
    short_description: str | None = None
    cast: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    screenwriters: list[str] = field(default_factory=list)
    producers: list[str] = field(default_factory=list)
    composers: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    studio: str | None = None
    rating: Rating | None = None
    series_name: str | None = None
    series_description: str | None = None
    season: int | None = None
    episode_number: int | None = None
    episode_id: str | None = None
    service_content_id: str | None = None
    service_episode_id: str | None = None
    itunes_url: str | None = None
    artworks: list[Artwork] = field(default_factory=list)


def search_movies(term: str, store: Store) -> list[SearchHit]:
    with session():
        return _search(term, store, kind="Movie")


def search_shows(term: str, store: Store) -> list[SearchHit]:
    with session():
        return _search(term, store, kind="Show")


def _search(term: str, store: Store, *, kind: str) -> list[SearchHit]:
    q = quote(_normalize(term), safe="")
    url = f"{_SEARCH}?&sf={store.store_code}&locale={store.locale}{_OPTIONS}&q={q}"
    payload = _get(url)
    if not payload:
        return []
    canvas = (payload.get("data") or {}).get("canvas") or {}
    hits: list[SearchHit] = []
    for shelf in canvas.get("shelves") or []:
        for item in shelf.get("items") or []:
            if item.get("type") == kind and item.get("id"):
                hits.append(_to_hit(item))
    return hits


def _to_hit(item: dict[str, Any]) -> SearchHit:
    return SearchHit(
        id=item["id"],
        type=item.get("type", ""),
        title=item.get("title"),
        description=item.get("description"),
        release_date_ms=item.get("releaseDate"),
        images=item.get("images") or {},
        roles_summary=item.get("rolesSummary"),
        rating_display=(item.get("rating") or {}).get("displayName"),
        raw=item,
    )


# ----------------------------------------------------------------- Movie details


def fetch_movie(hit: SearchHit, store: Store) -> Metadata:
    with session():
        return _fetch_movie(hit, store)


def _fetch_movie(hit: SearchHit, store: Store) -> Metadata:
    meta = Metadata(
        media_kind="movie",
        title=hit.title,
        release_date=hit.release_date_iso,
        long_description=hit.description,
        service_content_id=hit.id,
        itunes_url=hit.raw.get("url"),
        cast=(hit.roles_summary or {}).get("cast") or [],
        directors=(hit.roles_summary or {}).get("directors") or [],
        artworks=_artworks_from(hit.images, prefer16x9=True),
    )
    if hit.rating_display:
        meta.rating = lookup(store.store_code, MEDIA_MOVIE, hit.rating_display)
    _enrich_with_details(meta, hit.id, store, media=MEDIA_MOVIE)
    return meta


def _enrich_with_details(meta: Metadata, content_id: str, store: Store, *, media: str) -> None:
    url = f"{_DETAILS}{content_id}?&sf={store.store_code}&locale={store.locale}{_OPTIONS}"
    payload = _get(url)
    if not payload:
        return
    data = payload.get("data") or {}
    content = data.get("content") or {}
    roles = data.get("roles") or []

    meta.genres = [g["name"] for g in content.get("genres") or [] if g.get("name")]
    meta.studio = content.get("studio")
    meta.short_description = content.get("description") or meta.short_description

    # Backfill title/series-name when caller arrived via --metadata-id (synthetic
    # SearchHit has no title). For movies content.title is the movie title; for
    # TV the details endpoint is hit against the *show* id, so content.title is
    # the show name and feeds series_name.
    detail_title = content.get("title")
    if detail_title:
        if media == MEDIA_MOVIE and not meta.title:
            meta.title = detail_title
        if media == MEDIA_TV and not meta.series_name:
            meta.series_name = detail_title
    if media == MEDIA_TV and not meta.series_description:
        meta.series_description = content.get("description") or meta.series_description

    def names(of_type: str) -> list[str]:
        return [r["personName"] for r in roles if r.get("type") == of_type and r.get("personName")]

    meta.cast = names("Actor") + names("Voice") or meta.cast
    meta.screenwriters = names("Writer")
    meta.producers = names("Producer")
    directors = names("Director")
    if directors:
        meta.directors = directors
    composers = names("Music")
    if composers:
        meta.composers = composers

    rating_display = (content.get("rating") or {}).get("displayName")
    if rating_display:
        new_rating = lookup(store.store_code, media, rating_display)
        if new_rating:
            meta.rating = new_rating


# ----------------------------------------------------------------- TV


def fetch_seasons(show_id: str, store: Store) -> list[tuple[int, int]]:
    """Return list of (season_number, episode_count) for a show, ordered by season."""
    url = f"{_EPISODES}{show_id}/episodes?sf={store.store_code}&locale={store.locale}{_OPTIONS}"
    payload = _get(url)
    if not payload:
        return []
    data = payload.get("data") or {}
    summaries = data.get("seasonSummaries") or []

    # Prefer pairing season number with episode count from the same summary
    # entry — zipping with availableChannels by position can misalign when
    # the two sources differ in order or length.
    pairs: list[tuple[int, int]] = []
    for s in summaries:
        n = to_int(s.get("seasonNumber") or s.get("season") or s.get("number"))
        if n is not None:
            pairs.append((n, to_int(s.get("episodeCount") or s.get("count")) or 0))

    if not pairs:
        # Last-resort fallback: derive season list from availableChannels with
        # zero counts (caller will then need to fetch more pages).
        available = sorted({s for c in data.get("availableChannels") or [] for s in c.get("seasonNumbers") or []})
        pairs = [(n, 0) for n in available]

    pairs.sort(key=lambda p: p[0])
    return pairs


def fetch_episodes(show_id: str, store: Store, *, skip: int, count: int) -> list[dict[str, Any]]:
    url = (
        f"{_EPISODES}{show_id}/episodes?skip={skip}&count={count}&sf={store.store_code}&locale={store.locale}{_OPTIONS}"
    )
    payload = _get(url)
    if not payload:
        return []
    return (payload.get("data") or {}).get("episodes") or []


def fetch_season_artwork(show_id: str, season: int, store: Store) -> list[Artwork]:
    """Pull Apple's purpose-made per-season cover art from `show/{id}/itunesSeasons`.

    Returns 0..2 Artwork entries (portrait `coverArt` and/or 16x9). Empty when
    Apple hasn't published catalog art for that season yet — observed for
    in-progress seasons where the show predates the season being added (e.g.
    The Boys S5 wasn't in the catalog mid-2026).
    """
    url = f"{_EPISODES}{show_id}/itunesSeasons?sf={store.store_code}&locale={store.locale}{_OPTIONS}"
    payload = _get(url)
    if not payload:
        return []
    seasons_blob = (payload.get("data") or {}).get("seasons") or {}
    # Response groups seasons under canonical-show keys.
    entries: list[dict[str, Any]] = []
    if isinstance(seasons_blob, dict):
        for v in seasons_blob.values():
            if isinstance(v, list):
                entries.extend(entry for entry in v if isinstance(entry, dict))
    elif isinstance(seasons_blob, list):
        entries = [entry for entry in seasons_blob if isinstance(entry, dict)]
    match = next((s for s in entries if to_int(s.get("seasonNumber")) == season), None)
    if not match:
        return []
    return _artworks_from(match.get("images") or {}, prefer16x9=False)


def fetch_episode(show_hit: SearchHit, season: int, episode: int, store: Store) -> Metadata | None:
    """Find a single episode by (season, episode) within a show search hit."""
    with session():
        return _fetch_episode(show_hit, season, episode, store)


def _fetch_episode(show_hit: SearchHit, season: int, episode: int, store: Store) -> Metadata | None:
    seasons = fetch_seasons(show_hit.id, store)
    season_index = next((i for i, (n, _) in enumerate(seasons) if n == season), -1)
    if season_index < 0:
        return None
    skip = sum(c for _, c in seasons[:season_index])
    length = seasons[season_index][1]
    episodes = fetch_episodes(show_hit.id, store, skip=skip, count=length)
    ep = next((e for e in episodes if e.get("episodeNumber") == episode), None)
    if not ep:
        return None

    aired = _date_from_ms(ep.get("releaseDate")) or show_hit.release_date
    meta = Metadata(
        media_kind="tvShow",
        title=ep.get("title"),
        release_date=aired.isoformat() if aired else None,
        long_description=ep.get("description"),
        series_name=show_hit.title,
        series_description=show_hit.description,
        season=ep.get("seasonNumber"),
        episode_number=ep.get("episodeNumber"),
        episode_id=f"{ep.get('seasonNumber') or 0}{ep.get('episodeNumber') or 0:02d}",
        service_content_id=show_hit.id,
        service_episode_id=ep.get("id"),
        itunes_url=ep.get("showUrl"),
        artworks=(
            # Order matters: catalog season poster first (Apple's purpose-made
            # per-season art) so it lands at covr index 0 — TV.app uses the
            # lowest-numbered episode's covr[0] as the season tile, and shared
            # poster across episodes is the only way to get a distinct season
            # tile for sideloaded files.
            fetch_season_artwork(show_hit.id, season, store)
            + _artworks_from(ep.get("seasonImages") or {}, prefer16x9=True)
            + _artworks_from(show_hit.images, prefer16x9=True)
            + _artworks_from(ep.get("images") or {}, prefer16x9=False, preview=True)
        ),
    )
    rating_display = (ep.get("rating") or {}).get("displayName")
    if rating_display:
        meta.rating = lookup(store.store_code, MEDIA_TV, rating_display)
    _enrich_with_details(meta, show_hit.id, store, media=MEDIA_TV)
    return meta


# ----------------------------------------------------------------- helpers


def _date_from_ms(ms: float | None) -> date | None:
    """Apple's catalog dates are epoch milliseconds, UTC."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date() if ms else None


def _artworks_from(images: dict[str, Any], *, prefer16x9: bool, preview: bool = False) -> list[Artwork]:
    out: list[Artwork] = []
    if preview:
        a = from_image(images.get("previewFrame"))
        if a:
            out.append(a)
        return out
    keys = ("coverArt16X9", "coverArt") if prefer16x9 else ("coverArt", "coverArt16X9")
    for k in keys:
        a = from_image(images.get(k))
        if a:
            out.append(a)
    return out
