# encoding: utf-8
"""
Unit tests for glyph metadata schema, dump, and apply logic.

Run from the repo root::

    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/test_glyph_metadata_schema.py
"""

import json
import os
import sys

_RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)

from tools.glyph_metadata import (
    apply_glyph_metadata,
    dump_glyph_metadata,
    handle_get_glyph_metadata,
    json_schema_from_dataclass,
    metadata_json_schema,
    writable_field_names,
    GlyphMetadata,
)
from tools.model_toolset import ModelToolset
from tests.mock import _MockGlyph, _MockGlyphsList, build_mock_font


def _test_full_and_patch_schema_share_writable_properties():
    full = metadata_json_schema(mode="full")
    patch = metadata_json_schema(mode="patch")
    writable = writable_field_names()
    for key in writable:
        assert full["properties"][key] == patch["properties"][key]
    assert "name" in full["properties"]
    assert "name" not in patch["properties"]


def _test_patch_schema_has_no_required():
    patch = metadata_json_schema(mode="patch")
    assert "required" not in patch


def _test_dump_keys_match_full_schema():
    font = build_mock_font()
    glyph = font.glyphs["Dje-cy"]
    dumped = dump_glyph_metadata(glyph)
    props = metadata_json_schema(mode="full")["properties"]
    assert set(dumped.keys()) == set(props.keys())
    assert dumped["name"] == "Dje-cy"
    assert dumped["unicode"] == "0402"
    assert dumped["export"] is True
    assert dumped["note"] is None


def _test_dump_empty_unicode_is_null():
    font = build_mock_font()
    glyph = _MockGlyph(".notdef", "018F", font.glyphs["Dje-cy"].layers._by_id)
    dumped = dump_glyph_metadata(glyph)
    assert dumped["unicode"] == "018F"


def _test_apply_clear_unicode():
    font = build_mock_font()
    glyph = _MockGlyph(".notdef", "018F", font.glyphs["Dje-cy"].layers._by_id)
    font.glyphs = _MockGlyphsList(list(font.glyphs) + [glyph])
    out = apply_glyph_metadata(glyph, {"unicode": None}, font)
    assert "unicode" in out
    assert glyph.unicode == ""
    assert glyph.unicodes == []


def _test_apply_clear_unicode_when_only_unicodes_list():
    font = build_mock_font()
    glyph = _MockGlyph(".notdef", "", font.glyphs["Dje-cy"].layers._by_id)
    glyph.unicodes = ["018F"]
    font.glyphs = _MockGlyphsList(list(font.glyphs) + [glyph])
    assert dump_glyph_metadata(glyph)["unicode"] == "018F"
    out = apply_glyph_metadata(glyph, {"unicode": None}, font)
    assert "unicode" in out
    assert glyph.unicodes == []
    assert dump_glyph_metadata(glyph)["unicode"] is None


def _test_apply_rejects_notdef_assign():
    font = build_mock_font()
    glyph = _MockGlyph(".notdef", "", font.glyphs["Dje-cy"].layers._by_id)
    font.glyphs = _MockGlyphsList(list(font.glyphs) + [glyph])
    out = apply_glyph_metadata(glyph, {"unicode": "0041"}, font)
    assert out.startswith("[error]")
    assert ".notdef" in out


def _test_apply_rejects_unknown_keys():
    font = build_mock_font()
    glyph = font.glyphs["Dje-cy"]
    out = apply_glyph_metadata(glyph, {"name": "Other"}, font)
    assert out.startswith("[error]")
    assert "name" in out


def _test_apply_rejects_duplicate_unicode():
    font = build_mock_font()
    glyph = font.glyphs["Dje-cy"]
    other = _MockGlyph("Other", "0402", glyph.layers._by_id)
    font.glyphs = _MockGlyphsList([glyph, other])
    out = apply_glyph_metadata(glyph, {"unicode": "0402"}, font)
    assert out.startswith("[error]")
    assert "already assigned" in out


def _test_set_tool_changes_schema_matches_patch():
    generated = {schema["name"]: schema for schema in ModelToolset.schemas()}
    changes = generated["edit_glyph_metadata"]["input_schema"]["properties"]["changes"]
    patch = metadata_json_schema(mode="patch")
    assert changes["type"] == patch["type"]
    assert changes["properties"] == patch["properties"]
    assert changes["additionalProperties"] == patch["additionalProperties"]


def _test_dataclass_round_trip_dict():
    meta = GlyphMetadata(name="A", unicode="0041", export=False, note="x")
    payload = json_schema_from_dataclass(GlyphMetadata, mode="full")
    assert "unicode" in payload["properties"]
    assert payload["properties"]["unicode"]["type"] == ["string", "null"]
    assert "leftKerningGroup" in payload["properties"]


def _test_dump_kerning_groups():
    font = build_mock_font()
    layer_map = font.glyphs["Dje-cy"].layers._by_id
    glyph_a = _MockGlyph("A", "0041", layer_map)
    glyph_a.leftKerningGroup = "A"
    glyph_a.rightKerningGroup = "A"
    glyph_acute = _MockGlyph("Aacute", "00C1", layer_map)
    font.glyphs = _MockGlyphsList([glyph_a, glyph_acute])
    dumped_a = dump_glyph_metadata(glyph_a)
    dumped_acute = dump_glyph_metadata(glyph_acute)
    assert dumped_a["leftKerningGroup"] == "@A"
    assert dumped_a["rightKerningGroup"] == "@A"
    assert dumped_acute["leftKerningGroup"] is None
    assert dumped_acute["rightKerningGroup"] is None


