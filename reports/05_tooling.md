# Tooling

## `dolby-tool` CLI commands relevant to spatial investigation

| Command | What it does |
|---|---|
| `dolby-tool inspect <file>` | `ffprobe`+`mediainfo`-derived audio/video/DV/HDR summary |
| `dolby-tool tvlog-capture` | `log stream` capture + parser; default predicate is TV.app focused |
| `dolby-tool tvlog-capture --profile local-player` | Capture predicate widened for QuickTime/Safari/SpatialProbe processes |
| `dolby-tool tvlog-parse <file.json or raw log>` | Re-parse a saved log without re-capturing |
| `dolby-tool hls-prepare <local.movpkg> <out>` | Flatten a persisted-HLS `.movpkg` into a folder layout for serving |
| `dolby-tool hls-package <local media file> <out>` | Stream-copy an ordinary local file into fMP4 HLS with a separate audio group; auto-adds Apple-player-friendly tags |
| `dolby-tool hls-serve <folder> --open quicktime` | Range-capable localhost server + auto-open in QuickTime |
| `dolby-tool hls-package-capture <local media file> <out>` | Wraps the working order: package → serve → start capture → open QuickTime |
| `dolby-tool movpkg <path>` | Inspect an Apple `.movpkg`; emits `movpkg_atmos_variant_missing_or_incomplete` when Atmos advertised but not `Complete=YES` |
| `dolby-tool movpkg-scan <root>` | Batch-scan top-level downloaded packages; skips interstitial child packages |

### Recommended user workflow (today)

For local Atmos spatial playback on MBP speakers:

```
dolby-tool hls-package-capture "/Users/me/Movies/Blood and Bone.mp4" .tmp/blood
```

Plays in QuickTime via FigStreamPlayer with active Atmos / spatial mixer.
Audible result is the proof; the captured JSON gives the engineering
verdict.

For investigating Apple TV+ download completeness:

```
dolby-tool movpkg-scan "/Users/me/Movies/TV/Media.localized"
```

Reports per-package which streams are `Complete=YES` and whether an
advertised Atmos group actually has local segments.

### Parser fields surfaced from earlier work

The capture parser (`dolby_tool/capture/`) already emits:

- `pipeline_engine` (FigFilePlayer / FigStreamPlayer)
- `asbdFormatID` (`ec+3`, `qc+3`, `qaac`, etc.) separate from source/HLS
  audio label
- `app_spatial_rendering_ever_true` and `_last_state`
- `spatial_rendering_changed_count`
- Lower-level Atmos/OAR/SpatializationManager evidence
- `immersive_rendering_requested`
- `allowed_spatialization_formats_mask` (when emitted by `log stream`;
  the actual integer is more reliable from dtrace)

## `harness/SpatialProbe` — Swift AVPlayer diagnostic

`harness/Package.swift` + `harness/Sources/SpatialProbe/main.swift`. ~100
lines. Loads any URL/path into `AVURLAsset`, sets
`item.allowedAudioSpatializationFormats = .monoStereoAndMultichannel`
(0x3), plays via `AVPlayer`. Run for 25–30s with `harness/spatial-probes.d`
attached for the dtrace gate trace.

This is the experimental control that proves the limitation is TV.app
behavior. It is not a daily player.

## DTrace script

`harness/spatial-probes.d`. Attaches `pid$target` probes against the
resolved symbols listed in `04_paths_explored.md §E`. Requires SIP
debug + dtrace restrictions disabled:

```
csrutil enable --without debug --without dtrace   # from Recovery
```

Not needed for `log stream`-only investigations, so SIP can otherwise
remain fully enabled for normal work.
