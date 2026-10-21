import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dolby_tool.web.app import HOST_ENV, create_app
from dolby_tool.web.security import LOOPBACK_HOSTS, OriginGuard, allowed_hosts_for


@pytest.mark.parametrize(
    ("bind", "expected"),
    [
        (None, list(LOOPBACK_HOSTS)),
        ("127.0.0.1", list(LOOPBACK_HOSTS)),
        ("192.168.1.20", [*LOOPBACK_HOSTS, "192.168.1.20"]),
        ("fe80::1", [*LOOPBACK_HOSTS, "[fe80::1]"]),
        ("Studio.local", [*LOOPBACK_HOSTS, "studio.local"]),
        ("0.0.0.0", ["*"]),
        ("::", ["*"]),
    ],
)
def test_allowed_hosts_for_bind_address(bind, expected):
    assert allowed_hosts_for(bind) == expected


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://127.0.0.1:7878", True),
        ("http://localhost:7878", True),
        ("http://[::1]:7878", True),
        ("https://LOCALHOST", True),
        ("http://evil.example", False),
        ("http://127.0.0.1.evil.example", False),
        ("null", False),
        ("", False),
        ("ftp://localhost", False),
        ("http://localhost:bad", False),
        ("http://user@localhost", False),
    ],
)
def test_origin_allowed(origin, allowed):
    guard = OriginGuard(app=None, allowed_hosts=LOOPBACK_HOSTS)
    assert guard.origin_allowed(origin) is allowed


@pytest.mark.parametrize("bind", ["0.0.0.0", "::"])
def test_wildcard_bind_rejects_foreign_origins_but_keeps_local_clients(monkeypatch, bind):
    monkeypatch.setenv(HOST_ENV, bind)
    monkeypatch.delenv("DOLBY_TOOL_ALLOWED_ORIGINS", raising=False)
    with TestClient(create_app(), base_url="http://127.0.0.1:7878") as client:
        blocked = client.post("/api/inspect", json={"path": "/missing"}, headers={"origin": "https://evil.example"})
        assert blocked.status_code == 403
        for headers in ({}, {"origin": "http://localhost:7878"}, {"origin": "http://[::1]:7878"}):
            assert client.post("/api/inspect", json={"path": "/missing"}, headers=headers).status_code == 400
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect("/ws/tvlog", headers={"origin": "https://evil.example"}),
        ):
            pass
        assert exc.value.code == 1008
        with client.websocket_connect("/ws/tvlog") as ws:
            ws.send_json({"cmd": "ping"})
            assert ws.receive_json()["type"] == "pong"


def test_wildcard_bind_can_trust_an_explicit_lan_origin(monkeypatch):
    monkeypatch.setenv(HOST_ENV, "0.0.0.0")
    monkeypatch.setenv("DOLBY_TOOL_ALLOWED_ORIGINS", " http://192.168.1.20:7878, http://[fd00::1]:7878 ")
    with TestClient(create_app(), base_url="http://192.168.1.20:7878") as client:
        for origin in ("http://192.168.1.20:7878", "http://[fd00::1]:7878"):
            assert client.post("/api/inspect", json={"path": "/missing"}, headers={"origin": origin}).status_code == 400
            with client.websocket_connect("/ws/tvlog", headers={"origin": origin}) as ws:
                ws.send_json({"cmd": "ping"})
                assert ws.receive_json()["type"] == "pong"
        for origin in ("http://192.168.1.20:9999", "https://192.168.1.20:7878", "https://evil.example"):
            assert client.post("/api/inspect", json={"path": "/missing"}, headers={"origin": origin}).status_code == 403


def test_explicit_origin_configuration_rejects_wildcards():
    with pytest.raises(ValueError, match="explicit HTTP"):
        OriginGuard(app=None, allowed_hosts=["*"], allowed_origins=["*"])
