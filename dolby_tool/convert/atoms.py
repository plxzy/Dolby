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
  * `dec3` (EAC-3 sample entry config) → parse the ETSI TS 102 366 Annex F
    layout and, only when the box already holds the two optional extension
    bytes, set `flag_ec3_extension_type_a` (JOC) in place. Tracks with
    dependent substreams or a non-standard layout (ffmpeg writes a 6-byte
    payload for plain E-AC-3) are left alone and the reason is reported.
    EXPERIMENTAL: the flag is a claim about the bitstream, so it can lie when
    the source is not Atmos. Gated behind `--experimental-force-dec3-joc`.

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
# Bytes between the box header and the first child of a sound sample entry,
# by the entry's version field (v1 adds 16 bytes of QuickTime fields, v2 36).
_AUDIO_ENTRY_HEADER_LEN = {0: 28, 1: 44, 2: 64}

_NCLX_PAYLOAD_LEN = 11  # colour_type (4) + primaries, transfer, matrix (2 each) + range flags (1)

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
            if len(largesize_bytes) < 8:
                break  # truncated 64-bit size field
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
        header_len = _audio_entry_header_len(f, box)
        if header_len is None:
            return []
        start = box.payload_offset + header_len
    else:
        return []
    return _parse_range(f, start, box.end, parent=box) if start < box.end else []


def _audio_entry_header_len(f: BinaryIO, box: Box) -> int | None:
    """Fixed-header length of a sound sample entry, or None if it's too short or an unknown version."""
    f.seek(box.payload_offset + 8)
    version = f.read(2)
    if len(version) < 2:
        return None
    return _AUDIO_ENTRY_HEADER_LEN.get(struct.unpack(">H", version)[0])


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

    Returns True if any patch was applied. Only `nclx` boxes are rewritten: a
    legacy `nclc` payload is one byte shorter than `nclx`, so it can't be
    patched in place and is skipped (remux with explicit `-color_*` flags
    instead). An entry may carry several `colr` boxes (e.g. `prof` and `nclx`);
    each one is checked.
    """
    changed = False
    with open(path, "r+b") as f:
        for entry in _sample_entries(f, parse(f), b"vide"):
            if entry.type not in _VIDEO_SAMPLE_ENTRIES:
                continue
            for colr in entry.find_all(b"colr"):
                if colr.payload_size < _NCLX_PAYLOAD_LEN:
                    continue
                f.seek(colr.payload_offset)
                if f.read(4) == b"nclx":
                    f.write(struct.pack(">HHHB", primaries, transfer, matrix, 0x80 if full_range else 0x00))
                    changed = True
    return changed


# --------------------------------------------------------------------- dec3 JOC


@dataclass(frozen=True)
class Dec3Layout:
    """The parts of an `EC3SpecificBox` payload (ETSI TS 102 366 Annex F) that locate the JOC flag."""

    data_rate: int  # kbit/s
    dependent_substreams: tuple[int, ...]  # num_dep_sub of each independent substream
    base_size: int  # bytes before the optional extension
    payload_size: int

    @property
    def has_extension(self) -> bool:
        """True when exactly the two spec extension bytes follow the substream config."""
        return self.payload_size == self.base_size + 2


def parse_dec3(payload: bytes) -> Dec3Layout | None:
    """Parse a `dec3` payload, or return None when it is too short for its own substream count."""
    if len(payload) < 2:
        return None
    header = struct.unpack(">H", payload[:2])[0]
    data_rate, independent = header >> 3, (header & 0x7) + 1
    pos = 2
    dependent: list[int] = []
    for _ in range(independent):
        if pos + 3 > len(payload):
            return None
        num_dep_sub = (payload[pos + 2] >> 1) & 0xF
        dependent.append(num_dep_sub)
        pos += 4 if num_dep_sub else 3  # chan_loc takes a bit more room than the reserved bit
    if pos > len(payload):
        return None
    return Dec3Layout(data_rate, tuple(dependent), pos, len(payload))


@dataclass(frozen=True)
class Dec3Result:
    """What `patch_dec3_joc` did to one E-AC-3 track (0-based among the file's E-AC-3 tracks)."""

    track: int
    patched: bool
    reason: str


def patch_dec3_joc(path: Path, *, joc_complexity_index: int = 16) -> list[Dec3Result]:
    """Set `flag_ec3_extension_type_a` (JOC) in every E-AC-3 track's `dec3` box, where it fits.

    The payload is parsed first. Only a box that already ends in the two
    extension bytes (reserved(7) + flag(1), complexity_index(8)) is patched,
    in place: the flag is set and a zero complexity index becomes
    `joc_complexity_index`. Growing a box would need a moov rewrite, so a box
    without extension bytes, with a non-standard length (ffmpeg appends a
    stray byte for plain E-AC-3) or with dependent substreams is refused.
    One result per E-AC-3 track says what happened and why.
    """
    results: list[Dec3Result] = []
    with open(path, "r+b") as f:
        for entry in _sample_entries(f, parse(f), b"soun"):
            if entry.type != b"ec-3":
                continue
            track = len(results)
            dec3 = entry.find(b"dec3")
            if dec3 is None:
                results.append(Dec3Result(track, False, "no dec3 box"))
                continue
            f.seek(dec3.payload_offset)
            payload = f.read(dec3.payload_size)
            results.append(_patch_dec3_payload(f, dec3, payload, track, joc_complexity_index))
    return results


def _patch_dec3_payload(f: BinaryIO, dec3: Box, payload: bytes, track: int, complexity_index: int) -> Dec3Result:
    layout = parse_dec3(payload)
    if layout is None:
        return Dec3Result(track, False, "malformed dec3 payload")
    if any(layout.dependent_substreams):
        return Dec3Result(track, False, f"dependent substreams {layout.dependent_substreams} are not supported")
    if layout.payload_size == layout.base_size:
        return Dec3Result(track, False, "no in-place room: dec3 has no extension bytes")
    if not layout.has_extension:
        return Dec3Result(
            track,
            False,
            f"no in-place room: non-standard dec3 length {layout.payload_size} (expected {layout.base_size + 2})",
        )
    flag, complexity = payload[layout.base_size] | 0x01, payload[layout.base_size + 1] or complexity_index
    if (flag, complexity) == tuple(payload[layout.base_size :]):
        return Dec3Result(track, False, f"already signals JOC (complexity index {complexity})")
    f.seek(dec3.payload_offset + layout.base_size)
    f.write(bytes([flag, complexity]))
    return Dec3Result(track, True, f"set JOC flag, complexity index {complexity}")


# --------------------------------------------------------------------- helpers


def _iter_traks(f: BinaryIO, top: list[Box], handler: bytes) -> Iterator[Box]:
    """Yield every `trak` whose media handler type (`vide`, `soun`, …) is `handler`."""
    moov = find_path(top, (b"moov",))
    if moov is None:
        return
    for trak in moov.find_all(b"trak"):
        hdlr = trak.find((b"mdia", b"hdlr"))
        if hdlr is None or hdlr.payload_size < 12:
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
