import asyncio
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dolby_tool import __version__
from dolby_tool.common import ToolError
from dolby_tool.web import filesystem
from dolby_tool.web.app import create_app
from dolby_tool.web.routes import capture as capture_routes

FIXTURES = Path(__file__).parents[1] / "fixtures"
LOCAL = "http://127.0.0.1:7878"
WS_URL = "ws://127.0.0.1:7878/ws/tvlog"  # websocket_connect ignores base_url


@pytest.fixture
def client():
    with TestClient(create_app(), base_url="http://127.0.0.1:7878") as c:
        yield c


# ---------------------------------------------------------------------------
# static UI + system


def test_index_and_static_assets_are_served_without_heuristic_caching(client):
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert index.headers["cache-control"] == "no-cache"

    asset = next(p for p in (Path(__file__).parents[2] / "dolby_tool/web/static").rglob("*.css"))
    rel = asset.relative_to(Path(__file__).parents[2] / "dolby_tool/web/static").as_posix()
    static = client.get(f"/static/{rel}")
    assert static.status_code == 200
    assert static.headers["cache-control"] == "no-cache"
    assert "etag" in static.headers


def test_health_and_capabilities(client):
    assert client.get("/api/health").json() == {"status": "ok", "version": __version__}
    caps = client.get("/api/capabilities").json()
    assert set(caps["tools"]) == {"ffprobe", "mediainfo", "osascript", "log", "mdfind"}
    assert isinstance(caps["platform"], str)


