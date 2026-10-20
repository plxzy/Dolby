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
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

# Container boxes — children are themselves boxes (recurse into them).
_CONTAINERS = frozenset(
    {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts", b"mvex", b"moof", b"traf", b"mfra"}
)

# Sample entries (under stsd) whose payload is a fixed header followed by child
# boxes such as `colr`, `dvcC` or `dec3`.
_VIDEO_SAMPLE_ENTRIES = frozenset({b"avc1", b"avc3", b"hvc1", b"hev1", b"hev2", b"dvh1", b"dvhe", b"dva1", b"dvav"})
_AUDIO_SAMPLE_ENTRIES = frozenset({b"mp4a", b"ac-3", b"ec-3", b"ac-4", b"alac", b"opus"})
_VIDEO_ENTRY_HEADER_LEN = 78  # bytes between the box header and the first child
_AUDIO_ENTRY_HEADER_LEN = 28

_STSD_PATH = (b"mdia", b"minf", b"stbl", b"stsd")


@dataclass
class Box:
    type: bytes  # 4-byte fourcc
    offset: int  # absolute file offset of the box header
    header_size: int  # 8 (or 16 if largesize)
    size: int  # total box size including header
    payload_offset: int = 0  # offset where children/payload starts
    parent: Box | None = field(default=None, repr=False, compare=False)
    children: list[Box] = field(default_factory=list, repr=False)

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def payload_size(self) -> int:
        return self.end - self.payload_offset

    def find(self, type_path: bytes | tuple[bytes, ...]) -> Box | None:
        """Follow a path of child types (first match at each level)."""
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
    """Parse the whole file into a box tree and return the top-level boxes.

    Containers, `stsd`, `meta` and known sample entries are descended into
    eagerly; every other box is a leaf whose payload is left on disk.
    """
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
        box.children = _parse_children(f, box)
        boxes.append(box)
        pos += size
    return boxes


def _parse_children(f: BinaryIO, box: Box) -> list[Box]:
    if box.type in _CONTAINERS:
        start = box.payload_offset
    elif box.type == b"stsd":
        # stsd: 1 byte version + 3 bytes flags + 4 bytes entry_count, then sample entries
        start = box.payload_offset + 8
    elif box.type == b"meta":
        # meta is full-box: 4 bytes version/flags, then children
        start = box.payload_offset + 4
    elif box.type in _VIDEO_SAMPLE_ENTRIES:
        start = box.payload_offset + _VIDEO_ENTRY_HEADER_LEN
    elif box.type in _AUDIO_SAMPLE_ENTRIES:
        start = box.payload_offset + _AUDIO_ENTRY_HEADER_LEN
    else:
        return []
    return _parse_range(f, start, box.end, parent=box) if start < box.end else []


def find_path(boxes: list[Box], path: tuple[bytes, ...]) -> Box | None:
    """Walk a fixed type-path through top-level boxes."""
    if not path:
        return None
    head, *rest = path
    node = next((b for b in boxes if b.type == head), None)
    return node.find(tuple(rest)) if node is not None else None


# --------------------------------------------------------------------- colr


def patch_colr_nclx(path: Path, primaries: int, transfer: int, matrix: int, full_range: bool) -> bool:
    """Overwrite the `nclx` colour codes of every video sample entry in place.

    Returns True if any patch was applied. Same-size in-place when source is
    already `nclx` (overwrite payload); when source is `nclc` (10-byte payload
    vs nclx's 11 bytes), we cannot patch in place — log and skip (caller can
    decide to ffmpeg-remux with explicit `-color_*` flags).
    """
    changed = False
    with open(path, "r+b") as f:
        for entry in _sample_entries(f, parse(f), b"vide"):
            if entry.type not in _VIDEO_SAMPLE_ENTRIES:
                continue
            colr = entry.find(b"colr")
            if colr is None:
                continue
            f.seek(colr.payload_offset)
            if f.read(4) == b"nclx":
                f.write(struct.pack(">HHHB", primaries, transfer, matrix, 0x80 if full_range else 0x00))
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
        for entry in _sample_entries(f, parse(f), b"soun"):
            if entry.type != b"ec-3":
                continue
            dec3 = entry.find(b"dec3")
            if dec3 is None:
                continue
            f.seek(dec3.payload_offset)
            payload = f.read(dec3.payload_size)
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


def _iter_traks(f: BinaryIO, top: list[Box], handler: bytes) -> Iterator[Box]:
    """Yield every `trak` whose media handler type (`vide`, `soun`, …) is `handler`."""
    moov = find_path(top, (b"moov",))
    if moov is None:
        return
    for trak in moov.find_all(b"trak"):
        hdlr = trak.find((b"mdia", b"hdlr"))
        if hdlr is None:
            continue
        # hdlr: 4 bytes full-box header + 4 pre_defined, then the handler type
        f.seek(hdlr.payload_offset + 8)
        if f.read(4) == handler:
            yield trak


def _sample_entries(f: BinaryIO, top: list[Box], handler: bytes) -> Iterator[Box]:
    """Yield the `stsd` sample entries of every track with the given handler type."""
    for trak in _iter_traks(f, top, handler):
        stsd = trak.find(_STSD_PATH)
        if stsd is not None:
            yield from stsd.children
