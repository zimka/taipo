# encoding: utf-8
"""
Smoke tests — no Glyphs.app required.

Run from the repo root::

    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/smoke.py

On success a single OK line is printed; on failure an ``AssertionError`` with
traceback is raised.

For integration tests that need the real Glyphs SDK, see ``tests/glyphs.py``.
"""

import os
import sys

_RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)

from tests.execute_tool import execute_tool
from tests.mock import (
    _MockComponent,
    _MockGlyph,
    _MockGlyphsList,
    _MockLayer,
    build_composite_mock_font,
    build_mock_font,
)


def _test_utils_basics():
    from utils import (
        _chat_endpoint,
        format_usage_caption,
        mode_contract_text,
        mode_switch_notice,
        normalize_session_mode,
        normalize_tool_result_content,
        normalize_usage,
        TOOL_TAG_EDIT_ONLY,
    )

    assert _chat_endpoint("") == ""
    assert _chat_endpoint("   ") == ""
    assert _chat_endpoint("https://api.example.com") == "https://api.example.com/v1/chat/completions"
    assert _chat_endpoint("https://api.example.com/") == "https://api.example.com/v1/chat/completions"
    assert _chat_endpoint(None) == ""

    z = normalize_usage(None)
    assert z["input_tokens"] == 0 and z["output_tokens"] == 0
    assert normalize_usage({"input_tokens": 100, "output_tokens": 50})["output_tokens"] == 50
    assert normalize_usage({"input_tokens": "12", "bad": "x"})["input_tokens"] == 12

    cap = format_usage_caption(
        {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    )
    assert "last:" in cap and "session:" in cap

    blocks = normalize_tool_result_content("hello")
    assert blocks == [{"type": "text", "text": "hello"}]
    blocks = normalize_tool_result_content(b"\x89PNG\r\n\x1a\n")
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
    blocks = normalize_tool_result_content(["hdr", b"\x89PNG\r\n\x1a\n"])
    assert blocks[0]["type"] == "text" and blocks[1]["type"] == "image"

    assert normalize_session_mode("Edit") == "edit"
    assert normalize_session_mode("") == "inspect"
    inspect_contract = mode_contract_text("inspect")
    assert inspect_contract.startswith("Current mode: Inspect.")
    assert TOOL_TAG_EDIT_ONLY in inspect_contract
    assert mode_contract_text("edit").startswith("Current mode: Edit.")
    assert "Mutation tools are available" in mode_switch_notice("edit")
    assert "will not be changed" in mode_switch_notice("inspect")


def _test_parse_provider_response():
    from provider import parse_response

    tool_payload = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "let me look",
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "list_masters",
                            "arguments": "{}",
                        }
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 42, "completion_tokens": 11, "total_tokens": 53},
    }
    p = parse_response(tool_payload)
    assert p["error"] is None
    assert p["stop_reason"] == "tool_use"
    assert p["text"] == "let me look"
    assert len(p["tool_uses"]) == 1
    assert p["tool_uses"][0]["name"] == "list_masters"
    assert p["tool_uses"][0]["id"] == "call_abc"
    assert p["usage"]["input_tokens"] == 42
    assert p["usage"]["output_tokens"] == 11

    end_payload = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "DOD PASSED",
                "tool_calls": None,
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    p = parse_response(end_payload)
    assert p["error"] is None
    assert p["text"] == "DOD PASSED"
    assert p["tool_uses"] == []
    assert p["stop_reason"] == "end_turn"

    err_payload = {"error": {"type": "invalid_request_error", "message": "boom"}}
    p = parse_response(err_payload)
    assert p["error"] and "boom" in p["error"]


