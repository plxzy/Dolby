import base64
import hashlib
import io
import re
import tarfile

import pytest

from dolby_tool.common import ToolError
from dolby_tool.web import browser_build
from dolby_tool.web.browser_build import build_browser, sri_sha512

VENDOR = {
    "package/dist/esm-bundle/index.min.js": b"export const mediaInfoFactory = () => null;\n",
    "package/dist/MediaInfoModule.wasm": b"\0asm\1\0\0\0fake",
    "package/LICENSE.txt": b"BSD-2-Clause\n",
}


@pytest.fixture
def tarball(tmp_path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in VENDOR.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    path = tmp_path / "mediainfo.tgz"
    path.write_bytes(buffer.getvalue())
    return path


def test_build_writes_a_browser_mode_page_with_a_matching_script_hash(tmp_path, tarball):
    out = build_browser(tmp_path / "out", tarball=tarball, integrity=sri_sha512(tarball.read_bytes()))

    html = (out / "index.html").read_text()
    assert '<meta name="dolby-mode" content="browser">' in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    inline = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
    digest = base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode()
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert f"'sha256-{digest}'" in csp
    assert "'wasm-unsafe-eval'" in csp
    assert "'unsafe-eval'" not in csp.replace("'wasm-unsafe-eval'", "")

    generations = list((out / "static").iterdir())
    assert len(generations) == 1
    assets = generations[0]
    assert re.fullmatch(r"[0-9a-f]{64}", assets.name)
    vendor = assets / "vendor" / "mediainfo"
    for member, data in VENDOR.items():
        assert (vendor / browser_build.VENDOR_MEMBERS[member]).read_bytes() == data
    assert (assets / "js" / "main.js").is_file()
    assert not (out / "static" / "index.html").exists()

    headers = (out / "_headers").read_text()
    assert f"Content-Security-Policy: {csp}; frame-ancestors https://psporidis.com https://www.psporidis.com" in headers
    assert "X-Robots-Tag: noindex, nofollow" in headers
    assert "/static/*\n  Cache-Control: public, max-age=31536000, immutable" in headers
    for route in ("/", "/index.html"):
        assert f"{route}\n  Cache-Control: public, max-age=0, must-revalidate" in headers


def test_startup_preloads_cover_the_import_graph_without_lazy_analysis(tmp_path, tarball):
    out = build_browser(tmp_path / "out", tarball=tarball, integrity=sri_sha512(tarball.read_bytes()))
    html = (out / "index.html").read_text()
    urls = re.findall(r'<link rel="modulepreload" href="([^"]+)">', html)
    assets = next((out / "static").iterdir())
    paths = {url.removeprefix(f"/static/{assets.name}/") for url in urls}
    assert len(urls) == len(paths) == 32
    assert paths == {
        "js/api.js",
        "js/browser/compare-sessions.js",
        "js/browser/compare.js",
        "js/browser/report.js",
        "js/browser/session.js",
        "js/components/badge.js",
        "js/components/card.js",
        "js/components/copy.js",
        "js/components/drawer.js",
        "js/components/feedback.js",
        "js/components/icon.js",
        "js/components/meter.js",
        "js/components/scorebar.js",
        "js/components/table.js",
        "js/components/tooltip.js",
        "js/dom.js",
        "js/dragdrop.js",
        "js/embed.js",
        "js/format.js",
        "js/hdr.js",
        "js/icons.js",
        "js/main.js",
        "js/mode.js",
        "js/router.js",
        "js/theme.js",
        "js/views/browser-compare.js",
        "js/views/capture-summary.js",
        "js/views/capture-timeline.js",
        "js/views/capture.js",
        "js/views/compare.js",
        "js/views/convert.js",
        "js/views/inspect.js",
    }
    assert all((out / url.lstrip("/")).is_file() for url in urls)
    assert not any("worker" in url or "vendor" in url or ".wasm" in url for url in urls)
    session = (assets / "js/browser/session.js").read_text()
    assert "new URL('./worker.js', import.meta.url)" in session
    mediainfo = (assets / "js/browser/parsers/mediainfo.js").read_text()
    assert f"/static/{assets.name}/vendor/mediainfo/index.min.js" in mediainfo
    assert f"/static/{assets.name}/vendor/mediainfo/MediaInfoModule.wasm" in mediainfo


def test_graph_follows_new_imports_reexports_and_cycles_not_dynamic_imports(tmp_path):
    js = tmp_path / "js"
    js.mkdir()
    (js / "main.js").write_text(
        "/*\nimport './comment.js';\n*/\n"
        "import {\n value,\n} from './child.js';\n"
        "import './effect.js';\n"
        "import('./lazy.js');\n"
        "new Worker(new URL('./worker.js', import.meta.url));\n"
    )
    (js / "child.js").write_text("export { value } from './leaf.js';\n")
    (js / "leaf.js").write_text("import './main.js';\nexport const value = 1;\n")
    (js / "effect.js").write_text("")
    assert browser_build._startup_modules(tmp_path) == [
        "js/child.js",
        "js/effect.js",
        "js/leaf.js",
        "js/main.js",
    ]


def test_asset_generation_changes_with_transitive_or_lazy_content(tmp_path, tarball, monkeypatch):
    source = tmp_path / "source"
    import shutil

    shutil.copytree(browser_build.STATIC_DIR, source)
    monkeypatch.setattr(browser_build, "STATIC_DIR", source)
    integrity = sri_sha512(tarball.read_bytes())
    first = build_browser(tmp_path / "first", tarball=tarball, integrity=integrity)
    again = build_browser(tmp_path / "again", tarball=tarball, integrity=integrity)
    first_generation = next((first / "static").iterdir()).name
    assert next((again / "static").iterdir()).name == first_generation
    for name in ("js/dom.js", "js/browser/worker.js"):
        path = source / name
        path.write_text(path.read_text() + "\n// changed generation\n")
        changed = build_browser(tmp_path / "changed", tarball=tarball, integrity=integrity)
        generation = next((changed / "static").iterdir()).name
        assert generation != first_generation
        assert f"/static/{first_generation}/" not in (changed / "index.html").read_text()
        first_generation = generation


def test_build_rejects_a_tarball_that_fails_its_integrity_check(tmp_path, tarball):
    with pytest.raises(ToolError, match="integrity"):
        build_browser(tmp_path / "out", tarball=tarball, integrity=sri_sha512(b"something else"))
    assert not (tmp_path / "out").exists()


def test_build_refuses_to_empty_the_repository(tarball):
    with pytest.raises(ToolError, match="refusing"):
        build_browser(browser_build.REPO_ROOT, tarball=tarball, integrity=sri_sha512(tarball.read_bytes()))
    assert (browser_build.REPO_ROOT / "pyproject.toml").is_file()
