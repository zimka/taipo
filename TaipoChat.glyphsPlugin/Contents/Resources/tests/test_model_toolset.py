# encoding: utf-8
"""
Unit tests for ``model_toolset`` introspection and schema generation.

Run from the repo root::

    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/test_model_toolset.py
"""

import os
import sys

_RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)

from tools.model_toolset import ModelToolSpec, ModelToolset, model_tool
from provider import _convert_tool_schema
from tests.fixtures.legacy_tool_schemas import LEGACY_TOOL_SCHEMAS


def _schema_diff_path(expected, actual, prefix=""):
    if expected == actual:
        return None
    if type(expected) is not type(actual):
        return "%s type mismatch: %r vs %r" % (prefix, type(expected), type(actual))
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            path = "%s.%s" % (prefix, key) if prefix else key
            if key not in expected:
                return "%s unexpected key in actual" % path
            if key not in actual:
                return "%s missing key in actual" % path
            sub = _schema_diff_path(expected[key], actual[key], path)
            if sub:
                return sub
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return "%s length mismatch: %d vs %d" % (prefix, len(expected), len(actual))
        for i, (exp_item, act_item) in enumerate(zip(expected, actual)):
            sub = _schema_diff_path(exp_item, act_item, "%s[%d]" % (prefix, i))
            if sub:
                return sub
        return None
    return "%s value mismatch: %r vs %r" % (prefix, expected, actual)


def assert_schemas_equal(expected, actual):
    diff = _schema_diff_path(expected, actual)
    assert diff is None, diff


def _test_parse_google_docstring_summary():
    summary, args = ModelToolSpec.parse_docstring(
        """
        First sentence of summary.
        Second sentence wrapped.

        Args:
            alpha: First parameter.
            beta: Second parameter
                with continuation.
        """
    )
    assert summary == "First sentence of summary. Second sentence wrapped."
    assert args == {
        "alpha": "First parameter.",
        "beta": "Second parameter with continuation.",
    }


def _test_parse_google_docstring_indented_block():
    summary, args = ModelToolSpec.parse_docstring(
        """
        Header line.

        Filter modes (all case-insensitive):
          By name substring:  filter='cy'
          By unicode hex:     filter='0402'

        Args:
            limit: Max entries.
        """
    )
    assert "Header line." in summary
    assert "Filter modes (all case-insensitive):" in summary
    assert "  By name substring:" in summary
    assert args == {"limit": "Max entries."}


def _test_parse_google_docstring_no_args():
    summary, args = ModelToolSpec.parse_docstring("Single-line tool description.")
    assert summary == "Single-line tool description."
    assert args == {}


def _test_signature_to_json_schema_required_optional():
    @model_tool
    def sample(self, name: str, master: str | None = None, limit: int = 200) -> str:
        """
        Summary.

        Args:
            name: Glyph name.
            master: Optional master.
            limit: Max entries.
        """

    schema = ModelToolSpec.input_schema_from_signature(sample, {"name": "Glyph name.", "master": "Optional master.", "limit": "Max entries."})
    assert schema == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Glyph name."},
            "master": {"type": "string", "description": "Optional master."},
            "limit": {"type": "integer", "description": "Max entries."},
        },
        "required": ["name"],
    }


def _test_signature_to_json_schema_arrays():
    @model_tool
    def sample(self, glyphs: list[str], nodes: list[int]) -> str:
        """
        Summary.

        Args:
            glyphs: Glyph names.
            nodes: Node indices.
        """

    schema = ModelToolSpec.input_schema_from_signature(sample, {"glyphs": "Glyph names.", "nodes": "Node indices."})
    assert schema["properties"]["glyphs"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Glyph names.",
    }
    assert schema["properties"]["nodes"] == {
        "type": "array",
        "items": {"type": "integer"},
        "description": "Node indices.",
    }
    assert schema["required"] == ["glyphs", "nodes"]


def _test_schema_parity_with_legacy_fixture():
    generated = ModelToolset.schemas()
    assert len(generated) == len(LEGACY_TOOL_SCHEMAS)
    for expected, actual in zip(LEGACY_TOOL_SCHEMAS, generated):
        assert expected["name"] == actual["name"]
        assert_schemas_equal(expected, actual)


def _test_schema_parity_per_tool():
    generated = {schema["name"]: schema for schema in ModelToolset.schemas()}
    for expected in LEGACY_TOOL_SCHEMAS:
        name = expected["name"]
        assert name in generated, "missing generated schema for %r" % name
        assert_schemas_equal(expected, generated[name])


def _test_provider_round_trip():
    generated = {schema["name"]: schema for schema in ModelToolset.schemas()}
    legacy = {schema["name"]: schema for schema in LEGACY_TOOL_SCHEMAS}
    for name in legacy:
        assert _convert_tool_schema(generated[name]) == _convert_tool_schema(legacy[name])


def _test_legacy_fixture_matches_model_toolset():
    assert_schemas_equal(LEGACY_TOOL_SCHEMAS, ModelToolset.schemas())


def run_tests():
    _test_parse_google_docstring_summary()
    _test_parse_google_docstring_indented_block()
    _test_parse_google_docstring_no_args()
    _test_signature_to_json_schema_required_optional()
    _test_signature_to_json_schema_arrays()
    _test_legacy_fixture_matches_model_toolset()
    _test_schema_parity_with_legacy_fixture()
    _test_schema_parity_per_tool()
    _test_provider_round_trip()
    print("Taipo Chat Resources/tests/test_model_toolset.py: run_tests() OK")


if __name__ == "__main__":
    run_tests()