def _test_tool_handlers_pure():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    out = execute_tool("list_masters", {}, ctx)
    assert "Regular" in out and "Bold" in out and "M_BOLD" in out

    out = execute_tool("list_glyphs", {"filter": "dje"}, ctx)
    assert "Dje-cy" in out and "U+0402" in out

    out = execute_tool("list_glyphs", {"filter": "Ђ"}, ctx)
    assert "Dje-cy" in out, "character filter 'Ђ' should match U+0402"

    out = execute_tool("list_glyphs", {"filter": "0402"}, ctx)
    assert "Dje-cy" in out

    out = execute_tool(
        "get_glyph", {"name": "Dje-cy", "master": "Bold"}, ctx
    )
    assert "glyph: Dje-cy" in out
    assert "master: Bold" in out
    assert "paths: 1" in out
    assert "x=100 y=1230" in out
    assert "x=100 y=1420" in out

    out = execute_tool(
        "move_nodes",
        {
            "glyph": "Dje-cy",
            "master": "Bold",
            "path": 0,
            "nodes": [0, 1],
            "dx": 0,
            "dy": -72,
        },
        ctx,
    )
    assert "Moved 2 node(s)" in out
    layer = font.glyphs["Dje-cy"].layers["M_BOLD"]
    ys = sorted(int(n.position.y) for n in layer.paths[0].nodes)
    assert ys == [1158, 1158, 1420, 1420], ys

    out = execute_tool(
        "move_nodes",
        {
            "glyph": "Dje-cy",
            "master": "Bold",
            "path": 0,
            "nodes": [],
            "dx": 0,
            "dy": 1,
        },
        ctx,
    )
    assert out.startswith("[error]")

    out = execute_tool(
        "move_nodes",
        {
            "glyph": "Dje-cy",
            "master": "Bold",
            "path": 0,
            "nodes": [99],
            "dx": 0,
            "dy": -10,
        },
        ctx,
    )
    assert out.startswith("[error]")

    out = execute_tool("get_glyph", {"name": "Missing"}, ctx)
    assert out.startswith("[error]")

    out = execute_tool("unknown_tool", {}, ctx)
    assert out.startswith("[error] Unknown tool")


def _test_inspect_mode_rejects_mutations():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="inspect")
    layer = font.glyphs["Dje-cy"].layers["M_BOLD"]
    before = [(int(n.position.x), int(n.position.y)) for n in layer.paths[0].nodes]
    out = execute_tool(
        "move_nodes",
        {
            "glyph": "Dje-cy",
            "master": "Bold",
            "path": 0,
            "nodes": [0],
            "dx": 10,
            "dy": 0,
        },
        ctx,
    )
    assert "locked in Inspect mode" in out
    after = [(int(n.position.x), int(n.position.y)) for n in layer.paths[0].nodes]
    assert after == before

    out = execute_tool("list_masters", {}, ctx)
    assert "Regular" in out


def _test_agent_loop_fake():
    from state import ChatState

    script = [
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Looking at masters.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "list_masters",
                                "arguments": "{}",
                            }
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
        {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "DOD PASSED",
                    "tool_calls": None,
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        },
    ]

    import provider as provider_mod

    original_post = provider_mod.post_request
    calls = {"n": 0}

    def fake_post(body, url, auth_value):
        i = calls["n"]
        calls["n"] += 1
        return script[i]

    provider_mod.post_request = fake_post
    try:
        s = ChatState()
        s.update_settings_from_ui_fields(
            "https://fake.example",
            "token",
            "m",
            "1024",
            "sys",
        )
        events = []

        def executor(name, args):
            assert name == "list_masters"
            return "masters: [0] id=A name=Regular"

        s.run_agent_turn(
            user_text="Fix it",
            tool_executor=executor,
            tool_schemas=[{"name": "list_masters", "input_schema": {"type": "object"}}],
            on_event=events.append,
        )
    finally:
        provider_mod.post_request = original_post

    kinds = [e.get("kind") for e in events]
    assert kinds[0] == "user"
    assert "tool_use" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "done", kinds
    assert s.messages[-1]["role"] == "assistant"
    assert s.messages[-2]["role"] == "user"


def _test_render_specimen_diff_unknown_id():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")
    out = execute_tool("render_specimen_diff", {"reference_render_specimen_id": 1}, ctx)
    assert out.startswith("[error]") and "render_specimen_id" in out.lower(), out


def _assert_render_specimen_ok(out, *, master="Bold"):
    """Successful render_specimen returns [header, png_bytes] with an id."""
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


