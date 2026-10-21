"""Compile the macOS helper and exercise its actual callback completion boundary."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("clang"), reason="needs macOS and clang")


def test_nowplaying_timeouts_and_snapshot(tmp_path):
    source = Path(__file__).with_name("nowplaying_test.m")
    executable = tmp_path / "nowplaying-test"
    flags = ["clang", "-fobjc-arc", "-framework", "Foundation"]
    subprocess.run([*flags, str(source), "-o", str(executable)], check=True, capture_output=True, timeout=60)
    subprocess.run([str(executable)], check=True, capture_output=True, timeout=10)
    production = Path(__file__).parents[2] / "dolby_tool/capture/nowplaying.m"
    subprocess.run(
        [*flags, "-dynamiclib", str(production), "-o", str(tmp_path / "nowplaying.dylib")],
        check=True,
        capture_output=True,
        timeout=60,
    )
