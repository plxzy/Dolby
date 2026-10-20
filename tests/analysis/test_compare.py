from dolby_tool.analysis.compare import compare_files


def test_compare_uses_custom_weights(make_spec, monkeypatch):
    spec = make_spec(
        "a.mp4", dv="8.1", fourcc="dvh1", hdr10_plus=True, bit_rate_mbps=80, height=2160, atmos=True, subtitles=3
    )
    monkeypatch.setattr("dolby_tool.analysis.compare.inspect_file", lambda _p: spec)

    res = compare_files(["/media/a.mp4"], weights={"dv_present": 100, "subtitles": 0})

    assert res.weights["dv_present"] == 100.0
    assert res.weights["subtitles"] == 0.0
    assert res.scores[0].components["dv_present"] == 100.0
    assert res.scores[0].components["subtitles"] == 0.0
