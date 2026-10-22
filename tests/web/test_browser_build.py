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

    vendor = out / "static" / "vendor" / "mediainfo"
    for member, data in VENDOR.items():
        assert (vendor / browser_build.VENDOR_MEMBERS[member]).read_bytes() == data
    assert (out / "static" / "js" / "main.js").is_file()
    assert not (out / "static" / "index.html").exists()

    headers = (out / "_headers").read_text()
    assert f"Content-Security-Policy: {csp}; frame-ancestors https://psporidis.com https://www.psporidis.com" in headers
    assert "X-Robots-Tag: noindex, nofollow" in headers


def test_build_rejects_a_tarball_that_fails_its_integrity_check(tmp_path, tarball):
    with pytest.raises(ToolError, match="integrity"):
        build_browser(tmp_path / "out", tarball=tarball, integrity=sri_sha512(b"something else"))
    assert not (tmp_path / "out").exists()


def test_build_refuses_to_empty_the_repository(tarball):
    with pytest.raises(ToolError, match="refusing"):
        build_browser(browser_build.REPO_ROOT, tarball=tarball, integrity=sri_sha512(tarball.read_bytes()))
    assert (browser_build.REPO_ROOT / "pyproject.toml").is_file()
