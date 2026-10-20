"""Flatten a downloaded persisted-HLS `.movpkg` into a plain folder that `hls-serve` can serve."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .playlist import MASTER_PLAYLIST, first_segment_uri, read_playlist


@dataclass(frozen=True, slots=True)
class BootStream:
    """A complete stream listed in the package's `boot.xml`."""

    id: str
    path: str
    network_url: str


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """A stream folder's `StreamInfoBoot.xml`: its local playlist and fragments in playback order."""

    playlist: Path
    fragments: list[Path]
    media_bytes_stored: int


@dataclass(frozen=True, slots=True)
class FlatStream:
    """A persisted stream and the names of the two files it becomes: its playlist and one segment."""

    stream_id: str
    info: StreamInfo
    playlist: str
    segment: str


def prepare_hls_from_movpkg(
    movpkg: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    """Flatten a simple persisted-HLS `.movpkg` into a serveable HLS folder.

    Each stream's fragments are concatenated into the one file its playlist byte-ranges into.
    Those file names come from the package, so any that would land outside `output_dir` (a URL,
    `../`, an absolute path) are refused before anything is written. `overwrite` accepts a
    non-empty `output_dir`: files with the same names are replaced and anything else already
    there, such as an earlier package's segments, is left in place.
    """
    source = Path(movpkg).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f".movpkg directory does not exist: {source}")
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise ValueError(f"Output directory is not empty: {output}")

    boot = _parse_xml(source / "boot.xml")
    master = _master_playlist_path(source, boot)
    streams = [_flat_stream(source, stream, output) for stream in _boot_streams(source, boot)]

    output.mkdir(parents=True, exist_ok=True)
    (output / MASTER_PLAYLIST).write_text(read_playlist(master), encoding="utf-8")
    return {
        "source": str(source),
        "output_dir": str(output),
        "master_playlist": MASTER_PLAYLIST,
        "streams": [_write_stream(output, stream) for stream in streams],
    }


def prepare_hls_summary_markdown(summary: dict[str, object]) -> str:
    """Render a `prepare_hls_from_movpkg` summary as the Markdown `hls-prepare` prints."""
    lines = ["# Local HLS prepare summary\n"]
    lines.append(f"- source: `{summary['source']}`")
    lines.append(f"- output: `{summary['output_dir']}`")
    lines.append(f"- master playlist: `{summary['master_playlist']}`")
    lines.append("")
    lines.append("| stream ID | playlist | segment | fragments | bytes | media bytes stored |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: |")
    for stream in summary.get("streams", []):
        row = stream if isinstance(stream, dict) else {}
        lines.append(
            "| "
            f"`{row.get('stream_id')}` | "
            f"`{row.get('playlist')}` | "
            f"`{row.get('segment')}` | "
            f"{row.get('fragments')} | "
            f"{row.get('bytes')} | "
            f"{row.get('media_bytes_stored')} |"
        )
    return "\n".join(lines) + "\n"


def _flat_stream(source: Path, stream: BootStream, output: Path) -> FlatStream:
    info = _parse_stream_info(source / stream.path)
    flat = FlatStream(
        stream_id=stream.id,
        info=info,
        playlist=_url_basename(stream.network_url) or info.playlist.name,
        segment=first_segment_uri(info.playlist),
    )
    for name in (flat.playlist, flat.segment):
        _require_inside(output, name)
    return flat


def _write_stream(output: Path, stream: FlatStream) -> dict[str, object]:
    """Concatenate the stream's fragments into its segment file and copy its playlist next to it."""
    segment_path = output / stream.segment
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    with segment_path.open("wb") as f:
        for fragment in stream.info.fragments:
            f.write(fragment.read_bytes())

    (output / stream.playlist).write_text(read_playlist(stream.info.playlist), encoding="utf-8")
    return {
        "stream_id": stream.stream_id,
        "playlist": stream.playlist,
        "segment": stream.segment,
        "bytes": segment_path.stat().st_size,
        "fragments": len(stream.info.fragments),
        "media_bytes_stored": stream.info.media_bytes_stored,
    }


def _require_inside(output: Path, name: str) -> None:
    """Refuse a name from the package that would not land inside `output`."""
    target = (output / name).resolve()
    if urlparse(name).scheme or target == output or not target.is_relative_to(output):
        raise ValueError(f"Refusing to write {name!r}: it does not resolve to a file inside {output}")


def _master_playlist_path(movpkg: Path, boot: ET.Element) -> Path:
    data_item = boot.find(".//{*}DataItem[{*}Role='Master']")
    if data_item is None:
        raise ValueError(f"No master playlist DataItem in {movpkg / 'boot.xml'}")
    data_path = data_item.findtext("{*}DataPath")
    if not data_path:
        raise ValueError("Master playlist DataItem has no DataPath")
    data_dir = boot.find(".//{*}DataItems")
    directory = data_dir.attrib.get("Directory", "Data") if data_dir is not None else "Data"
    return movpkg / directory / data_path


def _boot_streams(movpkg: Path, boot: ET.Element) -> list[BootStream]:
    streams = []
    for stream in boot.findall(".//{*}Streams/{*}Stream"):
        stream_id = stream.attrib.get("ID")
        path = stream.attrib.get("Path")
        complete = stream.findtext("{*}Complete")
        if not stream_id or not path:
            continue
        if complete and complete.upper() != "YES":
            continue
        streams.append(BootStream(id=stream_id, path=path, network_url=stream.attrib.get("NetworkURL", "")))
    if not streams:
        raise ValueError(f"No complete streams found in {movpkg / 'boot.xml'}")
    return streams


def _parse_stream_info(stream_dir: Path) -> StreamInfo:
    info = _parse_xml(stream_dir / "StreamInfoBoot.xml")
    playlist_rel = info.findtext(".//{*}MediaPlaylist/{*}PathToLocalCopy")
    if not playlist_rel:
        raise ValueError(f"No local media playlist in {stream_dir / 'StreamInfoBoot.xml'}")
    init_segments = info.findall(".//{*}MediaInitializationSegments/{*}ISEG")
    media_segments = sorted(
        info.findall(".//{*}MediaSegments/{*}SEG"),
        key=lambda element: int(element.attrib.get("SeqNum", "0")),
    )
    return StreamInfo(
        playlist=stream_dir / playlist_rel,
        fragments=[stream_dir / _fragment_path(element) for element in [*init_segments, *media_segments]],
        media_bytes_stored=int(info.findtext("{*}MediaBytesStored") or "0"),
    )


def _fragment_path(element: ET.Element) -> str:
    if path := element.attrib.get("PATH"):
        return path
    raise ValueError("StreamInfo fragment is missing PATH")


def _url_basename(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.path:
        return None
    return Path(parsed.path).name


def _parse_xml(path: Path) -> ET.Element:
    return ET.fromstring(path.read_text(encoding="utf-8"))
