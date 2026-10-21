"""Guards that keep a loopback-only tool from being driven by other websites.

The server can read arbitrary local paths and start `log stream`, so two classic
browser attacks matter even on 127.0.0.1:

* DNS rebinding — a hostile domain re-resolves to 127.0.0.1 and becomes
  "same-origin" with us. Blocked by an allow-list on the Host header
  (Starlette's `TrustedHostMiddleware`, configured with `allowed_hosts_for`).
* Cross-site requests — any page may open `ws://127.0.0.1:7878/ws/tvlog` or fire
  a POST at us; WebSockets are not covered by the same-origin policy. Blocked by
  `OriginGuard`, which rejects WebSocket handshakes and unsafe HTTP methods whose
  `Origin` is neither an allowed host nor an explicitly trusted origin. Requests
  without an Origin (curl, scripts, the CLI) pass through.
"""

from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
_WILDCARD_BINDS = {"", "0.0.0.0", "::", "[::]"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hosts_for(bind_host: str | None) -> list[str]:
    """Host-header allow-list for a server bound to `bind_host`.

    Loopback names are always allowed; a specific non-loopback bind address is
    added; binding every interface (0.0.0.0 / ::) disables the check ("*").
    """
    hosts = list(LOOPBACK_HOSTS)
    if bind_host is None:
        return hosts
    if bind_host in _WILDCARD_BINDS:
        return ["*"]
    host = f"[{bind_host}]" if ":" in bind_host and not bind_host.startswith("[") else bind_host
    if host.lower() not in hosts:
        hosts.append(host.lower())
    return hosts


class OriginGuard:
    """Pure ASGI middleware rejecting cross-origin WebSocket handshakes and unsafe requests."""

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str], allowed_origins: Sequence[str] = ()) -> None:
        self.app = app
        # Wildcard Host headers allow network access, never arbitrary browser origins.
        hosts = LOOPBACK_HOSTS if "*" in allowed_hosts else allowed_hosts
        self.allowed_hosts = {h.lower() for h in hosts}
        self.allowed_origins = set()
        for origin in allowed_origins:
            if (key := self._origin_key(origin)) is None:
                raise ValueError(f"Expected an explicit HTTP(S) origin: {origin!r}")
            self.allowed_origins.add(key)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._needs_check(scope):
            origin = Headers(scope=scope).get("origin")
            if origin is not None and not self.origin_allowed(origin):
                if scope["type"] == "websocket":
                    await WebSocketClose(code=1008, reason="cross-origin WebSocket rejected")(scope, receive, send)
                else:
                    await PlainTextResponse("Cross-origin request rejected", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _needs_check(scope: Scope) -> bool:
        if scope["type"] == "websocket":
            return True
        return scope["type"] == "http" and scope["method"] not in _SAFE_METHODS

    def origin_allowed(self, origin: str) -> bool:
        if (key := self._origin_key(origin)) is None:
            return False
        hostname = key[1]
        host = f"[{hostname}]" if ":" in hostname else hostname
        return host in self.allowed_hosts or key in self.allowed_origins

    @staticmethod
    def _origin_key(origin: str) -> tuple[str, str, int] | None:
        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None
        return parsed.scheme, hostname.lower(), port if port is not None else 443 if parsed.scheme == "https" else 80
