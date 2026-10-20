"""CLI dispatch helpers for `dolby-tool convert` and `dolby-tool tag`.

Wired from `dolby_tool/cli.py`. Kept thin: argparse + glue.
"""

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from ..common import ToolError
from . import atoms, mux, subs_pgs
from .metadata import apple_tv, storefronts, tagger

# "Show.Name.S02E03…": the SxxExx marker must follow a separator and end at a non-digit,
# so words like "Classes2e1" or "S01E021080p" don't parse as an episode.
_TV_RE = re.compile(r"(?P<show>.+?)[. _-]+[sS](?P<s>\d{1,2})[eE](?P<e>\d{1,3})(?!\d)")
# A year is a standalone 19xx/20xx token; "(2017)" is preferred, otherwise the last one wins
# ("Blade.Runner.2049.2017" is the 2017 film Blade Runner 2049).
_PAREN_YEAR_RE = re.compile(r"\((?:19|20)\d{2}\)")
_YEAR_RE = re.compile(r"(?<![0-9A-Za-z])(?:19|20)\d{2}(?![0-9A-Za-z])")
# Apple-TV / Subler layout: ".../TV Shows/<Show>/Season N/EE Title.ext"
_SEASON_DIR_RE = re.compile(r"^(?:Season|Sezon|Series|Staffel)[\s_]*(\d{1,2})$", re.IGNORECASE)
_EP_PREFIX_RE = re.compile(r"^(?P<ep>\d{1,3})[\s._-]+(?P<title>.+)$")


def guess_query(filename: str | Path) -> tuple[str, str, int | None, int | None]:
    """Heuristic title/year/season/episode guess from a filename (or path).

    Returns (kind, title, season, episode) where kind is "movie" or "tvShow".
    Path is preferred over bare filename so we can pick up the
    `…/<Show>/Season N/EE Title.ext` library layout.
    """
    p = Path(filename)
    stem = p.stem
    tv = _TV_RE.search(stem)
    if tv:
        return ("tvShow", _clean(tv.group("show")), int(tv.group("s")), int(tv.group("e")))

    # Library-layout fallback: parent directory matches "Season N", episode
    # number is the leading digits of the filename, show name is the dir above.
    parents = list(p.resolve().parents) if p.is_absolute() else list(p.parents)
    if parents:
        season_match = _SEASON_DIR_RE.match(parents[0].name)
        ep_match = _EP_PREFIX_RE.match(stem)
        if season_match and ep_match and len(parents) >= 2:
            return (
                "tvShow",
                _clean(parents[1].name),
                int(season_match.group(1)),
                int(ep_match.group("ep")),
            )

    return ("movie", _movie_title(stem), None, None)


def _movie_title(stem: str) -> str:
    """The title before the release year, or the whole cleaned stem when there is no year."""
    years = list(_PAREN_YEAR_RE.finditer(stem)) or list(_YEAR_RE.finditer(stem))
    for year in reversed(years):
        if title := _clean(stem[: year.start()]).rstrip(" ([-"):
            return title
    return _clean(stem)


def _clean(s: str) -> str:
    return re.sub(r"[._]+", " ", s).strip()


# ISO/IEC 23001-8 string → numeric code map for the values ffprobe surfaces.
_PRIMARIES = {
    "bt709": 1,
    "unknown": 2,
    "bt470bg": 5,
    "smpte170m": 6,
    "bt2020": 9,
    "smpte428": 10,
    "smpte431": 11,
    "smpte432": 12,
}
_TRANSFER = {
    "bt709": 1,
    "unknown": 2,
    "bt470bg": 5,
    "smpte170m": 6,
    "bt2020-10": 14,
    "bt2020-12": 15,
    "smpte2084": 16,
    "arib-std-b67": 18,
    "iec61966-2-1": 13,
}
_MATRIX = {"bt709": 1, "unknown": 2, "fcc": 4, "bt470bg": 5, "smpte170m": 6, "bt2020nc": 9, "bt2020c": 10}


def _resolve_colr_args(args: argparse.Namespace, src: Path) -> tuple[int, int, int, bool] | None:
    """Return (primaries, transfer, matrix, full_range) or None when not patching.

    Refuses to fall back to an SDR default: ambiguity is reported and skipped
    so the caller doesn't accidentally mislabel an HDR/DV source as BT.709.
    """
    explicit = (args.colr_primaries, args.colr_transfer, args.colr_matrix)
    if any(v is not None for v in explicit):
        if any(v is None for v in explicit):
            print(
                "--colr-primaries/--colr-transfer/--colr-matrix must be passed together; skipping colr patch.",
                file=sys.stderr,
            )
            return None
        return (args.colr_primaries, args.colr_transfer, args.colr_matrix, args.colr_full_range)
    if not args.colr_from_source:
        return None
    info = mux.probe(src)
    v = info.video.raw
    p = _PRIMARIES.get((v.get("color_primaries") or "").lower())
    t = _TRANSFER.get((v.get("color_transfer") or "").lower())
    m = _MATRIX.get((v.get("color_space") or "").lower())
    if not (p and t and m):
        print(
            f"--colr-from-source: ffprobe returned ambiguous color tags "
            f"(primaries={v.get('color_primaries')!r}, transfer={v.get('color_transfer')!r}, "
            f"matrix={v.get('color_space')!r}). Skipping colr patch — pass explicit "
            "--colr-primaries/--colr-transfer/--colr-matrix instead.",
            file=sys.stderr,
        )
        return None
    fr = (v.get("color_range") or "").lower() == "pc" or args.colr_full_range
    return (p, t, m, fr)


