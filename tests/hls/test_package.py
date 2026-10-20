import subprocess

import pytest

from dolby_tool.hls.package import package_hls_from_file


def test_package_hls_from_file_invokes_ffmpeg_for_split_audio_group(tmp_path, monkeypatch):
    source = tmp_path / "input.mp4"
    output = tmp_path / "out"
    source.write_bytes(b"media")
    calls = []

    def fake_run(cmd, *, check, capture_output, text):
        calls.append(cmd)
        assert check is True
        assert capture_output is True
        assert text is True
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(
                cmd,
                0,
                """{
                  "streams": [
                    {
                      "codec_type": "video",
                      "codec_tag_string": "dvh1",
                      "avg_frame_rate": "24000/1001",
                      "color_transfer": "smpte2084",
                      "color_primaries": "bt2020"
                    },
                    {
                      "codec_type": "audio",
                      "codec_name": "aac",
                      "codec_tag_string": "mp4a",
                      "channels": 2
                    },
                    {
                      "codec_type": "audio",
                      "codec_name": "eac3",
                      "codec_tag_string": "ec-3",
                      "profile": "Dolby Digital Plus + Dolby Atmos",
                      "channels": 6,
                      "tags": {"language": "eng"}
                    }
                  ]
                }""",
                "",
            )
        output.mkdir(exist_ok=True)
        (output / "master.m3u8").write_text(
            '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="group_audio",URI="stream_English.m3u8"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="group_audio"\nstream_video.m3u8\n',
            encoding="utf-8",
        )
        (output / "stream_video.m3u8").write_text(
            '#EXTM3U\n#EXT-X-MAP:URI="init_0.mp4"\nstream_video_0000.m4s\n',
            encoding="utf-8",
        )
        (output / "stream_English.m3u8").write_text(
            '#EXTM3U\n#EXT-X-MAP:URI="init_1.mp4"\nstream_English_0000.m4s\n',
            encoding="utf-8",
        )
        (output / "init_0.mp4").write_bytes(b"vinit")
        (output / "init_1.mp4").write_bytes(b"ainit")
        (output / "stream_video_0000.m4s").write_bytes(b"video")
        (output / "stream_English_0000.m4s").write_bytes(b"audio")
        return subprocess.CompletedProcess(cmd, 0, "", "ffmpeg stderr")

    monkeypatch.setattr("dolby_tool.hls.package.subprocess.run", fake_run)

    summary = package_hls_from_file(source, output, audio_stream=1, segment_time=4)

    cmd = calls[1]
    assert "-map" in cmd
    assert "0:a:1" in cmd
    assert "-hls_segment_type" in cmd
    assert "fmp4" in cmd
    assert "v:0,agroup:audio,name:video a:0,agroup:audio,language:eng,name:English,default:yes" in cmd
    assert summary["split_audio_group"] is True
    assert summary["ffmpeg_stderr_tail"] == ["ffmpeg stderr"]
    assert summary["master_playlist_tags"] == {
        "audio_channels": "6/JOC",
        "codecs": "dvh1.05.06,ec-3",
        "frame_rate": "23.976",
        "language": "en",
        "video_range": "PQ",
    }
    assert (output / "master.m3u8").read_text(encoding="utf-8") == (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="group_audio",URI="stream_English.m3u8",'
        'LANGUAGE="en",NAME="English",AUTOSELECT=YES,CHANNELS="6/JOC"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="group_audio",'
        'FRAME-RATE=23.976,CODECS="dvh1.05.06,ec-3",VIDEO-RANGE=PQ\n'
        "stream_video.m3u8\n"
    )
    assert summary["playlists"] == [
        {"playlist": "master.m3u8", "segments": 0, "init_maps": 0, "bytes": 0},
        {"playlist": "stream_English.m3u8", "segments": 1, "init_maps": 1, "bytes": 10},
        {"playlist": "stream_video.m3u8", "segments": 1, "init_maps": 1, "bytes": 10},
    ]


def test_package_hls_from_file_rejects_non_empty_output_without_overwrite(tmp_path, monkeypatch):
    source = tmp_path / "input.mp4"
    output = tmp_path / "out"
    source.write_bytes(b"media")
    output.mkdir()
    (output / "old.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("ffmpeg should not run")

    monkeypatch.setattr("dolby_tool.hls.package.subprocess.run", fail_run)

    with pytest.raises(ValueError, match="Output directory is not empty"):
        package_hls_from_file(source, output)
