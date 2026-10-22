"""Build the static browser edition of the UI: Inspect runs on-device, no server or file paths involved.

The output folder is plain static files (`index.html`, `static/…`, a Cloudflare-style `_headers`) with
mediainfo.js vendored from its integrity-checked npm tarball. Nothing here needs Node or npm.
"""

import base64
import hashlib
import io
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

from ..common import ToolError

MEDIAINFO_VERSION = "0.3.8"
MEDIAINFO_TARBALL_URL = f"https://registry.npmjs.org/mediainfo.js/-/mediainfo.js-{MEDIAINFO_VERSION}.tgz"
MEDIAINFO_INTEGRITY = "sha512-34ruGC47ytdp5IE9lXx4Yj8XMxESbzqLxDGfnYXimnokxH+aavC/YqubakOKm0vwrC5ZisL55WMmud6R6TmjBw=="
EMBED_PARENTS = ("https://psporidis.com", "https://www.psporidis.com")

STATIC_DIR = Path(__file__).parent / "static"
REPO_ROOT = Path(__file__).resolve().parents[2]
# Tarball member → file name under static/vendor/mediainfo/.
VENDOR_MEMBERS = {
    "package/dist/esm-bundle/index.min.js": "index.min.js",
    "package/dist/MediaInfoModule.wasm": "MediaInfoModule.wasm",
    "package/LICENSE.txt": "LICENSE.txt",
}
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL)
_CHARSET_META = '<meta charset="utf-8">'
_MAIN_OPEN = '<main class="main" id="main" tabindex="-1">'
_BROWSER_NOSCRIPT = """
      <noscript>
        <style>
          .app { display: block; background: none; }
          .app > .sidebar, .skip-link, .main > .view { display: none; }
          .browser-nojs { max-width: 42rem; margin-inline: auto; }
        </style>
        <section class="browser-nojs view-heading" aria-labelledby="browser-nojs-title">
          <h1 id="browser-nojs-title">Browser inspector needs JavaScript</h1>
          <p>This inspector runs in your browser and needs JavaScript to inspect and compare files.</p>
          <p>Files are read locally on your device and never uploaded.</p>
          <p>Enable JavaScript, or <a href="https://github.com/plxzy/Dolby#run">get the macOS app</a>.</p>
        </section>
      </noscript>"""
