from dolby_tool.capture.tvlog import LogCapture, _parse_line, summarize_log_lines, tvlog_summary_markdown


def test_parse_line_figalternate():
    line = (
        "FigAlternate(504) [Peak/Avg 30570719/24765202] [3840x1606] "
        "[dvh1.05.06,ec-3] [VideoRange PQ] [HDCP Type1] [FrameRate 23.976]"
    )
    out = _parse_line(line)
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
    out = _parse_line(line)
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


def test_movpkg_selected_stereo_with_atmos_evidence_gets_verdict():
    lines = [
        (
            "2026-05-23 03:22:42.326 TV <<<< FigStreamPlayer >>>> "
            "to [<FigAlternate(82):[0x78c451180] [Peak/Avg 4834137/2890759] "
            "[1920x1080] [AudioGroup audio-stereo-160_download-ap-aoc.tv.apple.com] "
            "[SubtitleGroup subtitles_download-ap-aoc.tv.apple.com] "
            "[hvc1.2.20000000.L123.B0,mp4a.40.2] [VideoRange SDR] "
            "[HDCP Type0] [FrameRate 23.976]>]"
        ),
        (
            "2026-05-23 03:22:51.378 TV MEMixerChannel.cpp:3300 "
            "mFormatID='ec+3', mNumChannels=16, mBestAvailableContentType=3, "
            "mContentspatializable=1, mSpatializationStatus=0, err=0"
        ),
        "AUDIO_FORMAT qaac is decodable ch=2",
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]

    assert playback["source"] == "hls"
    assert playback["hls_delivery"] == "downloaded_movpkg"
    assert playback["selected_hls_audio_group"] == "audio-stereo-160_download-ap-aoc.tv.apple.com"
    assert playback["selected_hls_audio_group_kind"] == "stereo"
    assert playback["audio_codec"] == "mp4a.40.2"
    assert playback["downloaded_hls_verdict"] == "movpkg_atmos_variant_present_but_not_selected"


def test_parse_line_codec_type():
    out = _parse_line("CodecType: dvh1 (HW decoder), DecodedPixelBuffer: &xv0, 3840 x 1600")
    assert out is not None
    assert out["kind"] == "codec_type"
    assert out["fourcc"] == "dvh1"
    assert out["width"] == 3840
    assert out["height"] == 1600


