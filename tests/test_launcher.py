"""Exercise the actual launcher, substituting only its external commands."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("args", "authority"),
    [
        ([], "127.0.0.1:7878"),
        (["--host", "192.168.1.20", "--port=9000"], "192.168.1.20:9000"),
        (["serve", "--host=0.0.0.0"], "127.0.0.1:7878"),
        (["--host", "::"], "[::1]:7878"),
        (["--host=fe80::1", "--port", "9001"], "[fe80::1]:9001"),
        (["--host=[::1]"], "[::1]:7878"),
    ],
)
def test_launcher_uses_bind_host_for_probe_and_browser(tmp_path, args, authority):
    launcher = tmp_path / "dolby-tool"
    shutil.copyfile(Path(__file__).parents[1] / "dolby-tool", launcher)
    commands = {
        "curl": 'printf "%s\\n" "$@" > "$LAUNCH_RECORD/health"',
        "open": 'printf "%s\\n" "$@" > "$LAUNCH_RECORD/open"',
        "uv": 'for i in $(seq 1 50); do [ -f "$LAUNCH_RECORD/open" ] && exit 0; sleep 0.1; done; exit 1',
    }
    for name, body in commands.items():
        executable = tmp_path / name
        executable.write_text(f"#!/bin/sh\n{body}\n")
        executable.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "LAUNCH_RECORD": str(tmp_path)}
    subprocess.run(["bash", str(launcher), *args], env=env, check=True, capture_output=True, timeout=10)
    assert (tmp_path / "health").read_text().splitlines()[-1] == f"http://{authority}/api/health"
    assert (tmp_path / "open").read_text().splitlines() == ["-a", "Safari", f"http://{authority}"]
