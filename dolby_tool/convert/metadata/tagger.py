"""Write iTunes-style metadata to an MP4 via AtomicParsley.

AtomicParsley is the boring-but-correct option here: it handles the chunk
offset adjustments that come with re-writing the moov, knows the iTunes
atom set, and is one `brew install atomicparsley` away. We don't try to
re-implement it.

Atom reference:
  ©nam = name           ©day = release date         desc = description (≤255)
  ldes = long desc      ©gen = genre                ©ART = artist (cast)
  ©dir = director       ©wrt = writers              tvsh = show name
  tvsn = season number  tves = episode number       tven = episode id
  stik = media kind     hdvd = HD video             covr = cover art (jpg)
  iTunEXTC = content rating string (e.g. "mpaa|PG-13|300|")
"""

import tempfile
from pathlib import Path

from ...common import run_tool, which_any
from .apple_tv import Metadata
from .artwork import download

# stik values per iTunes spec: 1 Music, 6 Music Video, 9 Movie, 10 TV Show.
# AtomicParsley's `--stik` accepts the name OR `value=N` — bare numbers are
# treated as a string lookup and fall back to "Home Video", so use value=.
_STIK_MOVIE = "value=9"
_STIK_TV = "value=10"


def _atomicparsley() -> str | None:
    return which_any("AtomicParsley", "atomicparsley")


def have_atomicparsley() -> bool:
    return _atomicparsley() is not None


def apply(path: Path, meta: Metadata) -> dict[str, object]:
    """Write metadata onto `path` in place. Returns a summary dict."""
    atomicparsley = _atomicparsley()
    if atomicparsley is None:
        return {"applied": False, "reason": "AtomicParsley not installed (brew install atomicparsley)"}

    args: list[str] = [atomicparsley, str(path)]
    fields_set: list[str] = []

    def add(flag: str, value: str | int | None) -> None:
        if value in (None, "", []):
            return
        args.extend([flag, str(value)])
        fields_set.append(flag)

    if meta.media_kind == "tvShow":
        add("--stik", _STIK_TV)
        add("--TVShowName", meta.series_name)
        add("--TVNetwork", meta.studio)
        add("--TVSeasonNum", meta.season)
        add("--TVEpisodeNum", meta.episode_number)
        add("--TVEpisode", meta.episode_id)
        add("--title", meta.title)
        # Library-grouping atoms TV.app uses to bucket episodes under a show/season.
        add("--albumArtist", meta.series_name)
        if meta.series_name and meta.season is not None:
            add("--album", f"{meta.series_name}, Season {meta.season}")
        if meta.episode_number is not None:
            add("--tracknum", meta.episode_number)
    else:
        add("--stik", _STIK_MOVIE)
        add("--title", meta.title)

    add("--year", meta.release_date)
    add("--description", (meta.long_description or "")[:255] or None)
    add("--longdesc", meta.long_description)
    add("--genre", ", ".join(meta.genres) or None)
    # cast → ©ART (artist). director/screenwriters/producer would need an
    # iTunMOVI plist via --rDNSatom; deferred as not critical for TV.app display.
    add("--artist", ", ".join(meta.cast) or None)
    add("--composer", ", ".join(meta.composers) or None)
    if meta.rating:
        # iTunEXTC content-rating string lives in a reverse-DNS atom.
        args += [
            "--rDNSatom",
            meta.rating.itunes_extc(),
            "name=iTunEXTC",
            "domain=com.apple.iTunes",
        ]
        fields_set.append("iTunEXTC")

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        artwork_paths: list[Path] = []
        for i, art in enumerate(meta.artworks[:5]):  # cap at 5 to bound writes
            try:
                blob = download(art.url)
            except Exception:
                continue
            p = tmp / f"art_{i}.jpg"
            p.write_bytes(blob)
            artwork_paths.append(p)
            add("--artwork", str(p))

        args += ["--overWrite"]
        r = run_tool(args, check=False)
        if r.returncode != 0:
            return {
                "applied": False,
                "reason": f"AtomicParsley exit {r.returncode}",
                "stderr_tail": r.stderr.splitlines()[-10:],
            }

    return {
        "applied": True,
        "fields": sorted(set(fields_set)),
        "artwork_count": len(artwork_paths) if "artwork_paths" in dir() else 0,
        "rating": meta.rating.itunes_extc() if meta.rating else None,
    }
