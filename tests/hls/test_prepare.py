import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dolby_tool.hls.prepare import prepare_hls_from_movpkg, prepare_hls_summary_markdown


def _write_movpkg(root: Path, *, video_segment: str = "video_seg.m4s") -> Path:
    movpkg = root / "Test.movpkg"
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
    _write_stream_info(video_dir, "video-source.m3u8", video_segment, b"vinit", [b"v0", b"v1"])
    _write_stream_info(audio_dir, "audio-source.m3u8", "audio_seg.m4s", b"ainit", [b"a0", b"a1"])
    return movpkg


def _write_stream_info(
    stream_dir: Path,
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


def test_prepare_hls_from_movpkg_flattens_persisted_streams(tmp_path):
    movpkg = _write_movpkg(tmp_path)

    output = tmp_path / "out"
    summary = prepare_hls_from_movpkg(movpkg, output)

    assert (output / "master.m3u8").read_text(encoding="utf-8").endswith("video.m3u8\n")
    assert (output / "video.m3u8").read_text(encoding="utf-8").count("video_seg.m4s") == 3
    assert (output / "audio.m3u8").read_text(encoding="utf-8").count("audio_seg.m4s") == 3
    assert (output / "video_seg.m4s").read_bytes() == b"vinitv0v1"
    assert (output / "audio_seg.m4s").read_bytes() == b"ainita0a1"
    assert summary["master_playlist"] == "master.m3u8"
    assert summary["streams"] == [
        {
            "stream_id": "0-video",
            "playlist": "video.m3u8",
            "segment": "video_seg.m4s",
            "bytes": 9,
            "fragments": 3,
            "media_bytes_stored": 9,
        },
        {
            "stream_id": "1-audio",
            "playlist": "audio.m3u8",
            "segment": "audio_seg.m4s",
            "bytes": 9,
            "fragments": 3,
            "media_bytes_stored": 9,
        },
    ]
    rendered = prepare_hls_summary_markdown(summary)
    assert "| `0-video` | `video.m3u8` | `video_seg.m4s` | 3 | 9 | 9 |" in rendered


@pytest.mark.parametrize(
    "segment",
    [
        pytest.param("../escaped.m4s", id="parent-directory"),
        pytest.param("{tmp}/escaped.m4s", id="absolute-path"),
        pytest.param("https://cdn.example/escaped.m4s", id="url"),
    ],
)
def test_prepare_refuses_segment_names_outside_the_output(tmp_path, segment):
    movpkg = _write_movpkg(tmp_path, video_segment=segment.format(tmp=tmp_path))
    output = tmp_path / "out"

    with pytest.raises(ValueError, match="does not resolve to a file inside"):
        prepare_hls_from_movpkg(movpkg, output)

    assert not (tmp_path / "escaped.m4s").exists()
    assert not output.exists()


def test_prepare_writes_nested_segment_names_inside_the_output(tmp_path):
    movpkg = _write_movpkg(tmp_path, video_segment="media/video_seg.m4s")
    output = tmp_path / "out"

    summary = prepare_hls_from_movpkg(movpkg, output)

    assert (output / "media" / "video_seg.m4s").read_bytes() == b"vinitv0v1"
    assert summary["streams"][0]["segment"] == "media/video_seg.m4s"


@pytest.mark.parametrize(
    "segment", ["audio_seg.m4s", "./audio_seg.m4s", "AUDIO_SEG.M4S", "master.m3u8", "audio.m3u8", "audio.m3u8/seg"]
)
def test_prepare_rejects_colliding_output_files_before_writing(tmp_path, segment):
    movpkg = _write_movpkg(tmp_path, video_segment=segment)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="Conflicting output"):
        prepare_hls_from_movpkg(movpkg, output)
    assert not output.exists()


