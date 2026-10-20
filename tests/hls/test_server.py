from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dolby_tool.hls.server import playlist_url, running_hls_server


def test_hls_server_supports_byte_ranges(tmp_path):
    (tmp_path / "master.m3u8").write_text("#EXTM3U\nsegment.m4s\n", encoding="utf-8")
    (tmp_path / "segment.m4s").write_bytes(b"0123456789")

    with running_hls_server(tmp_path, port=0, quiet=True) as server:
        url = playlist_url(server, "segment.m4s")
        request = Request(url, headers={"Range": "bytes=2-5"})
        with urlopen(request, timeout=5) as response:
            body = response.read()

    assert response.status == 206
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert body == b"2345"


def test_hls_server_supports_suffix_byte_ranges(tmp_path):
    (tmp_path / "segment.m4s").write_bytes(b"0123456789")

    with running_hls_server(tmp_path, port=0, quiet=True) as server:
        url = playlist_url(server, "segment.m4s")
        request = Request(url, headers={"Range": "bytes=-3"})
        with urlopen(request, timeout=5) as response:
            body = response.read()

    assert response.status == 206
    assert response.headers["Content-Range"] == "bytes 7-9/10"
    assert body == b"789"


def test_hls_server_rejects_unsatisfiable_ranges(tmp_path):
    (tmp_path / "segment.m4s").write_bytes(b"0123456789")

    with running_hls_server(tmp_path, port=0, quiet=True) as server:
        url = playlist_url(server, "segment.m4s")
        request = Request(url, headers={"Range": "bytes=99-120"})
        try:
            urlopen(request, timeout=5)
        except HTTPError as error:
            status = error.code
            content_range = error.headers["Content-Range"]
        else:
            raise AssertionError("expected HTTP 416")

    assert status == 416
    assert content_range == "bytes */10"