def _test_render_spec_pure():
    from tools.render_spec import RenderSpec

    spec = RenderSpec(
        lines=("AVA",),
        master_id="M_BOLD",
        em_px=160.0,
        canvas_w=200,
        canvas_h=100,
        pad=12.0,
        baseline_y0=56.0,
        line_height=200.0,
    )
    grown = spec.with_canvas(300, 80)
    assert grown.canvas_w == 300
    assert grown.canvas_h == 100
    assert grown.pad == spec.pad
    assert grown.baseline_y0 == spec.baseline_y0
    assert grown.line_height == spec.line_height
    assert grown.origin_locked == spec.origin_locked
    assert spec.with_canvas(200, 100) is spec

    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")
    built, err = RenderSpec.from_agent_args(
        {"text": "Ђ", "master": "Bold", "size": 160}, font, ctx
    )
    assert err is None, err
    assert built.lines == ("Ђ",)
    assert built.master_id == "M_BOLD"
    assert built.em_px == 160.0
    assert built.canvas_w >= 120
    assert built.canvas_h >= 80
    assert built.origin_locked is False
    assert built.pad == 80.0


def _test_render_specimen_diff_sequential():
    """render_specimen assigns ids; render_specimen_diff replays the stored spec."""
    from tests._render_stubs import stub_render_with_spec_deps

    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    out = execute_tool("render_specimen_diff", {"reference_render_specimen_id": 1}, ctx)
    assert out.startswith("[error]"), out
    assert "No specimens" in out or "Unknown" in out, out

    with stub_render_with_spec_deps():
        out = execute_tool("render_specimen", {"text": "Ђ", "master": "Bold"}, ctx)
        rid = _assert_render_specimen_ok(out)
        assert rid == 1

        out = execute_tool(
            "render_specimen_diff", {"reference_render_specimen_id": rid}, ctx
        )
        _assert_render_specimen_diff_ok(out, reference_id=rid)

        execute_tool(
            "move_nodes",
            {
                "glyph": "Dje-cy",
                "master": "Bold",
                "path": 0,
                "nodes": [0, 1],
                "dx": 0,
                "dy": -72,
            },
            ctx,
        )
        out = execute_tool(
            "render_specimen_diff", {"reference_render_specimen_id": rid}, ctx
        )
        _assert_render_specimen_diff_ok(out, reference_id=rid)

        out = execute_tool(
            "render_specimen_diff", {"reference_render_specimen_id": 99}, ctx
        )
        assert isinstance(out, str) and out.startswith("[error]"), out
        assert "99" in out, out


def _test_render_specimen_diff_tier_mismatch():
    from tests._render_stubs import stub_render_with_spec_deps
    from tools.render_coretext import RenderTier

    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")
    with stub_render_with_spec_deps(
        tiers=[RenderTier.CORETEXT_FULL, RenderTier.GEOMETRY]
    ):
        out = execute_tool("render_specimen", {"text": "Ђ", "master": "Bold"}, ctx)
        rid = _assert_render_specimen_ok(out)
        out = execute_tool(
            "render_specimen_diff", {"reference_render_specimen_id": rid}, ctx
        )
        assert isinstance(out, str), out
        assert "overlay skipped" in out, out
        assert "reference_render=full" in out, out
        assert "current_render=glyph-limited" in out, out
        assert "Do not treat this result as a visual diff" in out, out


def _test_provider_image_injection_single():
    import base64
    from provider import _convert_messages

    fake_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")

    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "c1", "name": "render_specimen", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_call_id": "c1",
                    "content": [
                        {"type": "text", "text": "render_specimen master=Regular"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": fake_b64}},
                    ],
                }
            ],
        },
    ]

    result = _convert_messages(msgs, "sys")
    assert len(result) == 4, result
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "assistant"

    tool_msg = result[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "c1"
    assert isinstance(tool_msg["content"], str), "tool content must be str"
    assert "[TOOL_IMAGE_1]" in tool_msg["content"]
    assert "render_specimen master=Regular" in tool_msg["content"]
    assert fake_b64 not in tool_msg["content"]

    img_msg = result[3]
    assert img_msg["role"] == "user"
    assert isinstance(img_msg["content"], list)
    assert len(img_msg["content"]) == 2
    assert img_msg["content"][0] == {"type": "text", "text": "[TOOL_IMAGE_1]"}
    iu = img_msg["content"][1]
    assert iu["type"] == "image_url"
    assert iu["image_url"]["url"].startswith("data:image/png;base64,")
    assert fake_b64 in iu["image_url"]["url"]


def _test_provider_image_injection_no_images():
    from provider import _convert_messages

    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_call_id": "c1",
                    "content": [{"type": "text", "text": "masters: Regular"}],
                }
            ],
        }
    ]

    result = _convert_messages(msgs, "")
    assert len(result) == 1, result
    assert result[0]["role"] == "tool"
    assert result[0]["content"] == "masters: Regular"


