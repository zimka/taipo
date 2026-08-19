# encoding: utf-8
"""Tests for kerning tools v2 (mock font, no Glyphs SDK)."""

from __future__ import annotations

import json
import os
import sys

_RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)

from tests.mock import (
    build_blocking_zero_mock_font,
    build_class_slot_mock_font,
    build_ghe_cy_mock_font,
    build_hunck_like_mock_font,
    build_kerning_mock_font,
    build_pair_only_mock_font,
    build_warning_mock_font,
)
from tools.kerning import (
    _ID_INDEX_CACHE,
    _TABLE_CACHE,
    handle_edit_kerning_pairs,
    handle_find_kerning_rules,
    handle_read_kerning_pairs,
)


def _read(font, pairs, master="Regular"):
    out = handle_read_kerning_pairs({"master": master, "pairs": pairs}, None, font)
    if out.startswith("[error]"):
        raise AssertionError(out)
    return json.loads(out)


def _find(font, target, **kwargs):
    args = {"master": "Regular", "target": target, **kwargs}
    out = handle_find_kerning_rules(args, None, font)
    if out.startswith("[error]"):
        raise AssertionError(out)
    return json.loads(out)


def _test_pair_exists_parent_root():
    font = build_pair_only_mock_font()
    payload = _read(font, [{"left": "T", "right": "A"}])
    row = payload["results"][0]
    assert row["stored_value"] == -100
    assert row["effective_value"] == -100
    assert row["parent"] == {"left": None, "right": None, "stored_value": 0}
    assert "WARNING" not in row


def _test_empty_glyph_inherits_class():
    font = build_hunck_like_mock_font()
    payload = _read(font, [{"left": "T", "right": "Aacute"}])
    row = payload["results"][0]
    assert row["stored_value"] is None
    assert row["effective_value"] == -100
    assert row["parent"] == {"left": "T", "right": "@A", "stored_value": -100}


def _test_read_blocking_zero():
    font = build_blocking_zero_mock_font()
    payload = _read(font, [{"left": "T", "right": "Aacute"}])
    row = payload["results"][0]
    assert row["stored_value"] == 0
    assert row["effective_value"] == 0
    assert row["parent"]["stored_value"] == -100


def _test_redundant_pair_and_class():
    font = build_hunck_like_mock_font()
    payload = _read(font, [{"left": "T", "right": "A"}])
    row = payload["results"][0]
    assert row["stored_value"] == -100
    assert row["parent"]["stored_value"] == -100
    assert row["parent"]["right"] == "@A"


def _test_class_slot_parent_class_class():
    font = build_class_slot_mock_font()
    payload = _read(font, [{"left": "T", "right": "@A"}])
    row = payload["results"][0]
    assert row["stored_value"] is None
    assert row["effective_value"] == -49
    assert row["parent"] == {"left": "@T", "right": "@A", "stored_value": -49}


def _test_batch_results_and_errors():
    font = build_hunck_like_mock_font()
    out = handle_read_kerning_pairs(
        {
            "master": "Regular",
            "pairs": [
                {"left": "T", "right": "Aacute"},
                {"left": "mY_Non_existing_glyph", "right": "A"},
            ],
        },
        None,
        font,
    )
    payload = json.loads(out)
    assert len(payload["results"]) == 1
    assert len(payload["errors"]) == 1
    assert "unknown glyph" in payload["errors"][0]["error"]


def _test_reject_mmk_operand():
    font = build_hunck_like_mock_font()
    out = handle_read_kerning_pairs(
        {"master": "Regular", "pairs": [{"left": "@MMK_L_A", "right": "T"}]},
        None,
        font,
    )
    payload = json.loads(out)
    assert payload["errors"]
    assert "use @A not @MMK_L_A" in payload["errors"][0]["error"]


def _test_read_after_edit_round_trip():
    font = build_hunck_like_mock_font()
    handle_edit_kerning_pairs(
        {
            "master": "Regular",
            "changes": [{"left": "T", "right": "Aacute", "stored_value": -120}],
        },
        None,
        font,
    )
    row = _read(font, [{"left": "T", "right": "Aacute"}])["results"][0]
    assert row["stored_value"] == -120
    assert row["effective_value"] == -120


def _test_cascade_matches_kerning_for_pair():
    font = build_hunck_like_mock_font()
    payload = _read(font, [{"left": "T", "right": "Aacute"}])
    assert "WARNING" not in payload["results"][0]


def _test_warning_on_mismatch():
    font = build_warning_mock_font()
    row = _read(font, [{"left": "T", "right": "Aacute"}])["results"][0]
    assert row["effective_value"] == 0
    assert row["WARNING"] == (
        "EXPECTED EFFECTIVE_VALUE TO BE -100 BASED ON KERNING TABLE BUT FOUND 0 IN GLYPHS API"
    )