def _pick_interactive(prompt: str, options: list[str]) -> int | None:
    print(prompt, file=sys.stderr)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}", file=sys.stderr)
    print("  -. skip metadata", file=sys.stderr)
    # input() would write its prompt to stdout and corrupt --json output.
    print("Pick: ", end="", file=sys.stderr, flush=True)
    try:
        raw = input().strip()
    except EOFError:
        return None
    if raw in ("", "-", "0"):
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return idx
    except ValueError:
        pass
    return None


def resolve_metadata(
    source_name: str,
    *,
    storefront: str,
    language: str | None,
    content_id: str | None,
    interactive: bool,
    show_override: str | None = None,
    season_override: int | None = None,
    episode_override: int | None = None,
) -> apple_tv.Metadata | None:
    store = storefronts.resolve(storefront, language)
    kind, title, season, episode = guess_query(source_name)
    if show_override:
        kind, title = "tvShow", show_override
    if season_override is not None:
        season, kind = season_override, "tvShow"
    if episode_override is not None:
        episode, kind = episode_override, "tvShow"

    with apple_tv.session():
        if content_id:
            # Direct fetch path
            fake_hit = apple_tv.SearchHit(
                id=content_id,
                type=("Show" if kind == "tvShow" else "Movie"),
                title=None,
                description=None,
                release_date_ms=None,
                images={},
                roles_summary=None,
                rating_display=None,
                raw={},
            )
            if kind == "tvShow":
                if season is None or episode is None:
                    print(
                        "TV mode requires --season and --episode (or an SxxExx filename) "
                        "to fetch episode metadata; refusing to fall back to movie tagging.",
                        file=sys.stderr,
                    )
                    return None
                return apple_tv.fetch_episode(fake_hit, season, episode, store)
            return apple_tv.fetch_movie(fake_hit, store)

        if kind == "tvShow":
            hits = apple_tv.search_shows(title, store)
        else:
            hits = apple_tv.search_movies(title, store)

        if not hits:
            print(f"No Apple TV matches for {title!r} ({storefront}).", file=sys.stderr)
            return None

        if interactive:
            labels = [f"{(h.title or '?')} ({h.year or '?'}) — id={h.id}" for h in hits[:8]]
            idx = _pick_interactive(f"Apple TV results for {title!r}:", labels)
            if idx is None:
                return None
            chosen = hits[idx]
        else:
            chosen = hits[0]

        if kind == "tvShow":
            if season is None or episode is None:
                print(
                    f"TV match {chosen.title!r} chosen but season/episode missing; "
                    "pass --season/--episode or use an SxxExx filename. Skipping metadata.",
                    file=sys.stderr,
                )
                return None
            return apple_tv.fetch_episode(chosen, season, episode, store)
        return apple_tv.fetch_movie(chosen, store)


def _storefront(value: str) -> str:
    """argparse type: a known storefront country code, upper-cased."""
    country = value.upper()
    if country not in storefronts.countries():
        raise argparse.ArgumentTypeError(f"unknown storefront {value!r} (known: {', '.join(storefronts.countries())})")
    return country


