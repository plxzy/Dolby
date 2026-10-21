"""M3U8 helpers shared by HLS packaging, `.movpkg` flattening and the `.movpkg` inventory."""

import re
from dataclasses import dataclass
from pathlib import Path

MASTER_PLAYLIST = "master.m3u8"

# One `KEY=value` entry of an attribute list (`TYPE=AUDIO,NAME="English"`). It starts the
# list or follows a comma; quoted values may themselves contain commas.
_ATTRIBUTE = re.compile(r'(?:^|(?<=,))\s*(?P<key>[^=,\s]+)\s*=\s*(?P<value>"[^"]*"|[^,]*)')


@dataclass(frozen=True, slots=True)
class PlaylistReferences:
    """The URIs a playlist points at: `#EXT-X-MAP` init sections and plain URI lines."""

    init_maps: list[str]
    segments: list[str]


def parse_attributes(text: str) -> dict[str, str]:
    """Parse an attribute list into `{KEY: value}`, with quoted values unquoted."""
    return {match["key"]: _unquote(match["value"].strip()) for match in _ATTRIBUTE.finditer(text)}


def set_attribute(line: str, key: str, value: str, *, quoted: bool) -> str:
    """Set `key` on a tag line such as `#EXT-X-MEDIA:...`, appending it when absent.

    Only a whole attribute name matches, so setting `LANGUAGE` leaves `ASSOC-LANGUAGE` alone.
    """
    tag, _, attributes = line.partition(":")
    rendered = f'{key}="{value}"' if quoted else f"{key}={value}"
    for match in _ATTRIBUTE.finditer(attributes):
        if match["key"] == key:
            return f"{tag}:{attributes[: match.start('key')]}{rendered}{attributes[match.end() :]}"
    return f"{tag}:{attributes},{rendered}" if attributes else f"{tag}:{rendered}"


def read_playlist(path: Path) -> str:
    """Playlist text ending in exactly one newline (persisted copies are NUL-padded)."""
    return path.read_text(encoding="utf-8", errors="replace").rstrip("\x00\r\n") + "\n"


def playlist_references(path: Path) -> PlaylistReferences:
    """Collect the init-section and segment (or variant playlist) URIs of a playlist."""
    init_maps: list[str] = []
    segments: list[str] = []
    for line in read_playlist(path).splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MAP:"):
            if uri := parse_attributes(line.removeprefix("#EXT-X-MAP:")).get("URI"):
                init_maps.append(uri)
        elif line and not line.startswith("#"):
            segments.append(line)
    return PlaylistReferences(init_maps=init_maps, segments=segments)


def first_segment_uri(path: Path) -> str:
    """The first URI line of a media playlist."""
    if segments := playlist_references(path).segments:
        return segments[0]
    raise ValueError(f"No segment URI found in {path}")


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
