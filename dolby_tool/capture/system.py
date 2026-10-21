"""Playback evidence a standard (non-admin) macOS account can read while TV.app plays.

`log stream` refuses to run for accounts outside the `admin` group ("Must be admin to
run 'stream' command"), and every other route to the unified log (Console, xctrace,
`OS_ACTIVITY_DT_MODE` on platform binaries) is gated the same way. These sources are
not, and together they describe the playback from the outside:

| Source                                  | What it shows                                                     |
|-----------------------------------------|-------------------------------------------------------------------|
| `ioreg` AppleAVDUserClient              | hardware decoder sessions: codec, coded size, bit depth, DRM, owner |
| NSScreen EDR headroom (via `osascript`) | HDR/EDR content is being presented                                |
| `ioreg` IOMobileFramebufferShim         | built-in panel brightness cap (300 → 1600 nits in XDR mode)       |
| `pmset -g assertions`                   | the player's playback assertion and the audio output opened for it |
| `nettop`                                | per-process download throughput (streaming bitrate)               |
| `lsof`                                  | the local media file (or .movpkg) the player has open             |
| MediaRemote (helper dylib run by perl)  | title, show, duration, position, playback rate                    |

What stays out of reach without the log: the decoded FourCC (Dolby Vision vs HDR10),
the HLS variant/codec string, and the Atmos/spatialization decisions, all of which
happen inside TV.app's own process.

`SystemSampler` polls these sources on background threads, emits an event whenever
something changes, and folds the samples into the `system` block of a capture summary.
"""

import contextlib
import grp
import hashlib
import json
import logging
import os
import platform
import plistlib
import pwd
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..common import MEDIA_EXTENSIONS

logger = logging.getLogger(__name__)

Event = dict[str, Any]

# Seconds between samples of the cheap sources; nettop always takes ~5 s per sample.
SAMPLE_INTERVAL_S = 2.0
NOW_PLAYING_INTERVAL_S = 4.0
PROBE_TIMEOUT_S = 8.0

# EDR headroom of an SDR desktop sits at 1.0–1.3 depending on brightness; HDR content
# pushes it toward the panel's potential (up to 16 on XDR panels). Both signals are
# display-wide: any app's HDR content (or another display) moves them, so they show
# that EDR was active, never that the player's video was HDR.
EDR_HEADROOM_THRESHOLD = 2.0
# The built-in XDR panel caps SDR at 300 nits (some models 500); EDR content lifts it.
EDR_NITS_CAP_THRESHOLD = 600

# Player processes the sampler follows, per capture profile (the `log stream` predicate's
# counterparts). Safari decodes in its WebKit GPU process.
PLAYER_PROCESSES: dict[str, tuple[str, ...]] = {
    "tv": ("TV",),
    "local-player": ("TV", "QuickTime Player", "com.apple.WebKit.GPU"),
}

# AppleAVDUserClient `CodecType` values observed on Apple silicon (1: H.264, 2: HEVC).
AVD_CODECS = {1: "H.264", 2: "HEVC"}
# `EncryptionScheme`: 6 on clear local files and trailers, 4 on FairPlay-protected Apple TV+ HLS.
AVD_CLEAR_SCHEMES = frozenset({6})
AVD_PROTECTED_SCHEMES = frozenset({4})
# chroma_format_idc as in the HEVC/H.264 specs.
CHROMA_FORMATS = {0: "4:0:0", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}
# ioreg truncates IOUserClientCreator names to 16 characters.
SHARED_DECODER_SERVICE = "VTDecoderXPCServ"

NOT_OBSERVABLE_WITHOUT_LOG = (
    "Decoded FourCC — Dolby Vision (dvh1/qdh1) vs HDR10/SDR fallback (hvc1)",
    "HLS variant: codec string, advertised bitrate and audio group",
    "Audio codec, Atmos (JOC) decode and spatialization",
)

# Media files a player may hold open; .movpkg segments live inside the package directory.
_PLAYER_MEDIA_EXTENSIONS = MEDIA_EXTENSIONS | {".mp4a", ".m4a", ".m4s", ".movpkg", ".frag"}
_MOVPKG_RE = re.compile(r"^(.*?\.movpkg)(?:/|$)")