def _test_provider_image_injection_multi_in_one_result():
    import base64
    from provider import _convert_messages

    def fb():
        return base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")

    b1, b2, b3 = fb(), fb(), fb()

    msgs = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_call_id": "c1",
                    "content": [
                        {"type": "text", "text": "diff_pre_post"},
                        {"type": "text", "text": "pre (snapshot):"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b1}},
                        {"type": "text", "text": "post (live):"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b2}},
                        {"type": "text", "text": "overlay:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b3}},
                    ],
                }
            ],
        }
    ]

    result = _convert_messages(msgs, "")
    assert len(result) == 2, result

    tool_msg = result[0]
    assert tool_msg["role"] == "tool"
    tc = tool_msg["content"]
    assert "[TOOL_IMAGE_1]" in tc
    assert "[TOOL_IMAGE_2]" in tc
    assert "[TOOL_IMAGE_3]" in tc
    assert "pre (snapshot):" in tc
    assert "post (live):" in tc
    assert "overlay:" in tc

    img_msg = result[1]
    assert img_msg["role"] == "user"
    uc = img_msg["content"]
    assert len(uc) == 6, uc
    assert uc[0] == {"type": "text", "text": "[TOOL_IMAGE_1]"}
    assert uc[1]["type"] == "image_url"
    assert b1 in uc[1]["image_url"]["url"]
    assert uc[2] == {"type": "text", "text": "[TOOL_IMAGE_2]"}
    assert uc[3]["type"] == "image_url"
    assert b2 in uc[3]["image_url"]["url"]
    assert uc[4] == {"type": "text", "text": "[TOOL_IMAGE_3]"}
    assert uc[5]["type"] == "image_url"
    assert b3 in uc[5]["image_url"]["url"]


def _test_provider_image_injection_global_counter():
    import base64
    from provider import _convert_messages

    def img_block():
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}

    def tool_result(call_id, *blocks):
        return {"type": "tool_result", "tool_call_id": call_id, "content": list(blocks)}

    def assistant_tool_use(call_id):
        return {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": "render_specimen", "input": {}}],
        }

    def user_result(*results):
        return {"role": "user", "content": list(results)}

    msgs = [
        assistant_tool_use("c1"),
        user_result(tool_result("c1", img_block())),
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        assistant_tool_use("c2"),
        user_result(tool_result("c2", img_block(), img_block())),
    ]

    result = _convert_messages(msgs, "")

    tool_msgs = [m for m in result if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert "[TOOL_IMAGE_1]" in tool_msgs[0]["content"]
    assert "[TOOL_IMAGE_2]" in tool_msgs[1]["content"]
    assert "[TOOL_IMAGE_3]" in tool_msgs[1]["content"]

    img_user_msgs = [
        m for m in result
        if m["role"] == "user"
        and isinstance(m.get("content"), list)
        and any(b.get("type") == "image_url" for b in m.get("content", []))
    ]
    assert len(img_user_msgs) == 2
    assert img_user_msgs[0]["content"][0] == {"type": "text", "text": "[TOOL_IMAGE_1]"}
    assert img_user_msgs[1]["content"][0] == {"type": "text", "text": "[TOOL_IMAGE_2]"}
    assert img_user_msgs[1]["content"][2] == {"type": "text", "text": "[TOOL_IMAGE_3]"}


def _test_numeric_judge_new_helpers():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

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
    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
        ctx,
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
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    code = (
        "p0 = g['Dje-cy'][0]\n"
        "print('nodes:', len(p0))\n"
        "bb = bbox(p0)\n"
        "print('x_range:', bb['x1'] - bb['x0'])\n"
    )
    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
        ctx,
    )
    assert "nodes: 4" in result, result
    assert "x_range: 700" in result, result