def _test_edit_set_and_remove():
    font = build_hunck_like_mock_font()
    handle_edit_kerning_pairs(
        {
            "master": "Regular",
            "changes": [{"left": "T", "right": "Aacute", "stored_value": -88}],
        },
        None,
        font,
    )
    row = _read(font, [{"left": "T", "right": "Aacute"}])["results"][0]
    assert row["stored_value"] == -88

    handle_edit_kerning_pairs(
        {
            "master": "Regular",
            "changes": [{"left": "T", "right": "Aacute", "stored_value": None}],
        },
        None,
        font,
    )
    row = _read(font, [{"left": "T", "right": "Aacute"}])["results"][0]
    assert row["stored_value"] is None
    assert row["effective_value"] == -100


def _test_edit_class_class_and_class_glyph():
    font = build_kerning_mock_font()
    out = handle_edit_kerning_pairs(
        {
            "master": "Regular",
            "changes": [{"left": "@T", "right": "@A", "stored_value": -55}],
        },
        None,
        font,
    )
    assert "edit_kerning_pairs" in out
    assert "@MMK_L_T" in out or "T" in out

    out = handle_edit_kerning_pairs(
        {
            "master": "Regular",
            "changes": [{"left": "@T", "right": "Aacute", "stored_value": -70}],
        },
        None,
        font,
    )
    assert "high" in out or "class-glyph" in out


def _test_edit_max_changes():
    font = build_hunck_like_mock_font()
    changes = [{"left": "T", "right": "A", "stored_value": 0}] * 21
    out = handle_edit_kerning_pairs({"master": "Regular", "changes": changes}, None, font)
    assert out.startswith("[error]")
    assert "20" in out


def _test_edit_reject_mmk():
    font = build_hunck_like_mock_font()
    out = handle_edit_kerning_pairs(
        {
            "master": "Regular",
            "changes": [{"left": "@MMK_L_T", "right": "A", "stored_value": -10}],
        },
        None,
        font,
    )
    assert "use @T not @MMK_L_T" in out


def _test_find_ghe_cy_empty_neighbours():
    font = build_ghe_cy_mock_font()
    payload = _find(font, "Ghe-cy")
    assert payload["right_kerning_group"] == "@T"
    assert payload["left_kerning_group"] == "@I"
    assert payload.get("left") is None
    assert payload.get("right") is None


def _test_find_class_left_neighbour():
    font = build_ghe_cy_mock_font()
    payload = _find(font, "@T")
    assert payload["left"]["class"] == ["@V"]
    assert "Ghe-cy" in payload["right_kerning_group"]


def _test_find_class_right_neighbour():
    font = build_ghe_cy_mock_font()
    payload = _find(font, "@T")
    assert payload["right"]["class"] == ["@V"]


def _test_find_non_transitivity():
    font = build_ghe_cy_mock_font()
    payload = _find(font, "@T")
    left_classes = payload.get("left", {}).get("class", [])
    right_classes = payload.get("right", {}).get("class", [])
    assert "@A" not in left_classes
    assert "@A" not in right_classes


def _test_find_side_and_neighbor_kind_filters():
    font = build_ghe_cy_mock_font()
    payload = _find(font, "@T", side="left", neighbor_kind="class")
    assert "right" not in payload
    assert payload["left"]["class"] == ["@V"]
    assert "glyph" not in payload["left"]


def _test_find_glyph_keyed_row():
    font = build_hunck_like_mock_font()
    payload = _find(font, "T")
    assert "A" in payload["right"]["glyph"]


def _reset_kerning_cache():
    _TABLE_CACHE.clear()
    _ID_INDEX_CACHE.clear()


def run_tests():
    tests = [
        _test_pair_exists_parent_root,
        _test_empty_glyph_inherits_class,
        _test_read_blocking_zero,
        _test_redundant_pair_and_class,
        _test_class_slot_parent_class_class,
        _test_batch_results_and_errors,
        _test_reject_mmk_operand,
        _test_read_after_edit_round_trip,
        _test_cascade_matches_kerning_for_pair,
        _test_warning_on_mismatch,
        _test_edit_set_and_remove,
        _test_edit_class_class_and_class_glyph,
        _test_edit_max_changes,
        _test_edit_reject_mmk,
        _test_find_ghe_cy_empty_neighbours,
        _test_find_class_left_neighbour,
        _test_find_class_right_neighbour,
        _test_find_non_transitivity,
        _test_find_side_and_neighbor_kind_filters,
        _test_find_glyph_keyed_row,
    ]
    for test in tests:
        _reset_kerning_cache()
        test()
    print("tests/test_kerning.py: OK")


if __name__ == "__main__":
    run_tests()