def main_convert(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool convert")
    parser.add_argument("input", help="Source media file (MKV, MP4, etc.).")
    parser.add_argument("output", help="Output .m4v path.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-metadata", action="store_true", help="Skip Apple TV metadata lookup.")
    _add_metadata_args(parser)
    parser.add_argument("--skip-pgs", action="store_true", help="Don't try PGS→VobSub conversion.")
    # `colr` patching is intentionally explicit. There is NO safe default
    # because writing BT.709/SDR values into an HDR10 or Dolby Vision file
    # silently mislabels it as SDR and TV.app will tone-map it incorrectly.
    # Pick exactly one source of values:
    parser.add_argument(
        "--colr-from-source",
        action="store_true",
        help="Patch nclx colr in-place using primaries/transfer/matrix read from "
        "the source via ffprobe. Safe for HDR10/DV (does not change codes).",
    )
    parser.add_argument(
        "--colr-primaries",
        type=int,
        default=None,
        help="ISO/IEC 23001-8 colour primaries (e.g. 1=BT.709, 9=BT.2020). Requires --colr-transfer and --colr-matrix.",
    )
    parser.add_argument(
        "--colr-transfer", type=int, default=None, help="Transfer characteristics (1=BT.709, 16=SMPTE2084/PQ, 18=HLG)."
    )
    parser.add_argument("--colr-matrix", type=int, default=None, help="Matrix coefficients (1=BT.709, 9=BT.2020 NC).")
    parser.add_argument(
        "--colr-full-range", action="store_true", help="Set the nclx full-range flag (default: limited range)."
    )
    parser.add_argument(
        "--experimental-force-dec3-joc",
        action="store_true",
        help="EXPERIMENTAL. Sets the JOC flag (flag_ec3_extension_type_a) in the dec3 "
        "box of every E-AC-3 track that already has room for it; tracks without "
        "room (e.g. ffmpeg's plain E-AC-3 dec3) or with dependent substreams are "
        "left untouched and reported. Recent dtrace shows TV.app local playback "
        "spatializes without this patch on the measured build — only use if you "
        "have an independent Atmos/JOC verification of the source.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    src = Path(args.input).expanduser().resolve()
    dst = Path(args.output).expanduser().resolve()

    summary: dict[str, object] = {"input": str(src), "output": str(dst)}
    try:
        summary["mux"] = mux.mux(src, dst, overwrite=args.overwrite)
    except (ValueError, ToolError) as e:
        sys.exit(f"error: {e}")

    # PGS pass (re-mux source bitmap subs as VobSub into a sibling .m4v)
    if not args.skip_pgs:
        info = mux.probe(src)
        if info.bitmap_subs:
            pgs_out = dst.with_suffix(".pgs.m4v")
            r = subs_pgs.convert_and_remux(src, dst, info.bitmap_subs, output=pgs_out)
            summary["pgs"] = {
                "converted": r.converted,
                "skipped": r.skipped,
                "reason": r.reason,
                "output": str(pgs_out) if r.converted else None,
            }
            if r.converted:
                pgs_out.replace(dst)

    colr_values = _resolve_colr_args(args, src)
    if colr_values is not None:
        p, t, m, fr = colr_values
        summary["colr_patched"] = {
            "patched": atoms.patch_colr_nclx(dst, p, t, m, fr),
            "primaries": p,
            "transfer": t,
            "matrix": m,
            "full_range": fr,
        }
    if args.experimental_force_dec3_joc:
        print(
            "WARNING: --experimental-force-dec3-joc sets the dec3 JOC flag. If the "
            "source is not actually Atmos/JOC this writes a lie into the bitstream. "
            "Recent dtrace shows TV.app spatializes without this patch — prefer "
            "skipping unless you have an independent Atmos verification of the source.",
            file=sys.stderr,
        )
        summary["dec3_joc_patched"] = [asdict(r) for r in atoms.patch_dec3_joc(dst)]

    if not args.no_metadata:
        summary |= _tag(dst, args)

    _print_summary(summary, title="convert summary", as_json=args.json)


def main_tag(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool tag")
    parser.add_argument("file", help="Existing .m4v/.mp4 to tag in place.")
    _add_metadata_args(parser)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.file).expanduser().resolve()
    summary: dict[str, object] = {"file": str(path)}
    summary |= _tag(path, args)
    _print_summary(summary, title="tag summary", as_json=args.json)


def _add_metadata_args(parser: argparse.ArgumentParser) -> None:
    """Apple TV lookup options shared by `convert` and `tag`."""
    parser.add_argument("--metadata-id", help="Apple TV content id (skip search).")
    parser.add_argument("--storefront", type=_storefront, default="US", help="Country code (US, GB, AU, ...).")
    parser.add_argument("--language", default=None, help="Locale, e.g. en_US.")
    parser.add_argument("--non-interactive", action="store_true", help="Auto-pick top metadata hit.")
    parser.add_argument("--show", help="Override show name (forces TV mode).")
    parser.add_argument("--season", type=int, help="Override season number.")
    parser.add_argument("--episode", type=int, help="Override episode number.")


def _tag(path: Path, args: argparse.Namespace) -> dict[str, object]:
    """Look up Apple TV metadata for `path` and write it in place; returns summary entries."""
    meta = resolve_metadata(
        str(path),
        storefront=args.storefront,
        language=args.language,
        content_id=args.metadata_id,
        interactive=not args.non_interactive and sys.stdin.isatty(),
        show_override=args.show,
        season_override=args.season,
        episode_override=args.episode,
    )
    if meta is None:
        return {"metadata": {"applied": False, "reason": "no match / skipped"}}
    return {"metadata": tagger.apply(path, meta), "matched_title": meta.title}


def _print_summary(summary: dict[str, object], *, title: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return
    print(f"# {title}\n")
    for k, v in summary.items():
        print(f"- **{k}**: `{v}`")
