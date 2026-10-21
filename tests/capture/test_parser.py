"""`parse_line`: one compact `log stream` line → one event."""

from dolby_tool.capture import parse_line


def test_parse_line_figalternate():
    line = (
        "FigAlternate(504) [Peak/Avg 30570719/24765202] [3840x1606] "
        "[dvh1.05.06,ec-3] [VideoRange PQ] [HDCP Type1] [FrameRate 23.976]"
    )
    out = parse_line(line)
    assert out is not None
    assert out["kind"] == "hls_variant"
    assert out["peak_bps"] == 30570719
    assert out["height"] == 1606


def test_parse_line_figalternate_with_audio_group():
    line = (
        "2026-05-22 17:34:59.058 Df TV[767:1dc50c] "
        "Variant [<FigAlternate(441):[0x8aa607200] [Peak/Avg 8170165/5064990] "
        "[1918x802] [AudioGroup audio-atmos_vod-ap-aoc.tv.apple.com] "
        "[SubtitleGroup subtitles_vod-ap-aoc.tv.apple.com] [dvh1.05.03,ec-3] "
        "[VideoRange PQ] [HDCP Type1] [FrameRate 23.976] [Pathway ap]>]"
    )
    out = parse_line(line)
    assert out is not None
    assert out["kind"] == "hls_variant"
    assert out["id"] == 441
    assert out["peak_bps"] == 8170165
    assert out["avg_bps"] == 5064990
    assert out["width"] == 1918
    assert out["height"] == 802
    assert out["audio_group"] == "audio-atmos_vod-ap-aoc.tv.apple.com"
    assert out["codecs"] == "dvh1.05.03,ec-3"
    assert out["video_range"] == "PQ"
    assert out["hdcp"] == "Type1"
    assert out["fps"] == 23.976


def test_parse_line_codec_type():
    out = parse_line("CodecType: dvh1 (HW decoder), DecodedPixelBuffer: &xv0, 3840 x 1600")
    assert out is not None
    assert out["kind"] == "codec_type"
    assert out["fourcc"] == "dvh1"
    assert out["width"] == 3840
    assert out["height"] == 1600


def test_audio_format_keeps_non_adjacent_spatialization_fields():
    events = [
        parse_line(
            "2026-05-22 17:34:57.401 Df TV[767:1dc770] "
            "fpfs_ReportAudioPlaybackThroughFigLog: [AudioFormat qc+3 is  decodable] "
            "[AudioChannels 16] [Spatialization Eligible yes] "
            "[Client permits multi: yes, stereo: no] [Spatialization yes] "
            "[StereoSpatialization no] [Rendition Multichannel] [SampleRate 48000]"
        ),
        parse_line(
            "2026-05-22 17:36:06.933 Df TV[12389:1dd6b6] "
            "itemfig_ReportAudioPlaybackThroughFigLog: [AudioFormat ec+3] "
            "[AudioChannels 16] [Spatialization Eligible yes] "
            "[Client permits multi: yes, stereo: no] [Spatialization yes] "
            "[StereoSpatialization no] [item requires immersive rendering no]"
        ),
    ]
    assert all(event is not None for event in events)

    assert events[0]["format"] == "qc+3"
    assert events[0]["channels"] == 16
    assert events[0]["decodable"] is True
    assert events[0]["spatialization_eligible"] == "yes"
    assert events[0]["spatialization"] == "yes"
    assert events[0]["sample_rate"] == 48000
    assert events[1]["format"] == "ec+3"
    assert events[1]["channels"] == 16
    assert events[1]["spatialization_eligible"] == "yes"
    assert events[1]["spatialization"] == "yes"
