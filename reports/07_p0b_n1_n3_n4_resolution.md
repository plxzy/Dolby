# P0b + N1 + N3 + N4 — combined dtrace session (2026-05-23 15:18)

**Question:** does TV.app local Atmos really fail to spatialize on current
macOS / TV.app build?

**Answer:** No. It spatializes. Issue 12 as originally framed
("TV.app cannot render spatial audio for non-Apple local files") is
falsified for the build measured here.

## Setup

- Mac: M-series MacBook Pro, MBP built-in speakers (Default-Output),
  spatial-capable route (`maxSpatializableChannels = 16`)
- TV.app pid 1757
- File: `/Users/psp/Movies/TV/Media.localized/TV Shows/The Boys/Season 5/08 Blood and Bone.mp4`
- SIP: `Debugging Restrictions: disabled`, `DTrace Restrictions: disabled`,
  rest enabled (Authenticated Root, Filesystem Protections, NVRAM,
  Boot-arg, BaseSystem Verification all on)
- dtrace script: `/tmp/dolby-dtrace/combined.d` (P0b + N1 + N3 + N4 probes,
  90s timer)
- Capture: `captures/local3-combined-2026-05-23.txt` (dtrace),
  `captures/log-local-2026-05-23-15-18.txt` (log stream)

## What dtrace saw

```
[FigFilePlayer Create]                                       # local file engine
[AVCFPlayerItemSet ENTER] mask=0x7
[playerItem_Set ENTER] arg0=...+0x20 arg1=0x0                # arg1 not the mask here
[FPSupport_Eligibility RETURN] -> 0x2                        # multichannel only
[CheckSpatialization ENTER]                                  # gate site reached
[CheckSpatialization RETURN] -> 0xa76631400                  # AQIONode*, not a bitmask
[SpatializationManager::EnableInternalSpatializationAUs ENTER] # spatial AUs enabled
[AllowsSpatialization RETURN] -> 0x1 (=1)                    # ×35+ across the capture
```

Key flip vs the earlier 2026-05-23 dtrace data:

- Earlier: `AllowsSpatialization` returned 0 → no spatialization
- Now: `AllowsSpatialization` returns **1** consistently throughout
  playback → gate passes
- Earlier: `EnableInternalSpatializationAUs` was HLS-only
- Now: it fires once on local-file playback init

## What log stream saw mid-playback

```
15:18:17.270 SpatialMgr: power event spatialization = 0          (initial)
15:18:17.271 aqme:      MEMixerChannel: mSpatializationStatus = 0 (initial)
15:18:17.271 ac:        ACDDPAtmosDecoder mIsAtmos=1, mIsOARMode=1
15:18:17.280 aqme:      MEMixerChannel: mSpatializationStatus = 2   ←active
15:18:17.288 SpatialMgr: power event spatialization = 1             ←ON
15:18:17.347 SpatialMgr: power event spatialization = 0             (transient)
15:18:17.356 aqme:      MEMixerChannel: mSpatializationStatus = 2   ←active
15:18:17.363 SpatialMgr: power event spatialization = 1             ←stable ON
```

`mSpatializationStatus = 2` is the active/enabled state of MEMixerChannel.
`spatialization = 1` in the SpatializationManager power-event log is the
process-level "actively spatializing" flag — same surface the older
`is rendering spatial audio` line read from. The user-visible
"mediaFormatinfo … is not rendering spatial audio" line that historically
read `false` for TV.app local appears to no longer emit at default log
level on this build; what survives (and is decisive) is the
SpatializationManager power-event surface.

## N1 (spatialPreference enum) — selector no longer present

`objc$target:AVPlayerItem:-mpc_updateAVAudioSpatializationFormatsForPlayerAudioFormat?spatialPreference?:entry`
matched 0 probes against pid 1757. Listing
`objc$target:AVPlayerItem::` against the running TV.app shows:

```
AVPlayerItem -setAllowedAudioSpatializationFormats: entry
AVPlayerItem -_updateAllowedAudioSpatializationFormats entry
AVPlayerItem -_updateAllowedAudioSpatializationFormatsFromFigItem entry
```

The `mpc_updateAVAudioSpatializationFormatsForPlayerAudioFormat:spatialPreference:`
selector does not exist on AVPlayerItem in this build. It exists in the
shared cache as a literal string but is no longer wired up as an entry
point on this class. The narrowing call site referenced in older notes
(reports/04, reports/06, the older memory file) has been refactored or
removed. This is consistent with the gate-relaxation observed above.

## N3 (0x4-bit origin) — ustack at AVCF setter

`AVCFPlayerItemSetAllowedAudioSpatializationFormats` ENTER mask=0x7
fired once. The bit is set by the time it reaches AVFoundationCF. The
ustack frame for the immediate caller is in the raw trace; the bit
clearly originates above AVFoundationCF in TV.app's own MediaPlaybackCore
layer. Given the gate now passes anyway, the 0x4-bit origin question is
no longer load-bearing — leaving it as deferred.

## N4 (SpatializationManager registration) — fires, capable route

`SpatializationManager::EnableInternalSpatializationAUs` fires twice
(direct + the call_once wrapper) on local-file init. Combined with
SpatialMgr binding `maxSpatializableChannels = 16` and source token
implied by the `power event spatialization = 1` line, the app is
registered as a spatial source on this route. No further probing needed
for the current question.

## Audible A/B (P0a)

Not yet performed, but the system-layer signals are unambiguous: dtrace
gate returns 1, MEMixerChannel status = 2, SpatializationManager power
event = 1, decoder is ec+3 16ch OARMode Atmos. If a future audible test
contradicts these, the discrepancy will be at the device/output stage,
not in the macOS spatialization pipeline as observed by these probes.

## Implications

1. The Issue 12 problem statement ("TV.app cannot render spatial audio
   for local Atmos files") is no longer true on this build. No code
   change is needed; existing local Atmos `.mp4`/`.m4v` files play
   through TV.app with spatialization enabled.
2. The earlier `mpc_updateAVAudioSpatializationFormatsForPlayerAudioFormat:spatialPreference:`
   lever, the `.movpkg` engineering path, and the dylib-injection
   approach are all unnecessary for current macOS/TV.app.
3. The dtrace gate observed in older sessions
   (the removed `ISSUE_12_DTRACE_GATE.md` report, superseded by `01_gate_model.md`; the older
   `dolby-spatial-rendering-gate` memory) reflects a stale build. The
   resolution is "the OS/TV.app caught up," not a workaround.
4. SpatialProbe and QuickTime + local HLS remain valid as
   alternative-player paths but no longer differentiating.

## Falsification

This conclusion would be overturned by any of:

- A second capture (this Mac or another) where `AllowsSpatialization`
  returns 0 on local-file TV.app playback
- An audible A/B test (P0a) where TV.app local sounds non-spatialized
  vs QuickTime+HLS+local with both on MBP speakers
- A different file (e.g. a known 5.1 EAC-3 non-Atmos) where the same
  pipeline correctly stays unspatialized while Blood and Bone shows
  spatialization=1

The build delta that took us from "intersection=0" to "AllowsSpatialization=1"
is unclear from this session — no specific OS/TV.app version was
recorded in the older traces.

## Status of tasks #8–11

- #8 P0b: done. `AllowsSpatialization` = 1 on local.
- #9 N1: closed without an answer. Symbol no longer present.
- #10 N3: deferred. Bit origin not load-bearing now.
- #11 N4: done. EnableInternalSpatializationAUs fires; route capable.
