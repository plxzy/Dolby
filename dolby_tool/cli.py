"""`dolby-tool` command line: starts the web UI (default) or runs one of the analysis subcommands."""

import argparse
import ipaddress
import json
import sys
import time

import uvicorn

from .capture import (
    LOCAL_PLAYER_PREDICATE,
    PREDICATE,
    LogCapture,
    summarize_log_file,
    tvlog_summary_markdown,
)
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


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "movpkg":
        _main_movpkg(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "movpkg-scan":
        _main_movpkg_scan(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "tvlog-parse":
        _main_tvlog_parse(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "tvlog-capture":
        _main_tvlog_capture(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "hls-serve":
        _main_hls_serve(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "hls-prepare":
        _main_hls_prepare(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "hls-package":
        _main_hls_package(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "hls-package-capture":
        _main_hls_package_capture(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "convert":
        from .convert.commands import main_convert

        main_convert(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "tag":
        from .convert.commands import main_tag

        main_tag(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(prog="dolby-tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7878)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev).")
    args = parser.parse_args()

    try:
        is_loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        is_loopback = args.host in {"localhost"}

    if not is_loopback:
        print(
            f"⚠️  Warning: binding to non-loopback host {args.host}. This tool can inspect arbitrary local file paths."
        )

    uvicorn.run(
        "dolby_tool.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


def _main_movpkg(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool movpkg")
    parser.add_argument("path", help="Path to a downloaded TV.app .movpkg package.")
    parser.add_argument(
        "--selected-group",
        help="Optional HLS AudioGroup selected in a TV Capture, used to refine the verdict.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = analyze_movpkg(args.path, selected_group=args.selected_group)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(movpkg_summary_markdown(summary))


def _main_movpkg_scan(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool movpkg-scan")
    parser.add_argument("root", help="TV.app media directory, season folder, or a single `.movpkg` package.")
    parser.add_argument(
        "--selected-group",
        help="Optional HLS AudioGroup selected in a TV Capture, used to refine verdicts.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    scan = scan_movpkgs(args.root, selected_group=args.selected_group)
    if args.json:
        print(json.dumps(scan, indent=2, sort_keys=True))
    else:
        print(movpkg_scan_markdown(scan))


def _main_tvlog_parse(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool tvlog-parse")
    parser.add_argument("path", help="Path to a saved `log stream --style compact` text file.")
    parser.add_argument(
        "--profile",
        choices=["tv", "local-player"],
        default="local-player",
        help="Predicate profile recorded with the capture. Used only for summary metadata.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = summarize_log_file(args.path, predicate=_predicate_for_profile(args.profile))
    _print_tvlog_summary(summary, as_json=args.json)


def _main_tvlog_capture(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool tvlog-capture")
    parser.add_argument(
        "--profile",
        choices=["tv", "local-player"],
        default="tv",
        help="Use `tv` for normal TV.app capture or `local-player` for QuickTime/Safari controls.",
    )
    parser.add_argument("--seconds", type=float, default=60.0, help="Capture duration.")
    parser.add_argument("--output", help="Optional path to write the structured JSON summary.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    capture = LogCapture(predicate=_predicate_for_profile(args.profile))
    print(
        f"Capturing {args.profile} playback logs for {args.seconds:g}s...",
        file=sys.stderr,
    )
    capture.start()
    try:
        time.sleep(max(args.seconds, 0))
    except KeyboardInterrupt:
        pass
    summary = capture.stop()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")
    _print_tvlog_summary(summary, as_json=args.json)


def _main_hls_serve(argv: list[str]) -> None:
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


def _main_hls_prepare(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool hls-prepare")
    parser.add_argument("movpkg", help="Persisted-HLS .movpkg directory to flatten.")
    parser.add_argument("output_dir", help="Output HLS directory for hls-serve.")
    parser.add_argument("--overwrite", action="store_true", help="Allow writing into a non-empty output dir.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = prepare_hls_from_movpkg(args.movpkg, args.output_dir, overwrite=args.overwrite)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(prepare_hls_summary_markdown(summary))


def _main_hls_package(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool hls-package")
    parser.add_argument("input", help="Local media file to stream-copy into fMP4 HLS.")
    parser.add_argument("output_dir", help="Output HLS directory for hls-serve.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output dir.")
    parser.add_argument("--audio-stream", type=int, default=0, help="Zero-based input audio stream to map.")
    parser.add_argument("--segment-time", type=float, default=6.0, help="Target HLS segment duration.")
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Mux audio and video into one media playlist instead of a separate audio group.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    summary = package_hls_from_file(
        args.input,
        args.output_dir,
        overwrite=args.overwrite,
        audio_stream=args.audio_stream,
        segment_time=args.segment_time,
        split_audio_group=not args.combined,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(package_hls_summary_markdown(summary))


def _main_hls_package_capture(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="dolby-tool hls-package-capture")
    parser.add_argument("input", help="Local media file to stream-copy into fMP4 HLS.")
    parser.add_argument("output_dir", help="Output HLS directory for the temporary server.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output dir.")
    parser.add_argument("--audio-stream", type=int, default=0, help="Zero-based input audio stream to map.")
    parser.add_argument("--segment-time", type=float, default=6.0, help="Target HLS segment duration.")
    parser.add_argument("--seconds", type=float, default=60.0, help="Capture duration after opening QuickTime.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host; keep loopback for local playback.")
    parser.add_argument("--port", type=int, default=0, help="Bind port. Default 0 uses an ephemeral port.")
    parser.add_argument("--playlist", default="master.m3u8", help="Playlist filename to open.")
    parser.add_argument("--output", help="Optional path to write the structured JSON capture summary.")
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Mux audio and video into one media playlist instead of a separate audio group.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args(argv)

    package_summary = package_hls_from_file(
        args.input,
        args.output_dir,
        overwrite=args.overwrite,
        audio_stream=args.audio_stream,
        segment_time=args.segment_time,
        split_audio_group=not args.combined,
    )
    with running_hls_server(args.output_dir, host=args.host, port=args.port, quiet=True) as server:
        url = playlist_url(server, args.playlist)
        capture = LogCapture(predicate=LOCAL_PLAYER_PREDICATE)
        print(f"Serving HLS from {args.output_dir}", file=sys.stderr, flush=True)
        print(f"Opening QuickTime URL: {url}", file=sys.stderr, flush=True)
        print(f"Capturing local-player playback logs for {args.seconds:g}s...", file=sys.stderr, flush=True)
        capture.start()
        open_hls_url(url, "quicktime")
        try:
            time.sleep(max(args.seconds, 0))
        except KeyboardInterrupt:
            pass
        summary = capture.stop()
    summary["hls_package"] = package_summary
    summary["hls_url"] = url
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")
    _print_tvlog_summary(summary, as_json=args.json)


def _predicate_for_profile(profile: str) -> str:
    if profile == "local-player":
        return LOCAL_PLAYER_PREDICATE
    return PREDICATE


def _print_tvlog_summary(summary: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(tvlog_summary_markdown(summary))


if __name__ == "__main__":
    main()