# ---------------------------------------------------------------------------
# log stream access


def log_stream_access() -> dict[str, Any]:
    """Whether this account may run `log stream`: macOS, the binary present, and root or `admin` membership."""
    if platform.system() != "Darwin":
        return {"available": False, "reason": "live log capture needs macOS"}
    if shutil.which("log") is None:
        return {"available": False, "reason": "`log` is not on PATH"}
    if os.geteuid() == 0:
        return {"available": True, "reason": "running as root"}
    user = str(os.getuid())
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
        is_admin = grp.getgrnam("admin").gr_gid in os.getgrouplist(user, os.getgid())
    except KeyError, OSError:
        is_admin = False
    if is_admin:
        return {"available": True, "reason": f"{user} is an administrator"}
    return {
        "available": False,
        "reason": f"{user} is a standard account; macOS only lets administrators read the unified log",
    }


# ---------------------------------------------------------------------------
# parsers (pure: raw tool output in, plain dicts out)


def parse_decoder_sessions(plist: bytes) -> list[dict[str, Any]]:
    """`ioreg -r -c AppleAVDUserClient -a` → one dict per hardware decoder session."""
    try:
        entries = plistlib.loads(plist) if plist.strip() else []
    except plistlib.InvalidFileException, ValueError:
        return []
    sessions = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or "FrameWidth" not in entry:
            continue
        creator_pid, creator = _parse_creator(entry.get("IOUserClientCreator", ""))
        codec_type = entry.get("CodecType")
        scheme = entry.get("EncryptionScheme")
        chroma = entry.get("ChromaFormat")
        sessions.append(
            {
                "creator_pid": creator_pid,
                "creator": creator,
                "created": entry.get("CreationTime"),
                "codec": AVD_CODECS.get(codec_type, f"codec type {codec_type}") if codec_type is not None else None,
                "codec_type": codec_type,
                "width": entry.get("FrameWidth"),
                "height": entry.get("FrameHeight"),
                "bit_depth": entry.get("LumaBitDepth"),
                "chroma": CHROMA_FORMATS.get(chroma) if isinstance(chroma, int) else None,
                "encryption_scheme": scheme,
                "protected": True
                if scheme in AVD_PROTECTED_SCHEMES
                else False
                if scheme in AVD_CLEAR_SCHEMES
                else None,
            }
        )
    return sessions


def _parse_creator(text: str) -> tuple[int | None, str | None]:
    match = re.match(r"pid (\d+), (.+)", text or "")
    return (int(match[1]), match[2].strip()) if match else (None, None)


def parse_panel_nits_cap(plist: bytes) -> float | None:
    """`ioreg -r -c IOMobileFramebufferShim -a -d 1` → the built-in panel's brightness cap in nits."""
    try:
        entries = plistlib.loads(plist) if plist.strip() else []
    except plistlib.InvalidFileException, ValueError:
        return None
    caps = [e["BLNitsCap"] for e in entries if isinstance(e, dict) and isinstance(e.get("BLNitsCap"), int)]
    return round(max(caps) / 65536, 1) if caps else None  # 16.16 fixed point


_ASSERTION_RE = re.compile(r"^\s*pid (\d+)\((.*?)\): \[[^\]]*\] \S+ (\w+) named: \"(.*)\"")


def parse_pmset_assertions(text: str) -> list[dict[str, Any]]:
    """`pmset -g assertions` → the per-process assertions with their resources and beneficiary PID."""
    assertions: list[dict[str, Any]] = []
    for line in text.splitlines():
        if match := _ASSERTION_RE.match(line):
            assertions.append(
                {"pid": int(match[1]), "process": match[2], "type": match[3], "name": match[4], "resources": []}
            )
        elif assertions and (stripped := line.strip()).startswith("Resources:"):
            assertions[-1]["resources"] = stripped.removeprefix("Resources:").split()
        elif assertions and (found := re.match(r"\s*Created for PID: (\d+)", line)):
            assertions[-1]["created_for_pid"] = int(found[1])
    return assertions