# Static declarations only: dynamic import(), import.meta and Worker URLs stay lazy.
_IMPORT = re.compile(
    r"""^\s*(?:import\s*(?:[^;'"]*?\bfrom\s*)?|export\s+[^;'"]*?\bfrom\s*)['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_JS_LITERAL = re.compile(r"""//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`""", re.DOTALL)


def build_browser(out: Path, *, tarball: Path | None = None, integrity: str = MEDIAINFO_INTEGRITY) -> Path:
    """Write the static browser build to `out` (emptied first) and return its resolved path."""
    data = _tarball_bytes(tarball, integrity)
    out = _prepare_out(Path(out))

    shutil.copytree(STATIC_DIR, out / "static", ignore=_skip_root_index)
    vendor = out / "static" / "vendor" / "mediainfo"
    vendor.mkdir(parents=True)
    _extract_vendor(data, vendor)

    # A whole-tree fingerprint keeps relative imports, worker URLs and lazy WASM in
    # one generation. Hash before URL substitution to avoid a self-referential hash.
    static = out / "static"
    digest = hashlib.sha256(b"dolby-browser-assets-v1\0")
    for path in sorted(static.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(static).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    generation = digest.hexdigest()
    assets = out / generation
    static.rename(assets)
    static.mkdir()
    assets.rename(static / generation)
    assets = static / generation
    prefix = f"/static/{generation}/"
    for path in assets.rglob("*"):
        if path.suffix in {".js", ".css"}:
            source = path.read_text(encoding="utf-8")
            path.write_text(source.replace("/static/", prefix), encoding="utf-8")

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("/static/", prefix)
    preloads = "\n".join(f'  <link rel="modulepreload" href="{prefix}{path}">' for path in _startup_modules(assets))
    html = html.replace("</head>", f"{preloads}\n</head>")
    csp = _csp(html)
    (out / "index.html").write_text(_browser_index(html, csp), encoding="utf-8")
    frame_ancestors = " ".join(EMBED_PARENTS)
    (out / "_headers").write_text(
        "/*\n"
        f"  Content-Security-Policy: {csp}; frame-ancestors {frame_ancestors}\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: no-referrer\n"
        "  X-Robots-Tag: noindex, nofollow\n"
        "/\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
        "/index.html\n"
        "  Cache-Control: public, max-age=0, must-revalidate\n"
        "/static/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n",
        encoding="utf-8",
    )
    return out


def _startup_modules(assets: Path) -> list[str]:
    """Follow local static import/re-export declarations, not runtime asset URLs."""
    pending = [assets / "js" / "main.js"]
    seen = set()
    while pending:
        path = pending.pop().resolve()
        if path in seen:
            continue
        if not path.is_relative_to(assets.resolve()) or not path.is_file():
            raise ToolError(f"startup import is missing or outside static assets: {path}")
        seen.add(path)
        source = path.read_text(encoding="utf-8")
        # Preserve newlines so declaration anchoring survives block comments.
        source = _JS_LITERAL.sub(
            lambda m: "\n" * m[0].count("\n") if m[0].startswith(("//", "/*", "`")) else m[0], source
        )
        for specifier in _IMPORT.findall(source):
            if not specifier.startswith(("./", "../")):
                raise ToolError(f"startup import must be relative: {specifier} in {path}")
            pending.append(path.parent / specifier)
    return sorted(path.relative_to(assets.resolve()).as_posix() for path in seen)


def sri_sha512(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def _tarball_bytes(tarball: Path | None, integrity: str) -> bytes:
    path = tarball or Path.home() / ".cache" / "dolby-tool" / f"mediainfo.js-{MEDIAINFO_VERSION}.tgz"
    if tarball is None and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        with urllib.request.urlopen(MEDIAINFO_TARBALL_URL, timeout=60) as response:
            partial.write_bytes(response.read())
        partial.replace(path)
    data = path.read_bytes()
    if sri_sha512(data) != integrity:
        if tarball is None:
            path.unlink(missing_ok=True)
        raise ToolError("mediainfo.js tarball failed its integrity check")
    return data


def _prepare_out(out: Path) -> Path:
    out = out.expanduser().resolve()
    static = STATIC_DIR.resolve()
    if out in (Path("/"), Path.home().resolve(), REPO_ROOT) or out == static or out in static.parents:
        raise ToolError(f"refusing to use {out} as the browser build folder")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    return out


def _skip_root_index(directory: str, names: list[str]) -> set[str]:
    return {"index.html"} if Path(directory).resolve() == STATIC_DIR.resolve() else set()


def _extract_vendor(data: bytes, vendor: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member, name in VENDOR_MEMBERS.items():
            try:
                info = archive.getmember(member)
            except KeyError:
                raise ToolError(f"mediainfo.js tarball is missing {member}") from None
            source = archive.extractfile(info) if info.isfile() else None
            if source is None:
                raise ToolError(f"mediainfo.js tarball entry {member} isn't a regular file")
            (vendor / name).write_bytes(source.read())


def _csp(html: str) -> str:
    scripts = _INLINE_SCRIPT.findall(html)
    if len(scripts) != 1:
        raise ToolError(f"expected exactly one inline <script> in index.html, found {len(scripts)}")
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode("ascii")
    return (
        "default-src 'self'; "
        f"script-src 'self' 'wasm-unsafe-eval' 'sha256-{digest}'; "
        "worker-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'"
    )


def _browser_index(html: str, csp: str) -> str:
    if html.count(_CHARSET_META) != 1:
        raise ToolError('index.html needs exactly one <meta charset="utf-8">')
    if html.count(_MAIN_OPEN) != 1:
        raise ToolError("index.html needs exactly one main content opening")
    html = html.replace(_MAIN_OPEN, _MAIN_OPEN + _BROWSER_NOSCRIPT)
    inserted = (
        f"{_CHARSET_META}\n"
        '  <meta name="dolby-mode" content="browser">\n'
        '  <meta name="robots" content="noindex, nofollow">\n'
        f'  <meta http-equiv="Content-Security-Policy" content="{csp}">'
    )
    return html.replace(_CHARSET_META, inserted)
