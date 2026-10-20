"""Fixtures shared by the analysis tests."""

from collections.abc import Callable

import pytest

from dolby_tool.analysis.models import AudioTrack, Container, DolbyVision, Hdr10, MediaSpec, SubtitleTrack, VideoSpec


@pytest.fixture
def make_spec() -> Callable[..., MediaSpec]:
    """Return a factory for `MediaSpec`s: a plain 1080p HEVC file unless told otherwise.

    ``make_spec("a.mkv", dv="8.1", fourcc="dvh1", bit_rate_mbps=40, atmos=True, subtitles=3)``
    """

    def make(
        filename: str = "clip.mp4",
        *,
        dv: str | None = None,
        fourcc: str = "hvc1",
        hdr10_plus: bool = False,
        hdr10: bool = False,
        height: int = 1080,
        bit_rate_mbps: float | None = None,
        atmos: bool | None = None,
        channels: int = 6,
        subtitles: int = 0,
        audio: bool = True,
    ) -> MediaSpec:
        width = height * 16 // 9
        dolby_vision = DolbyVision()
        if dv:
            dolby_vision = DolbyVision(
                present=True, profile_raw=int(dv.split(".")[0]), profile=dv, compatibility_name="HDR10"
            )
        tracks = [AudioTrack(index=1, codec="eac3", channels=channels, atmos=atmos, bit_rate_kbps=640)]
        return MediaSpec(
            path=f"/media/{filename}",
            filename=filename,
            container=Container(size_pretty="1.00 GB", duration_pretty="1m 00s"),
            video=VideoSpec(
                codec="hevc",
                fourcc=fourcc,
                width=width,
                height=height,
                resolution=f"{width}x{height}",
                fps=24.0,
                bit_rate_mbps=bit_rate_mbps,
                dolby_vision=dolby_vision,
                hdr10=Hdr10(mdcv=hdr10, cll=hdr10),
                hdr10_plus=hdr10_plus,
            ),
            audio=tracks if audio else [],
            subtitles=[SubtitleTrack(index=2 + i, codec="subrip") for i in range(subtitles)],
        )

    return make
