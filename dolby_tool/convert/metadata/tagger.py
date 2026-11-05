"""Write iTunes-style metadata to an MP4 via AtomicParsley.

AtomicParsley is the boring-but-correct option here: it handles the chunk
offset adjustments that come with re-writing the moov, knows the iTunes
atom set, and is one `brew install atomicparsley` away. We don't try to
re-implement it.

Atoms written (AtomicParsley flag → iTunes atom):
  --title ©nam            --year ©day             --description desc (≤255)
  --longdesc ldes         --genre ©gen            --artist ©ART (cast)
  --composer ©wrt         --stik stik (movie/TV)  --artwork covr (JPEG, ≤5)
  --TVShowName tvsh       --TVNetwork tvnn        --TVSeasonNum tvsn
  --TVEpisodeNum tves     --TVEpisode tven        --albumArtist aART
  --album ©alb            --tracknum trkn         (library grouping of episodes)
  --rDNSatom iTunEXTC     content rating string (e.g. "mpaa|PG-13|300|")
  --rDNSatom iTunMOVI     cast / director / producer / screenwriter / studio plist
  --hdvideo hdvd          HD badge, from the video's resolution
"""

import plistlib
import subprocess
import tempfile
from pathlib import Path

import httpx

from ...common import ToolError, ffprobe_json, run_tool, to_int, which_any
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


def _retag_via_copy(args: list[str], path: Path) -> subprocess.CompletedProcess[str]:
    """Redo a tag that didn't fit the file's metadata padding by writing a full copy, then swapping it in.

    AtomicParsley's in-place path fails ("insufficient space to retag") when the new tags are larger
    than the padding; without `--overWrite` it rewrites the file to `<name>-temp-<pid>.<ext>` instead.
    That needs free disk space roughly the size of the file.
    """
    rewrite = [a for a in args if a != "--overWrite"]
    existing = set(path.parent.glob(f"{path.stem}-temp-*{path.suffix}"))
    r = run_tool(rewrite, check=False)
    fresh = sorted(set(path.parent.glob(f"{path.stem}-temp-*{path.suffix}")) - existing)
    if r.returncode == 0 and fresh:
        fresh[-1].replace(path)
        fresh = fresh[:-1]
    for leftover in fresh:  # a failed or partial rewrite must not leave a file-sized orphan behind
        leftover.unlink(missing_ok=True)
    return r


def itunmovi_plist(meta: Metadata) -> str | None:
    """The iTunMOVI XML plist TV.app reads credits from (Cast, Directors, Producers, Screenwriters, Studio)."""
    credits: dict[str, object] = {}
    for key, names in (
        ("cast", meta.cast),
        ("directors", meta.directors),
        ("producers", meta.producers),
        ("screenwriters", meta.screenwriters),
    ):
        if names:
            credits[key] = [{"name": n} for n in names]
    if meta.studio:
        credits["studio"] = meta.studio
    return plistlib.dumps(credits, fmt=plistlib.FMT_XML).decode() if credits else None


def is_hd(path: Path) -> bool | None:
    """Whether the first video stream is HD (>= 720 lines or >= 1280 wide); `None` if it can't be probed."""
    try:
        streams = ffprobe_json(path, "-select_streams", "v:0", "-show_entries", "stream=width,height")["streams"]
    except ToolError, KeyError, OSError:
        return None
    if not streams:
        return None
    width, height = to_int(streams[0].get("width")) or 0, to_int(streams[0].get("height")) or 0
    return height >= 720 or width >= 1280


def apply_artwork(path: Path, image: Path) -> dict[str, object]:
    """Replace every `covr` atom on `path` with `image` (in place).

    Use case: "season poster" — stamp the same image across every episode in
    a season so TV.app uses it as the season tile (it derives the season
    artwork from the episodes' covr atoms; if they all match, it picks that).
    """
    atomicparsley = _atomicparsley()
    if atomicparsley is None:
        return {"applied": False, "reason": "AtomicParsley not installed"}
    if not image.is_file():
        return {"applied": False, "reason": f"artwork not found: {image}"}
    args = [
        atomicparsley,
        str(path),
        "--artwork",
        "REMOVE_ALL",
        "--artwork",
        str(image),
        "--overWrite",
    ]
    r = run_tool(args, check=False)
    if r.returncode != 0:
        return {
            "applied": False,
            "reason": f"AtomicParsley exit {r.returncode}",
            "stderr_tail": r.stderr.splitlines()[-5:],
        }
    return {"applied": True, "image": str(image)}


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

    if movi := itunmovi_plist(meta):
        args += ["--rDNSatom", movi, "name=iTunMOVI", "domain=com.apple.iTunes"]
        fields_set.append("iTunMOVI")
    if (hd := is_hd(path)) is not None:
        add("--hdvideo", "true" if hd else "false")

    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        artwork_paths: list[Path] = []
        for i, art in enumerate(meta.artworks[:5]):  # cap at 5 to bound writes
            try:
                blob = download(art.url)
            except httpx.HTTPError, httpx.InvalidURL:
                continue
            p = tmp / f"art_{i}.jpg"
            p.write_bytes(blob)
            artwork_paths.append(p)
            add("--artwork", str(p))

        if artwork_paths:
            # Re-tagging must replace the old images, not stack new ones behind them
            # (TV.app shows the first). REMOVE_ALL has to come before the --artwork args.
            at = args.index("--artwork")
            args[at:at] = ["--artwork", "REMOVE_ALL"]
        args += ["--overWrite"]
        r = run_tool(args, check=False)
        if r.returncode != 0 and "insufficient space" in r.stdout:
            r = _retag_via_copy(args, path)
        if r.returncode != 0:
            return {
                "applied": False,
                "reason": f"AtomicParsley exit {r.returncode}",
                "stderr_tail": r.stderr.splitlines()[-10:],
            }

    return {
        "applied": True,
        "fields": sorted(set(fields_set)),
        "artwork_count": len(artwork_paths),
        "rating": meta.rating.itunes_extc() if meta.rating else None,
    }
