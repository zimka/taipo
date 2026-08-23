# encoding: utf-8
"""
Smoke tests for ``ModelToolset`` — mirrors tool scenarios from ``tests/smoke.py``.

Run from the repo root::

    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/smoke_model_toolset.py
"""

import os
import sys

_RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)

from tools.model_toolset import ModelToolset
from tests.mock import (
    _MockComponent,
    _MockGlyph,
    _MockGlyphsList,
    _MockLayer,
    build_composite_mock_font,
    build_mock_font,
)


def _ctx(font):
    import tools

    return tools.ToolContext(font_provider=lambda: font, session_mode="edit")


def _test_tool_handlers_pure():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    out = toolset.execute("list_masters", {})
    assert "Regular" in out and "Bold" in out and "M_BOLD" in out

    out = toolset.execute("list_glyphs", {"filter": "dje"})
    assert "Dje-cy" in out and "U+0402" in out

    out = toolset.execute("list_glyphs", {"filter": "Ђ"})
    assert "Dje-cy" in out, "character filter 'Ђ' should match U+0402"

    out = toolset.execute("list_glyphs", {"filter": "0402"})
    assert "Dje-cy" in out

    out = toolset.execute("get_glyph", {"name": "Dje-cy", "master": "Bold"})
    assert "glyph: Dje-cy" in out
    assert "master: Bold" in out
    assert "paths: 1" in out
    assert "x=100 y=1230" in out
    assert "x=100 y=1420" in out

    out = toolset.execute(
        "move_nodes",
        {
            "glyph": "Dje-cy",
            "master": "Bold",
            "path": 0,
            "nodes": [0, 1],
            "dx": 0,
            "dy": -72,
        },
    )
    assert "Moved 2 node(s)" in out
    layer = font.glyphs["Dje-cy"].layers["M_BOLD"]
    ys = sorted(int(n.position.y) for n in layer.paths[0].nodes)
    assert ys == [1158, 1158, 1420, 1420], ys

    out = toolset.execute(
        "move_nodes",
        {
            "glyph": "Dje-cy",
            "master": "Bold",
            "path": 0,
            "nodes": [],
            "dx": 0,
            "dy": 1,
        },
    )
    assert out.startswith("[error]")

    out = toolset.execute(
        "move_nodes",
        {
            "glyph": "Dje-cy",
            "master": "Bold",
            "path": 0,
            "nodes": [99],
            "dx": 0,
            "dy": -10,
        },
    )
    assert out.startswith("[error]")

    out = toolset.execute("get_glyph", {"name": "Missing"})
    assert out.startswith("[error]")

    out = toolset.execute("unknown_tool", {})
    assert out.startswith("[error] Unknown tool")


def _test_render_specimen_diff_unknown_id():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))
    out = toolset.execute("render_specimen_diff", {"reference_render_specimen_id": 1})
    assert out.startswith("[error]") and "render_specimen_id" in out.lower(), out


def _assert_render_specimen_ok(out, *, master="Bold"):
    assert isinstance(out, list) and len(out) == 2, out
    header, png_bytes = out
    assert isinstance(header, str), type(header)
    assert "render_specimen_id=" in header, header
    assert header.splitlines()[1].startswith("render_specimen"), header
    assert master in header, header
    assert isinstance(png_bytes, bytes), type(png_bytes)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", png_bytes[:16]
    return int(header.split("render_specimen_id=", 1)[1].split(None, 1)[0].split("\n", 1)[0])


def _assert_render_specimen_diff_ok(out, *, reference_id, master="Bold"):
    assert isinstance(out, list) and len(out) == 2, out
    header, png_bytes = out
    assert isinstance(header, str), type(header)
    assert header.startswith("render_specimen_diff"), header
    assert "reference_id=%d" % reference_id in header, header
    assert master in header, header
    assert isinstance(png_bytes, bytes), type(png_bytes)
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", png_bytes[:16]


