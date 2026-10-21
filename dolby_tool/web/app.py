"""FastAPI application: API routers, the static single-page UI and loopback security guards."""

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from .. import __version__
from ..analysis import InspectError
from ..common import ToolError
from .routes import capture, convert, files, media, system
from .security import OriginGuard, allowed_hosts_for

STATIC_DIR = Path(__file__).parent / "static"

# Make browsers revalidate (ETag / Last-Modified) instead of heuristically caching
# the UI, so upgrades show up without cache-busting query strings.
NO_CACHE = {"Cache-Control": "no-cache"}

# The CLI exports the bind host here before starting uvicorn (which imports this
# module by name), so the Host/Origin allow-lists match how the server was bound.
HOST_ENV = "DOLBY_TOOL_HOST"

_TAGS = [
    {"name": "analysis", "description": "Inspect a file's Dolby Vision / HDR / Atmos signalling, or rank several."},
    {"name": "files", "description": "Native file dialog, Finder drag-and-drop resolution and folder listings."},
    {"name": "capture", "description": "Live TV.app playback capture via macOS `log stream`, and saved-log parsing."},
    {"name": "system", "description": "Tool availability and health."},
    {"name": "convert", "description": "Convert local media and apply Apple TV metadata or season artwork."},
]


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(NO_CACHE)
        return response


async def _bad_request(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


def create_app(*, allowed_hosts: Sequence[str] | None = None, allowed_origins: Sequence[str] | None = None) -> FastAPI:
    """Build the app, with independent Host and explicit additional browser-origin policies."""
    hosts = list(allowed_hosts) if allowed_hosts is not None else allowed_hosts_for(os.environ.get(HOST_ENV))
    origins = (
        list(allowed_origins)
        if allowed_origins is not None
        else [
            origin.strip() for origin in os.environ.get("DOLBY_TOOL_ALLOWED_ORIGINS", "").split(",") if origin.strip()
        ]
    )
    hub = capture.CaptureHub()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await hub.stop()  # never leave a `log stream` process behind on shutdown

    app = FastAPI(
        title="dolby-tool",
        version=__version__,
        summary="Inspect Dolby Vision / HDR / Atmos media and capture what TV.app actually plays.",
        openapi_tags=_TAGS,
        lifespan=lifespan,
    )
    app.state.capture_hub = hub

    # Middleware added last runs first: reject foreign Host headers, then cross-origin requests.
    app.add_middleware(OriginGuard, allowed_hosts=hosts, allowed_origins=origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    app.add_exception_handler(InspectError, _bad_request)
    app.add_exception_handler(ToolError, _bad_request)

    for router in (media.router, files.router, capture.router, system.router, convert.router):
        app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)

    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
