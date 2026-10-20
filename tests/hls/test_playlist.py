import pytest

from dolby_tool.hls.playlist import first_segment_uri, parse_attributes, playlist_references, read_playlist


def test_parse_attributes_unquotes_values_and_keeps_commas_inside_quotes():
    attrs = parse_attributes(
        'TYPE=AUDIO,GROUP-ID="atmos",NAME="English ",CHARACTERISTICS="a.is-default,b.original",CHANNELS="16/JOC"'
    )

    assert attrs == {
        "TYPE": "AUDIO",
        "GROUP-ID": "atmos",
        "NAME": "English ",
        "CHARACTERISTICS": "a.is-default,b.original",
        "CHANNELS": "16/JOC",
    }


def test_read_playlist_strips_nul_padding_to_one_newline(tmp_path):
    path = tmp_path / "p.m3u8"
    path.write_text("#EXTM3U\nseg.m4s\n\x00\x00", encoding="utf-8")

    assert read_playlist(path) == "#EXTM3U\nseg.m4s\n"


def test_playlist_references_separates_init_maps_from_uri_lines(tmp_path):
    path = tmp_path / "p.m3u8"
    path.write_text(
        '#EXTM3U\n#EXT-X-MAP:URI="init.mp4",BYTERANGE="5@0"\n#EXTINF:1.0,\n seg_0.m4s \n\n#EXTINF:1.0,\nseg_1.m4s\n',
        encoding="utf-8",
    )

    references = playlist_references(path)

    assert references.init_maps == ["init.mp4"]
    assert references.segments == ["seg_0.m4s", "seg_1.m4s"]
    assert first_segment_uri(path) == "seg_0.m4s"


def test_first_segment_uri_requires_a_uri_line(tmp_path):
    path = tmp_path / "p.m3u8"
    path.write_text("#EXTM3U\n#EXT-X-ENDLIST\n", encoding="utf-8")

    with pytest.raises(ValueError, match="No segment URI"):
        first_segment_uri(path)
