"""Inspect Apple TV.app downloaded `.movpkg` package inventories."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SEARCH_TERMS = [
    "audio-atmos",
    "audio-stereo",
    "ec-3",
    "mp4a.40.2",
    "Complete>YES",
    "download-ap-aoc",
    "vod-ap-aoc",
    "AD",
    "description",
    "accessibility",
    "public.accessibility.describes-video",
]


def analyze_movpkg(path: str | Path, selected_group: str | None = None) -> dict[str, Any]:
    """Return a structured inventory summary for a downloaded `.movpkg` package."""
    movpkg = Path(path)
    files, errors = _walk_files(movpkg)
    manifests = _manifest_inventory(movpkg, files, errors)
    streams, stream_by_g = _stream_inventory(movpkg, manifests["stream_info"], errors)
    media_rows, variant_rows, playlists = _playlist_inventory(movpkg, manifests["playlists"])
    audio_table = _audio_table(media_rows, variant_rows, stream_by_g)
    verdict = _inventory_verdict(audio_table, selected_group=selected_group)

    return {
        "movpkg": str(movpkg),
        "file_count": len(files),
        "manifest_like_count": len(manifests["manifest_like"]),
        "boot_xml": [str(p.relative_to(movpkg)) for p in manifests["boot_xml"]],
        "root_xml": [str(p.relative_to(movpkg)) for p in manifests["root_xml"]],
        "stream_info_count": len(manifests["stream_info"]),
        "playlist_count": len(manifests["playlists"]),
        "content_hits": dict(manifests["content_hits"]),
        "selected_group": selected_group,
        "inventory_verdict": verdict,
        "audio_table": audio_table,
        "stream_rows": streams,
        "playlist_summaries": playlists,
        "errors": errors[:200],
    }


def find_top_level_movpkgs(root: str | Path) -> list[Path]:
    """Return top-level `.movpkg` packages under a TV.app media directory."""
    root_path = Path(root)
    if root_path.suffix == ".movpkg":
        return [root_path]

    packages: list[Path] = []
    for path in root_path.rglob("*.movpkg"):
        if any(parent.suffix == ".movpkg" for parent in path.parents):
            continue
        packages.append(path)
    return sorted(packages)


def scan_movpkgs(root: str | Path, selected_group: str | None = None) -> dict[str, Any]:
    """Analyze all top-level `.movpkg` packages under a root path."""
    packages = find_top_level_movpkgs(root)
    summaries = [analyze_movpkg(path, selected_group=selected_group) for path in packages]
    return {
        "root": str(Path(root)),
        "package_count": len(packages),
        "packages": [_compact_scan_row(summary) for summary in summaries],
    }


def movpkg_scan_markdown(scan: dict[str, Any]) -> str:
    """Render a compact Markdown table for a downloaded `.movpkg` survey."""
    lines = [
        "# movpkg scan\n\n",
        f"`{scan['root']}`\n",
        f"- top-level packages: {scan['package_count']}\n\n",
        "| package | verdict | complete main audio | Atmos status |\n",
        "|---|---|---|---|\n",
    ]
    for row in scan.get("packages", []):
        lines.append(
            f"| `{row['movpkg']}` | `{row.get('inventory_verdict') or ''}` | "
            f"{_markdown_cell(row.get('complete_main_audio') or ['none'])} | "
            f"{_markdown_cell(row.get('atmos_status') or ['none'])} |\n"
        )
    return "".join(lines)


def movpkg_summary_markdown(summary: dict[str, Any]) -> str:
    """Render a compact Markdown summary suitable for reports."""
    lines = [
        "# movpkg audio summary\n\n",
        f"`{summary['movpkg']}`\n",
        f"- files: {summary['file_count']}\n",
        f"- manifest-like: {summary['manifest_like_count']}\n",
        f"- StreamInfoBoot.xml: {summary['stream_info_count']}\n",
        f"- playlists: {summary['playlist_count']}\n",
    ]
    if summary.get("inventory_verdict"):
        lines.append(f"- inventory verdict: `{summary['inventory_verdict']}`\n")
    lines.append("\n## Search hits\n")
    hits = summary.get("content_hits", {})
    for term in SEARCH_TERMS:
        lines.append(f"- `{term}`: {hits.get(term, 0)}\n")

    lines.append("\n## Audio groups\n\n")
    lines.append(
        "| stream ID | scope | g | group/name | lang | role | codec/channels | "
        "complete | bytes/segments | looks like |\n"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|\n")
    for row in summary.get("audio_table", []):
        role = (row.get("role") or "").replace("|", "\\|")
        lines.append(
            f"| `{row.get('stream_id', '')}` | `{row.get('scope', '')}` | `{row.get('g', '')}` | "
            f"`{row.get('group', '')}` / `{row.get('name', '')}` | `{row.get('language', '')}` | `{role}` | "
            f"`{row.get('codec', '')}` / `{row.get('channels', '')}` | `{row.get('complete', '')}` | "
            f"{row.get('media_bytes_stored_sum', 0)} media bytes; {row.get('dir_bytes_sum', 0)} dir bytes; "
            f"{row.get('segments', '')}; {row.get('variant_count', 0)} variants | {row.get('looks_like', '')} |\n"
        )
    return "".join(lines)


def _compact_scan_row(summary: dict[str, Any]) -> dict[str, Any]:
    audio_rows = summary.get("audio_table", [])
    complete_main_audio = _unique_descriptions(
        row
        for row in audio_rows
        if row.get("has_complete_main_stream") and row.get("group", "").startswith("audio-")
    )
    atmos_rows = [row for row in audio_rows if "Atmos" in row.get("looks_like", "")]
    complete_main_atmos = [row for row in atmos_rows if row.get("has_complete_main_stream")]
    main_atmos_refs = [row for row in atmos_rows if "main" in row.get("scope", "")]

    if complete_main_atmos:
        atmos_status = _unique_descriptions(complete_main_atmos)
    elif main_atmos_refs:
        atmos_status = ["No complete main Atmos; advertised/inferred Atmos is missing or incomplete for main content"]
    elif atmos_rows:
        atmos_status = ["No complete main Atmos; Atmos appears only outside main content"]
    else:
        atmos_status = ["No Atmos group found"]

    return {
        "movpkg": summary.get("movpkg"),
        "inventory_verdict": summary.get("inventory_verdict"),
        "complete_main_audio": complete_main_audio,
        "atmos_status": atmos_status,
    }


def _unique_descriptions(rows: Any) -> list[str]:
    descriptions: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = (
            row.get("stream_id"),
            row.get("group"),
            row.get("channels"),
            row.get("media_bytes_stored_sum"),
            row.get("segments"),
            row.get("looks_like"),
        )
        if key in seen:
            continue
        seen.add(key)
        channels = row.get("channels", "")
        description = (
            f"{row.get('group', '')} "
            f"(channels {channels}, {row.get('media_bytes_stored_sum', 0)} bytes, "
            f"{row.get('segments', '')}, {row.get('looks_like', '')})"
        )
        descriptions.append(description)
    return descriptions or ["none"]


def _markdown_cell(items: list[str]) -> str:
    return "<br>".join(item.replace("|", "\\|") for item in items)


def _walk_files(movpkg: Path) -> tuple[list[tuple[Path, int]], list[str]]:
    files: list[tuple[Path, int]] = []
    errors: list[str] = []
    for root, _dirs, names in os.walk(movpkg, onerror=lambda e: errors.append(str(e))):
        for name in names:
            path = Path(root) / name
            try:
                files.append((path, path.stat().st_size))
            except OSError as exc:
                errors.append(f"stat {path}: {exc}")
    return files, errors


def _manifest_inventory(movpkg: Path, files: list[tuple[Path, int]], errors: list[str]) -> dict[str, Any]:
    manifest_like: list[Path] = []
    boot_xml: list[Path] = []
    root_xml: list[Path] = []
    stream_info: list[Path] = []
    playlists: list[Path] = []
    content_hits: Counter[str] = Counter()

    for path, _size in files:
        lname = path.name.lower()
        is_manifest = lname in {"boot.xml", "root.xml", "playlist.m3u8"} or lname.endswith((".xml", ".m3u8"))
        if is_manifest:
            manifest_like.append(path)
        if lname == "boot.xml":
            boot_xml.append(path)
        if lname == "root.xml":
            root_xml.append(path)
        if lname == "streaminfoboot.xml":
            stream_info.append(path)
        if lname.endswith(".m3u8") or lname == "playlist.m3u8":
            playlists.append(path)
        if is_manifest:
            text = _safe_text(path, errors, limit=2_000_000)
            if text is None:
                continue
            for term in SEARCH_TERMS:
                if term in text:
                    content_hits[term] += 1

    return {
        "manifest_like": manifest_like,
        "boot_xml": boot_xml,
        "root_xml": root_xml,
        "stream_info": stream_info,
        "playlists": playlists,
        "content_hits": content_hits,
    }


def _stream_inventory(
    movpkg: Path, stream_info: list[Path], errors: list[str]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    by_g: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in stream_info:
        text = _safe_text(path, errors)
        if text is None:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            errors.append(f"parse {path}: {exc}")
            continue
        network_url = _text_by_local(root, "NetworkURL") or ""
        local_path = _text_by_local(root, "Path") or ""
        stream_dir = path.parent
        counts: Counter[str] = Counter()
        bytes_by_ext: Counter[str] = Counter()
        try:
            for child in stream_dir.iterdir():
                if child.is_file():
                    ext = child.suffix or child.name
                    counts[ext] += 1
                    bytes_by_ext[ext] += child.stat().st_size
        except OSError as exc:
            errors.append(f"iter {stream_dir}: {exc}")

        rel = str(path.relative_to(movpkg))
        g = _group_num_from_text(network_url) or _group_num_from_text(local_path) or _group_num_from_text(text)
        if not g:
            g = _group_num_from_stream_dir(stream_dir, errors)

        row = {
            "id": _text_by_local(root, "ID") or path.parent.name,
            "rel": rel,
            "scope": "interstitial" if rel.startswith("InterstitialAssets/") else "main",
            "g": g,
            "complete": _text_by_local(root, "Complete"),
            "media_bytes_stored": _int(_text_by_local(root, "MediaBytesStored")) or 0,
            "network_url": network_url,
            "path": local_path,
            "dir_file_counts": dict(counts),
            "dir_bytes_by_ext": dict(bytes_by_ext),
        }
        rows.append(row)
        if row["g"]:
            by_g[row["g"]].append(row)
    return rows, by_g


def _playlist_inventory(
    movpkg: Path, playlists: list[Path]
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    media_rows: list[dict[str, str]] = []
    variant_rows: list[dict[str, str]] = []
    playlist_summaries: list[dict[str, Any]] = []
    for path in playlists:
        try:
            data = path.read_bytes()[:8_000_000]
        except OSError:
            continue
        text = data.decode("utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        playlist_summaries.append(
            {
                "rel": str(path.relative_to(movpkg)),
                "bytes": len(data),
                "extinf_count": sum(1 for line in lines if line.startswith("#EXTINF")),
                "endlist": any(line == "#EXT-X-ENDLIST" for line in lines),
                "g": _group_num_from_text(text),
            }
        )
        pending_stream: dict[str, str] | None = None
        for line in lines:
            if line.startswith("#EXT-X-MEDIA:"):
                attrs = _parse_hls_attrs(line.split(":", 1)[1])
                if attrs.get("TYPE") == "AUDIO":
                    attrs["playlist_rel"] = str(path.relative_to(movpkg))
                    attrs["g"] = _group_num_from_text(json.dumps(attrs)) or ""
                    media_rows.append(attrs)
            elif line.startswith("#EXT-X-STREAM-INF:"):
                pending_stream = _parse_hls_attrs(line.split(":", 1)[1])
            elif pending_stream is not None and not line.startswith("#"):
                row = pending_stream
                row["uri"] = line
                row["playlist_rel"] = str(path.relative_to(movpkg))
                row["g"] = _group_num_from_text(json.dumps(row)) or ""
                variant_rows.append(row)
                pending_stream = None
    return media_rows, variant_rows, playlist_summaries


def _audio_table(
    media_rows: list[dict[str, str]],
    variant_rows: list[dict[str, str]],
    stream_by_g: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in media_rows:
        key = (row.get("GROUP-ID", ""), row.get("NAME", ""), row.get("LANGUAGE", ""), row.get("CHARACTERISTICS", ""))
        groups.setdefault(
            key,
            {
                "group": row.get("GROUP-ID", ""),
                "name": row.get("NAME", ""),
                "language": row.get("LANGUAGE", ""),
                "role": row.get("CHARACTERISTICS", ""),
                "channels": row.get("CHANNELS", ""),
                "uri": row.get("URI", ""),
                "g": row.get("g", ""),
                "codecs": set(),
                "variant_count": 0,
            },
        )
    for row in variant_rows:
        gid = row.get("AUDIO", "")
        for group in groups.values():
            if group["group"] == gid:
                if row.get("CODECS"):
                    group["codecs"].add(row["CODECS"])
                group["variant_count"] += 1
                if row.get("g") and not group["g"]:
                    group["g"] = row["g"]

    table: list[dict[str, Any]] = []
    for group in groups.values():
        g = group.get("g") or _group_num_from_text(group.get("uri", "")) or _group_num_from_text(group.get("group", ""))
        streams = stream_by_g.get(g or "", [])
        main_streams = [stream for stream in streams if stream.get("scope") == "main"]
        scoped = main_streams or streams
        complete_vals = sorted({s.get("complete") or "" for s in scoped if s.get("complete")})
        media_bytes = sum(s.get("media_bytes_stored", 0) for s in scoped)
        frag_count = sum(s.get("dir_file_counts", {}).get(".frag", 0) for s in scoped)
        initfrag_count = sum(s.get("dir_file_counts", {}).get(".initfrag", 0) for s in scoped)
        byte_count = sum(sum(s.get("dir_bytes_by_ext", {}).values()) for s in scoped)
        codecs = sorted(c for c in group["codecs"] if c)
        codec_short = ", ".join(codecs[:4])
        table.append(
            {
                "stream_id": ", ".join(s["id"] for s in scoped[:3]) or "",
                "scope": _scope_label(streams),
                "g": g or "",
                "group": group["group"],
                "name": group["name"],
                "language": group["language"],
                "role": group["role"],
                "codec": codec_short,
                "channels": group["channels"],
                "complete": "/".join(complete_vals) or "referenced only",
                "media_bytes_stored_sum": media_bytes,
                "dir_bytes_sum": byte_count,
                "segments": f"{frag_count} .frag, {initfrag_count} .initfrag",
                "variant_count": group["variant_count"],
                "looks_like": _classify_group(
                    group["group"], group["name"], group["role"], codec_short, group["channels"]
                ),
                "has_complete_main_stream": any(_stream_complete(stream) for stream in main_streams),
                "has_any_complete_stream": any(_stream_complete(stream) for stream in streams),
            }
        )
    table.sort(key=lambda r: (r["group"], r["name"], r["role"]))
    return table


def _inventory_verdict(audio_table: list[dict[str, Any]], selected_group: str | None) -> str | None:
    atmos_rows = [row for row in audio_table if "Atmos" in row.get("looks_like", "")]
    has_complete_main_atmos = any(row.get("has_complete_main_stream") for row in atmos_rows)
    selected_kind = _group_kind(selected_group or "")

    if selected_kind == "atmos" and has_complete_main_atmos:
        return "movpkg_atmos_variant_selected"
    if selected_kind == "stereo" and has_complete_main_atmos:
        return "movpkg_atmos_variant_present_but_not_selected"
    if atmos_rows and not has_complete_main_atmos:
        return "movpkg_atmos_variant_missing_or_incomplete"
    if selected_kind == "stereo":
        return "movpkg_figstreamplayer_selected_stereo"
    return None


def _stream_complete(stream: dict[str, Any]) -> bool:
    return (
        str(stream.get("complete") or "").upper() == "YES"
        and (stream.get("media_bytes_stored") or 0) > 0
        and stream.get("dir_file_counts", {}).get(".frag", 0) > 0
    )


def _scope_label(streams: list[dict[str, Any]]) -> str:
    if not streams:
        return "referenced"
    scopes = sorted({stream.get("scope", "unknown") for stream in streams})
    return "/".join(scopes)


def _group_kind(group: str) -> str | None:
    group = group.lower()
    if "audio-atmos" in group:
        return "atmos"
    if "audio-stereo" in group:
        return "stereo"
    return None


def _classify_group(group: str, name: str = "", role: str = "", codecs: str = "", channels: str = "") -> str:
    haystack = " ".join([group or "", name or "", role or "", codecs or "", channels or ""]).lower()
    labels = []
    if "describes-video" in haystack or "audio description" in haystack or re.search(r"\bad\b", haystack):
        labels.append("AD")
    if "atmos" in haystack or "16/joc" in haystack or "ec-3" in haystack:
        labels.append("Atmos")
    if "ac-3" in haystack or channels == "6":
        labels.append("5.1")
    if "stereo" in haystack or "mp4a.40.2" in haystack or channels == "2":
        labels.append("stereo")
    return ", ".join(dict.fromkeys(labels or ["unknown"]))


def _parse_hls_attrs(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    key = ""
    value = ""
    in_key = True
    in_quote = False
    items: list[tuple[str, str]] = []
    for char in text:
        if in_key:
            if char == "=":
                in_key = False
            else:
                key += char
        elif char == '"':
            in_quote = not in_quote
            value += char
        elif char == "," and not in_quote:
            items.append((key.strip(), value.strip()))
            key = ""
            value = ""
            in_key = True
        else:
            value += char
    if key:
        items.append((key.strip(), value.strip()))
    for item_key, item_value in items:
        if len(item_value) >= 2 and item_value[0] == '"' and item_value[-1] == '"':
            item_value = item_value[1:-1]
        attrs[item_key] = item_value
    return attrs


def _group_num_from_text(text: str | None) -> str | None:
    if not text:
        return None
    for pattern in (r"[?&]g=(\d+)", r"_gr(\d+)_", r"-gr(\d+)-", r"/gr(\d+)[/_-]"):
        if match := re.search(pattern, text):
            return match.group(1)
    return None


def _group_num_from_stream_dir(stream_dir: Path, errors: list[str]) -> str | None:
    try:
        playlists = sorted(stream_dir.glob("*.m3u8"))
    except OSError as exc:
        errors.append(f"glob {stream_dir}: {exc}")
        return None
    for playlist in playlists:
        text = _safe_text(playlist, errors, limit=2_000_000)
        if g := _group_num_from_text(text):
            return g
    return None


def _safe_text(path: Path, errors: list[str], limit: int = 20_000_000) -> str | None:
    try:
        return path.read_bytes()[:limit].decode("utf-8", errors="ignore")
    except OSError as exc:
        errors.append(f"read {path}: {exc}")
        return None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text_by_local(root: ET.Element, name: str) -> str | None:
    for elem in root.iter():
        if _strip_ns(elem.tag) == name and elem.text is not None:
            return elem.text.strip()
    return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["SEARCH_TERMS", "analyze_movpkg", "movpkg_summary_markdown"]