def _test_apply_kerning_groups():
    font = build_mock_font()
    layer_map = font.glyphs["Dje-cy"].layers._by_id
    glyph_a = _MockGlyph("A", "0041", layer_map)
    glyph_a.leftKerningGroup = "A"
    glyph_a.rightKerningGroup = "A"
    glyph_acute = _MockGlyph("Aacute", "00C1", layer_map)
    font.glyphs = _MockGlyphsList([glyph_a, glyph_acute])
    out = apply_glyph_metadata(
        glyph_acute,
        {"leftKerningGroup": "@A", "rightKerningGroup": "@A"},
        font,
    )
    assert "leftKerningGroup" in out
    assert glyph_acute.leftKerningGroup == "A"
    assert glyph_acute.rightKerningGroup == "A"
    dumped = dump_glyph_metadata(glyph_acute)
    assert dumped["leftKerningGroup"] == "@A"
    assert dumped["rightKerningGroup"] == "@A"


def _test_apply_metrics_key_links_to_base():
    font = build_mock_font()
    layer_map = font.glyphs["Dje-cy"].layers._by_id
    glyph_a = _MockGlyph("A", "0041", layer_map)
    glyph_acute = _MockGlyph("Aacute", "00C1", layer_map)
    font.glyphs = _MockGlyphsList([glyph_a, glyph_acute])
    out = apply_glyph_metadata(
        glyph_acute,
        {
            "widthMetricsKey": "=A",
            "leftMetricsKey": "=A",
            "rightMetricsKey": "=A",
        },
        font,
    )
    assert "widthMetricsKey" in out
    assert glyph_acute.widthMetricsKey == "=A"
    assert dump_glyph_metadata(glyph_acute)["leftMetricsKey"] == "=A"


def _test_apply_rejects_missing_metrics_glyph_ref():
    font = build_mock_font()
    glyph = font.glyphs["Dje-cy"]
    out = apply_glyph_metadata(glyph, {"leftMetricsKey": "=MissingGlyph"}, font)
    assert out.startswith("[error]")
    assert "MissingGlyph" in out


def _test_apply_category_sets_store_flag():
    font = build_mock_font()
    glyph = font.glyphs["Dje-cy"]
    out = apply_glyph_metadata(glyph, {"category": "Letter"}, font)
    assert "category" in out
    assert glyph.storeCategory is True
    assert glyph.category == "Letter"
    assert dump_glyph_metadata(glyph)["category"] == "Letter"


def _test_apply_clear_category_clears_store_flag():
    font = build_mock_font()
    glyph = font.glyphs["Dje-cy"]
    glyph.storeCategory = True
    glyph.category = "Letter"
    out = apply_glyph_metadata(glyph, {"category": None}, font)
    assert "cleared" in out
    assert glyph.storeCategory is False
    assert glyph.category == ""
    assert dump_glyph_metadata(glyph)["category"] is None


def _test_apply_case_round_trip():
    font = build_mock_font()
    glyph = font.glyphs["Dje-cy"]
    out = apply_glyph_metadata(glyph, {"case": "uppercase"}, font)
    assert "case" in out
    assert glyph.storeCase is True
    assert glyph.case == 1
    assert dump_glyph_metadata(glyph)["case"] == "uppercase"
    out = apply_glyph_metadata(glyph, {"case": None}, font)
    assert "cleared" in out
    assert glyph.storeCase is False
    assert dump_glyph_metadata(glyph)["case"] is None


def _test_spacing_with_master():
    from tests.mock import _MockGlyph, _MockLayer, _MockMaster, _MockFont, _MockGlyphsList

    m = _MockMaster("M_REG", "Regular")
    layer = _MockLayer(width=712, paths=[], lsb=28, rsb=0)
    glyph = _MockGlyph("A", "0041", {m.id: layer})
    font = _MockFont()
    font.masters = [m]
    font.glyphs = _MockGlyphsList([glyph])

    out = handle_get_glyph_metadata({"glyph": "A", "master": "Regular"}, None, font)
    payload = json.loads(out)
    assert payload["spacing"]["width"] == 712
    assert payload["spacing"]["lsb"] == 28

    edit_out = apply_glyph_metadata(
        glyph, {"width": 720}, font, master=m
    )
    assert "width" in edit_out
    assert layer.width == 720


def run_tests():
    _test_full_and_patch_schema_share_writable_properties()
    _test_patch_schema_has_no_required()
    _test_dump_keys_match_full_schema()
    _test_dump_empty_unicode_is_null()
    _test_apply_clear_unicode()
    _test_apply_clear_unicode_when_only_unicodes_list()
    _test_apply_rejects_notdef_assign()
    _test_apply_rejects_unknown_keys()
    _test_apply_rejects_duplicate_unicode()
    _test_set_tool_changes_schema_matches_patch()
    _test_dataclass_round_trip_dict()
    _test_dump_kerning_groups()
    _test_apply_kerning_groups()
    _test_apply_metrics_key_links_to_base()
    _test_apply_rejects_missing_metrics_glyph_ref()
    _test_apply_category_sets_store_flag()
    _test_apply_clear_category_clears_store_flag()
    _test_apply_case_round_trip()
    _test_spacing_with_master()
    print("Taipo Chat Resources/tests/test_glyph_metadata_schema.py: run_tests() OK")


if __name__ == "__main__":
    run_tests()
