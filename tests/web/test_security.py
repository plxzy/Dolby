import pytest

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
    ],
)
def test_origin_allowed(origin, allowed):
    guard = OriginGuard(app=None, allowed_hosts=LOOPBACK_HOSTS)
    assert guard.origin_allowed(origin) is allowed
