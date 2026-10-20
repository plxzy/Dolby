"""Compare multiple inspected specs side-by-side and pick a winner."""
from __future__ import annotations

from typing import Any

from .inspect import inspect_file

# Score weights — tunable. Higher = more impactful on overall score.
WEIGHTS = {
    "dv_present": 25,
    "dv_fourcc_ok": 25,  # huge — without this DV silently falls back in TV.app
    "hdr10_plus": 10,
    "atmos": 15,
    "bit_rate_mbps": 20,  # scaled 0..20 (capped at 80 Mbps)
    "resolution": 10,  # 0=SD, 5=1080p, 10=2160p+
    "subtitles": 5,  # 0..5 by count, capped at 5 tracks
}


def _score(spec: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    v = spec["video"]
    dv = v["dolby_vision"]

    dv_present = bool(dv.get("present"))
    dv_fourcc_ok = dv_present and v.get("fourcc") in {"dvh1", "dvhe", "dvav", "dva1"}
    hdr10_plus = bool(v.get("hdr10_plus"))
    atmos = any(a.get("atmos") is True for a in spec["audio"])

    br = v.get("bit_rate_mbps") or 0
    br_score = min(br / 80.0, 1.0) * weights["bit_rate_mbps"]

    h = v.get("height") or 0
    if h >= 2000:
        res_score = weights["resolution"]
    elif h >= 1000:
        res_score = weights["resolution"] * 0.5
    elif h > 0:
        res_score = weights["resolution"] * 0.2
    else:
        res_score = 0

    sub_count = len(spec["subtitles"])
    sub_score = min(sub_count / 5.0, 1.0) * weights["subtitles"]

    components = {
        "dv_present": weights["dv_present"] if dv_present else 0,
        "dv_fourcc_ok": weights["dv_fourcc_ok"] if dv_fourcc_ok else 0,
        "hdr10_plus": weights["hdr10_plus"] if hdr10_plus else 0,
        "atmos": weights["atmos"] if atmos else 0,
        "bit_rate_mbps": round(br_score, 1),
        "resolution": round(res_score, 1),
        "subtitles": round(sub_score, 1),
    }
    total = round(sum(components.values()), 1)
    return {"total": total, "components": components, "max": sum(WEIGHTS.values())}


def _summary_row(spec: dict[str, Any]) -> dict[str, Any]:
    """Flatten to a row suitable for table display."""
    v = spec["video"]
    dv = v["dolby_vision"]
    atmos_track = next((a for a in spec["audio"] if a.get("atmos") is True), None)
    primary_audio = atmos_track or (spec["audio"][0] if spec["audio"] else {})

    return {
        "filename": spec["filename"],
        "path": spec["path"],
        "size": spec["container"].get("size_pretty"),
        "duration": spec["container"].get("duration_pretty"),
        "resolution": v.get("resolution"),
        "fps": v.get("fps"),
        "video_codec": v.get("codec"),
        "fourcc": v.get("fourcc"),
        "fourcc_ok": v.get("fourcc") in {"dvh1", "dvhe", "dvav", "dva1"},
        "video_bitrate_mbps": v.get("bit_rate_mbps"),
        "dv_profile": dv.get("profile") if dv.get("present") else None,
        "dv_compat": dv.get("compatibility_name") if dv.get("present") else None,
        "hdr10": v.get("hdr10", {}).get("mdcv") or v.get("hdr10", {}).get("cll"),
        "hdr10_plus": v.get("hdr10_plus"),
        "audio_codec": primary_audio.get("codec"),
        "audio_channels": primary_audio.get("channels"),
        "audio_atmos": primary_audio.get("atmos"),
        "audio_bitrate_kbps": primary_audio.get("bit_rate_kbps"),
        "subtitle_count": len(spec["subtitles"]),
        "warnings": [v["fourcc_warning"]] if v.get("fourcc_warning") else [],
    }


def _winners(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    """For each comparable dimension, return list of row indexes that tie for the win."""
    if not rows:
        return {}
    winners: dict[str, list[int]] = {}

    def best(key: str, *, falsy_is_loss: bool = True) -> list[int]:
        values = [(i, r.get(key)) for i, r in enumerate(rows)]
        present = [(i, v) for i, v in values if v is not None and (not falsy_is_loss or v)]
        if not present:
            return []
        if isinstance(present[0][1], bool):
            top = any(v for _, v in present)
            return [i for i, v in present if v == top and top]
        best_val = max(v for _, v in present)
        return [i for i, v in present if v == best_val]

    winners["video_bitrate_mbps"] = best("video_bitrate_mbps")
    winners["audio_bitrate_kbps"] = best("audio_bitrate_kbps")
    winners["audio_channels"] = best("audio_channels")
    winners["subtitle_count"] = best("subtitle_count")
    winners["fourcc_ok"] = best("fourcc_ok", falsy_is_loss=True)
    winners["dv_profile"] = [i for i, r in enumerate(rows) if r.get("dv_profile")]
    winners["hdr10_plus"] = [i for i, r in enumerate(rows) if r.get("hdr10_plus")]
    winners["audio_atmos"] = [i for i, r in enumerate(rows) if r.get("audio_atmos") is True]
    return winners


def compare_files(paths: list[str], weights: dict[str, float] | None = None) -> dict[str, Any]:
    specs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for p in paths:
        try:
            specs.append(inspect_file(p))
        except Exception as e:  # noqa: BLE001
            errors.append({"path": p, "error": str(e)})

    effective_weights = dict(WEIGHTS)
    if weights:
        for k, v in weights.items():
            if k in effective_weights:
                effective_weights[k] = float(v)

    rows = [_summary_row(s) for s in specs]
    scores = [_score(s, effective_weights) for s in specs]

    if scores:
        best_total = max(s["total"] for s in scores)
        for i, s in enumerate(scores):
            s["is_best"] = s["total"] == best_total

    return {
        "rows": rows,
        "scores": scores,
        "winners": _winners(rows),
        "specs": specs,  # full data, in case UI wants drill-down
        "errors": errors,
        "weights": effective_weights,
    }