def _test_numeric_judge_dist_and_area():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    code = (
        "p0 = g['Dje-cy'][0]\n"
        "print('horiz:', int(dist(p0[0], p0[1])))\n"
        "print('area:', int(area(p0)))\n"
    )
    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
        ctx,
    )
    assert "horiz: 700" in result, result
    assert "area: 133000" in result, result


def _test_numeric_judge_runtime_error():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    code = (
        "print('before')\n"
        "x = g['Dje-cy'][99][0]['x']\n"
    )
    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
        ctx,
    )
    assert "before" in result, result
    assert "[error]" in result, result
    assert "IndexError" in result or "index" in result.lower(), result


def _test_numeric_judge_missing_glyph():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["NoSuchGlyph"], "code": "print('hi')"},
        ctx,
    )
    assert result.startswith("[error]"), result
    assert "NoSuchGlyph" in result, result


def _test_numeric_judge_no_output_message():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "code": "x = 1 + 1"},
        ctx,
    )
    assert "no output" in result.lower(), result


def _test_numeric_judge_no_mutations():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    layer_bold = font.glyphs["Dje-cy"].layers["M_BOLD"]
    original_x = float(layer_bold.paths[0].nodes[0].position.x)

    execute_tool(
        "numeric_judge",
        {
            "glyphs": ["Dje-cy"],
            "master": "Bold",
            "code": "g['Dje-cy'][0][0]['x'] = 999\nprint('ok')",
        },
        ctx,
    )

    assert float(layer_bold.paths[0].nodes[0].position.x) == original_x, "font was mutated"


def _test_get_glyph_component_transform():
    import tools

    font = build_composite_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    out = execute_tool("get_glyph", {"name": "Composite-cy", "master": "Regular"}, ctx)
    assert "components: 1" in out, out
    assert "Dje-cy" in out, out
    assert "offset=" in out or "identity" in out or "mirror" in out or "matrix=" in out, out
    assert "used as component in: (none)" in out, out

    out2 = execute_tool("get_glyph", {"name": "Dje-cy", "master": "Bold"}, ctx)
    assert "used as component in (1): Composite-cy" in out2, out2


def _test_numeric_judge_composite_transform():
    import tools

    font = build_composite_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    code = (
        "p0 = g['Composite-cy'][0]\n"
        "print('component:', p0[0]['component'])\n"
        "print('x0:', p0[0]['x'])\n"
        "print('y0:', p0[0]['y'])\n"
    )
    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["Composite-cy"], "master": "Regular", "code": code},
        ctx,
    )
    assert "component: Dje-cy" in result, result
    assert "x0: 200" in result, result
    assert "y0: 1208" in result, result


def _test_numeric_judge_composite_mirror():
    import tools

    font = build_mock_font()
    comp = _MockComponent("Dje-cy", transform=(-1, 0, 0, 1, 1000, 0))
    comp_layer = _MockLayer(width=1000, paths=[], components=[comp])
    mirrored = _MockGlyph("Mirrored-cy", "FFFD", {"M_REG": comp_layer})
    font.glyphs = _MockGlyphsList(list(font.glyphs) + [mirrored])
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    code = (
        "p0 = g['Mirrored-cy'][0]\n"
        "print('x0:', p0[0]['x'])\n"
    )
    result = execute_tool(
        "numeric_judge",
        {"glyphs": ["Mirrored-cy"], "master": "Regular", "code": code},
        ctx,
    )
    assert "x0: 900" in result, result


def _test_get_glyph_no_component_uses():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    out = execute_tool("get_glyph", {"name": "Dje-cy", "master": "Bold"}, ctx)
    assert "used as component in: (none)" in out, out


