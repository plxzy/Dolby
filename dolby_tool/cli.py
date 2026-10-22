"""`dolby-tool` command line: starts the web UI (default) or runs one of the subcommands."""

import argparse
import ipaddress
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import InspectError, compare_files, compare_markdown, inspect_file, spec_markdown
from .capture import (
    LOCAL_PLAYER_PREDICATE,
    PLAYER_PROCESSES,
    PREDICATE,
    LogCapture,
    SystemSampler,
    summarize_log_file,
    tvlog_summary_markdown,
)
from .common import ToolError
from .hls import (
    analyze_movpkg,
    create_hls_server,
    movpkg_scan_markdown,
    movpkg_summary_markdown,
    open_hls_url,
    package_hls_from_file,
    package_hls_summary_markdown,
    playlist_url,
    prepare_hls_from_movpkg,
    prepare_hls_summary_markdown,
    running_hls_server,
    scan_movpkgs,
)

DEFAULT_PORT = 7878
PREDICATES = {"tv": PREDICATE, "local-player": LOCAL_PLAYER_PREDICATE}


@dataclass(frozen=True, slots=True)
class Command:
    help: str
    run: Callable[[list[str]], None]


def main(argv: list[str] | None = None) -> int:
    """Dispatch `dolby-tool [command] …`; with no command (or only server flags) start the web UI."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv and argv[0] in COMMANDS:
            COMMANDS[argv[0]].run(argv[1:])
        else:
            _serve(argv)
    except KeyboardInterrupt:
        return 130
    except (InspectError, ToolError, ValueError, OSError) as e:
        print(f"dolby-tool: error: {e}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# web UI


def _serve(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="dolby-tool",
        description="Inspect Dolby Vision / HDR / Atmos media and capture what TV.app actually plays.\n"
        "Without a command, starts the web UI on http://127.0.0.1:7878.",
        epilog=_commands_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback only).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT}).")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (development).")
    parser.add_argument("--version", action="version", version=f"dolby-tool {__version__}")
    args = parser.parse_args(argv)

    if not _is_loopback(args.host):
        print(
            f"⚠️  Warning: binding to non-loopback host {args.host}. This tool can inspect arbitrary local file paths.",
            file=sys.stderr,
        )
    os.environ["DOLBY_TOOL_HOST"] = args.host  # read by dolby_tool.web.app to build its Host/Origin allow-lists

    import uvicorn

    uvicorn.run("dolby_tool.web.app:app", host=args.host, port=args.port, reload=args.reload, log_level="info")


def _commands_epilog() -> str:
    width = max(map(len, COMMANDS))
    lines = [f"  {name.ljust(width)}  {command.help}" for name, command in COMMANDS.items()]
    return "commands:\n" + "\n".join(lines) + "\n\nRun `dolby-tool <command> --help` for a command's options."


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host == "localhost"


def _browser_build(argv: list[str]) -> None:
    from .web.browser_build import build_browser

    parser = argparse.ArgumentParser(
        prog="dolby-tool browser-build",
        description="Build the static browser inspector: Inspect runs in the visitor's browser, nothing is uploaded.",
    )
    parser.add_argument("--out", default="dist/browser", help="Output folder, emptied first (default: dist/browser).")
    parser.add_argument("--tarball", help="Use this mediainfo.js tarball instead of the cached download.")
    args = parser.parse_args(argv)

    tarball = Path(args.tarball).expanduser() if args.tarball else None
    print(build_browser(Path(args.out), tarball=tarball))


# ---------------------------------------------------------------------------
# analysis


def _inspect(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool inspect", description="Inspect one media file.")
    parser.add_argument("path", help="Media file (.mp4, .m4v, .mkv, .mov, .ts).")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    spec = inspect_file(str(Path(args.path).expanduser()))
    _emit(spec.model_dump(mode="json"), spec_markdown(spec), as_json=args.json)


def _compare(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool compare", description="Rank several media files.")
    parser.add_argument("paths", nargs="+", help="Media files to compare.")
    parser.add_argument(
        "--weight",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override a score weight, e.g. --weight atmos=30 (repeatable).",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    weights = dict(_parse_weight(item, parser) for item in args.weight) or None
    result = compare_files([str(Path(p).expanduser()) for p in args.paths], weights)
    _emit(result.model_dump(mode="json"), compare_markdown(result), as_json=args.json)


def _parse_weight(item: str, parser: argparse.ArgumentParser) -> tuple[str, float]:
    key, sep, value = item.partition("=")
    try:
        if not sep:
            raise ValueError
        return key.strip(), float(value)
    except ValueError:
        parser.error(f"invalid --weight {item!r}; expected KEY=NUMBER")


# ---------------------------------------------------------------------------
# .movpkg inventory


def _movpkg(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool movpkg")
    parser.add_argument("path", help="Path to a downloaded TV.app .movpkg package.")
    parser.add_argument(
        "--selected-group",
        help="Optional HLS AudioGroup selected in a TV Capture, used to refine the verdict.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = analyze_movpkg(args.path, selected_group=args.selected_group)
    _emit(summary, movpkg_summary_markdown(summary), as_json=args.json)


def _movpkg_scan(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool movpkg-scan")
    parser.add_argument("root", help="TV.app media directory, season folder, or a single `.movpkg` package.")
    parser.add_argument(
        "--selected-group",
        help="Optional HLS AudioGroup selected in a TV Capture, used to refine verdicts.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    scan = scan_movpkgs(args.root, selected_group=args.selected_group)
    _emit(scan, movpkg_scan_markdown(scan), as_json=args.json)


# ---------------------------------------------------------------------------
# playback log capture


def _tvlog_parse(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool tvlog-parse")
    parser.add_argument("path", help="Path to a saved `log stream --style compact` text file.")
    parser.add_argument(
        "--profile",
        choices=list(PREDICATES),
        default="local-player",
        help="Predicate profile recorded with the capture. Used only for summary metadata.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = summarize_log_file(args.path, predicate=PREDICATES[args.profile])
    _emit(summary, tvlog_summary_markdown(summary), as_json=args.json)


def _tvlog_capture(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool tvlog-capture")
    parser.add_argument(
        "--profile",
        choices=list(PREDICATES),
        default="tv",
        help="Use `tv` for normal TV.app capture or `local-player` for QuickTime/Safari controls.",
    )
    parser.add_argument("--seconds", type=float, default=60.0, help="Capture duration.")
    parser.add_argument("--output", help="Optional path to write the structured JSON summary.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    capture = LogCapture(predicate=PREDICATES[args.profile], system=SystemSampler(PLAYER_PROCESSES[args.profile]))
    _announce_capture(capture, args.profile, args.seconds)
    with capture:
        _sleep(args.seconds)
        summary = capture.stop()
    _finish_capture(summary, output=args.output, as_json=args.json)


# ---------------------------------------------------------------------------
# local HLS


def _hls_serve(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool hls-serve")
    parser.add_argument("directory", help="Prepared HLS directory containing a playlist.")
    parser.add_argument("--playlist", default="master.m3u8", help="Playlist filename to print/open.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host; keep loopback for local playback.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port. Use 0 for an ephemeral port.")
    parser.add_argument(
        "--open",
        choices=["none", "quicktime", "safari"],
        default="none",
        help="Optionally open the playlist URL in an Apple player.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress request logging.")
    args = parser.parse_args(argv)

    server = create_hls_server(args.directory, host=args.host, port=args.port, quiet=args.quiet)
    url = playlist_url(server, args.playlist)
    print(f"Serving HLS from {args.directory}", flush=True)
    print(f"URL: {url}", flush=True)
    if args.open != "none":
        open_hls_url(url, args.open)
        print(f"Opened in {args.open}.", flush=True)
    print("Press Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _hls_prepare(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool hls-prepare")
    parser.add_argument("movpkg", help="Persisted-HLS .movpkg directory to flatten.")
    parser.add_argument("output_dir", help="Output HLS directory for hls-serve.")
    parser.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty output dir.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = prepare_hls_from_movpkg(args.movpkg, args.output_dir, overwrite=args.overwrite)
    _emit(summary, prepare_hls_summary_markdown(summary), as_json=args.json)


def _add_package_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", help="Local media file to stream-copy into fMP4 HLS.")
    parser.add_argument("output_dir", help="Output HLS directory.")
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace files from a previous package run in the output dir."
    )
    parser.add_argument("--audio-stream", type=int, default=0, help="Zero-based input audio stream to map.")
    parser.add_argument("--segment-time", type=float, default=6.0, help="Target HLS segment duration.")
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Mux audio and video into one media playlist instead of a separate audio group.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")


def _package(args: argparse.Namespace) -> dict[str, Any]:
    return package_hls_from_file(
        args.input,
        args.output_dir,
        overwrite=args.overwrite,
        audio_stream=args.audio_stream,
        segment_time=args.segment_time,
        split_audio_group=not args.combined,
    )


def _hls_package(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool hls-package")
    _add_package_arguments(parser)
    args = parser.parse_args(argv)

    summary = _package(args)
    _emit(summary, package_hls_summary_markdown(summary), as_json=args.json)


def _hls_package_capture(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool hls-package-capture")
    _add_package_arguments(parser)
    parser.add_argument("--seconds", type=float, default=60.0, help="Capture duration after opening QuickTime.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host; keep loopback for local playback.")
    parser.add_argument("--port", type=int, default=0, help="Bind port. Default 0 uses an ephemeral port.")
    parser.add_argument("--playlist", default="master.m3u8", help="Playlist filename to open.")
    parser.add_argument("--output", help="Optional path to write the structured JSON capture summary.")
    args = parser.parse_args(argv)

    package_summary = _package(args)
    with running_hls_server(args.output_dir, host=args.host, port=args.port, quiet=True) as server:
        url = playlist_url(server, args.playlist)
        print(f"Serving HLS from {args.output_dir}", file=sys.stderr, flush=True)
        print(f"Opening QuickTime URL: {url}", file=sys.stderr, flush=True)
        capture = LogCapture(predicate=LOCAL_PLAYER_PREDICATE, system=SystemSampler(PLAYER_PROCESSES["local-player"]))
        _announce_capture(capture, "local-player", args.seconds)
        with capture:
            open_hls_url(url, "quicktime")
            _sleep(args.seconds)
            summary = capture.stop()
    summary["hls_package"] = package_summary
    summary["hls_url"] = url
    _finish_capture(summary, output=args.output, as_json=args.json)


# ---------------------------------------------------------------------------
# MKV → Apple TV (.m4v) conversion — imported lazily: it pulls in the HTTP client


def _convert(argv: list[str]) -> None:
    from .convert.commands import main_convert

    main_convert(argv)


def _tag(argv: list[str]) -> None:
    from .convert.commands import main_tag

    main_tag(argv)


# ---------------------------------------------------------------------------
# helpers


def _emit(data: Any, markdown: str, *, as_json: bool) -> None:
    print(json.dumps(data, indent=2, sort_keys=True) if as_json else markdown)


def _sleep(seconds: float) -> None:
    try:
        time.sleep(max(seconds, 0))
    except KeyboardInterrupt:
        pass


def _announce_capture(capture: LogCapture, profile: str, seconds: float) -> None:
    """Say up front whether the unified log is in reach; without admin only the system sampler runs."""
    access = capture.log_access
    sources = "playback logs + system evidence" if access["available"] else "system evidence only"
    print(f"Capturing {profile} {sources} for {seconds:g}s...", file=sys.stderr, flush=True)
    if not access["available"]:
        print(f"  log stream skipped: {access['reason']}", file=sys.stderr, flush=True)


def _finish_capture(summary: dict[str, Any], *, output: str | None, as_json: bool) -> None:
    if output:
        Path(output).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _emit(summary, tvlog_summary_markdown(summary), as_json=as_json)


COMMANDS: dict[str, Command] = {
    "serve": Command("Start the web UI (the default when no command is given).", _serve),
    "browser-build": Command("Build the static browser inspector (no server needed).", _browser_build),
    "inspect": Command("Inspect one file: Dolby Vision, HDR, Atmos, tracks.", _inspect),
    "compare": Command("Score and rank several files against each other.", _compare),
    "tvlog-capture": Command("Capture TV.app / QuickTime playback logs for N seconds (macOS).", _tvlog_capture),
    "tvlog-parse": Command("Summarise a saved `log stream` capture.", _tvlog_parse),
    "movpkg": Command("Inventory a downloaded TV.app .movpkg package.", _movpkg),
    "movpkg-scan": Command("Inventory every .movpkg under a folder.", _movpkg_scan),
    "hls-package": Command("Package a local file as fMP4 HLS for QuickTime/Safari tests.", _hls_package),
    "hls-package-capture": Command("hls-package + serve + open QuickTime + capture logs.", _hls_package_capture),
    "hls-prepare": Command("Flatten a persisted-HLS .movpkg into a servable folder.", _hls_prepare),
    "hls-serve": Command("Serve an HLS folder with byte-range support (loopback).", _hls_serve),
    "convert": Command("Remux MKV → Apple-TV-ready .m4v (+ optional Apple TV metadata).", _convert),
    "tag": Command("Write Apple TV metadata/artwork into existing .m4v/.mp4 files.", _tag),
}