def playback_activity(assertions: Iterable[dict[str, Any]], pids: set[int]) -> dict[str, Any]:
    """Which of `pids` hold a CoreMedia playback assertion, and the audio outputs opened for them."""
    playing = sorted({a["pid"] for a in assertions if a["pid"] in pids and a["name"].startswith("com.apple.coremedia")})
    outputs: list[str] = []
    for a in assertions:
        if a.get("created_for_pid") not in pids or "audio-out" not in a["resources"]:
            continue
        index = a["resources"].index("audio-out")
        if index + 1 < len(a["resources"]) and a["resources"][index + 1] not in outputs:
            outputs.append(a["resources"][index + 1])
    return {"playing_pids": playing, "audio_outputs": outputs}


_NETTOP_ROW_RE = re.compile(r"^(.+)\.(\d+),(\d+),")


def parse_nettop(text: str) -> dict[int, tuple[str, int]]:
    """`nettop -P -L 1 -J bytes_in -x` → {pid: (process name, cumulative bytes in)}."""
    rows = {}
    for line in text.splitlines():
        if match := _NETTOP_ROW_RE.match(line):
            rows[int(match[2])] = (match[1], int(match[3]))
    return rows


def parse_lsof_media(text: str) -> list[str]:
    """`lsof -F n` → media files the process has open; .movpkg internals collapse to the package."""
    found: list[str] = []
    for line in text.splitlines():
        if not line.startswith("n/"):
            continue
        path = line[1:]
        if path.startswith(("/System/", "/Library/", "/private/var/", "/usr/")):
            continue
        if match := _MOVPKG_RE.match(path):
            path = match[1]
        elif Path(path).suffix.lower() not in _PLAYER_MEDIA_EXTENSIONS:
            continue
        if path not in found:
            found.append(path)
    return found


# ---------------------------------------------------------------------------
# probes (run one tool, never raise)


def _run(cmd: Sequence[str], *, timeout: float = PROBE_TIMEOUT_S) -> bytes | None:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except OSError, subprocess.TimeoutExpired:
        return None
    return result.stdout if result.returncode == 0 else None


def probe_pids(names: Iterable[str]) -> dict[int, str]:
    pids: dict[int, str] = {}
    for name in names:
        out = _run(["pgrep", "-x", name]) or b""
        for token in out.split():
            pids[int(token)] = name
    return pids


def probe_decoder_sessions() -> list[dict[str, Any]]:
    return parse_decoder_sessions(_run(["ioreg", "-r", "-c", "AppleAVDUserClient", "-a"]) or b"")


def probe_panel_nits_cap() -> float | None:
    return parse_panel_nits_cap(_run(["ioreg", "-r", "-c", "IOMobileFramebufferShim", "-a", "-d", "1"]) or b"")


_EDR_JXA = """
ObjC.import('AppKit');
const screens = $.NSScreen.screens, out = [];
for (let i = 0; i < screens.count; i++) {
  const s = screens.objectAtIndex(i);
  out.push({name: s.localizedName.js,
            headroom: s.maximumExtendedDynamicRangeColorComponentValue,
            potential: s.maximumPotentialExtendedDynamicRangeColorComponentValue});
}
JSON.stringify(out);
"""


def probe_edr_screens() -> list[dict[str, Any]]:
    out = _run(["osascript", "-l", "JavaScript", "-e", _EDR_JXA])
    try:
        screens = json.loads(out or b"[]")
    except ValueError:
        return []
    return [s for s in screens if isinstance(s, dict)]


def probe_playback_activity(pids: set[int]) -> dict[str, Any]:
    text = (_run(["pmset", "-g", "assertions"]) or b"").decode(errors="replace")
    return playback_activity(parse_pmset_assertions(text), pids)


def probe_open_media(pids: Iterable[int]) -> list[str]:
    pid_list = ",".join(str(p) for p in pids)
    if not pid_list:
        return []
    # lsof exits 1 when some descriptors could not be listed; its output is still usable.
    try:
        result = subprocess.run(
            ["lsof", "-n", "-P", "-F", "n", "-p", pid_list], capture_output=True, timeout=PROBE_TIMEOUT_S, check=False
        )
    except OSError, subprocess.TimeoutExpired:
        return []
    return parse_lsof_media(result.stdout.decode(errors="replace"))


def probe_nettop() -> dict[int, tuple[str, int]]:
    return parse_nettop((_run(["nettop", "-P", "-L", "1", "-J", "bytes_in", "-x"], timeout=15) or b"").decode())


