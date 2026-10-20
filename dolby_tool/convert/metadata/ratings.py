"""Content-rating lookup, ported from Subler's Ratings.json.

Produces iTunes `iTunEXTC`-style strings (e.g. `mpaa|PG-13|300|`) that TV.app reads.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DATA_PATH = Path(__file__).with_name("data") / "Ratings.json"

# Subler's Ratings.json uses "Movie" and "TV" as the media discriminator; storefronts
# that rate both kinds with one scheme (GB, FR, NL, …) list them as "Movie & TV".
MEDIA_MOVIE = "Movie"
MEDIA_TV = "TV"
MEDIA_BOTH = "Movie & TV"


@dataclass(frozen=True)
class Rating:
    country: str
    store_code: int
    media: str
    prefix: str
    itunes_code: str
    itunes_value: str
    description: str

    def itunes_extc(self) -> str:
        """Format as `iTunEXTC` atom payload, e.g. `mpaa|PG-13|300|`."""
        return f"{self.prefix}|{self.itunes_code}|{self.itunes_value}|"


@cache
def _table() -> list[dict]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def lookup(store_code: int, media: str, code: str) -> Rating | None:
    """Find rating by storefront id, media kind ("Movie"|"TV"), and rating code (e.g. "PG-13").

    A "Movie & TV" entry matches either kind; an entry for exactly `media` wins over it.
    """
    wanted = code.lower()
    shared: Rating | None = None
    for country_block in _table():
        if int(country_block["storeCode"]) != store_code:
            continue
        for r in country_block["ratings"]:
            if r["itunes-code"].lower() != wanted or r["media"] not in (media, MEDIA_BOTH):
                continue
            if r["media"] == media:
                return _rating(country_block, r)
            shared = shared or _rating(country_block, r)
    return shared


def _rating(country_block: dict, r: dict) -> Rating:
    return Rating(
        country=country_block["country"],
        store_code=int(country_block["storeCode"]),
        media=r["media"],
        prefix=r["prefix"],
        itunes_code=r["itunes-code"],
        itunes_value=r["itunes-value"],
        description=r["description"],
    )