def _test_render_specimen_diff_sequential():
    from tests._render_stubs import stub_render_with_spec_deps

    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    out = toolset.execute("render_specimen_diff", {"reference_render_specimen_id": 1})
    assert out.startswith("[error]"), out

    with stub_render_with_spec_deps():
        out = toolset.execute("render_specimen", {"text": "Ђ", "master": "Bold"})
        rid = _assert_render_specimen_ok(out)
        out = toolset.execute(
            "render_specimen_diff", {"reference_render_specimen_id": rid}
        )
        _assert_render_specimen_diff_ok(out, reference_id=rid)

        out = toolset.execute(
            "render_specimen_diff", {"reference_render_specimen_id": 99}
        )
        assert isinstance(out, str) and out.startswith("[error]"), out


def _test_render_specimen_diff_tier_mismatch():
    from tests._render_stubs import stub_render_with_spec_deps
    from tools.render_coretext import RenderTier

    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))
    with stub_render_with_spec_deps(
        tiers=[RenderTier.CORETEXT_FULL, RenderTier.GEOMETRY]
    ):
        out = toolset.execute("render_specimen", {"text": "Ђ", "master": "Bold"})
        rid = _assert_render_specimen_ok(out)
        out = toolset.execute(
            "render_specimen_diff", {"reference_render_specimen_id": rid}
        )
        assert isinstance(out, str), out
        assert "overlay skipped" in out, out
        assert "current_render=glyph-limited" in out, out


def _test_numeric_judge_new_helpers():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    code = """
p0 = g['Dje-cy'][0]
a = p0[0]
b = p0[1]
c = p0[2]

print('angle_ab:', round(angle(a, b), 1))
print('perp_dist:', round(perpendicular_distance(c, a, b), 1))

proj = projection(c, a, b)
print('proj_x:', round(proj['x'], 1))
print('proj_y:', round(proj['y'], 1))

mid = lerp(a, b, 0.5)
print('lerp_x:', round(mid['x'], 1))

ref = reflect(a, 450)
print('reflect_x:', round(ref['x'], 1))

t = tangent_at(p0, 0)
print('tangent_dy:', round(t[1], 3))

tp = transform_point(a, 1, 0, 0, 1, 50, 100)
print('tp_x:', round(tp['x'], 1))
print('tp_y:', round(tp['y'], 1))
"""
    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
    )
    assert "angle_ab: 0.0" in result, result
    assert "perp_dist: 190.0" in result, result
    assert "proj_x: 800.0" in result, result
    assert "proj_y: 1230.0" in result, result
    assert "lerp_x: 450.0" in result, result
    assert "reflect_x: 800.0" in result, result
    assert "tangent_dy: -1.0" in result, result
    assert "tp_x: 150.0" in result, result
    assert "tp_y: 1330.0" in result, result


def _test_numeric_judge_basic():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    code = (
        "p0 = g['Dje-cy'][0]\n"
        "print('nodes:', len(p0))\n"
        "bb = bbox(p0)\n"
        "print('x_range:', bb['x1'] - bb['x0'])\n"
    )
    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
    )
    assert "nodes: 4" in result, result
    assert "x_range: 700" in result, result


def _test_numeric_judge_dist_and_area():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    code = (
        "p0 = g['Dje-cy'][0]\n"
        "print('horiz:', int(dist(p0[0], p0[1])))\n"
        "print('area:', int(area(p0)))\n"
    )
    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
    )
    assert "horiz: 700" in result, result
    assert "area: 133000" in result, result


def _test_numeric_judge_runtime_error():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    code = (
        "print('before')\n"
        "x = g['Dje-cy'][99][0]['x']\n"
    )
    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
    )
    assert "before" in result, result
    assert "[error]" in result, result
    assert "IndexError" in result or "index" in result.lower(), result


def _test_numeric_judge_missing_glyph():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["NoSuchGlyph"], "code": "print('hi')"},
    )
    assert result.startswith("[error]"), result
    assert "NoSuchGlyph" in result, result


def _test_numeric_judge_no_output_message():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "code": "x = 1 + 1"},
    )
    assert "no output" in result.lower(), result


