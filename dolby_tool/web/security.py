"""Guards that keep a loopback-only tool from being driven by other websites.

The server can read arbitrary local paths and start `log stream`, so two classic
browser attacks matter even on 127.0.0.1:

* DNS rebinding — a hostile domain re-resolves to 127.0.0.1 and becomes
  "same-origin" with us. Blocked by an allow-list on the Host header
  (Starlette's `TrustedHostMiddleware`, configured with `allowed_hosts_for`).
* Cross-site requests — any page may open `ws://127.0.0.1:7878/ws/tvlog` or fire
  a POST at us; WebSockets are not covered by the same-origin policy. Blocked by
  `OriginGuard`, which rejects WebSocket handshakes and unsafe HTTP methods whose
  `Origin` names a host outside the allow-list. Requests without an Origin (curl,
  scripts, the CLI) are not browser-initiated and pass through.
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

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self.allow_any = "*" in allowed_hosts
        self.allowed_hosts = {h.lower() for h in allowed_hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.allow_any and self._needs_check(scope):
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
        try:
            hostname = urlsplit(origin).hostname
        except ValueError:
            return False
        if not hostname:  # e.g. the opaque "null" origin of file:// pages
            return False
        host = f"[{hostname}]" if ":" in hostname else hostname
        return host.lower() in self.allowed_hosts