def _test_resolve_specimen_lines():
    from tools.render_coretext import resolve_specimen_lines

    lines, err = resolve_specimen_lines({"text": "Ta"})
    assert err is None and lines == ["Ta"]

    lines, err = resolve_specimen_lines({"text": "Ta\nVa"})
    assert err is None and lines == ["Ta", "Va"]

    lines, err = resolve_specimen_lines({"lines": ["Ta", "Va"]})
    assert err is None and lines == ["Ta", "Va"]

    lines, err = resolve_specimen_lines({"text": ""})
    assert lines is None and err and "required" in err

    lines, err = resolve_specimen_lines({"lines": []})
    assert lines is None and err


def _test_temporary_export_fixups():
    from tests.mock import _MockFont, _MockGlyph, _MockGlyphsList, _MockLayer
    from tools.render_coretext import _temporary_export_fixups

    font = _MockFont()
    notdef = _MockGlyph(".notdef", "0000", {"M_REG": _MockLayer(width=600, paths=[])})
    font.glyphs = _MockGlyphsList([notdef])

    with _temporary_export_fixups(font):
        assert notdef.unicode == ""
    assert notdef.unicode == "0000"


def _test_format_render_tier_block():
    from tools.render_coretext import RenderTier, format_render_tier_block

    full = format_render_tier_block(RenderTier.CORETEXT_FULL)
    assert full == "render=full"
    assert "render_limitations" not in full
    assert "fallback" not in full

    block = format_render_tier_block(
        RenderTier.CORETEXT_NO_FEATURES,
        tier1_error="Font export failed: broken feature",
    )
    assert "render=feature-limited" in block
    assert "render_limitations:" in block
    assert "full export failed" in block
    assert "render_tier" not in block
    assert "reliable_for" not in block

    geo = format_render_tier_block(
        RenderTier.GEOMETRY,
        tier1_error="err1",
        tier2_error="err2",
        coretext_error="CTFontCreateWithURL failed",
    )
    assert "render=glyph-limited" in geo
    assert "CoreText load failed" in geo


def _test_render_glyph_missing_glyph():
    import tools

    font = build_mock_font()
    ctx = tools.ToolContext(font_provider=lambda: font, session_mode="edit")

    result = execute_tool(
        "render_glyph",
        {"name": "NoSuch"},
        ctx,
    )
    assert isinstance(result, str) and result.startswith("[error]"), result
    assert "NoSuch" in result, result


def _test_transcript_format():
    from transcript_format import attributed_markdown, thumbnail_size

    assert thumbnail_size(0, 10, 440, 140) == (0, 0)
    assert thumbnail_size(220, 70, 440, 140) == (220, 70)
    assert thumbnail_size(880, 280, 440, 140) == (440, 140)
    tw, th = thumbnail_size(4530, 823, 440, 140)
    assert tw <= 440 and th <= 140
    assert tw > 0 and th > 0

    md = attributed_markdown("**bold** and a list:\n\n- one\n- two")
    if md is None:
        return
    plain = str(md.string())
    assert "**" not in plain, plain
    assert "bold" in plain
    assert "one" in plain
    assert "\n" in plain, plain
    assert "one" in plain and "two" in plain

    glued = attributed_markdown("**Finding**\n\nKerning is **not consistent**.")
    if glued is not None:
        glued_plain = str(glued.string())
        assert "FindingKerning" not in glued_plain, glued_plain
        assert "Finding" in glued_plain and "Kerning" in glued_plain
        assert "\n" in glued_plain, glued_plain


def run_smoke():
    """Run all smoke tests that do not require a live Glyphs font."""
    _test_utils_basics()
    _test_parse_provider_response()
    _test_tool_handlers_pure()
    _test_inspect_mode_rejects_mutations()
    _test_agent_loop_fake()
    _test_render_specimen_diff_unknown_id()
    _test_render_spec_pure()
    _test_render_specimen_diff_sequential()
    _test_render_specimen_diff_tier_mismatch()
    _test_provider_image_injection_single()
    _test_provider_image_injection_no_images()
    _test_provider_image_injection_multi_in_one_result()
    _test_provider_image_injection_global_counter()
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
    _test_resolve_specimen_lines()
    _test_temporary_export_fixups()
    _test_format_render_tier_block()
    _test_transcript_format()
    print("Taipo Chat Resources/tests/smoke.py: run_smoke() OK")


if __name__ == "__main__":
    run_smoke()