def test_openapi_documents_every_area(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == __version__
    assert {"/api/inspect", "/api/compare", "/api/list", "/api/find", "/api/pick", "/api/tvlog/parse"} <= set(
        schema["paths"]
    )
    assert "MediaSpec" in schema["components"]["schemas"]


# ---------------------------------------------------------------------------
# analysis


@pytest.mark.requires_ffmpeg
def test_inspect_and_compare_real_clips(client, media_clip):
    sdr = media_clip("web_sdr.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    hevc = media_clip("web_hevc.mp4", "-c:v", "libx265", "-tag:v", "hvc1", "-x265-params", "log-level=error", "-an")

    spec = client.post("/api/inspect", json={"path": f"file://{sdr}"}).json()
    assert spec["filename"] == "web_sdr.mp4"
    assert spec["video"]["codec"] == "h264"
    assert spec["audio"][0]["codec"] == "aac"

    result = client.post("/api/compare", json={"paths": [str(sdr), str(hevc)], "weights": {"atmos": 0}}).json()
    assert [row["filename"] for row in result["rows"]] == ["web_sdr.mp4", "web_hevc.mp4"]
    assert result["weights"]["atmos"] == 0
    assert all(score["max"] == sum(result["weights"].values()) for score in result["scores"])


def test_inspect_error_is_a_400_with_detail(client, tmp_path):
    response = client.post("/api/inspect", json={"path": str(tmp_path / "missing.mp4")})
    assert response.status_code == 400
    assert "not a file" in response.json()["detail"]


def test_compare_requires_paths(client):
    assert client.post("/api/compare", json={"paths": []}).status_code == 422


# ---------------------------------------------------------------------------
# files


def test_list_returns_only_media_files_sorted(client, tmp_path):
    for name in ("b.mkv", "a.MP4", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "folder.mov").mkdir()

    listing = client.get("/api/list", params={"path": str(tmp_path)}).json()
    assert [f["name"] for f in listing["files"]] == ["a.MP4", "b.mkv"]
    assert listing["files"][0] == {"path": str(tmp_path / "a.MP4"), "name": "a.MP4", "size": 1}


def test_list_rejects_non_directories(client, tmp_path):
    response = client.get("/api/list", params={"path": str(tmp_path / "nope")})
    assert response.status_code == 400


def test_find_prefers_exact_spotlight_matches(client, monkeypatch):
    monkeypatch.setattr(filesystem, "_spotlight", lambda name: ["/v/Movie.mkv", "/v/Movie.mkv.part", "/x/Movie.mkv"])
    body = client.get("/api/find", params={"name": "Movie.mkv"}).json()
    assert body == {"name": "Movie.mkv", "paths": ["/v/Movie.mkv", "/x/Movie.mkv"]}


def test_find_falls_back_to_find_with_literal_names(client, monkeypatch, tmp_path):
    monkeypatch.setattr(filesystem, "_spotlight", lambda name: [])
    target = tmp_path / "deep" / "Show [2024] S01E01.mkv"
    target.parent.mkdir()
    target.write_bytes(b"x")
    (tmp_path / "Show 2 S01E01.mkv").write_bytes(b"x")  # would match if [2024] were treated as a glob

    body = client.get("/api/find", params={"name": target.name, "hint": str(tmp_path)}).json()
    assert body["paths"] == [str(target)]


def test_pick_reports_missing_osascript(client, monkeypatch):
    def missing(*_args, **_kwargs):
        raise ToolError("command not found: osascript")

    monkeypatch.setattr(filesystem, "run_tool", missing)
    response = client.get("/api/pick")
    assert response.status_code == 501


def test_pick_returns_selected_paths_and_empty_on_cancel(client, monkeypatch):
    results = iter([(0, "/a.mp4\n/b.mkv\n"), (1, "")])

    def fake_run_tool(cmd, **_kwargs):
        code, out = next(results)
        return subprocess.CompletedProcess(cmd, code, out, "")

    monkeypatch.setattr(filesystem, "run_tool", fake_run_tool)
    assert client.get("/api/pick", params={"multi": True}).json() == {"paths": ["/a.mp4", "/b.mkv"]}
    assert client.get("/api/pick").json() == {"paths": []}


# ---------------------------------------------------------------------------
# saved-log parsing


def test_parse_saved_log_matches_cli_summary(client):
    text = (FIXTURES / "tvapp_sample.log").read_text(encoding="utf-8")
    summary = client.post("/api/tvlog/parse", json={"text": text}).json()["summary"]
    assert summary["event_count"] > 0
    assert summary["playback"]


# ---------------------------------------------------------------------------
# security


def test_foreign_host_header_is_rejected(client):
    response = client.get("/api/health", headers={"host": "evil.example:7878"})
    assert response.status_code == 400


def test_cross_origin_unsafe_requests_are_rejected(client, tmp_path):
    blocked = client.post("/api/inspect", json={"path": str(tmp_path)}, headers={"origin": "http://evil.example"})
    assert blocked.status_code == 403
    same_origin = client.post("/api/inspect", json={"path": str(tmp_path)}, headers={"origin": LOCAL})
    assert same_origin.status_code == 400  # reached the handler (path is a directory)


def test_cross_origin_websocket_is_rejected(client):
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(WS_URL, headers={"origin": "http://evil.example"}) as ws,
    ):
        ws.receive_json()
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# live capture WebSocket (LogCapture replaced by a fake: CI has no `log`)


class FakeCapture:
    instances: ClassVar[list[FakeCapture]] = []
    fail_with: ClassVar[Exception | None] = None

    def __init__(self, predicate: str, **_kwargs):
        self.predicate = predicate
        self.log_access = {"available": False, "reason": "standard account"}
        self.capture_mode = "system"
        self.stopped = 0
        self.queue: asyncio.Queue | None = None
        FakeCapture.instances.append(self)

    def start(self, loop=None):
        if FakeCapture.fail_with is not None:
            raise FakeCapture.fail_with
        self.loop = loop

    def subscribe(self):
        self.queue = asyncio.Queue()
        self.queue.put_nowait({"kind": "codec_type", "codec": "dvh1", "raw": "CodecType dvh1"})
        return self.queue

    def stop(self):
        self.stopped += 1
        return {"event_count": 1, "predicate": self.predicate}


@pytest.fixture
def fake_capture(monkeypatch):
    FakeCapture.instances = []
    FakeCapture.fail_with = None
    monkeypatch.setattr(capture_routes, "LogCapture", FakeCapture)
    return FakeCapture


def test_websocket_capture_round_trip(client, fake_capture):
    with client.websocket_connect(WS_URL, headers={"origin": LOCAL}) as ws:
        ws.send_json({"cmd": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"cmd": "start"})
        started = ws.receive_json()
        assert started["type"] == "started"
        event = ws.receive_json()
        assert event == {"type": "event", "event": {"kind": "codec_type", "codec": "dvh1", "raw": "CodecType dvh1"}}
        ws.send_json({"cmd": "stop"})
        summary = ws.receive_json()
        assert summary == {"type": "summary", "summary": {"event_count": 1, "predicate": started["predicate"]}}
        ws.send_json({"cmd": "stop"})
        assert ws.receive_json() == {"type": "error", "message": "no capture running"}
    assert fake_capture.instances[0].stopped == 1


def _missing_log_error() -> ToolError:
    try:
        raise ToolError("could not start log: No such file or directory") from FileNotFoundError("log")
    except ToolError as e:
        return e


@pytest.mark.parametrize("failure", [FileNotFoundError("log"), _missing_log_error()])
def test_websocket_reports_missing_log_binary(client, fake_capture, failure):
    fake_capture.fail_with = failure
    with client.websocket_connect(WS_URL) as ws:
        ws.send_json({"cmd": "start"})
        error = ws.receive_json()
    assert error["type"] == "error"
    assert "macOS" in error["message"]


def test_websocket_rejects_unknown_and_malformed_messages(client, fake_capture):
    with client.websocket_connect(WS_URL) as ws:
        ws.send_json({"cmd": "dance"})
        assert ws.receive_json() == {"type": "error", "message": "unknown cmd: dance"}
        ws.send_text("not json")
        assert ws.receive_json()["type"] == "error"


def test_disconnect_and_shutdown_stop_running_captures(fake_capture):
    with TestClient(create_app(), base_url=LOCAL) as client, client.websocket_connect(WS_URL) as ws:
        ws.send_json({"cmd": "start"})
        ws.receive_json()
    # leaving the socket (and shutting the app down) stops the capture it owned
    assert fake_capture.instances[0].stopped == 1
