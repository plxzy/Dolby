from dolby_tool.analysis.compare import compare_files


def test_compare_uses_custom_weights(monkeypatch):
    spec = {
        "path": "/tmp/a.mp4",
        "filename": "a.mp4",
        "video": {
            "dolby_vision": {"present": True, "profile": "8.1"},
            "fourcc": "dvh1",
            "hdr10_plus": True,
            "bit_rate_mbps": 80,
            "width": 3840,
            "height": 2160,
        },
        "audio": [{"atmos": True}],
        "subtitles": [1, 2, 3],
        "container": {"size_pretty": "1.0 GiB", "duration_pretty": "01:00:00"},
    }

    monkeypatch.setattr("dolby_tool.analysis.compare.inspect_file", lambda _p: spec)
    res = compare_files(["/tmp/a.mp4"], weights={"dv_present": 100, "subtitles": 0})
    assert res["weights"]["dv_present"] == 100.0
    assert res["weights"]["subtitles"] == 0.0
    assert res["scores"][0]["components"]["dv_present"] == 100.0
