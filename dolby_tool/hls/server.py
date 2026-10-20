"""Loopback HTTP server for prepared HLS folders, with the byte-range support Apple players need."""

import contextlib
import os
import subprocess
import threading
from collections.abc import Iterator
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from .playlist import MASTER_PLAYLIST

# `--open` choices and the macOS application each one launches.
_PLAYER_APPS = {"quicktime": "QuickTime Player", "safari": "Safari"}


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler variant with single-range support."""

    range: tuple[int, int] | None = None

    def send_head(self) -> BinaryIO | None:
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        ctype = self.guess_type(path)
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        size = os.fstat(f.fileno()).st_size
        start, end = self._parse_range(size)
        if start is None:
            self.range = None
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", self.date_time_string(os.fstat(f.fileno()).st_mtime))
            self.end_headers()
            return f

        if start >= size or end < start:
            f.close()
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return None

        end = min(end, size - 1)
        self.range = (start, end)
        f.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", self.date_time_string(os.fstat(f.fileno()).st_mtime))
        self.end_headers()
        return f

    def copyfile(self, source: BinaryIO, outputfile: BinaryIO) -> None:
        if self.range is None:
            return super().copyfile(source, outputfile)

        start, end = self.range
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def _parse_range(self, size: int) -> tuple[int | None, int]:
        header = self.headers.get("Range")
        if not header:
            return None, size - 1
        if not header.startswith("bytes=") or "," in header:
            return size, 0

        spec = header.removeprefix("bytes=").strip()
        start_s, sep, end_s = spec.partition("-")
        if sep != "-":
            return size, 0
        try:
            if start_s == "":
                suffix = int(end_s)
                if suffix <= 0:
                    return size, 0
                return max(size - suffix, 0), size - 1
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        except ValueError:
            return size, 0
        return start, end

    def log_message(self, format: str, *args: object) -> None:
        if not getattr(self.server, "quiet", False):
            super().log_message(format, *args)


class HLSServer(ThreadingHTTPServer):
    """Threaded HTTP server that knows whether request logging is muted."""

    quiet: bool = False


def create_hls_server(
    directory: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    quiet: bool = False,
) -> HLSServer:
    """Bind (but do not start) a server for `directory`; port 0 picks a free port."""
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"HLS directory does not exist: {root}")
    server = HLSServer((host, port), partial(RangeRequestHandler, directory=str(root)))
    server.quiet = quiet
    return server


def playlist_url(server: ThreadingHTTPServer, playlist: str = MASTER_PLAYLIST) -> str:
    """The URL under which `server` serves `playlist`."""
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/{quote(playlist)}"


@contextlib.contextmanager
def running_hls_server(
    directory: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    quiet: bool = False,
) -> Iterator[HLSServer]:
    """Serve `directory` from a background thread for the duration of the `with` block."""
    server = create_hls_server(directory, host=host, port=port, quiet=quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def open_hls_url(url: str, app: str) -> None:
    """Open `url` in QuickTime Player or Safari (`app` is a `--open` choice; "none" does nothing)."""
    if app == "none":
        return
    subprocess.run(["open", "-a", _PLAYER_APPS[app], url], check=False)
