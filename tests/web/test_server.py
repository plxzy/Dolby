import asyncio

from fastapi.responses import JSONResponse

from dolby_tool.web import app as server


def test_api_inspect_uses_threadpool_without_changing_response(monkeypatch):
    calls = []
    expected = {"path": "/tmp/example.mp4", "ok": True}

    async def fake_run_in_threadpool(func, *args):
        calls.append((func.__name__, args))
        return func(*args)

    monkeypatch.setattr(server, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(server, "inspect_file", lambda path: {**expected, "path": path})

    result = asyncio.run(server.api_inspect(server.PathPayload(path=expected["path"])))

    assert calls == [("_inspect_response", (server.PathPayload(path=expected["path"]),))]
    assert result == expected


def test_api_compare_uses_threadpool_without_changing_response(monkeypatch):
    calls = []
    expected = {"rows": [{"path": "/tmp/a.mp4"}], "errors": []}

    async def fake_run_in_threadpool(func, *args):
        calls.append((func.__name__, args))
        return func(*args)

    monkeypatch.setattr(server, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(server, "compare_files", lambda paths, weights: {**expected, "weights": weights})

    body = server.PathsPayload(paths=["/tmp/a.mp4"], weights={"dv_present": 10})
    result = asyncio.run(server.api_compare(body))

    assert calls == [("_compare_response", (body,))]
    assert result == {**expected, "weights": {"dv_present": 10}}


def test_api_find_uses_threadpool_without_changing_response(monkeypatch):
    calls = []

    async def fake_run_in_threadpool(func, *args):
        calls.append((func.__name__, args))
        return func(*args)

    def fake_find_response(name, hint=""):
        return JSONResponse({"name": name, "hint": hint, "paths": ["/tmp/a.mp4"]})

    monkeypatch.setattr(server, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(server, "_find_response", fake_find_response)

    response = asyncio.run(server.api_find("a.mp4", hint="/tmp"))

    assert calls == [("fake_find_response", ("a.mp4", "/tmp"))]
    assert response.body == b'{"name":"a.mp4","hint":"/tmp","paths":["/tmp/a.mp4"]}'
