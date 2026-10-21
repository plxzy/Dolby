"""Storefront / locale lookups, ported from Subler's Storefronts.json + iTunes store mapping."""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DATA_PATH = Path(__file__).with_name("data") / "Storefronts.json"


@dataclass(frozen=True)
class Store:
    """Resolved storefront for an Apple TV / iTunes Store API call.

    - country: ISO 3166-1 alpha-2 (e.g. "US", "GB", "AU").
    - store_code: numeric storefront id (e.g. 143441 for US).
    - locale: Apple-style underscore locale (e.g. "en_US", "en_GB").
    """

    country: str
    store_code: int
    locale: str


@cache
def _table() -> dict[str, dict]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))["data"]


def resolve(country: str, language: str | None = None) -> Store:
    """Resolve (country, optional language) to a Store. Raises KeyError if unknown."""
    country = country.upper()
    entry = _table()[country]
    locales: list[str] = entry["localesSupported"]
    if language:
        # Accept "en", "en-US", "en_US"
        normalized = language.replace("-", "_")
        for loc in locales:
            if loc == normalized or loc.split("_")[0] == normalized.split("_")[0]:
                return Store(country=country, store_code=int(entry["storefrontId"]), locale=loc)
    return Store(country=country, store_code=int(entry["storefrontId"]), locale=locales[0])


def countries() -> list[str]:
    return sorted(_table().keys())
