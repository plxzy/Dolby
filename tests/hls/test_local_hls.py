import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from dolby_tool.hls.local_hls import (
    package_hls_from_file,
    playlist_url,
    prepare_hls_from_movpkg,
    running_hls_server,
)


def test_hls_server_supports_byte_ranges(tmp_path):
    (tmp_path / "master.m3u8").write_text("#EXTM3U\nsegment.m4s\n", encoding="utf-8")
    (tmp_path / "segment.m4s").write_bytes(b"0123456789")

    with running_hls_server(tmp_path, port=0, quiet=True) as server:
        url = playlist_url(server, "segment.m4s")
        request = Request(url, headers={"Range": "bytes=2-5"})
        with urlopen(request, timeout=5) as response:
            body = response.read()

    assert response.status == 206
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert body == b"2345"


def test_hls_server_supports_suffix_byte_ranges(tmp_path):
    (tmp_path / "segment.m4s").write_bytes(b"0123456789")

    with running_hls_server(tmp_path, port=0, quiet=True) as server:
        url = playlist_url(server, "segment.m4s")
        request = Request(url, headers={"Range": "bytes=-3"})
        with urlopen(request, timeout=5) as response:
            body = response.read()

    assert response.status == 206
    assert response.headers["Content-Range"] == "bytes 7-9/10"
    assert body == b"789"


def test_hls_server_rejects_unsatisfiable_ranges(tmp_path):
    (tmp_path / "segment.m4s").write_bytes(b"0123456789")

    with running_hls_server(tmp_path, port=0, quiet=True) as server:
        url = playlist_url(server, "segment.m4s")
        request = Request(url, headers={"Range": "bytes=99-120"})
        try:
            urlopen(request, timeout=5)
        except HTTPError as error:
            status = error.code
            content_range = error.headers["Content-Range"]
        else:
            raise AssertionError("expected HTTP 416")

    assert status == 416
    assert content_range == "bytes */10"


def test_prepare_hls_from_movpkg_flattens_persisted_streams(tmp_path):
    movpkg = tmp_path / "Test.movpkg"
    video_dir = movpkg / "0-video"
    audio_dir = movpkg / "1-audio"
    data_dir = movpkg / "Data"
    video_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)
    data_dir.mkdir()
    (movpkg / "boot.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<HLSMoviePackage xmlns="http://apple.com/IMG/Schemas/HLSMoviePackage">
  <Streams>
    <Stream ID="0-video" NetworkURL="http://127.0.0.1:8765/video.m3u8" Path="0-video"><Complete>YES</Complete></Stream>
    <Stream ID="1-audio" NetworkURL="http://127.0.0.1:8765/audio.m3u8" Path="1-audio"><Complete>YES</Complete></Stream>
  </Streams>
  <DataItems Directory="Data">
    <DataItem><DataPath>master-source.m3u8</DataPath><Role>Master</Role></DataItem>
  </DataItems>
</HLSMoviePackage>
""",
        encoding="utf-8",
    )
    (data_dir / "master-source.m3u8").write_text(
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="atmos"\nvideo.m3u8\n\x00',
        encoding="utf-8",
    )
    _write_stream_info(video_dir, "video-source.m3u8", "video_seg.m4s", b"vinit", [b"v0", b"v1"])
    _write_stream_info(audio_dir, "audio-source.m3u8", "audio_seg.m4s", b"ainit", [b"a0", b"a1"])

    output = tmp_path / "out"
    summary = prepare_hls_from_movpkg(movpkg, output)

    assert (output / "master.m3u8").read_text(encoding="utf-8").endswith("video.m3u8\n")
    assert (output / "video.m3u8").read_text(encoding="utf-8").count("video_seg.m4s") == 3
    assert (output / "audio.m3u8").read_text(encoding="utf-8").count("audio_seg.m4s") == 3
    assert (output / "video_seg.m4s").read_bytes() == b"vinitv0v1"
    assert (output / "audio_seg.m4s").read_bytes() == b"ainita0a1"
    assert summary["master_playlist"] == "master.m3u8"
    assert len(summary["streams"]) == 2


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

    monkeypatch.setattr("dolby_tool.hls.local_hls.subprocess.run", fake_run)

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

    monkeypatch.setattr("dolby_tool.hls.local_hls.subprocess.run", fail_run)

    with pytest.raises(ValueError, match="Output directory is not empty"):
        package_hls_from_file(source, output)


def _write_stream_info(
    stream_dir,
    playlist_name: str,
    segment_name: str,
    init: bytes,
    fragments: list[bytes],
) -> None:
    (stream_dir / "init.initfrag").write_bytes(init)
    offset = len(init)
    segment_xml = []
    playlist_lines = [
        "#EXTM3U",
        f'#EXT-X-MAP:URI="{segment_name}",BYTERANGE="{len(init)}@0"',
    ]
    for index, data in enumerate(fragments):
        path = f"{index}.frag"
        (stream_dir / path).write_bytes(data)
        segment_xml.append(f'<SEG Dur="1.0" Len="{len(data)}" Off="{offset}" PATH="{path}" SeqNum="{index}" />')
        playlist_lines.extend(
            [
                "#EXTINF:1.0,",
                f"#EXT-X-BYTERANGE:{len(data)}@{offset}",
                segment_name,
            ]
        )
        offset += len(data)
    (stream_dir / playlist_name).write_text("\n".join(playlist_lines) + "\n", encoding="utf-8")
    (stream_dir / "StreamInfoBoot.xml").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<StreamInfo xmlns="http://apple.com/IMG/Schemas/HLSPersistentStreamInfo">
  <Complete>YES</Complete>
  <MediaPlaylist><PathToLocalCopy>{playlist_name}</PathToLocalCopy></MediaPlaylist>
  <MediaInitializationSegments>
    <ISEG Len="{len(init)}" Off="0" PATH="init.initfrag" SeqNum="0" />
  </MediaInitializationSegments>
  <MediaSegments>
    {"".join(segment_xml)}
  </MediaSegments>
  <MediaBytesStored>{offset}</MediaBytesStored>
</StreamInfo>
""",
        encoding="utf-8",
    )