def test_prepare_rejects_duplicate_playlist_names_before_writing(tmp_path):
    movpkg = _write_movpkg(tmp_path)
    boot = movpkg / "boot.xml"
    boot.write_text(boot.read_text().replace("/audio.m3u8", "/video.m3u8"))
    with pytest.raises(ValueError, match="Conflicting output"):
        prepare_hls_from_movpkg(movpkg, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_prepare_rejects_master_output_symlink_escape(tmp_path):
    movpkg = _write_movpkg(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    outside = tmp_path / "unrelated"
    outside.write_text("keep me")
    (output / "master.m3u8").symlink_to(outside)
    with pytest.raises(ValueError, match="Refusing to write"):
        prepare_hls_from_movpkg(movpkg, output, overwrite=True)
    assert outside.read_text() == "keep me"
    assert list(output.iterdir()) == [output / "master.m3u8"]


@pytest.mark.parametrize("field", ["directory", "data_path", "stream", "playlist", "init", "fragment"])
@pytest.mark.parametrize("escape", ["absolute", "parent", "symlink"])
def test_prepare_contains_every_source_reference_to_original_package(tmp_path, field, escape):
    movpkg = _write_movpkg(tmp_path)
    boot_path = movpkg / "boot.xml"
    stream_dir = movpkg / "0-video"
    info_path = stream_dir / "StreamInfoBoot.xml"
    boot, info = ET.parse(boot_path), ET.parse(info_path)
    refs = {
        "directory": (boot, ".//{*}DataItems", "Directory", movpkg, movpkg / "Data"),
        "data_path": (boot, ".//{*}DataPath", None, movpkg / "Data", movpkg / "Data/master-source.m3u8"),
        "stream": (boot, ".//{*}Stream", "Path", movpkg, stream_dir),
        "playlist": (info, ".//{*}PathToLocalCopy", None, stream_dir, stream_dir / "video-source.m3u8"),
        "init": (info, ".//{*}ISEG", "PATH", stream_dir, stream_dir / "init.initfrag"),
        "fragment": (info, ".//{*}SEG", "PATH", stream_dir, stream_dir / "0.frag"),
    }
    tree, selector, attr, parent, original = refs[field]
    outside = tmp_path / "outside"
    if original.is_dir():
        shutil.copytree(original, outside)
    else:
        shutil.copyfile(original, outside)
    if escape == "absolute":
        reference = str(outside)
    elif escape == "parent":
        reference = os.path.relpath(outside, parent)
    else:
        link = parent / "escape"
        link.symlink_to(outside, target_is_directory=outside.is_dir())
        reference = link.name
    element = tree.find(selector)
    assert element is not None
    if attr is None:
        element.text = reference
    else:
        element.set(attr, reference)
    tree.write(boot_path if tree is boot else info_path, encoding="unicode")
    with pytest.raises(ValueError, match="Refusing to read"):
        prepare_hls_from_movpkg(movpkg, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("relative", ["boot.xml", "0-video/StreamInfoBoot.xml"])
def test_prepare_contains_fixed_xml_symlinks(tmp_path, relative):
    movpkg = _write_movpkg(tmp_path)
    xml = movpkg / relative
    outside = tmp_path / "outside.xml"
    outside.write_bytes(xml.read_bytes())
    xml.unlink()
    xml.symlink_to(outside)
    with pytest.raises(ValueError, match="Refusing to read"):
        prepare_hls_from_movpkg(movpkg, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_prepare_accepts_internal_symlinks_and_parent_references(tmp_path):
    movpkg = _write_movpkg(tmp_path)
    stream = movpkg / "0-video"
    (movpkg / "shared.frag").write_bytes(b"shared")
    (stream / "0.frag").unlink()
    (stream / "0.frag").symlink_to(movpkg / "shared.frag")
    info = stream / "StreamInfoBoot.xml"
    info.write_text(info.read_text().replace('PATH="1.frag"', 'PATH="../shared.frag"'))
    prepare_hls_from_movpkg(movpkg, tmp_path / "out")
    assert (tmp_path / "out/video_seg.m4s").read_bytes() == b"vinitsharedshared"


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_prepare_rejects_existing_output_aliases_before_replacement(tmp_path, alias):
    movpkg = _write_movpkg(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    segment = output / "video_seg.m4s"
    segment.write_bytes(b"previous segment")
    other = output / "audio_seg.m4s"
    if alias == "symlink":
        other.symlink_to(segment)
    else:
        other.hardlink_to(segment)
    with pytest.raises(ValueError, match="Conflicting output"):
        prepare_hls_from_movpkg(movpkg, output, overwrite=True)
    assert segment.read_bytes() == b"previous segment"
    assert not (output / "master.m3u8").exists()


def test_prepare_rejects_unicode_equivalent_output_names(tmp_path):
    movpkg = _write_movpkg(tmp_path, video_segment="café.m4s")
    playlist = movpkg / "1-audio/audio-source.m3u8"
    playlist.write_text(playlist.read_text().replace("audio_seg.m4s", "cafe\u0301.m4s"))
    with pytest.raises(ValueError, match="Conflicting output"):
        prepare_hls_from_movpkg(movpkg, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_prepare_overwrite_replaces_outputs_and_retains_unrelated_files(tmp_path):
    movpkg = _write_movpkg(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    (output / "master.m3u8").write_text("old master")
    (output / "video_seg.m4s").write_bytes(b"old video")
    (output / "keep.txt").write_text("unrelated")
    prepare_hls_from_movpkg(movpkg, output, overwrite=True)
    assert (output / "video_seg.m4s").read_bytes() == b"vinitv0v1"
    assert (output / "keep.txt").read_text() == "unrelated"


def test_prepare_preserves_internal_playlist_symlink_name_without_network_url(tmp_path):
    movpkg = _write_movpkg(tmp_path)
    boot = movpkg / "boot.xml"
    boot.write_text(boot.read_text().replace(' NetworkURL="http://127.0.0.1:8765/video.m3u8"', ""))
    playlist = movpkg / "0-video/video-source.m3u8"
    playlist.rename(playlist.with_name("video-real.m3u8"))
    playlist.symlink_to("video-real.m3u8")
    master = movpkg / "Data/master-source.m3u8"
    master.write_text(master.read_text().replace("video.m3u8", "video-source.m3u8"))

    summary = prepare_hls_from_movpkg(movpkg, tmp_path / "out")

    assert summary["streams"][0]["playlist"] == "video-source.m3u8"
    assert (tmp_path / "out/video-source.m3u8").read_text() == playlist.read_text()
