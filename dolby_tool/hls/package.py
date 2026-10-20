"""Stream-copy a local media file into fMP4 HLS that QuickTime and Safari can play from `hls-serve`."""

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Self

from .playlist import MASTER_PLAYLIST, playlist_references, set_attribute

# HEVC Dolby Vision sample entries; the HLS CODECS attribute needs a profile/level suffix for them.
_DV_HEVC_TAGS = frozenset({"dvh1", "dvhe"})


@dataclass(frozen=True, slots=True)
class MasterTags:
    """Master-playlist attributes worked out from the probed streams (ffmpeg's own are too bare)."""

    codecs: str | None
    audio_channels: str | None
    frame_rate: str | None
    video_range: str | None
    language: str

    @classmethod
    def for_streams(cls, video: dict[str, Any], audio: dict[str, Any]) -> Self:
        codecs = ",".join(codec for codec in (_video_codec(video), _audio_codec(audio)) if codec)
        return cls(
            codecs=codecs or None,
            audio_channels=_audio_channels(audio),
            frame_rate=_frame_rate(video),
            video_range=_video_range(video),
            language=_language(audio),
        )


def package_hls_from_file(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    audio_stream: int = 0,
    segment_time: float = 6.0,
    split_audio_group: bool = True,
) -> dict[str, object]:
    """Stream-copy a local media file into fMP4 HLS for QuickTime/Safari controls."""
    source = Path(input_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Input media file does not exist: {source}")
    if audio_stream < 0:
        raise ValueError("audio_stream must be >= 0")
    if segment_time <= 0:
        raise ValueError("segment_time must be > 0")

    if output.exists() and any(output.iterdir()):
        if not overwrite:
            raise ValueError(f"Output directory is not empty: {output}")
        _clear_directory(output)
    output.mkdir(parents=True, exist_ok=True)
    tags = _probe_master_tags(source, audio_stream=audio_stream)

    cmd = _ffmpeg_command(
        source,
        output,
        overwrite=overwrite,
        audio_stream=audio_stream,
        segment_time=segment_time,
        split_audio_group=split_audio_group,
    )
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    _patch_master_playlist(output / MASTER_PLAYLIST, tags)
    return {
        "source": str(source),
        "output_dir": str(output),
        "master_playlist": MASTER_PLAYLIST,
        "audio_stream": audio_stream,
        "segment_time": segment_time,
        "split_audio_group": split_audio_group,
        "master_playlist_tags": asdict(tags),
        "playlists": _playlist_inventory(output),
        "ffmpeg_stderr_tail": result.stderr.splitlines()[-20:],
    }


def package_hls_summary_markdown(summary: dict[str, object]) -> str:
    lines = ["# Local HLS package summary\n"]
    lines.append(f"- source: `{summary['source']}`")
    lines.append(f"- output: `{summary['output_dir']}`")
    lines.append(f"- master playlist: `{summary['master_playlist']}`")
    lines.append(f"- audio stream: `{summary['audio_stream']}`")
    lines.append(f"- split audio group: `{summary['split_audio_group']}`")
    tags = summary.get("master_playlist_tags")
    if isinstance(tags, dict):
        if tags.get("codecs"):
            lines.append(f"- master codecs: `{tags['codecs']}`")
        if tags.get("audio_channels"):
            lines.append(f"- audio channels tag: `{tags['audio_channels']}`")
    lines.append("")
    lines.append("| playlist | media segments | init maps | referenced bytes |")
    lines.append("| --- | ---: | ---: | ---: |")
    for playlist in summary.get("playlists", []):
        row = playlist if isinstance(playlist, dict) else {}
        lines.append(
            f"| `{row.get('playlist')}` | {row.get('segments')} | {row.get('init_maps')} | {row.get('bytes')} |"
        )
    return "\n".join(lines) + "\n"


def _ffmpeg_command(
    source: Path,
    output: Path,
    *,
    overwrite: bool,
    audio_stream: int,
    segment_time: float,
    split_audio_group: bool,
) -> list[str]:
    var_stream_map = (
        "v:0,agroup:audio,name:video a:0,agroup:audio,language:eng,name:English,default:yes"
        if split_audio_group
        else "v:0,a:0,name:main"
    )
    return [
        "ffmpeg",
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        f"0:a:{audio_stream}",
        "-c",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        f"{segment_time:g}",
        "-hls_playlist_type",
        "vod",
        "-hls_segment_type",
        "fmp4",
        "-hls_flags",
        "independent_segments",
        "-master_pl_name",
        MASTER_PLAYLIST,
        "-var_stream_map",
        var_stream_map,
        "-hls_segment_filename",
        str(output / "stream_%v_%04d.m4s"),
        str(output / "stream_%v.m3u8"),
    ]


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _probe_master_tags(source: Path, *, audio_stream: int) -> MasterTags:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout or "{}").get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    audio = audio_streams[audio_stream] if audio_stream < len(audio_streams) else None
    if video is None:
        raise ValueError(f"No video stream found in {source}")
    if audio is None:
        raise ValueError(f"No audio stream {audio_stream} found in {source}")
    return MasterTags.for_streams(video, audio)