class NowPlayingHelper:
    """Builds `nowplaying.m` once into the user cache and runs it inside /usr/bin/perl."""

    SOURCE = Path(__file__).with_name("nowplaying.m")

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or Path.home() / "Library" / "Caches" / "dolby-tool"
        self.unavailable: str | None = None
        self._dylib: Path | None = None

    def query(self) -> dict[str, Any] | None:
        """The Now Playing info ({} when idle), or None when the helper is unavailable."""
        dylib = self._ensure_built()
        if dylib is None:
            return None
        loader = f'use DynaLoader; DynaLoader::dl_load_file("{dylib}") or die DynaLoader::dl_error();'
        out = _run(["/usr/bin/perl", "-e", loader], timeout=6)
        try:
            info = json.loads(out or b"null")
        except ValueError:
            return None
        return info if isinstance(info, dict) else None

    def _ensure_built(self) -> Path | None:
        if self._dylib is not None or self.unavailable is not None:
            return self._dylib
        if not Path("/usr/bin/perl").exists():
            self.unavailable = "/usr/bin/perl is missing"
            return None
        digest = hashlib.sha256(self.SOURCE.read_bytes()).hexdigest()[:12]
        dylib = self.cache_dir / f"nowplaying-{digest}.dylib"
        if not dylib.exists():
            compiler = ["xcrun", "clang"] if shutil.which("xcrun") else ["clang"] if shutil.which("clang") else None
            if compiler is None:
                self.unavailable = "needs clang (xcode-select --install) to build the Now Playing helper"
                return None
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = dylib.with_suffix(".tmp")
            cmd = [*compiler, "-dynamiclib", "-fobjc-arc", "-framework", "Foundation", str(self.SOURCE), "-o", str(tmp)]
            if _run(cmd, timeout=60) is None or not tmp.exists():
                self.unavailable = "could not build the Now Playing helper"
                return None
            tmp.replace(dylib)
        self._dylib = dylib
        return dylib


# ---------------------------------------------------------------------------
# aggregation


def _session_key(session: dict[str, Any]) -> tuple[Any, ...]:
    return (session["creator_pid"], session["created"], session["width"], session["height"], session["codec_type"])


def _describe_session(s: dict[str, Any]) -> str:
    depth = f" {s['bit_depth']}-bit" if s.get("bit_depth") else ""
    chroma = f" {s['chroma']}" if s.get("chroma") else ""
    drm = {True: ", DRM-protected", False: ", clear", None: ""}[s.get("protected")]
    return f"{s['codec']} {s['width']}x{s['height']}{depth}{chroma} hardware decode{drm} ({s['creator']})"


