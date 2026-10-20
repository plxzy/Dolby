import json
import os
from pathlib import Path

import pytest
import uvicorn

from dolby_tool import __version__, cli

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    monkeypatch.delenv("DOLBY_TOOL_HOST", raising=False)
    return calls


@pytest.mark.parametrize(
    ("argv", "port"),
    [([], 7878), (["--port", "9000"], 9000), (["--port=9001"], 9001), (["serve", "--port", "9002"], 9002)],
)
def test_server_mode_keeps_its_argv_shape(served, argv, port):
    assert cli.main(argv) == 0
    app, kwargs = served[0]
    assert app == "dolby_tool.web.app:app"
    assert kwargs["port"] == port
    assert kwargs["host"] == "127.0.0.1"
    assert os.environ["DOLBY_TOOL_HOST"] == "127.0.0.1"


def test_non_loopback_bind_warns(served, capsys):
    cli.main(["--host", "0.0.0.0"])
    assert "non-loopback" in capsys.readouterr().err


def test_version_and_help_list_every_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    help_text = capsys.readouterr().out
    for name in cli.COMMANDS:
        assert name in help_text


def test_errors_are_one_line_with_exit_code_1(capsys, tmp_path):
    assert cli.main(["inspect", str(tmp_path / "missing.mp4")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("dolby-tool: error:")
    assert "Traceback" not in err


def test_tvlog_parse_prints_markdown_or_json(capsys):
    sample = str(FIXTURES / "tvapp_sample.log")
    assert cli.main(["tvlog-parse", sample]) == 0
    assert capsys.readouterr().out.startswith("# TV/log playback summary")
    assert cli.main(["tvlog-parse", sample, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["event_count"] > 0


@pytest.mark.requires_ffmpeg
def test_inspect_and_compare_commands(capsys, media_clip):
    a = media_clip("cli_a.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    b = media_clip("cli_b.mp4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "3M", "-an")

    assert cli.main(["inspect", str(a), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["filename"] == "cli_a.mp4"

    assert cli.main(["inspect", str(a)]) == 0
    assert "cli_a.mp4" in capsys.readouterr().out

    assert cli.main(["compare", str(a), str(b), "--weight", "atmos=0", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["weights"]["atmos"] == 0
    assert len(result["rows"]) == 2

    assert cli.main(["compare", str(a), str(b)]) == 0
    assert "cli_b.mp4" in capsys.readouterr().out


def test_compare_rejects_malformed_weights(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["compare", "a.mp4", "--weight", "atmos"])
    assert exc.value.code == 2
    assert "KEY=NUMBER" in capsys.readouterr().err
