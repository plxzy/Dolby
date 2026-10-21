"""Non-admin system evidence: parsing real tool output and folding samples into the `system` block."""

import plistlib

import pytest

from dolby_tool.capture import LogCapture
from dolby_tool.capture.system import (
    SystemState,
    parse_decoder_sessions,
    parse_lsof_media,
    parse_nettop,
    parse_panel_nits_cap,
    parse_pmset_assertions,
    playback_activity,
)
from dolby_tool.common import ToolError

# AppleAVDUserClient entries as `ioreg -r -c AppleAVDUserClient -a` prints them during Apple TV+
# playback (FairPlay HEVC decoded inside TV) next to a clear H.264 file in QuickTime.
AVD_PLIST = plistlib.dumps(
    [
        {
            "FrameWidth": 3840,
            "FrameHeight": 1920,
            "ChromaFormat": 1,
            "LumaBitDepth": 10,
            "CodecType": 2,
            "EncryptionScheme": 4,
            "CreationTime": "2026-10-21 17:39:09",
            "IOUserClientCreator": "pid 86365, TV",
        },
        {
            "FrameWidth": 1664,
            "FrameHeight": 1088,
            "ChromaFormat": 1,
            "LumaBitDepth": 8,
            "CodecType": 1,
            "EncryptionScheme": 6,
            "CreationTime": "2026-10-21 17:52:01",
            "IOUserClientCreator": "pid 96143, VTDecoderXPCServ",
        },
        {"IOUserClientCreator": "pid 1, launchd"},  # not a decoder session
    ]
)

PMSET = """\
Listed by owning process:
   pid 573(coreaudiod): [0x000058d200018da0] 00:00:37 PreventUserIdleSystemSleep named: "com.apple.audio.BuiltInSpeakerDevice.context.preventuseridlesleep"
\tCreated for PID: 80677.
\tResources: audio-out BuiltInSpeakerDevice
   pid 80677(TV): [0x000058f1000593ff] 00:00:05 PreventUserIdleDisplaySleep named: "com.apple.coremedia.iq.ca.client-1"
   pid 365(powerd): [0x000053c6000188cd] 00:22:08 PreventUserIdleSystemSleep named: "Powerd - Prevent sleep while display is on"
No kernel assertions.
"""  # noqa: E501


def test_decoder_sessions_decode_codec_drm_and_owner():
    tv, quicktime = parse_decoder_sessions(AVD_PLIST)

    assert (tv["codec"], tv["width"], tv["height"], tv["bit_depth"], tv["chroma"]) == ("HEVC", 3840, 1920, 10, "4:2:0")
    assert (tv["creator_pid"], tv["creator"], tv["protected"]) == (86365, "TV", True)
    assert (quicktime["codec"], quicktime["protected"], quicktime["creator"]) == ("H.264", False, "VTDecoderXPCServ")


def test_unreadable_ioreg_output_yields_nothing():
    assert parse_decoder_sessions(b"") == []
    assert parse_decoder_sessions(b"not a plist") == []
    assert parse_panel_nits_cap(b"") is None


def test_panel_nits_cap_is_the_largest_16_16_value():
    plist = plistlib.dumps([{"BLNitsCap": 104857600}, {"IOMFBBrightnessLevel": 65536}, {"BLNitsCap": 19660800}])
    assert parse_panel_nits_cap(plist) == 1600.0


def test_pmset_links_the_audio_output_to_the_player_it_was_opened_for():
    assertions = parse_pmset_assertions(PMSET)

    assert playback_activity(assertions, {80677}) == {
        "playing_pids": [80677],
        "audio_outputs": ["BuiltInSpeakerDevice"],
    }
    # another app's audio session is not the player's route
    assert playback_activity(assertions, {12345}) == {"playing_pids": [], "audio_outputs": []}


def test_nettop_rows_keep_process_names_containing_dots():
    text = ",bytes_in,\nTV.86365,852386446,\ncom.apple.WebKit.Networking.501,1200,\n"
    assert parse_nettop(text) == {86365: ("TV", 852386446), 501: ("com.apple.WebKit.Networking", 1200)}