def test_qdh1_cloud_capture_is_recognized_but_not_confirmed_dv():
    lines = [
        "FILE_PLAYER HEVC enc=4 3840x1606",
        "HLS_VARIANT nullxnull",
        "CODEC_TYPE qdh1 (HW decoder)",
        "LUMA_CHROMA luma=10 chroma=1",
        "FILE_PLAYER HEVC enc=4 1918x802",
        "AUDIO_FORMAT qc+3 is decodable ch=16",
        "CODEC_TYPE qdh1 (HW decoder)",
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    summary = capture.summarize()
    playback = summary["playback"]

    assert playback["source"] == "hls"
    assert playback["decoded_fourcc"] == "qdh1"
    assert playback["dolby_vision_active"] is None
    assert playback["dv_label"] == "Apple HLS private HDR/DV path (qdh1)"
    assert "Apple's private CoreMedia" in playback["dv_diagnosis"]
    assert playback["file_player"]["encryption_scheme"] == 4
    assert playback["audio"]["format"] == "qc+3"
    assert playback["audio"]["channels"] == 16
    assert playback["audio"]["decodable"] is True
    assert playback["audio"]["is_atmos"] is False
    assert "Apple/CoreMedia Dolby-like" in playback["audio"]["diagnosis"]
    assert playback["best_audio"]["format"] == "qc+3"
    assert playback["observed_audio"] == [{**playback["audio"], "count": 1}]


def test_tv_capture_keeps_current_best_and_observed_audio():
    lines = [
        "CODEC_TYPE qdh1 (HW decoder)",
        "AUDIO_FORMAT ec+3 is decodable ch=16",
        "AUDIO_FORMAT qaac is decodable ch=2",
        "AUDIO_FORMAT qaac is decodable ch=2",
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]

    assert playback["audio"]["format"] == "qaac"
    assert playback["audio"]["channels"] == 2
    assert playback["best_audio"]["format"] == "ec+3"
    assert playback["best_audio"]["channels"] == 16
    assert playback["observed_audio"] == [
        {
            "format": "ec+3",
            "channels": 16,
            "sample_rate": None,
            "spatialization": None,
            "spatialization_eligible": None,
            "decodable": True,
            "is_atmos": False,
            "count": 1,
        },
        {
            "format": "qaac",
            "channels": 2,
            "sample_rate": None,
            "spatialization": None,
            "spatialization_eligible": None,
            "decodable": True,
            "is_atmos": False,
            "count": 2,
        },
    ]


def test_local_file_prefers_figfileplayer_when_old_stream_player_is_observed():
    lines = [
        "2026-05-23 TV <<<< FigStreamPlayer >>>> fpfs_SetRateWithOptionsAndAnchorTime: "
        "called for reason: StopForSurrogatePlayerSwitch",
        "2026-05-23 TV <<<< FigFilePlayer >>>> FigPlayerFileCreateWithOptions: returning player",
        "2026-05-23 TV FILE_PLAYER HEVC enc=6 3840x1600",
        "2026-05-23 TV <<<< FigFilePlayer >>>> fp_buildAudioRenderPipelineForTrack called",
        "2026-05-23 TV AudioQueueNewOutput 16 ch, 48000 Hz, ec+3",
    ]

    playback = summarize_log_lines(lines)["playback"]

    assert playback["source"] == "local_file"
    assert playback["pipeline_engines_observed"] == ["FigStreamPlayer", "FigFilePlayer"]
    assert playback["pipeline_engine"] == "FigFilePlayer"


def test_audio_status_text_is_normalized_from_codec_labels():
    lines = [
        "AUDIO_FORMAT table:",
        "[AudioFormat aacp is decodable ch=2]",
        "[AudioFormat qaac is decodable ch=2]",
        "[AudioFormat qc+3 is decodable ch=16]",
        "AUDIO_FORMAT ec+3 ch=16",
    ]
    events = [_parse_line(line) for line in lines]
    assert events[0] is None
    assert all(event is not None for event in events[1:])

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]

    assert playback["audio"]["format"] == "ec+3"
    assert playback["audio"]["channels"] == 16
    assert playback["audio"]["decodable"] is None
    assert playback["best_audio"]["format"] == "qc+3"
    assert playback["best_audio"]["channels"] == 16
    assert playback["best_audio"]["decodable"] is True
    assert [audio["format"] for audio in playback["observed_audio"]] == [
        "aacp",
        "qaac",
        "qc+3",
        "ec+3",
    ]
    assert [audio["channels"] for audio in playback["observed_audio"]] == [2, 2, 16, 16]
    assert [audio["decodable"] for audio in playback["observed_audio"]] == [
        True,
        True,
        True,
        None,
    ]


def test_audio_format_keeps_spatialization_fields_without_inventing_atmos():
    events = [
        _parse_line(
            "[AudioFormat ec+3] [AudioChannels 16] [SampleRate 48000] "
            "[Spatialization Eligible yes] [Spatialization yes]"
        ),
        _parse_line(
            "[AudioFormat qc+3] [AudioChannels 16] [SampleRate 48000] [Spatialization Eligible yes] [Spatialization no]"
        ),
    ]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]

    assert playback["observed_audio"][0]["format"] == "ec+3"
    assert playback["observed_audio"][0]["spatialization_eligible"] == "yes"
    assert playback["observed_audio"][0]["spatialization"] == "yes"
    assert playback["observed_audio"][0]["sample_rate"] == 48000
    assert playback["observed_audio"][0]["is_atmos"] is True
    assert playback["observed_audio"][1]["format"] == "qc+3"
    assert playback["observed_audio"][1]["spatialization_eligible"] == "yes"
    assert playback["observed_audio"][1]["spatialization"] == "no"
    assert playback["observed_audio"][1]["is_atmos"] is False