def _test_numeric_judge_no_mutations():
    font = build_mock_font()
    ctx = _ctx(font)
    toolset = ModelToolset(ctx)

    layer_bold = font.glyphs["Dje-cy"].layers["M_BOLD"]
    original_x = float(layer_bold.paths[0].nodes[0].position.x)

    toolset.execute(
        "numeric_judge",
        {
            "glyphs": ["Dje-cy"],
            "master": "Bold",
            "code": "g['Dje-cy'][0][0]['x'] = 999\nprint('ok')",
        },
    )

    assert float(layer_bold.paths[0].nodes[0].position.x) == original_x, "font was mutated"


def _test_get_glyph_component_transform():
    font = build_composite_mock_font()
    toolset = ModelToolset(_ctx(font))

    out = toolset.execute("get_glyph", {"name": "Composite-cy", "master": "Regular"})
    assert "components: 1" in out, out
    assert "Dje-cy" in out, out
    assert "offset=" in out or "identity" in out or "mirror" in out or "matrix=" in out, out
    assert "used as component in: (none)" in out, out

    out2 = toolset.execute("get_glyph", {"name": "Dje-cy", "master": "Bold"})
    assert "used as component in (1): Composite-cy" in out2, out2


def _test_numeric_judge_composite_transform():
    font = build_composite_mock_font()
    toolset = ModelToolset(_ctx(font))

    code = (
        "p0 = g['Composite-cy'][0]\n"
        "print('component:', p0[0]['component'])\n"
        "print('x0:', p0[0]['x'])\n"
        "print('y0:', p0[0]['y'])\n"
    )
    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["Composite-cy"], "master": "Regular", "code": code},
    )
    assert "component: Dje-cy" in result, result
    assert "x0: 200" in result, result
    assert "y0: 1208" in result, result


def _test_numeric_judge_composite_mirror():
    font = build_mock_font()
    comp = _MockComponent("Dje-cy", transform=(-1, 0, 0, 1, 1000, 0))
    comp_layer = _MockLayer(width=1000, paths=[], components=[comp])
    mirrored = _MockGlyph("Mirrored-cy", "FFFD", {"M_REG": comp_layer})
    font.glyphs = _MockGlyphsList(list(font.glyphs) + [mirrored])
    toolset = ModelToolset(_ctx(font))

    code = (
        "p0 = g['Mirrored-cy'][0]\n"
        "print('x0:', p0[0]['x'])\n"
    )
    result = toolset.execute(
        "numeric_judge",
        {"glyphs": ["Mirrored-cy"], "master": "Regular", "code": code},
    )
    assert "x0: 900" in result, result


def _test_get_glyph_no_component_uses():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    out = toolset.execute("get_glyph", {"name": "Dje-cy", "master": "Bold"})
    assert "used as component in: (none)" in out, out


def _test_render_glyph_missing_glyph():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    result = toolset.execute("render_glyph", {"name": "NoSuch"})
    assert isinstance(result, str) and result.startswith("[error]"), result
    assert "NoSuch" in result, result


def _test_direct_method_calls():
    font = build_mock_font()
    toolset = ModelToolset(_ctx(font))

    out = toolset.list_masters()
    assert "Regular" in out
    out = toolset.list_glyphs(filter="dje")
    assert "Dje-cy" in out


def run_smoke_model_toolset():
    """Run ModelToolset behavioral tests (mirrors relevant smoke.py cases)."""
    _test_tool_handlers_pure()
    _test_render_specimen_diff_unknown_id()
    _test_render_specimen_diff_sequential()
    _test_render_specimen_diff_tier_mismatch()
    _test_numeric_judge_new_helpers()
    _test_numeric_judge_basic()
    _test_numeric_judge_dist_and_area()
    _test_numeric_judge_runtime_error()
    _test_numeric_judge_missing_glyph()
    _test_numeric_judge_no_output_message()
    _test_numeric_judge_no_mutations()
    _test_render_glyph_missing_glyph()
    _test_get_glyph_component_transform()
    _test_get_glyph_no_component_uses()
    _test_numeric_judge_composite_transform()
    _test_numeric_judge_composite_mirror()
    _test_direct_method_calls()
    print("Taipo Chat Resources/tests/smoke_model_toolset.py: run_smoke_model_toolset() OK")


if __name__ == "__main__":
    run_smoke_model_toolset()
