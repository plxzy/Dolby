from pathlib import Path

from dolby_tool.hls.movpkg import (
    analyze_movpkg,
    find_top_level_movpkgs,
    movpkg_scan_markdown,
    movpkg_summary_markdown,
    scan_movpkgs,
)


def _write_stream(
    root: Path,
    rel: str,
    *,
    group: int,
    complete: str = "YES",
    bytes_stored: int = 1000,
    xml_group: bool = True,
) -> None:
    stream_dir = root / rel
    stream_dir.mkdir(parents=True, exist_ok=True)
    network_url = f"https://download-ap-aoc.tv.apple.com/audio_en_gr{group}_x.m3u8?g={group}" if xml_group else ""
    (stream_dir / "StreamInfoBoot.xml").write_text(
        "\n".join(
            [
                "<StreamInfo>",
                f"  <ID>{stream_dir.name}</ID>",
                f"  <NetworkURL>{network_url}</NetworkURL>",
                f"  <Path>audio_en_gr{group}_x.m3u8</Path>",
                f"  <Complete>{complete}</Complete>",
                f"  <MediaBytesStored>{bytes_stored}</MediaBytesStored>",
                "</StreamInfo>",
            ]
        ),
        encoding="utf-8",
    )
    (stream_dir / "00000.frag").write_bytes(b"frag")
    (stream_dir / "init.initfrag").write_bytes(b"init")
    (stream_dir / "media.m3u8").write_text(
        f'#EXTM3U\n#EXT-X-MAP:URI="https://download-ap-aoc.tv.apple.com/audio_en_gr{group}_x-0.mp4"\n',
        encoding="utf-8",
    )


def _write_master(root: Path) -> None:
    default_role = "com.apple.amp.tv.is-default,public.original-content"
    (root / "playlist.m3u8").write_text(
        "\n".join(
            [
                "#EXTM3U",
                '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio-stereo-128_download-ap-aoc.tv.apple.com",'
                f'NAME="English",LANGUAGE="en",CHARACTERISTICS="{default_role}",'
                'CHANNELS="2",URI="stereo.m3u8?g=128"',
                '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio-atmos_download-ap-aoc.tv.apple.com",'
                f'NAME="English",LANGUAGE="en",CHARACTERISTICS="{default_role}",'
                'CHANNELS="16/JOC",URI="atmos.m3u8?g=2448"',
                '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio-atmos_download-ap-aoc.tv.apple.com",'
                'NAME="English ",LANGUAGE="en",CHARACTERISTICS="public.accessibility.describes-video",'
                'CHANNELS="16/JOC",URI="atmos-ad.m3u8?g=2448"',
                '#EXT-X-STREAM-INF:BANDWIDTH=1000,CODECS="dvh1.05.01,mp4a.40.2",AUDIO="audio-stereo-128_download-ap-aoc.tv.apple.com"',
                "video-stereo.m3u8?g=128",
                '#EXT-X-STREAM-INF:BANDWIDTH=1000,CODECS="dvh1.05.01,ec-3",AUDIO="audio-atmos_download-ap-aoc.tv.apple.com"',
                "video-atmos.m3u8?g=2448",
            ]
        ),
        encoding="utf-8",
    )


def test_movpkg_inventory_marks_interstitial_atmos_as_missing_for_main_episode(tmp_path):
    movpkg = tmp_path / "Episode.movpkg"
    movpkg.mkdir()
    _write_master(movpkg)
    _write_stream(movpkg, "1-main-stereo", group=128, complete="YES", bytes_stored=31_684_679)
    _write_stream(movpkg, "1-main-atmos-incomplete", group=2448, complete="NO", bytes_stored=0, xml_group=False)
    _write_stream(
        movpkg,
        "InterstitialAssets/preroll.movpkg/1-interstitial-atmos",
        group=2448,
        complete="YES",
        bytes_stored=450_734,
    )

    summary = analyze_movpkg(
        movpkg,
        selected_group="audio-stereo-128_download-ap-aoc.tv.apple.com",
    )

    assert summary["inventory_verdict"] == "movpkg_atmos_variant_missing_or_incomplete"
    atmos_rows = [row for row in summary["audio_table"] if row["group"] == "audio-atmos_download-ap-aoc.tv.apple.com"]
    assert atmos_rows
    assert all(row["has_complete_main_stream"] is False for row in atmos_rows)
    assert any(row["scope"] == "interstitial/main" for row in atmos_rows)
    assert any(row["complete"] == "NO" for row in atmos_rows)
    assert "inventory verdict: `movpkg_atmos_variant_missing_or_incomplete`" in movpkg_summary_markdown(summary)


def test_movpkg_inventory_detects_complete_main_atmos_not_selected(tmp_path):
    movpkg = tmp_path / "Episode.movpkg"
    movpkg.mkdir()
    _write_master(movpkg)
    _write_stream(movpkg, "1-main-stereo", group=128, complete="YES", bytes_stored=31_684_679)
    _write_stream(movpkg, "1-main-atmos", group=2448, complete="YES", bytes_stored=55_000_000)

    summary = analyze_movpkg(
        movpkg,
        selected_group="audio-stereo-128_download-ap-aoc.tv.apple.com",
    )

    assert summary["inventory_verdict"] == "movpkg_atmos_variant_present_but_not_selected"
    atmos_rows = [row for row in summary["audio_table"] if row["group"] == "audio-atmos_download-ap-aoc.tv.apple.com"]
    assert any(row["has_complete_main_stream"] is True for row in atmos_rows)


def test_movpkg_scan_skips_interstitial_child_packages(tmp_path):
    season = tmp_path / "Season"
    episode = season / "Episode.movpkg"
    child = episode / "InterstitialAssets" / "preroll.movpkg"
    episode.mkdir(parents=True)
    child.mkdir(parents=True)
    _write_master(episode)
    _write_master(child)
    _write_stream(episode, "1-main-stereo", group=128, complete="YES", bytes_stored=31_684_679)
    _write_stream(child, "1-interstitial-atmos", group=2448, complete="YES", bytes_stored=450_734)

    assert find_top_level_movpkgs(season) == [episode]

    scan = scan_movpkgs(season)

    assert scan["package_count"] == 1
    assert scan["packages"][0]["movpkg"] == str(episode)
    assert scan["packages"][0]["inventory_verdict"] == "movpkg_atmos_variant_missing_or_incomplete"
    assert "audio-stereo-128_download-ap-aoc.tv.apple.com" in scan["packages"][0]["complete_main_audio"][0]
    rendered = movpkg_scan_markdown(scan)
    assert "top-level packages: 1" in rendered
    assert "No complete main Atmos" in rendered


def test_movpkg_scan_root_inside_a_package_still_finds_its_children(tmp_path):
    episode = tmp_path / "Episode.movpkg"
    interstitials = episode / "InterstitialAssets"
    preroll = interstitials / "preroll.movpkg"
    nested = preroll / "Extra.movpkg"
    nested.mkdir(parents=True)

    assert find_top_level_movpkgs(interstitials) == [preroll]