def test_audio_format_keeps_non_adjacent_spatialization_fields():
    events = [
        _parse_line(
            "2026-05-22 17:34:57.401 Df TV[767:1dc770] "
            "fpfs_ReportAudioPlaybackThroughFigLog: [AudioFormat qc+3 is  decodable] "
            "[AudioChannels 16] [Spatialization Eligible yes] "
            "[Client permits multi: yes, stereo: no] [Spatialization yes] "
            "[StereoSpatialization no] [Rendition Multichannel] [SampleRate 48000]"
        ),
        _parse_line(
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


def test_best_audio_prefers_qc3_over_aacp_stereo_fallback():
    lines = [
        "AUDIO_FORMAT aacp is decodable ch=2",
        "AUDIO_FORMAT qc+3 is decodable ch=16",
        "AUDIO_FORMAT aacp is decodable ch=2",
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]

    assert playback["audio"]["format"] == "aacp"
    assert playback["audio"]["channels"] == 2
    assert playback["best_audio"]["format"] == "qc+3"
    assert playback["best_audio"]["channels"] == 16
    assert [audio["format"] for audio in playback["observed_audio"]] == ["aacp", "qc+3"]
    assert [audio["count"] for audio in playback["observed_audio"]] == [2, 1]


def test_renderer_hints_parse_local_spatial_lines():
    lines = [
        (
            "2026-05-22 17:49:32.743 Df TV[12389:1dd6aa] [com.apple.TV:ampplay] "
            "play> cm>> mediaFormatinfo '<private>' , audioCapabilities: 0x0 -> 0x1, "
            "0x0 -> 0x1, asbdFormatID = ec+3, Dolby Atmos, asbdNumChannels = 16, "
            "asbdSampleRate = 48.0 kHz, is not rendering spatial audio"
        ),
        (
            "2026-05-22 17:49:32.842 Df TV[12389:1e1cb7] [com.apple.coreaudio:SpatialMgr] "
            "SpatializationManager.cpp:1735  Logging power event: pid = 12389, name = TV, "
            "spatialization = 1, stereoUpmix = 0, headTracking = 0"
        ),
        "\t\tprefersHeadTrackedSpatialization = 0",
        "\tRoute = built-in speakers",
        (
            "2026-05-22 17:49:32.428 I  TV[12389:1e1cfa] [com.apple.coreaudio:ac] "
            "ACDDPAtmosDecoder.cpp:384   (0x77cbdc040) mIsAtmos = 1, "
            "mIsTVOS = 0, mIsOARMode = 1"
        ),
        (
            "2026-05-22 17:49:32.428 I  TV[12389:1e1cfa] [com.apple.coreaudio:ac] "
            "ACDDPAtmosDecoder.cpp:383   (0x77cbdc040) subType = 'ec+3', "
            "inputFormat = 0x16c7f08a0, outputFormat = 0xb0696e2f0"
        ),
        (
            "2026-05-22 17:49:32.833 Df TV[12389:1e1cb7] [com.apple.coreaudio:aqme] "
            "MEMixerChannel.cpp:3300  mFormatID='ec+3', mNumChannels=16, "
            "mBestAvailableContentType=3, mContentspatializable=1, "
            "mSpatializationStatus=2, err=0"
        ),
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]
    renderer = playback["renderer_evidence"]

    assert playback["capture_quality"]["label"] == "complete"
    assert playback["capture_quality"]["likely_missed_audio_init"] is False
    assert renderer["routes"] == ["built-in speakers"]
    assert renderer["route_capability_label"] == "built-in speakers"
    assert renderer["app_spatial_rendering_ever_true"] is False
    assert renderer["app_spatial_rendering_last_state"] is False
    assert renderer["spatial_power_active"] is True
    assert renderer["head_tracking_active"] is False
    assert renderer["atmos_decoder_active"] is True
    assert renderer["oar_mode_active"] is True
    assert renderer["mixer_content_spatializable"] is True
    assert renderer["mixer_spatialization_statuses"] == [2]
    assert renderer["lower_level_spatialization_active"] is True
    assert renderer["verdict"] == "lower_level_active_app_spatial_false"
    assert renderer["media_formatinfo"] == [
        {
            "format": "ec+3",
            "label": "Dolby Atmos",
            "channels": 16,
            "sample_rate": 48000.0,
            "rendering_spatial_audio": False,
        }
    ]
    assert renderer["spatial_power"] == [{"spatialization": True, "stereo_upmix": False, "head_tracking": False}]
    assert renderer["prefers_head_tracked_spatialization"] == [False]
    assert renderer["atmos_decoder_states"] == [{"decoder_is_atmos": True, "decoder_oar_mode": True}]
    assert renderer["decoder_subtypes"] == ["ec+3"]
    assert renderer["mixer_spatial_status"] == [
        {
            "format": "ec+3",
            "channels": 16,
            "content_spatializable": True,
            "spatialization_status": 2,
        }
    ]


def test_quicktime_local_hls_evidence_parses_figstream_and_auspatial():
    lines = [
        (
            "2026-05-23 05:22:30.724 Df QuickTime Player[9643:22260] "
            "[com.apple.coremedia:player] <<<< FigStreamPlayer >>>> "
            "FigPlayerStreamCreateWithOptions: [0xb61048000|P/FX]"
        ),
        (
            "2026-05-23 05:22:30.733 Df QuickTime Player[9643:23338] "
            "[com.apple.coremedia:player] <<<< FigStreamPlayer >>>> "
            "fpfs_ReportVariantSwitchStart: to [<FigAlternate( 0):[0xb6095d400] "
            "[Peak/Avg 16000000/15600000] [3840x1600] [AudioGroup atmos] "
            "[dvh1.05.06,ec-3] [VideoRange PQ] [FrameRate 24.000]>]"
        ),
        (
            "2026-05-23 05:22:30.745 Db QuickTime Player[9643:224f2] "
            "[com.apple.coreaudio:AQClient] AQ_API_V2Impl.cpp:132 "
            "AudioQueueNew: ->AudioQueueNewOutput 16 ch, 48000 Hz, ec+3 "
            "(0x00000000) 0 bits/channel"
        ),
        (
            "2026-05-23 05:22:30.746 I QuickTime Player[9643:224f2] "
            "[com.apple.coreaudio:ac] ACDDPAtmosDecoder.cpp:384 "
            "mIsAtmos = 1, mIsTVOS = 0, mIsOARMode = 1"
        ),
        (
            "2026-05-23 05:22:30.783 Df QuickTime Player[9643:224f2] "
            "[com.apple.coreaudio:AQ] AudioQueueObject.cpp:1085 "
            "Forcing 7.1.4 decoder for Atmos"
        ),
        (
            "2026-05-23 05:22:31.204 Df QuickTime Player[9643:23337] "
            "[com.apple.coreaudio:AUSpatialMixerV2] "
            "[0x825ab13b|SetAudioChannelLayout] [InputElement #0] "
            "Setting audio channel layout Atmos_7_1_4"
        ),
        (
            "2026-05-23 05:22:31.206 Df QuickTime Player[9643:23337] "
            "[com.apple.coreaudio:AUSpatialMixerV2] "
            "[0x825ab13b|InputElement #0|InitializeChannelProcessors] "
            "Initializing 12 channel processors"
        ),
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]
    renderer = playback["renderer_evidence"]

    assert playback["source"] == "hls"
    assert playback["pipeline_engine"] == "FigStreamPlayer"
    assert playback["selected_hls_audio_group"] == "atmos"
    assert playback["selected_hls_audio_group_kind"] == "atmos"
    assert playback["audio_codec"] == "ec-3"
    assert playback["audio"]["format"] == "ec+3"
    assert playback["audio"]["channels"] == 16
    assert renderer["atmos_decoder_active"] is True
    assert renderer["oar_mode_active"] is True
    assert renderer["audioqueue_forced_atmos_714"] is True
    assert renderer["auspatial_mixer_active"] is True
    assert renderer["auspatial_atmos_layout_active"] is True
    assert renderer["auspatial_channel_layouts"] == ["Atmos_7_1_4"]
    assert renderer["auspatial_channel_processors"] == [12]
    assert renderer["lower_level_spatialization_active"] is True
    assert renderer["verdict"] == "lower_level_spatialization_active"


def test_tvlog_summary_markdown_renders_local_player_controls():
    lines = [
        (
            "2026-05-23 05:22:30.733 Df QuickTime Player[9643:23338] "
            "[com.apple.coremedia:player] <<<< FigStreamPlayer >>>> "
            "to [<FigAlternate( 0):[0xb6095d400] [Peak/Avg 16000000/15600000] "
            "[3840x1600] [AudioGroup atmos] [dvh1.05.06,ec-3] [VideoRange PQ]>]"
        ),
        (
            "2026-05-23 05:22:30.745 Db QuickTime Player[9643:224f2] "
            "AudioQueueNew: ->AudioQueueNewOutput 16 ch, 48000 Hz, ec+3"
        ),
        ("2026-05-23 05:22:30.783 Df QuickTime Player[9643:224f2] Forcing 7.1.4 decoder for Atmos"),
        (
            "2026-05-23 05:22:31.204 Df QuickTime Player[9643:23337] "
            "[com.apple.coreaudio:AUSpatialMixerV2] "
            "Setting audio channel layout Atmos_7_1_4"
        ),
    ]

    summary = summarize_log_lines(lines)
    rendered = tvlog_summary_markdown(summary)

    assert "- source: `hls`" in rendered
    assert "- pipeline engine: `FigStreamPlayer`" in rendered
    assert "- selected HLS AudioGroup: `atmos`" in rendered
    assert "- HLS audio codec: `ec-3`" in rendered
    assert "- current audio: `ec+3 ch=16 48000 Hz`" in rendered
    assert "- AudioQueue forced 7.1.4 Atmos: `True`" in rendered
    assert "- AUSpatialMixer layouts: `Atmos_7_1_4`" in rendered


def test_renderer_hints_parse_spatialmgr_binding_block():
    # SpatializationManager emits multi-field binding blocks; each field is
    # parsed independently by the line-based parser. Verify the three new
    # signals (App, maxSpatializableChannels, spatialAudioSources) make it
    # into the renderer summary.
    lines = [
        "    App = SpatialProbe",
        "    Route = built-in speakers",
        "        maxSpatializableChannels = 16",
        "        spatialAudioSources = [ 'mlti' ]",
    ]
    events = [_parse_line(line) for line in lines]
    assert all(e is not None for e in events)

    capture = LogCapture.from_events(e for e in events if e is not None)
    renderer = capture.summarize()["playback"]["renderer_evidence"]

    assert renderer["max_spatializable_channels"] == 16
    assert renderer["route_spatial_capable"] is True
    assert renderer["spatial_audio_sources"] == ["mlti"]
    assert renderer["spatial_source_unknown"] is False
    assert renderer["spatial_binding_apps"] == ["SpatialProbe"]


def test_renderer_hints_parse_spatialmgr_non_capable_route():
    lines = [
        "    App = SpatialProbe",
        "    Route = not capable of spatialization",
        "        maxSpatializableChannels = 0",
        "        spatialAudioSources = [ '?src' ]",
    ]
    events = [_parse_line(line) for line in lines]
    assert all(e is not None for e in events)

    capture = LogCapture.from_events(e for e in events if e is not None)
    renderer = capture.summarize()["playback"]["renderer_evidence"]

    assert renderer["max_spatializable_channels"] == 0
    assert renderer["route_spatial_capable"] is False
    assert renderer["spatial_audio_sources"] == ["?src"]
    assert renderer["spatial_source_unknown"] is True


def test_renderer_hints_parse_hls_spatial_lines():
    lines = [
        (
            "2026-05-22 17:51:18.292 Df TV[12389:1dd6aa] [com.apple.TV:ampplay] "
            "play> cm>> mediaFormatinfo '<private>' , audioCapabilities: 0x8 -> 0x1, "
            "0x8 -> 0x1, asbdFormatID = qc+3, Dolby Atmos, asbdNumChannels = 16, "
            "asbdSampleRate = 48.0 kHz, is rendering spatial audio"
        ),
        (
            "2026-05-22 17:51:18.290 Df TV[12389:1e1f62] [com.apple.TV:cmplayer] "
            "play> avcff> noti> 'AVCFPlayerItemSpatialAudioRenderingDidChangeNotification' "
            "ppi=0x779715200 '<private>'"
        ),
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]
    renderer = playback["renderer_evidence"]

    assert playback["capture_quality"]["label"] == "complete"
    assert renderer["app_spatial_rendering_ever_true"] is True
    assert renderer["app_spatial_rendering_last_state"] is True
    assert renderer["lower_level_spatialization_active"] is False
    assert renderer["verdict"] == "app_spatial_rendering_active"
    assert renderer["media_formatinfo"][0]["format"] == "qc+3"
    assert renderer["media_formatinfo"][0]["rendering_spatial_audio"] is True
    assert renderer["spatial_rendering_changed_count"] == 1


def test_renderer_verdict_handles_app_spatial_true_then_false():
    lines = [
        (
            "2026-05-22 17:51:18.292 Df TV[12389:1dd6aa] [com.apple.TV:ampplay] "
            "play> cm>> mediaFormatinfo '<private>' , audioCapabilities: 0x8 -> 0x1, "
            "0x8 -> 0x1, asbdFormatID = qc+3, Dolby Atmos, asbdNumChannels = 16, "
            "asbdSampleRate = 48.0 kHz, is rendering spatial audio"
        ),
        (
            "2026-05-22 17:51:19.292 Df TV[12389:1dd6aa] [com.apple.TV:ampplay] "
            "play> cm>> mediaFormatinfo '<private>' , audioCapabilities: 0x8 -> 0x1, "
            "0x8 -> 0x1, asbdFormatID = qc+3, Dolby Atmos, asbdNumChannels = 16, "
            "asbdSampleRate = 48.0 kHz, is not rendering spatial audio"
        ),
    ]
    events = [_parse_line(line) for line in lines]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    renderer = capture.summarize()["playback"]["renderer_evidence"]

    assert renderer["app_spatial_rendering_ever_true"] is True
    assert renderer["app_spatial_rendering_last_state"] is False
    assert renderer["lower_level_spatialization_active"] is False
    assert renderer["verdict"] == "app_spatial_rendering_was_active"


def test_capture_quality_notes_likely_missed_audio_init():
    events = [
        _parse_line("CODEC_TYPE qdh1 (HW decoder)"),
        _parse_line("CODEC_TYPE qdh1 (HW decoder), DecodedPixelBuffer: &xv0, 3840 x 1606"),
    ]
    assert all(event is not None for event in events)

    capture = LogCapture.from_events(event for event in events if event is not None)
    playback = capture.summarize()["playback"]

    assert "audio" not in playback
    assert "renderer_evidence" not in playback
    assert playback["capture_quality"]["label"] == "likely missed audio init"
    assert playback["capture_quality"]["likely_missed_audio_init"] is True
    assert "no audio or renderer evidence" in playback["capture_quality"]["note"]


def _audio_capture_summary(total: int, cap: int):
    capture = LogCapture(raw_event_limit=cap)
    for idx in range(total):
        event = _parse_line(f"AUDIO_FORMAT qaac is decodable ch={idx + 1}")
        assert event is not None
        capture.add_event(event)
    return capture.summarize()


def test_log_capture_raw_event_cap_under_limit_returns_all_events():
    summary = _audio_capture_summary(total=3, cap=5)

    assert summary["event_count"] == 3
    assert summary["events_returned"] == 3
    assert summary["events_dropped"] == 0
    assert summary["raw_event_limit"] == 5
    assert [event["channels"] for event in summary["events"]] == [1, 2, 3]


def test_log_capture_raw_event_cap_at_limit_returns_all_events():
    summary = _audio_capture_summary(total=5, cap=5)

    assert summary["event_count"] == 5
    assert summary["events_returned"] == 5
    assert summary["events_dropped"] == 0
    assert summary["raw_event_limit"] == 5
    assert [event["channels"] for event in summary["events"]] == [1, 2, 3, 4, 5]


def test_log_capture_raw_event_cap_over_limit_keeps_newest_events():
    summary = _audio_capture_summary(total=7, cap=5)

    assert summary["event_count"] == 7
    assert summary["events_returned"] == 5
    assert summary["events_dropped"] == 2
    assert summary["raw_event_limit"] == 5
    assert [event["channels"] for event in summary["events"]] == [3, 4, 5, 6, 7]


def test_log_capture_summary_keeps_best_audio_after_raw_event_drop():
    capture = LogCapture(raw_event_limit=2)
    for line in [
        "AUDIO_FORMAT ec+3 is decodable ch=16",
        "AUDIO_FORMAT qaac is decodable ch=2",
        "AUDIO_FORMAT qaac is decodable ch=2",
    ]:
        event = _parse_line(line)
        assert event is not None
        capture.add_event(event)

    summary = capture.summarize()
    playback = summary["playback"]

    assert summary["event_count"] == 3
    assert summary["events_returned"] == 2
    assert summary["events_dropped"] == 1
    assert [event["channels"] for event in summary["events"]] == [2, 2]
    assert playback["audio"]["format"] == "qaac"
    assert playback["best_audio"]["format"] == "ec+3"
    assert playback["best_audio"]["channels"] == 16
    assert [audio["format"] for audio in playback["observed_audio"]] == ["ec+3", "qaac"]
