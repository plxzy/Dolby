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
        "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-movflags", "+write_colr",
    )  # fmt: skip
    path = tmp_path / "patched.mp4"
    path.write_bytes(clip.read_bytes())

    assert colr_codes(path) == [(b"nclx", struct.pack(">HHHB", 1, 1, 1, 0))]
    assert atoms.patch_colr_nclx(path, 9, 16, 9, True) is True
    assert colr_codes(path) == [(b"nclx", struct.pack(">HHHB", 9, 16, 9, 0x80))]
    # The file still demuxes: only the colr payload changed.
    assert ffprobe_json(path, "-show_streams")["streams"][0]["codec_name"] == "h264"


def test_patch_colr_checks_every_colr_child(tmp_path: Path) -> None:
    prof = box(b"colr", b"prof" + bytes(8))
    path = write(tmp_path, mp4(trak(b"vide", video_entry(prof, nclx(1, 1, 1)))))
    assert atoms.patch_colr_nclx(path, 9, 16, 9, False) is True
    assert colr_codes(path) == [(b"prof", bytes(8)), (b"nclx", struct.pack(">HHHB", 9, 16, 9, 0))]


def test_patch_colr_skips_a_payload_too_short_for_nclx(tmp_path: Path) -> None:
    short = box(b"colr", b"nclx" + bytes(3))
    path = write(tmp_path, mp4(trak(b"vide", video_entry(short))))
    before = path.read_bytes()
    assert atoms.patch_colr_nclx(path, 9, 16, 9, False) is False
    assert path.read_bytes() == before


def test_parse_stops_at_a_truncated_largesize_header(tmp_path: Path) -> None:
    path = write(tmp_path, box(b"ftyp", b"isom" + bytes(4)) + struct.pack(">I", 1) + b"moov" + bytes(3))
    with open(path, "rb") as f:
        assert [b.type for b in atoms.parse(f)] == [b"ftyp"]


# --------------------------------------------------------------------- sound entry versions


def ec3_entry(payload: bytes, *, version: int = 0) -> bytes:
    """An `ec-3` sound sample entry; v1 adds 16 and v2 36 bytes before the child boxes."""
    header = bytearray(28)
    header[8:10] = struct.pack(">H", version)
    return box(b"ec-3", bytes(header) + bytes({0: 0, 1: 16, 2: 36}[version]), box(b"dec3", payload))


@pytest.mark.parametrize("version", [0, 1, 2])
def test_sound_entry_header_length_follows_the_entry_version(tmp_path: Path, version: int) -> None:
    payload = dec3(extension=(0, 0))
    path = write(tmp_path, mp4(trak(b"soun", ec3_entry(payload, version=version))))
    assert [r.patched for r in atoms.patch_dec3_joc(path)] == [True]


def test_unknown_sound_entry_version_is_not_descended_into(tmp_path: Path) -> None:
    path = write(tmp_path, mp4(trak(b"soun", ec3_entry(dec3(extension=(0, 0)), version=0))))
    data = bytearray(path.read_bytes())
    data[data.index(b"ec-3") + 4 + 8 : data.index(b"ec-3") + 4 + 10] = struct.pack(">H", 9)
    path.write_bytes(bytes(data))
    before = path.read_bytes()
    (result,) = atoms.patch_dec3_joc(path)
    assert (result.patched, result.reason) == (False, "no dec3 box")
    assert path.read_bytes() == before


# --------------------------------------------------------------------- dec3 parse-and-report


def dec3(*, dependent: tuple[int, ...] = (0,), extension: tuple[int, int] | None = None) -> bytes:
    """A spec-compliant `EC3SpecificBox` payload: data_rate 768 kbit/s, 5.1, one group per independent substream."""
    out = struct.pack(">H", (768 << 3) | (len(dependent) - 1))
    for num_dep_sub in dependent:
        out += bytes([0x10, 0x00 | (0b111 << 1) | 1, num_dep_sub << 1])  # fscod/bsid, bsmod/acmod/lfeon, num_dep_sub
        if num_dep_sub:
            out += b"\x00"  # chan_loc low byte
    if extension is not None:
        out += bytes(extension)
    return out