class SystemState:
    """Running totals of the non-admin samples; `summary()` builds the capture's `system` block."""

    def __init__(self, processes: Sequence[str]) -> None:
        self.processes = list(processes)
        self.sample_count = 0
        self.pids: dict[int, str] = {}
        self.decoder_sessions: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.max_headroom: float | None = None
        self.potential_headroom: float | None = None
        self.edr_seen = False
        self.edr_active: bool | None = None
        self.max_nits_cap: float | None = None
        self.playing_seen = False
        self.audio_outputs: list[str] = []
        self.media_files: list[str] = []
        self.now_playing: dict[str, Any] | None = None
        self.now_playing_unavailable: str | None = None
        self.net_first: dict[int, tuple[float, int]] = {}
        self.net_last: dict[int, tuple[float, int]] = {}
        self.net_peak_mbps: float | None = None
        self.net_samples = 0

    # Each fold_* returns the events describing what changed (empty when nothing did).

    def fold_pids(self, pids: dict[int, str]) -> list[Event]:
        new = {pid: name for pid, name in pids.items() if pid not in self.pids}
        self.pids.update(pids)
        return [
            _event("system_process", f"{name} running (pid {pid})", pid=pid, process=name) for pid, name in new.items()
        ]

    def fold_decoders(self, sessions: Iterable[dict[str, Any]], now: float) -> list[Event]:
        events = []
        for session in sessions:
            if not self._relevant(session):
                continue
            key = _session_key(session)
            if key in self.decoder_sessions:
                self.decoder_sessions[key]["last_seen"] = now
                continue
            self.decoder_sessions[key] = {**session, "first_seen": now, "last_seen": now}
            events.append(_event("system_decoder", _describe_session(session), **session))
        return events

    def _relevant(self, session: dict[str, Any]) -> bool:
        if session["creator_pid"] in self.pids:
            return True
        # Local files decode in the shared VideoToolbox service, which serves every app.
        return session["creator"] == SHARED_DECODER_SERVICE

    def fold_display(self, screens: Sequence[dict[str, Any]], nits_cap: float | None) -> list[Event]:
        headrooms = [s["headroom"] for s in screens if isinstance(s.get("headroom"), int | float)]
        potentials = [s["potential"] for s in screens if isinstance(s.get("potential"), int | float)]
        headroom = max(headrooms) if headrooms else None
        if headroom is not None:
            self.max_headroom = max(self.max_headroom or headroom, headroom)
        if potentials:
            self.potential_headroom = max(potentials)
        if nits_cap is not None:
            self.max_nits_cap = max(self.max_nits_cap or nits_cap, nits_cap)
        if headroom is None and nits_cap is None:
            return []
        active = (headroom or 0) >= EDR_HEADROOM_THRESHOLD or (nits_cap or 0) >= EDR_NITS_CAP_THRESHOLD
        self.edr_seen |= active
        if active == self.edr_active:
            return []
        self.edr_active = active
        detail = ", ".join(
            part
            for part in (
                f"EDR headroom {headroom:.1f}×" if headroom is not None else None,
                f"panel cap {nits_cap:g} nits" if nits_cap is not None else None,
            )
            if part
        )
        text = f"Display EDR {'active' if active else 'inactive'}, any app ({detail})"
        return [_event("system_display", text, edr_active=active, edr_headroom=headroom, nits_cap=nits_cap)]

    def fold_activity(self, activity: dict[str, Any]) -> list[Event]:
        events = []
        if activity["playing_pids"] and not self.playing_seen:
            self.playing_seen = True
            names = ", ".join(self.pids.get(pid, str(pid)) for pid in activity["playing_pids"])
            events.append(_event("system_playback", f"{names} holds a CoreMedia playback assertion", **activity))
        for output in activity["audio_outputs"]:
            if output not in self.audio_outputs:
                self.audio_outputs.append(output)
                events.append(
                    _event("system_audio", f"CoreAudio output opened for the player: {output}", output=output)
                )
        return events

    def fold_media_files(self, paths: Iterable[str]) -> list[Event]:
        events = []
        for path in paths:
            if path not in self.media_files:
                self.media_files.append(path)
                events.append(_event("system_file", f"Player opened {path}", path=path))
        return events

    def fold_now_playing(self, info: dict[str, Any] | None, unavailable: str | None = None) -> list[Event]:
        if info is None:
            self.now_playing_unavailable = unavailable
            return []
        title_changed = (info.get("Title"), info.get("Album")) != (
            (self.now_playing or {}).get("Title"),
            (self.now_playing or {}).get("Album"),
        )
        self.now_playing = info
        if not title_changed or not info.get("Title"):
            return []
        show = f"{info['Album']} — " if info.get("Album") else ""
        return [_event("system_now_playing", f"Now Playing: {show}{info['Title']}", **_now_playing_fields(info))]

    def fold_network(self, rows: dict[int, tuple[str, int]], now: float) -> list[Event]:
        events = []
        for pid, (_name, total) in rows.items():
            if pid not in self.pids:
                continue
            previous = self.net_last.get(pid)
            self.net_first.setdefault(pid, (now, total))
            self.net_last[pid] = (now, total)
            if previous is None or now <= previous[0] or total < previous[1]:
                continue
            mbps = round((total - previous[1]) * 8 / (now - previous[0]) / 1_000_000, 2)
            self.net_samples += 1
            self.net_peak_mbps = max(self.net_peak_mbps or 0, mbps)
            events.append(_event("system_network", f"{self.pids[pid]} downloading {mbps:g} Mbit/s", pid=pid, mbps=mbps))
        return events

    # -- summary -------------------------------------------------------------

    def summary(self, *, log_available: bool = False) -> dict[str, Any]:
        """`log_available`: the capture also reads the unified log, so its caveats about it are dropped."""
        sessions = sorted(self.decoder_sessions.values(), key=lambda s: s["first_seen"])
        video = _primary_session(sessions, set(self.pids))
        network = self._network_summary()
        return {
            "sample_count": self.sample_count,
            "processes": [{"pid": pid, "name": name} for pid, name in sorted(self.pids.items())],
            "decoder_sessions": sessions,
            "video": video,
            "display": {
                # Display-wide, not attributable to the player: see EDR_HEADROOM_THRESHOLD.
                "edr_seen": self.edr_seen,
                "max_edr_headroom": _round(self.max_headroom),
                "potential_edr_headroom": _round(self.potential_headroom),
                "max_panel_nits_cap": self.max_nits_cap,
            },
            "audio": {"playback_assertion_seen": self.playing_seen, "outputs": self.audio_outputs},
            "network": network,
            "media_files": self.media_files,
            "now_playing": _now_playing_fields(self.now_playing) if self.now_playing else None,
            "now_playing_unavailable": self.now_playing_unavailable,
            "verdict": _verdict(video, self.edr_seen, network, self.media_files, log_available=log_available),
            "not_observable": [] if log_available else list(NOT_OBSERVABLE_WITHOUT_LOG),
        }

    def _network_summary(self) -> dict[str, Any] | None:
        if not self.net_samples:
            return None
        totals = []
        for pid, (t0, b0) in self.net_first.items():
            t1, b1 = self.net_last[pid]
            if t1 > t0 and b1 >= b0:
                totals.append((self.pids.get(pid), (b1 - b0) * 8 / (t1 - t0) / 1_000_000, b1 - b0))
        if not totals:
            return None
        name, avg, received = max(totals, key=lambda t: t[2])
        return {
            "process": name,
            "avg_mbps": round(avg, 2),
            "peak_mbps": self.net_peak_mbps,
            "received_mb": round(received / 1_000_000, 1),
            "samples": self.net_samples,
        }