def _patch_master_playlist(path: Path, tags: MasterTags) -> None:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line:
            line = set_attribute(line, "LANGUAGE", tags.language, quoted=True)
            line = set_attribute(line, "NAME", "English", quoted=True)
            line = set_attribute(line, "AUTOSELECT", "YES", quoted=False)
            if tags.audio_channels:
                line = set_attribute(line, "CHANNELS", tags.audio_channels, quoted=True)
        elif line.startswith("#EXT-X-STREAM-INF:"):
            if tags.frame_rate:
                line = set_attribute(line, "FRAME-RATE", tags.frame_rate, quoted=False)
            if tags.codecs:
                line = set_attribute(line, "CODECS", tags.codecs, quoted=True)
            if tags.video_range:
                line = set_attribute(line, "VIDEO-RANGE", tags.video_range, quoted=False)
        lines.append(line)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def _video_codec(stream: dict[str, Any]) -> str | None:
    tag = str(stream.get("codec_tag_string") or "").lower()
    if tag in _DV_HEVC_TAGS:
        # Current local Atmos/DV test files are Profile 5 Level 6. Keep the
        # explicit HLS codec token because QuickTime rejects FFmpeg's bare
        # split fMP4 master playlist before segment playback.
        return f"{tag}.05.06"
    return tag or None


def _audio_codec(stream: dict[str, Any]) -> str | None:
    codec = str(stream.get("codec_name") or "").lower()
    tag = str(stream.get("codec_tag_string") or "").lower()
    if codec == "eac3" or tag == "ec-3":
        return "ec-3"
    if codec == "ac3" or tag == "ac-3":
        return "ac-3"
    if codec == "aac" or tag.startswith("mp4a"):
        return tag if tag.startswith("mp4a") else "mp4a.40.2"
    return tag or codec or None


def _audio_channels(stream: dict[str, Any]) -> str | None:
    channels = stream.get("channels")
    if not isinstance(channels, int) or channels <= 0:
        return None
    profile = str(stream.get("profile") or "").lower()
    suffix = "/JOC" if "atmos" in profile else ""
    return f"{channels}{suffix}"


def _frame_rate(stream: dict[str, Any]) -> str | None:
    rate = _parse_fraction(str(stream.get("avg_frame_rate") or ""))
    if not rate:
        rate = _parse_fraction(str(stream.get("r_frame_rate") or ""))
    return f"{rate:.3f}" if rate else None


def _video_range(stream: dict[str, Any]) -> str | None:
    transfer = str(stream.get("color_transfer") or "").lower()
    primaries = str(stream.get("color_primaries") or "").lower()
    if transfer == "smpte2084" or primaries == "bt2020":
        return "PQ"
    return None


def _language(stream: dict[str, Any]) -> str:
    tags = stream.get("tags")
    language = str(tags.get("language") or "") if isinstance(tags, dict) else ""
    return {"eng": "en"}.get(language.lower(), language or "en")


def _parse_fraction(value: str) -> float | None:
    if not value or value == "0/0":
        return None
    numerator, sep, denominator = value.partition("/")
    try:
        if sep:
            den = float(denominator)
            return float(numerator) / den if den else None
        return float(value)
    except ValueError:
        return None


def _playlist_inventory(output: Path) -> list[dict[str, object]]:
    rows = []
    for playlist in sorted(output.glob("*.m3u8")):
        references = playlist_references(playlist)
        segment_uris = [uri for uri in references.segments if not uri.endswith(".m3u8")]
        referenced = [output / uri for uri in [*references.init_maps, *segment_uris]]
        rows.append(
            {
                "playlist": playlist.name,
                "segments": len(segment_uris),
                "init_maps": len(references.init_maps),
                "bytes": sum(path.stat().st_size for path in referenced if path.exists()),
            }
        )
    return rows
