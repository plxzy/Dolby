"""MP4 box parsing and the in-place `colr` patcher, on synthetic boxes and real ffmpeg output."""

import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

from dolby_tool.common import ffprobe_json
from dolby_tool.convert import atoms


def box(type_: bytes, payload: bytes = b"", *children: bytes) -> bytes:
    body = payload + b"".join(children)
    return struct.pack(">I", 8 + len(body)) + type_ + body


def trak(handler: bytes, *entries: bytes) -> bytes:
    hdlr = box(b"hdlr", bytes(8) + handler + bytes(13))
    stsd = box(b"stsd", bytes(4) + struct.pack(">I", len(entries)), *entries)
    stbl = box(b"stbl", b"", stsd)
    return box(b"trak", b"", box(b"mdia", b"", hdlr, box(b"minf", b"", stbl)))


def video_entry(*children: bytes, fourcc: bytes = b"hvc1") -> bytes:
    return box(fourcc, bytes(78), *children)


def nclx(primaries: int = 1, transfer: int = 1, matrix: int = 1) -> bytes:
    return box(b"colr", b"nclx" + struct.pack(">HHHB", primaries, transfer, matrix, 0))


def mp4(*traks: bytes) -> bytes:
    return box(b"ftyp", b"isom" + bytes(4)) + box(b"moov", b"", *traks)


def write(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(data)
    return path


def walk(boxes: list[atoms.Box]) -> Iterator[atoms.Box]:
    for b in boxes:
        yield b
        yield from walk(b.children)


def colr_codes(path: Path) -> list[tuple[bytes, bytes]]:
    """(colour_type, codes) of every colr box in the file, in order."""
    data = path.read_bytes()
    with open(path, "rb") as f:
        colrs = [b for b in walk(atoms.parse(f)) if b.type == b"colr"]
    return [(data[b.payload_offset : b.payload_offset + 4], data[b.payload_offset + 4 : b.end]) for b in colrs]


def test_parse_builds_the_sample_entry_tree(tmp_path: Path) -> None:
    path = write(tmp_path, mp4(trak(b"vide", video_entry(nclx()))))
    with open(path, "rb") as f:
        top = atoms.parse(f)
    assert [b.type for b in top] == [b"ftyp", b"moov"]
    stsd = atoms.find_path(top, (b"moov", b"trak", *atoms._STSD_PATH))
    assert stsd is not None
    entry = stsd.children[0]
    assert entry.type == b"hvc1"
    assert [c.type for c in entry.children] == [b"colr"]
    assert entry.find(b"colr").parent is entry


def test_patch_colr_rewrites_nclx_in_place(tmp_path: Path) -> None:
    path = write(tmp_path, mp4(trak(b"vide", video_entry(nclx(1, 1, 1)))))
    size = path.stat().st_size
    assert atoms.patch_colr_nclx(path, 9, 16, 9, False) is True
    assert path.stat().st_size == size
    assert colr_codes(path) == [(b"nclx", struct.pack(">HHHB", 9, 16, 9, 0))]


def test_patch_colr_leaves_nclc_alone(tmp_path: Path) -> None:
    nclc = box(b"colr", b"nclc" + struct.pack(">HHH", 1, 1, 1))
    path = write(tmp_path, mp4(trak(b"vide", video_entry(nclc))))
    before = path.read_bytes()
    assert atoms.patch_colr_nclx(path, 9, 16, 9, False) is False
    assert path.read_bytes() == before


def test_patch_colr_ignores_audio_tracks(tmp_path: Path) -> None:
    # A colr-shaped child under a sound track must never be touched.
    path = write(tmp_path, mp4(trak(b"soun", box(b"mp4a", bytes(28), nclx()))))
    before = path.read_bytes()
    assert atoms.patch_colr_nclx(path, 9, 16, 9, False) is False
    assert path.read_bytes() == before


@pytest.mark.requires_ffmpeg
def test_patch_colr_on_real_ffmpeg_output(media_clip, tmp_path: Path) -> None:
    clip = media_clip(
        "bt709.mp4",
        "-map", "0:v", "-c:v", "libx264", "-preset", "ultrafast",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
    )  # fmt: skip
    path = tmp_path / "patched.mp4"
    path.write_bytes(clip.read_bytes())

    assert colr_codes(path) == [(b"nclx", struct.pack(">HHHB", 1, 1, 1, 0))]
    assert atoms.patch_colr_nclx(path, 9, 16, 9, True) is True
    assert colr_codes(path) == [(b"nclx", struct.pack(">HHHB", 9, 16, 9, 0x80))]
    # The file still demuxes: only the colr payload changed.
    assert ffprobe_json(path, "-show_streams")["streams"][0]["codec_name"] == "h264"
