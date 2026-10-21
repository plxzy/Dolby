"""Shared pytest fixtures.

`media_clip` synthesises tiny real media files with ffmpeg so analysis code can be
exercised end-to-end; tests that use it are marked `requires_ffmpeg` and are
skipped automatically when ffmpeg/ffprobe are not installed.
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if HAVE_FFMPEG:
        return
    skip = pytest.mark.skip(reason="ffmpeg/ffprobe not installed")
    for item in items:
        if "requires_ffmpeg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def media_clip(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., Path]:
    """Return a factory: ``media_clip(name, *ffmpeg_output_args, inputs=...)`` → cached clip path.

    Inputs default to a 2-second 640x360 test pattern plus a 48 kHz sine tone.
    """
    cache: dict[str, Path] = {}
    root = tmp_path_factory.mktemp("clips")

    def make(name: str, *output_args: str, inputs: tuple[str, ...] | None = None, seconds: float = 1.0) -> Path:
        if name in cache:
            return cache[name]
        out = root / name
        default_inputs = (
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        )  # fmt: skip
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            *(inputs or default_inputs),
            "-t", str(seconds),
            *output_args,
            str(out),
        ]  # fmt: skip
        subprocess.run(cmd, check=True, capture_output=True)
        cache[name] = out
        return out

    return make