def _primary_session(sessions: list[dict[str, Any]], pids: set[int]) -> dict[str, Any] | None:
    """The decoder session most likely to be the main video: the player's own, then the largest."""
    if not sessions:
        return None
    own = [s for s in sessions if s["creator_pid"] in pids]
    pool = own or sessions
    best = max(pool, key=lambda s: ((s["width"] or 0) * (s["height"] or 0), s["last_seen"]))
    return {
        **{k: best[k] for k in ("codec", "width", "height", "bit_depth", "chroma", "protected", "creator", "created")},
        "coded_resolution": f"{best['width']}x{best['height']}",
        "decoded_in_player_process": best["creator_pid"] in pids,
        "description": _describe_session(best),
    }


def _verdict(
    video: dict[str, Any] | None,
    edr_seen: bool,
    network: dict[str, Any] | None,
    media_files: list[str],
    *,
    log_available: bool,
) -> dict[str, Any]:
    """What was observed, without claiming a dynamic range: no non-admin source ties HDR to the player.

    `hdr` is always "unconfirmed" here; only the log's decoded FourCC (the `playback` block) settles it.
    """
    if video is None:
        return {
            "label": "no hardware video decode seen",
            "hdr": "unconfirmed",
            "notes": ["Start the capture, then start playback; paused or software decodes may not register."],
        }
    depth = f"{video['bit_depth']}-bit " if video.get("bit_depth") else ""
    notes = []
    if edr_seen:
        notes.append(
            "A display was in EDR (HDR) mode during the capture. That is display-wide — any app or "
            "display can cause it — so it does not prove this video is HDR."
        )
    else:
        notes.append("No display entered EDR (HDR) mode during the capture.")
    if video.get("codec") == "HEVC" and video.get("bit_depth") == 8:
        notes.append("8-bit HEVC: HDR10 and Dolby Vision HEVC streams are 10-bit.")
    elif (video.get("bit_depth") or 0) >= 10:
        notes.append("10-bit decode is required for HDR10/Dolby Vision but is also used for SDR.")
    if not log_available:
        notes.append("Whether this is Dolby Vision, HDR10 or SDR needs the decoded FourCC from the admin log.")
    if video.get("protected"):
        notes.append("FairPlay-protected stream (Apple TV+ / iTunes HLS).")
    if network:
        notes.append(f"Measured download {network['avg_mbps']:g} Mbit/s average, {network['peak_mbps']:g} peak.")
    if media_files:
        notes.append("Inspect the opened file for its Dolby Vision profile and Atmos track.")
    return {"label": f"{depth}{video['codec']} hardware decode", "hdr": "unconfirmed", "notes": notes}


