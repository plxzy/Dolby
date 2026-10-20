"""Apple TV catalog client: response parsing against canned JSON (no network)."""

from dolby_tool.convert.metadata import apple_tv


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
