"""CLI dispatch helpers for `dolby-tool convert` and `dolby-tool tag`.

Wired from `dolby_tool/__main__.py`. Kept thin: argparse + glue.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import atoms, mux, subs_pgs
from .metadata import apple_tv, storefronts, tagger


_TV_RE = re.compile(r"(?P<show>.+?)[. _-]*[sS](?P<s>\d{1,2})[eE](?P<e>\d{1,3})")
_YEAR_RE = re.compile(r"(?P<title>.+?)[. _(]+(?P<year>(19|20)\d{2})")
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
    parents = list(p.resolve().parents) if str(p).startswith("/") else list(p.parents)
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

    yr = _YEAR_RE.search(stem)
    if yr:
        return ("movie", _clean(yr.group("title")), None, None)
    return ("movie", _clean(stem), None, None)


def _clean(s: str) -> str:
    return re.sub(r"[._]+", " ", s).strip()


# ISO/IEC 23001-8 string → numeric code map for the values ffprobe surfaces.
_PRIMARIES = {"bt709": 1, "unknown": 2, "bt470bg": 5, "smpte170m": 6, "bt2020": 9,
              "smpte428": 10, "smpte431": 11, "smpte432": 12}
_TRANSFER = {"bt709": 1, "unknown": 2, "bt470bg": 5, "smpte170m": 6, "bt2020-10": 14,
             "bt2020-12": 15, "smpte2084": 16, "arib-std-b67": 18, "iec61966-2-1": 13}
_MATRIX = {"bt709": 1, "unknown": 2, "fcc": 4, "bt470bg": 5, "smpte170m": 6,
           "bt2020nc": 9, "bt2020c": 10}


def _resolve_colr_args(args, src: Path) -> tuple[int, int, int, bool] | None:
    """Return (primaries, transfer, matrix, full_range) or None when not patching.

    Refuses to fall back to an SDR default: ambiguity is reported and skipped
    so the caller doesn't accidentally mislabel an HDR/DV source as BT.709.
    """
    explicit = (args.colr_primaries, args.colr_transfer, args.colr_matrix)
    if any(v is not None for v in explicit):
        if any(v is None for v in explicit):
            print("--colr-primaries/--colr-transfer/--colr-matrix must be passed together; "
                  "skipping colr patch.", file=sys.stderr)
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
        print(f"--colr-from-source: ffprobe returned ambiguous color tags "
              f"(primaries={v.get('color_primaries')!r}, transfer={v.get('color_transfer')!r}, "
              f"matrix={v.get('color_space')!r}). Skipping colr patch — pass explicit "
              "--colr-primaries/--colr-transfer/--colr-matrix instead.", file=sys.stderr)
        return None
    fr = (v.get("color_range") or "").lower() == "pc" or args.colr_full_range
    return (p, t, m, fr)


def _pick_interactive(prompt: str, options: list[str]) -> int | None:
    print(prompt, file=sys.stderr)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}", file=sys.stderr)
    print("  -. skip metadata", file=sys.stderr)
    try:
        raw = input("Pick: ").strip()
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

    if content_id:
        # Direct fetch path
        fake_hit = apple_tv.SearchHit(
            id=content_id, type=("Show" if kind == "tvShow" else "Movie"),
            title=None, description=None, release_date_ms=None,
            images={}, roles_summary=None, rating_display=None, raw={},
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
        labels = [
            f"{(h.title or '?')} ({h.year or '?'}) — id={h.id}"
            for h in hits[:8]
        ]
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


def main_convert(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool convert")
    parser.add_argument("input", help="Source media file (MKV, MP4, etc.).")
    parser.add_argument("output", help="Output .m4v path.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--no-metadata", action="store_true", help="Skip Apple TV metadata lookup.")
    parser.add_argument("--metadata-id", help="Apple TV content id (skip search).")
    parser.add_argument("--storefront", default="US", help="Country code (US, GB, AU, ...).")
    parser.add_argument("--language", default=None, help="Locale, e.g. en_US.")
    parser.add_argument("--non-interactive", action="store_true", help="Auto-pick top metadata hit.")
    parser.add_argument("--show", help="Override show name (forces TV mode).")
    parser.add_argument("--season", type=int, help="Override season number.")
    parser.add_argument("--episode", type=int, help="Override episode number.")
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
        "--colr-primaries", type=int, default=None,
        help="ISO/IEC 23001-8 colour primaries (e.g. 1=BT.709, 9=BT.2020). "
             "Requires --colr-transfer and --colr-matrix.",
    )
    parser.add_argument("--colr-transfer", type=int, default=None,
        help="Transfer characteristics (1=BT.709, 16=SMPTE2084/PQ, 18=HLG).")
    parser.add_argument("--colr-matrix", type=int, default=None,
        help="Matrix coefficients (1=BT.709, 9=BT.2020 NC).")
    parser.add_argument("--colr-full-range", action="store_true",
        help="Set the nclx full-range flag (default: limited range).")
    parser.add_argument(
        "--experimental-force-dec3-joc",
        action="store_true",
        help="DANGEROUS / EXPERIMENTAL. ORs the low bit of the last dec3 payload "
             "byte for every EAC-3 track. Does NOT parse the substream/JOC layout, "
             "and on plain E-AC-3 5.1 it lies to TV.app about Atmos capability. "
             "Recent dtrace shows TV.app local playback spatializes without this "
             "patch on the measured build — only use if you have an independent "
             "Atmos/JOC verification of the source.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    src = Path(args.input).expanduser().resolve()
    dst = Path(args.output).expanduser().resolve()

    summary: dict[str, object] = {"input": str(src), "output": str(dst)}
    summary["mux"] = mux.mux(src, dst, overwrite=args.overwrite)

    # PGS pass (re-mux source bitmap subs as VobSub into a sibling .m4v)
    if not args.skip_pgs:
        info = mux.probe(src)
        if info.bitmap_subs:
            pgs_out = dst.with_suffix(".pgs.m4v")
            r = subs_pgs.convert_and_remux(src, dst, info.bitmap_subs, output=pgs_out)
            summary["pgs"] = {
                "converted": r.converted, "skipped": r.skipped, "reason": r.reason,
                "output": str(pgs_out) if r.converted else None,
            }
            if r.converted:
                pgs_out.replace(dst)

    colr_values = _resolve_colr_args(args, src)
    if colr_values is not None:
        p, t, m, fr = colr_values
        summary["colr_patched"] = {
            "patched": atoms.patch_colr_nclx(dst, p, t, m, fr),
            "primaries": p, "transfer": t, "matrix": m, "full_range": fr,
        }
    if args.experimental_force_dec3_joc:
        print(
            "WARNING: --experimental-force-dec3-joc OR's the dec3 JOC bit without "
            "parsing the substream layout. If the source is not actually Atmos/JOC "
            "this writes a lie into the bitstream. Recent dtrace shows TV.app "
            "spatializes without this patch — prefer skipping unless you have an "
            "independent Atmos verification of the source.",
            file=sys.stderr,
        )
        summary["dec3_joc_patched"] = atoms.patch_dec3_joc(dst)

    if not args.no_metadata:
        meta = resolve_metadata(
            str(src),
            storefront=args.storefront,
            language=args.language,
            content_id=args.metadata_id,
            interactive=not args.non_interactive and sys.stdin.isatty(),
            show_override=args.show,
            season_override=args.season,
            episode_override=args.episode,
        )
        if meta is not None:
            summary["metadata"] = tagger.apply(dst, meta)
            summary["matched_title"] = meta.title
        else:
            summary["metadata"] = {"applied": False, "reason": "no match / skipped"}

    _print_summary(summary, as_json=args.json)


def main_tag(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool tag")
    parser.add_argument("file", help="Existing .m4v/.mp4 to tag in place.")
    parser.add_argument("--metadata-id", help="Apple TV content id.")
    parser.add_argument("--storefront", default="US")
    parser.add_argument("--language", default=None)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--show", help="Override show name (forces TV mode).")
    parser.add_argument("--season", type=int, help="Override season number.")
    parser.add_argument("--episode", type=int, help="Override episode number.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.file).expanduser().resolve()
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
    summary: dict[str, object] = {"file": str(path)}
    if meta is None:
        summary["metadata"] = {"applied": False, "reason": "no match / skipped"}
    else:
        summary["matched_title"] = meta.title
        summary["metadata"] = tagger.apply(path, meta)
    _print_summary(summary, as_json=args.json)


def _print_summary(summary: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return
    print(f"# convert summary\n")
    for k, v in summary.items():
        print(f"- **{k}**: `{v}`")