def _now_playing_fields(info: dict[str, Any]) -> dict[str, Any]:
    """`nowplaying.m` output → summary fields. MRContentItemMetadata.mediaType is 2 for TV.app video."""
    media_type = info.get("MediaType")
    return {
        "title": info.get("Title"),
        "album": info.get("Album"),
        "artist": info.get("Artist"),
        "media_type": "video" if media_type == 2 else media_type,
        "duration_s": _round(info.get("Duration")),
        "elapsed_s": _round(info.get("ElapsedTime")),
        "playback_rate": info.get("PlaybackRate"),
        "is_playing": info.get("IsPlaying"),
        "app_pid": info.get("ApplicationPID"),
        "bundle_id": info.get("BundleIdentifier"),
    }


def _round(value: Any) -> float | None:
    return round(value, 2) if isinstance(value, int | float) else None


def _event(kind: str, raw: str, **fields: Any) -> Event:
    return {"kind": kind, "raw": raw, "source": "system", **fields}


# ---------------------------------------------------------------------------
# sampler


class SystemSampler:
    """Polls the non-admin sources on background threads and reports each change to `emit`.

    `processes` are the player process names to follow (`TV`, `QuickTime Player`, …).
    """

    def __init__(
        self,
        processes: Sequence[str] = ("TV",),
        *,
        interval: float = SAMPLE_INTERVAL_S,
        now_playing: NowPlayingHelper | None = None,
    ) -> None:
        self.processes = list(processes)
        self.interval = interval
        self.now_playing = now_playing or NowPlayingHelper()
        self._state = SystemState(self.processes)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._emit: Callable[[Event], None] = lambda _event: None

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def start(self, emit: Callable[[Event], None]) -> None:
        if self.running:
            return
        self._emit = emit
        self._stop.clear()
        with self._lock:
            self._state = SystemState(self.processes)
        self._threads = [
            threading.Thread(target=self._loop, args=(self._sample_fast, self.interval), daemon=True),
            threading.Thread(target=self._loop, args=(self._sample_now_playing, NOW_PLAYING_INTERVAL_S), daemon=True),
            # nettop blocks ~5 s per sample on its own, so it needs no extra wait.
            threading.Thread(target=self._loop, args=(self._sample_network, 0), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=PROBE_TIMEOUT_S + 8)
        self._threads = []

    def summary(self, *, log_available: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._state.summary(log_available=log_available)

    def _loop(self, sample: Callable[[], list[Event]], wait: float) -> None:
        while not self._stop.is_set():
            try:
                events = sample()
            except Exception:
                logger.exception("system sample failed")
                events = []
            for event in events:
                event["ts"] = time.time()
                with contextlib.suppress(Exception):
                    self._emit(event)
            if wait:
                self._stop.wait(wait)

    def _sample_fast(self) -> list[Event]:
        pids = probe_pids(self.processes)
        sessions = probe_decoder_sessions()
        screens = probe_edr_screens()
        nits_cap = probe_panel_nits_cap()
        activity = probe_playback_activity(set(pids))
        media = probe_open_media(pids)
        now = time.time()
        with self._lock:
            self._state.sample_count += 1
            return [
                *self._state.fold_pids(pids),
                *self._state.fold_decoders(sessions, now),
                *self._state.fold_display(screens, nits_cap),
                *self._state.fold_activity(activity),
                *self._state.fold_media_files(media),
            ]

    def _sample_now_playing(self) -> list[Event]:
        info = self.now_playing.query()
        with self._lock:
            return self._state.fold_now_playing(info, self.now_playing.unavailable)

    def _sample_network(self) -> list[Event]:
        rows = probe_nettop()
        if not rows:
            self._stop.wait(self.interval)  # nettop missing or failing: don't spin
        now = time.time()
        with self._lock:
            return self._state.fold_network(rows, now)