def test_lsof_reports_user_media_and_collapses_movpkg_internals():
    text = "\n".join(
        [
            "p86365",
            "n/System/Applications/TV.app/Contents/MacOS/TV",
            "n/Users/dev/Movies/Film.m4v",
            "n/Users/dev/Library/Containers/x/Show.movpkg/0/seg1.frag",
            "n/Users/dev/Library/Containers/x/Show.movpkg/1/seg2.frag",
            "n/Users/dev/Library/Caches/com.apple.TV/Cache.db",
            "n[2a01::1]:443",
        ]
    )
    assert parse_lsof_media(text) == ["/Users/dev/Movies/Film.m4v", "/Users/dev/Library/Containers/x/Show.movpkg"]


def _playing_state() -> SystemState:
    state = SystemState(["TV"])
    state.fold_pids({86365: "TV"})
    state.fold_decoders(parse_decoder_sessions(AVD_PLIST), now=100.0)
    return state


def test_primary_video_prefers_the_players_own_session():
    state = _playing_state()
    video = state.summary()["video"]

    assert video["coded_resolution"] == "3840x1920"
    assert video["decoded_in_player_process"] is True


def test_decoder_sessions_of_unrelated_apps_are_ignored():
    state = SystemState(["TV"])
    state.fold_pids({86365: "TV"})
    other = plistlib.dumps([{"FrameWidth": 640, "FrameHeight": 360, "IOUserClientCreator": "pid 4242, Safari"}])

    assert state.fold_decoders(parse_decoder_sessions(other), now=1.0) == []
    assert state.summary()["video"] is None


def test_hdr_verdict_needs_ten_bit_decode_and_hdr_presentation():
    state = _playing_state()
    assert state.summary()["verdict"]["label"] == "10-bit video, no HDR presentation seen"

    [event] = state.fold_display([{"name": "Built-in", "headroom": 7.26, "potential": 16.0}], nits_cap=1600.0)
    assert event["kind"] == "system_display" and event["hdr_active"] is True
    assert state.summary()["verdict"]["label"] == "HDR video (Dolby Vision or HDR10)"


def test_display_events_fire_only_on_hdr_transitions():
    state = SystemState(["TV"])
    sdr = [{"headroom": 1.2, "potential": 16.0}]
    hdr = [{"headroom": 7.3, "potential": 16.0}]

    assert [e["hdr_active"] for e in state.fold_display(sdr, 300.0)] == [False]
    assert state.fold_display(sdr, 300.0) == []
    assert [e["hdr_active"] for e in state.fold_display(hdr, 1600.0)] == [True]
    assert [e["hdr_active"] for e in state.fold_display(sdr, 300.0)] == [False]
    assert state.summary()["display"]["hdr_presented"] is True


def test_network_rate_is_measured_between_samples_and_survives_a_counter_reset():
    state = SystemState(["TV"])
    state.fold_pids({86365: "TV"})

    assert state.fold_network({86365: ("TV", 0)}, now=0.0) == []
    [event] = state.fold_network({86365: ("TV", 25_000_000)}, now=5.0)  # 200 Mbit in 5 s
    assert event["mbps"] == 40.0
    assert state.fold_network({86365: ("TV", 10)}, now=10.0) == []  # TV relaunched: no negative rate
    assert state.summary()["network"]["peak_mbps"] == 40.0


def test_capture_without_log_access_runs_on_the_sampler_alone(monkeypatch):
    monkeypatch.setattr(
        "dolby_tool.capture.session.log_stream_access", lambda: {"available": False, "reason": "standard account"}
    )

    class FakeSampler:
        started = False

        def start(self, emit):
            FakeSampler.started = True
            emit({"kind": "system_decoder", "raw": "HEVC", "source": "system"})

        def stop(self):
            pass

        def summary(self, *, log_available):
            assert log_available is False
            return {"verdict": {"label": "SDR video", "notes": []}}

    capture = LogCapture(system=FakeSampler())  # type: ignore[arg-type]
    capture.start()
    summary = capture.stop()

    assert FakeSampler.started and capture._proc is None
    assert summary["capture_mode"] == "system"
    assert summary["log_access"] == {"available": False, "reason": "standard account"}
    assert summary["system_event_count"] == 1
    assert summary["playback"] is None and summary["system"]["verdict"]["label"] == "SDR video"


def test_capture_without_log_access_or_sampler_refuses_to_start(monkeypatch):
    monkeypatch.setattr(
        "dolby_tool.capture.session.log_stream_access", lambda: {"available": False, "reason": "standard account"}
    )
    with pytest.raises(ToolError, match="standard account"):
        LogCapture().start()
