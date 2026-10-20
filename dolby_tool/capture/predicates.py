"""`log stream` predicates selecting the processes that emit playback telemetry.

`PREDICATE` covers the five processes involved in TV.app playback on macOS:
mediaplaybackd, VTDecoderXPCService, coremediaxpc, audiomxd and TV itself. Together
they span both the HLS streaming path (FigStreamPlayer: Apple TV+ and downloaded
.movpkg) and the local library path (FigFilePlayer).

`LOCAL_PLAYER_PREDICATE` adds QuickTime Player and Safari/WebKit, the controls used
to compare TV.app against other players on the same content and audio route.
"""

PREDICATE = (
    'process == "mediaplaybackd"'
    ' OR process == "VTDecoderXPCService"'
    ' OR process == "coremediaxpc"'
    ' OR process == "audiomxd"'
    ' OR process == "TV"'
)

LOCAL_PLAYER_PREDICATE = (
    PREDICATE + ' OR process == "QuickTime Player"' + ' OR process == "Safari"' + ' OR process CONTAINS "WebKit"'
)