def test_parse_dec3_locates_the_extension_bytes() -> None:
    layout = atoms.parse_dec3(dec3(extension=(0, 0)))
    assert layout == atoms.Dec3Layout(768, (0,), base_size=5, payload_size=7)
    assert layout.has_extension

    two = atoms.parse_dec3(dec3(dependent=(0, 2)))
    assert (two.dependent_substreams, two.base_size) == ((0, 2), 2 + 3 + 4)
    assert not two.has_extension


@pytest.mark.parametrize("payload", [b"", b"\x01", dec3()[:4], dec3(dependent=(0, 0))[:6]])
def test_parse_dec3_rejects_truncated_payloads(payload: bytes) -> None:
    assert atoms.parse_dec3(payload) is None


def patched_dec3(tmp_path: Path, payload: bytes) -> tuple[list[atoms.Dec3Result], bytes, bytes]:
    path = write(tmp_path, mp4(trak(b"soun", ec3_entry(payload))))
    before = path.read_bytes()
    results = atoms.patch_dec3_joc(path)
    return results, before, path.read_bytes()


def test_dec3_with_extension_gets_the_joc_flag_and_complexity_index(tmp_path: Path) -> None:
    payload = dec3(extension=(0, 0))
    (result,), before, after = patched_dec3(tmp_path, payload)

    assert result.patched is True
    assert after == before.replace(payload, payload[:-2] + bytes([0x01, 16]))
    assert len(after) == len(before)


def test_existing_complexity_index_is_kept(tmp_path: Path) -> None:
    (result,), _, after = patched_dec3(tmp_path, dec3(extension=(0, 12)))
    assert result.patched is True
    assert bytes([0x01, 12]) in after


def test_dec3_that_already_signals_joc_is_left_alone(tmp_path: Path) -> None:
    (result,), before, after = patched_dec3(tmp_path, dec3(extension=(1, 16)))
    assert (result.patched, after == before) == (False, True)
    assert "already" in result.reason


def test_dec3_without_extension_bytes_is_refused(tmp_path: Path) -> None:
    (result,), before, after = patched_dec3(tmp_path, dec3())
    assert (result.patched, after == before) == (False, True)
    assert "no in-place room" in result.reason


def test_dec3_with_dependent_substream_is_refused(tmp_path: Path) -> None:
    (result,), before, after = patched_dec3(tmp_path, dec3(dependent=(2,), extension=(0, 0)))
    assert (result.patched, after == before) == (False, True)
    assert "dependent" in result.reason


def test_dec3_with_nonstandard_length_is_refused(tmp_path: Path) -> None:
    (result,), before, after = patched_dec3(tmp_path, dec3() + b"\x00")
    assert (result.patched, after == before) == (False, True)
    assert "non-standard" in result.reason


def test_malformed_dec3_is_reported(tmp_path: Path) -> None:
    (result,), before, after = patched_dec3(tmp_path, b"\x06")
    assert (result.patched, after == before) == (False, True)
    assert "malformed" in result.reason


@pytest.mark.requires_ffmpeg
def test_ffmpeg_made_eac3_is_not_corrupted_by_the_patcher(media_clip, tmp_path: Path) -> None:
    clip = media_clip("plain_eac3.mp4", "-map", "1:a", "-c:a", "eac3", "-ac", "2")
    path = tmp_path / "eac3.mp4"
    path.write_bytes(clip.read_bytes())

    before = path.read_bytes()
    (result,) = atoms.patch_dec3_joc(path)
    assert result.patched is False
    assert path.read_bytes() == before
    assert ffprobe_json(path, "-show_streams")["streams"][0]["codec_name"] == "eac3"
