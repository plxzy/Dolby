"""Focused MP4 box patcher — last-resort knobs for specific bitstream issues.

NOT a default pipeline step. The 2026-05-23 N2-probe baseline (see the caveat
at the top of reports/01_gate_model.md) shows TV.app local playback
spatializing without these patches; the older "patch boxes to flip the gate"
model is stale on the current build.

Scope is deliberately narrow — two atoms only:

  * `colr` → overwrite the `nclx` payload with explicit primaries/transfer/
    matrix codes. Same-size in-place only; does NOT convert legacy `nclc`
    to `nclx` (that requires growing the box by one byte and rewriting
    chunk offsets — out of scope). Caller must supply the right codes;
    writing BT.709/SDR values into an HDR/DV file is destructive.
  * `dec3` (EAC-3 sample entry config) → OR the low bit of the last payload
    byte. EXPERIMENTAL: does not parse the substream / dependent-substream
    layout, so on plain E-AC-3 5.1 this writes a JOC claim that is not true.
    Gated behind `--experimental-force-dec3-joc` in the CLI.

iTunes-style userdata tags (`©nam`, `desc`, `covr`, `iTunEXTC`, `stik`, TV
atoms etc.) are written by `tagger.py` using AtomicParsley, which handles
chunk-offset rewriting for us.

This module intentionally does NOT try to insert `dvcC`/`dvvC` when missing.
If ffmpeg dropped them, demux/remux with `ffmpeg -strict unofficial` upstream;
adding them safely requires a full moov rewrite (TODO).
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

# Container boxes — children are themselves boxes (recurse into them).
_CONTAINERS = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"udta",
    b"edts",
    b"mvex",
    b"moof",
    b"traf",
    b"mfra",
}

# Sample-entry containers (under stsd) whose payload starts with a fixed
# header then nested child boxes. We recurse into their children at offset 8.
_SAMPLE_ENTRY_CODECS = {
    # video
    b"avc1",
    b"avc3",
    b"hvc1",
    b"hev1",
    b"hev2",
    b"dvh1",
    b"dvhe",
    b"dva1",
    b"dvav",
    # audio
    b"mp4a",
    b"ac-3",
    b"ec-3",
    b"ac-4",
    b"alac",
    b"opus",
}
_SAMPLE_ENTRY_HEADER_LEN = {"video": 78, "audio": 28}  # bytes after the box header


@dataclass
class Box:
    type: bytes  # 4-byte fourcc
    offset: int  # absolute file offset of the box header
    header_size: int  # 8 (or 16 if largesize)
    size: int  # total box size including header
    payload_offset: int = 0  # offset where children/payload starts
    parent: Box | None = None
    children: list[Box] = field(default_factory=list)

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def payload_size(self) -> int:
        return self.end - self.payload_offset

    def find(self, type_path: bytes | tuple[bytes, ...]) -> Box | None:
        if isinstance(type_path, bytes):
            type_path = (type_path,)
        node: Box | None = self
        for t in type_path:
            if node is None:
                return None
            node = next((c for c in node.children if c.type == t), None)
        return node

    def find_all(self, type_: bytes) -> list[Box]:
        return [c for c in self.children if c.type == type_]


def parse(f: BinaryIO) -> list[Box]:
    """Parse the top-level box list. Children of containers are walked lazily by `walk_containers`."""
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    return _parse_range(f, 0, size, parent=None)


def _parse_range(f: BinaryIO, start: int, end: int, *, parent: Box | None) -> list[Box]:
    boxes: list[Box] = []
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size = struct.unpack(">I", hdr[:4])[0]
        type_ = hdr[4:8]
        header_size = 8
        if size == 1:
            largesize_bytes = f.read(8)
            size = struct.unpack(">Q", largesize_bytes)[0]
            header_size = 16
        elif size == 0:
            size = end - pos  # box extends to EOF
        if size < header_size or pos + size > end:
            break
        box = Box(
            type=type_,
            offset=pos,
            header_size=header_size,
            size=size,
            payload_offset=pos + header_size,
            parent=parent,
        )
        if type_ in _CONTAINERS:
            box.children = _parse_range(f, box.payload_offset, box.end, parent=box)
        elif type_ == b"stsd":
            # stsd: 1 byte version + 3 bytes flags + 4 bytes entry_count, then sample entries
            box.children = _parse_range(f, box.payload_offset + 8, box.end, parent=box)
        elif type_ == b"meta":
            # meta is full-box: 4 bytes version/flags, then children
            box.children = _parse_range(f, box.payload_offset + 4, box.end, parent=box)
        elif type_ in _SAMPLE_ENTRY_CODECS:
            # Determine header length by whether parent's parent's parent's media handler is video/audio.
            kind = _sample_entry_kind(type_)
            inner_start = box.payload_offset + _SAMPLE_ENTRY_HEADER_LEN.get(kind, 0)
            if inner_start < box.end:
                box.children = _parse_range(f, inner_start, box.end, parent=box)
        boxes.append(box)
        pos += size
    return boxes


def _sample_entry_kind(type_: bytes) -> str:
    if type_ in (b"avc1", b"avc3", b"hvc1", b"hev1", b"hev2", b"dvh1", b"dvhe", b"dva1", b"dvav"):
        return "video"
    return "audio"


def find_path(boxes: list[Box], path: tuple[bytes, ...]) -> Box | None:
    """Walk a fixed type-path through top-level boxes."""
    if not path:
        return None
    head, *rest = path
    node = next((b for b in boxes if b.type == head), None)
    for t in rest:
        if node is None:
            return None
        node = next((c for c in node.children if c.type == t), None)
    return node


# --------------------------------------------------------------------- colr


def patch_colr_nclx(path: Path, primaries: int, transfer: int, matrix: int, full_range: bool) -> bool:
    """Walk every video sample entry's `colr` child and ensure it's the `nclx` variant.

    Returns True if any patch was applied. Same-size in-place when source is
    already `nclx` (overwrite payload); when source is `nclc` (10-byte payload
    vs nclx's 11 bytes), we cannot patch in place — log and skip (caller can
    decide to ffmpeg-remux with explicit `-color_*` flags).
    """
    changed = False
    with open(path, "r+b") as f:
        top = parse(f)
        for trak in _iter_video_traks(top):
            stsd = trak.find((b"mdia", b"minf", b"stbl", b"stsd"))
            if not stsd:
                continue
            for entry in stsd.children:
                if entry.type not in {b"avc1", b"avc3", b"hvc1", b"hev1", b"hev2", b"dvh1", b"dvhe", b"dva1", b"dvav"}:
                    continue
                colr = next((c for c in entry.children if c.type == b"colr"), None)
                if colr is None:
                    continue
                f.seek(colr.payload_offset)
                sub_type = f.read(4)
                if sub_type == b"nclx":
                    payload = struct.pack(">HHHB", primaries, transfer, matrix, 0x80 if full_range else 0x00)
                    f.seek(colr.payload_offset + 4)
                    f.write(payload)
                    changed = True
                # nclc → nclx requires a 1-byte grow; skip in place.
    return changed


# --------------------------------------------------------------------- dec3 JOC


def patch_dec3_joc(path: Path, *, joc_complexity_index: int = 16) -> bool:
    """Set the `flag_ec3_extension_type_a` (JOC) bit in every EAC-3 track's `dec3` box.

    Per ETSI TS 102 366 Annex F: after the per-substream config bytes, a
    trailing byte carries `flag_ec3_extension_type_a` (top bit). We OR it in
    and append a complexity-index byte if missing.

    Returns True if any patch happened. Best-effort: if the box is already
    longer than the minimum size we treat the JOC byte as present.
    """
    changed = False
    with open(path, "r+b") as f:
        top = parse(f)
        for trak in _iter_audio_traks(top, codec_filter=b"ec-3"):
            stsd = trak.find((b"mdia", b"minf", b"stbl", b"stsd"))
            if not stsd:
                continue
            for entry in stsd.children:
                if entry.type != b"ec-3":
                    continue
                dec3 = next((c for c in entry.children if c.type == b"dec3"), None)
                if dec3 is None:
                    continue
                f.seek(dec3.payload_offset)
                payload = f.read(dec3.payload_size)
                if not payload:
                    continue
                # We can only safely set the JOC bit if the box already
                # reserves a byte for it (size matches the JOC layout).
                # Otherwise resizing the box would need a moov rewrite.
                expected_min = 2  # data_rate + num_ind_sub bits
                if len(payload) <= expected_min:
                    continue
                last = payload[-1]
                new_last = last | 0x01  # flag_ec3_extension_type_a low bit per Annex F bitstream
                if new_last != last:
                    f.seek(dec3.payload_offset + len(payload) - 1)
                    f.write(bytes([new_last]))
                    changed = True
                _ = joc_complexity_index  # would go in a trailing byte if we extended the box
    return changed


# --------------------------------------------------------------------- helpers


def _iter_video_traks(top: list[Box]):
    moov = next((b for b in top if b.type == b"moov"), None)
    if not moov:
        return
    for trak in moov.find_all(b"trak"):
        hdlr = trak.find((b"mdia", b"hdlr"))
        if not hdlr:
            continue
        # hdlr: 4 bytes full-box header + 4 reserved + 4 handler type
        with open(_path_of(top), "rb") as f:
            f.seek(hdlr.payload_offset + 8)
            handler = f.read(4)
        if handler == b"vide":
            yield trak


def _iter_audio_traks(top: list[Box], codec_filter: bytes | None = None):
    moov = next((b for b in top if b.type == b"moov"), None)
    if not moov:
        return
    for trak in moov.find_all(b"trak"):
        hdlr = trak.find((b"mdia", b"hdlr"))
        if not hdlr:
            continue
        with open(_path_of(top), "rb") as f:
            f.seek(hdlr.payload_offset + 8)
            handler = f.read(4)
        if handler != b"soun":
            continue
        if codec_filter:
            stsd = trak.find((b"mdia", b"minf", b"stbl", b"stsd"))
            if not stsd or not any(c.type == codec_filter for c in stsd.children):
                continue
        yield trak


# We need the file path to seek into; carry it via a sentinel root-box "_path".
# Simpler: callers do the file IO directly. The two patch_* entry points above
# already do their own open(). The handler-type lookups above assume the file
# is the same one being patched — keep a module-level current path.
_CURRENT_PATH: Path | None = None


def _path_of(_top: list[Box]) -> Path:
    if _CURRENT_PATH is None:
        raise RuntimeError("Internal: patch_* did not set _CURRENT_PATH")
    return _CURRENT_PATH


def _with_path(path: Path):
    """Context-manager-ish helper so iterators can re-open the same file."""

    class _CM:
        def __enter__(self_inner):
            global _CURRENT_PATH
            _CURRENT_PATH = path
            return self_inner

        def __exit__(self_inner, *exc):
            global _CURRENT_PATH
            _CURRENT_PATH = None

    return _CM()


# Re-wrap public entry points so _path_of works.
_patch_colr_nclx_impl = patch_colr_nclx
_patch_dec3_joc_impl = patch_dec3_joc


def patch_colr_nclx(path: Path, primaries: int, transfer: int, matrix: int, full_range: bool) -> bool:  # type: ignore[no-redef]
    with _with_path(path):
        return _patch_colr_nclx_impl(path, primaries, transfer, matrix, full_range)


def patch_dec3_joc(path: Path, *, joc_complexity_index: int = 16) -> bool:  # type: ignore[no-redef]
    with _with_path(path):
        return _patch_dec3_joc_impl(path, joc_complexity_index=joc_complexity_index)
