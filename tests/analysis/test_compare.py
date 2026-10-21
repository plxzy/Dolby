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


def test_score_max_follows_custom_weights(make_spec, monkeypatch):
    spec = make_spec("a.mp4")
    monkeypatch.setattr("dolby_tool.analysis.compare.inspect_file", lambda _p: spec)

    default = compare_files(["/media/a.mp4"]).scores[0]
    custom = compare_files(["/media/a.mp4"], weights={"atmos": 0, "bit_rate_mbps": 50}).scores[0]

    assert default.max == 110
    assert custom.max == 110 - 15 - 20 + 50


def _compare(monkeypatch, specs, paths=None):
    """Run `compare_files` with `inspect_file` serving `specs` by path; other paths fail to inspect."""
    by_path = {spec.path: spec for spec in specs}

    def fake_inspect(path):
        if path not in by_path:
            raise RuntimeError(f"not a file: {path}")
        return by_path[path]

    monkeypatch.setattr("dolby_tool.analysis.compare.inspect_file", fake_inspect)
    return compare_files(paths or list(by_path))


def test_best_file_and_winners(make_spec, monkeypatch):
    plain = make_spec("plain.mp4", bit_rate_mbps=10, channels=2, subtitles=1)
    dv = make_spec("dv.mkv", dv="8.1", fourcc="dvh1", bit_rate_mbps=40, atmos=True, height=2160, subtitles=3)
    res = _compare(monkeypatch, [plain, dv])

    assert [score.is_best for score in res.scores] == [False, True]
    assert res.scores[1].total > res.scores[0].total
    assert res.winners == {
        "video_bitrate_mbps": [1],
        "audio_bitrate_kbps": [0, 1],
        "audio_channels": [1],
        "subtitle_count": [1],
        "fourcc_ok": [1],
        "dv_profile": [1],
        "hdr10_plus": [],
        "audio_atmos": [1],
    }


def test_tied_files_are_all_best(make_spec, monkeypatch):
    res = _compare(monkeypatch, [make_spec("a.mp4"), make_spec("b.mp4")])

    assert [score.is_best for score in res.scores] == [True, True]


def test_scoring_components(make_spec, monkeypatch):
    spec = make_spec(
        "a.mp4", dv="8.1", fourcc="dvh1", hdr10_plus=True, bit_rate_mbps=40, height=2160, atmos=True, subtitles=2
    )
    score = _compare(monkeypatch, [spec]).scores[0]

    assert score.components == {
        "dv_present": 25,
        "dv_fourcc_ok": 25,
        "hdr10_plus": 10,
        "atmos": 15,
        "bit_rate_mbps": 10.0,
        "resolution": 10,
        "subtitles": 2.0,
    }
    assert score.total == 97.0


def test_dv_with_the_wrong_fourcc_earns_no_fourcc_points(make_spec, monkeypatch):
    score = _compare(monkeypatch, [make_spec("a.mp4", dv="8.1", fourcc="hvc1")]).scores[0]

    assert (score.components["dv_present"], score.components["dv_fourcc_ok"]) == (25, 0)


def test_unreadable_files_are_collected_not_raised(make_spec, monkeypatch):
    res = _compare(monkeypatch, [make_spec("a.mp4")], paths=["/media/a.mp4", "/media/missing.mp4"])

    assert [row.filename for row in res.rows] == ["a.mp4"]
    assert [(e.path, e.error) for e in res.errors] == [("/media/missing.mp4", "not a file: /media/missing.mp4")]


def test_compare_nothing_gives_an_empty_result():
    res = compare_files([])

    assert (res.rows, res.scores, res.specs, res.errors, res.winners) == ([], [], [], [], {})


def test_unknown_custom_weight_keys_are_ignored(make_spec, monkeypatch):
    res = _compare(monkeypatch, [make_spec("a.mp4")])
    custom = compare_files(["/media/a.mp4"], weights={"bogus": 99})  # the fake inspect_file is still patched

    assert custom.weights == res.weights


def test_result_serialises_like_the_legacy_dict(make_spec, monkeypatch):
    res = _compare(monkeypatch, [make_spec("a.mp4"), make_spec("b.mp4", dv="8.1", fourcc="dvh1")])
    data = res.model_dump(mode="json")

    assert list(data) == ["rows", "scores", "winners", "specs", "errors", "weights"]
    assert data["specs"][0]["video"]["dolby_vision"] == {"present": False}
    assert data["specs"][1]["video"]["dolby_vision"]["profile"] == "8.1"
    assert data["scores"][0]["max"] == 110
    assert data["weights"]["dv_present"] == 25
