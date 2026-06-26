# encoding: utf-8
"""
Level-3 smoke tests. Safe to run inside Glyphs (Macro Panel) and — for the
Glyphs-agnostic parts — also as a plain Python script.

How to run in Glyphs
--------------------
1. Open the Macro Panel: **Window → Macro Panel** (or **⌥⌘M**).
2. Paste, substituting the absolute path to the plugin's ``Resources`` folder:

    import sys
    sys.path.insert(0, "/Users/YOUR_NAME/my/grammafont_plugin/TaipoChat.glyphsPlugin/Contents/Resources")
    import test
    test.run_smoke()

A success line prints at the bottom; on failure an ``AssertionError`` with a
traceback is raised.
"""


def _test_utils_basics():
    from utils import (
        _chat_endpoint,
        format_usage_caption,
        normalize_tool_result_content,
        normalize_usage,
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


def _test_parse_provider_response():
    from provider import parse_response

    # Tool use turn (OpenAI format)
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

    # End turn (OpenAI format)
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

    # Error response
    err_payload = {"error": {"type": "invalid_request_error", "message": "boom"}}
    p = parse_response(err_payload)
    assert p["error"] and "boom" in p["error"]


class _FakeTransform:
    """Minimal stand-in for NSAffineTransformStruct."""
    def __init__(self, m11, m12, m21, m22, tX, tY):
        self.m11 = m11; self.m12 = m12
        self.m21 = m21; self.m22 = m22
        self.tX = tX;   self.tY = tY


class _FakeComponent:
    def __init__(self, name, transform=(1, 0, 0, 1, 0, 0)):
        self.componentName = name
        self.transform = _FakeTransform(*transform)
        self.position = _FakePosition(transform[4], transform[5])


class _FakeAxis:
    def __init__(self, name):
        self.name = name


class _FakeMaster:
    def __init__(self, mid, name, axes=None):
        self.id = mid
        self.name = name
        self.axes = list(axes or [])


class _FakePosition:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _FakeNode:
    def __init__(self, x, y, t="line", smooth=False):
        self.position = _FakePosition(x, y)
        self.type = t
        self.smooth = smooth


class _FakePath:
    def __init__(self, nodes, closed=True):
        self.nodes = list(nodes)
        self.closed = closed


class _FakeLayer:
    def __init__(self, width, paths, anchors=None, components=None):
        self.width = width
        self.paths = list(paths)
        self.anchors = list(anchors or [])
        self.components = list(components or [])
        self.completeBezierPath = None


class _LayerMap:
    def __init__(self, by_id):
        self._by_id = dict(by_id)

    def __getitem__(self, key):
        return self._by_id.get(key)


class _FakeGlyph:
    def __init__(self, name, unicode_hex, layers_by_id):
        self.name = name
        self.unicode = unicode_hex
        self.layers = _LayerMap(layers_by_id)


class _FakeGlyphsList:
    def __init__(self, glyphs):
        self._glyphs = list(glyphs)
        self._by_name = {g.name: g for g in self._glyphs}

    def __iter__(self):
        return iter(self._glyphs)

    def __getitem__(self, key):
        return self._by_name.get(key)


class _FakeFont:
    def __init__(self, upm=1000):
        self.upm = upm
        self.axes = [_FakeAxis("Weight")]
        self.masters = []
        self.glyphs = _FakeGlyphsList([])

    def glyphForCharacter_(self, code):
        for g in self.glyphs:
            if g.unicode and int(g.unicode, 16) == code:
                return g
        return None


def _build_fake_font():
    m_regular = _FakeMaster("M_REG", "Regular", axes=[400])
    m_bold = _FakeMaster("M_BOLD", "Bold", axes=[700])
    font = _FakeFont(upm=1000)
    font.masters = [m_regular, m_bold]

    nodes_bold_dje = [
        _FakeNode(100, 1230),
        _FakeNode(800, 1230),
        _FakeNode(800, 1420),
        _FakeNode(100, 1420),
    ]
    layer_bold = _FakeLayer(width=1200, paths=[_FakePath(nodes_bold_dje)])
    layer_regular = _FakeLayer(
        width=1200,
        paths=[_FakePath([_FakeNode(100, 1158), _FakeNode(800, 1158)])],
    )
    dje = _FakeGlyph(
        "Dje-cy",
        "0402",
        {m_regular.id: layer_regular, m_bold.id: layer_bold},
    )
    font.glyphs = _FakeGlyphsList([dje])
    return font


def _build_composite_font():
    """Font with Dje-cy (base) and Composite-cy (= Dje-cy with translation offset)."""
    font = _build_fake_font()
    # Composite: Dje-cy referenced with offset (100, 50)
    comp = _FakeComponent("Dje-cy", transform=(1, 0, 0, 1, 100, 50))
    comp_layer_reg = _FakeLayer(width=1400, paths=[], components=[comp])
    composite_glyph = _FakeGlyph(
        "Composite-cy", "FFFE",
        {"M_REG": comp_layer_reg},
    )
    all_glyphs = list(font.glyphs) + [composite_glyph]
    font.glyphs = _FakeGlyphsList(all_glyphs)
    return font


def _test_tool_handlers_pure():
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    out = tools.execute_tool("list_masters", {}, ctx)
    assert "Regular" in out and "Bold" in out and "M_BOLD" in out

    out = tools.execute_tool("list_glyphs", {"filter": "dje"}, ctx)
    assert "Dje-cy" in out and "U+0402" in out

    out = tools.execute_tool(
        "get_glyph", {"name": "Dje-cy", "master": "Bold"}, ctx
    )
    assert "glyph: Dje-cy" in out
    assert "master: Bold" in out
    assert "paths: 1" in out
    # New node format: x=... y=... (no parens, no comma)
    assert "x=100 y=1230" in out
    assert "x=100 y=1420" in out

    out = tools.execute_tool(
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

    # empty nodes list → error
    out = tools.execute_tool(
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

    # out-of-range node index → error
    out = tools.execute_tool(
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

    out = tools.execute_tool("get_glyph", {"name": "Missing"}, ctx)
    assert out.startswith("[error]")

    out = tools.execute_tool("unknown_tool", {}, ctx)
    assert out.startswith("[error] Unknown tool")


def _test_agent_loop_fake():
    from state import ChatState

    # OpenAI-format responses
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


def _test_snapshot_store_pure():
    import tools

    font = _build_fake_font()
    store = tools.SnapshotStore()
    ctx = tools.ToolContext(font_provider=lambda: font, snapshot_store=store)

    assert not store.has_snapshot()

    out = tools.execute_tool("reset_snapshot", {}, ctx)
    assert out.startswith("[error]"), out
    out = tools.execute_tool("render_diff", {"text": "Ђ"}, ctx)
    assert out.startswith("[error]") and "snapshot" in out.lower(), out

    out = tools.execute_tool("save_snapshot", {"glyph_names": []}, ctx)
    assert out.startswith("[error]"), out
    out = tools.execute_tool("save_snapshot", {"glyph_names": ["NoSuch"]}, ctx)
    assert out.startswith("[error]") and "NoSuch" in out, out

    out = tools.execute_tool("save_snapshot", {"glyph_names": ["Dje-cy"]}, ctx)
    assert "Snapshot saved" in out, out
    assert store.has_snapshot()
    assert store._glyph_names == ["Dje-cy"]
    assert set(store._slot["Dje-cy"].keys()) == {"M_REG", "M_BOLD"}
    bold_pre = store._slot["Dje-cy"]["M_BOLD"]
    assert bold_pre["width"] == 1200.0
    ys_pre = sorted(int(n["y"]) for n in bold_pre["paths"][0]["nodes"])
    assert ys_pre == [1230, 1230, 1420, 1420]

    tools.execute_tool(
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
    layer = font.glyphs["Dje-cy"].layers["M_BOLD"]
    ys_mid = sorted(int(n.position.y) for n in layer.paths[0].nodes)
    assert ys_mid == [1158, 1158, 1420, 1420], ys_mid

    out = tools.execute_tool("reset_snapshot", {}, ctx)
    assert "Snapshot restored" in out, out
    ys_post = sorted(int(n.position.y) for n in layer.paths[0].nodes)
    assert ys_post == [1230, 1230, 1420, 1420], ys_post
    assert store.has_snapshot(), "snapshot should persist after reset"

    out = tools.execute_tool("save_snapshot", {"glyph_names": ["Dje-cy"]}, ctx)
    assert "Overwrote previous snapshot" in out, out

    store.clear()
    assert not store.has_snapshot()
    out = tools.execute_tool("reset_snapshot", {}, ctx)
    assert out.startswith("[error]"), out


def _test_provider_image_injection_single():
    """tool result with one image → tool msg has placeholder, user msg has image_url."""
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
    # system, assistant, tool, user(images)
    assert len(result) == 4, result
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "assistant"

    tool_msg = result[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "c1"
    assert isinstance(tool_msg["content"], str), "tool content must be str"
    assert "[TOOL_IMAGE_1]" in tool_msg["content"]
    assert "render_specimen master=Regular" in tool_msg["content"]
    # no raw base64 in tool message
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
    """tool result with text only → no injected user message."""
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
    """diff_pre_post returns 3 images: tool gets 3 placeholders, user gets 6 interleaved blocks."""
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
    # interleaved: text, image, text, image, text, image
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
    """Counter is global: second batch continues from where the first left off."""
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
        user_result(tool_result("c1", img_block())),          # batch 1: 1 image → TOOL_IMAGE_1
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        assistant_tool_use("c2"),
        user_result(tool_result("c2", img_block(), img_block())),  # batch 2: 2 images → TOOL_IMAGE_2, TOOL_IMAGE_3
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


def _make_judge_ctx(font):
    import tools
    return tools.ToolContext(
        font_provider=lambda: font,
        api_settings={
            "baseUrl": "https://fake.example",
            "apiKey": "token",
            "model": "gpt-fake",
        },
    )


def _fake_render(_font, _master, _text, _contract):
    return b"\x89PNG\r\n\x1a\n"


def _make_judge_response(verdict, reasoning="ok"):
    import json
    content = json.dumps({"verdict": verdict, "reasoning": reasoning})
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
    }


def _test_visually_judge_request_format():
    """visually_judge sends a multimodal request with image_url and accusation text."""
    import tools
    import provider as pmod

    font = _build_fake_font()
    ctx = _make_judge_ctx(font)
    original_render = tools._render_layer_run
    original_post = pmod.post_request
    captured = {}

    def fake_post(body, url, auth_value):
        captured["body"] = body
        captured["url"] = url
        captured["auth"] = auth_value
        return _make_judge_response("TRUE", "Serif is clearly shorter.")

    tools._render_layer_run = _fake_render
    pmod.post_request = fake_post
    try:
        tools.execute_tool(
            "visually_judge",
            {"accusation": "The serif on n is too short.", "text": "n", "master": "Regular"},
            ctx,
        )
    finally:
        tools._render_layer_run = original_render
        pmod.post_request = original_post

    assert captured, "no HTTP call was made"
    body = captured["body"]
    msgs = body.get("messages", [])
    assert any(m["role"] == "system" for m in msgs), "system message missing"
    user_msg = next(m for m in msgs if m["role"] == "user")
    assert isinstance(user_msg["content"], list), "user content must be a list"
    img_block = next((b for b in user_msg["content"] if b.get("type") == "image_url"), None)
    assert img_block is not None, "image_url block must be present"
    assert "data:image/png;base64," in img_block["image_url"]["url"]
    text_blocks = [b for b in user_msg["content"] if b.get("type") == "text"]
    assert any("The serif on n is too short." in b.get("text", "") for b in text_blocks)
    assert "v1/chat/completions" in captured["url"]
    assert captured["auth"] == "token"


def _test_visually_judge_valid_response():
    """Valid JSON verdict is parsed and returned unchanged."""
    import json
    import tools
    import provider as pmod

    font = _build_fake_font()
    ctx = _make_judge_ctx(font)
    original_render = tools._render_layer_run
    original_post = pmod.post_request
    calls = {"n": 0}

    def fake_post(body, url, auth_value):
        calls["n"] += 1
        return _make_judge_response("TRUE", "The serif is clearly shorter.")

    tools._render_layer_run = _fake_render
    pmod.post_request = fake_post
    try:
        result = tools.execute_tool(
            "visually_judge",
            {"accusation": "Serif too short.", "text": "n"},
            ctx,
        )
    finally:
        tools._render_layer_run = original_render
        pmod.post_request = original_post

    d = json.loads(result)
    assert d["verdict"] == "TRUE"
    assert "serif" in d["reasoning"].lower()
    assert calls["n"] == 1, "should only call once for valid response"


def _test_visually_judge_invalid_json_retries():
    """Invalid JSON triggers exactly one retry; second valid response is returned."""
    import json
    import tools
    import provider as pmod

    font = _build_fake_font()
    ctx = _make_judge_ctx(font)
    original_render = tools._render_layer_run
    original_post = pmod.post_request
    calls = {"n": 0}

    def fake_post(body, url, auth_value):
        calls["n"] += 1
        if calls["n"] == 1:
            content = "not json at all"
        else:
            content = '{"verdict": "FALSE", "reasoning": "Looks fine."}'
        return {
            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    tools._render_layer_run = _fake_render
    pmod.post_request = fake_post
    try:
        result = tools.execute_tool(
            "visually_judge",
            {"accusation": "Test accusation.", "text": "n"},
            ctx,
        )
    finally:
        tools._render_layer_run = original_render
        pmod.post_request = original_post

    assert calls["n"] == 2, "should have retried once, got %d calls" % calls["n"]
    d = json.loads(result)
    assert d["verdict"] == "FALSE"


def _test_visually_judge_fallback_on_double_failure():
    """Two consecutive invalid responses → safe fallback with UNCERTAIN verdict."""
    import json
    import tools
    import provider as pmod

    font = _build_fake_font()
    ctx = _make_judge_ctx(font)
    original_render = tools._render_layer_run
    original_post = pmod.post_request

    def fake_post(body, url, auth_value):
        return {
            "choices": [{"message": {"role": "assistant", "content": "not json"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    tools._render_layer_run = _fake_render
    pmod.post_request = fake_post
    try:
        result = tools.execute_tool(
            "visually_judge",
            {"accusation": "Test.", "text": "a"},
            ctx,
        )
    finally:
        tools._render_layer_run = original_render
        pmod.post_request = original_post

    d = json.loads(result)
    assert d["verdict"] == "UNCERTAIN", d
    assert d["reasoning"]


def _test_visually_judge_all_verdicts():
    """All four verdict values (TRUE, FALSE, UNCERTAIN, INVALID) pass validation."""
    import json
    import tools
    import provider as pmod

    font = _build_fake_font()
    ctx = _make_judge_ctx(font)
    original_render = tools._render_layer_run
    original_post = pmod.post_request

    for verdict in ["TRUE", "FALSE", "UNCERTAIN", "INVALID"]:
        def make_fake_post(v):
            def fake_post(body, url, auth_value):
                return _make_judge_response(v, "test reason")
            return fake_post

        tools._render_layer_run = _fake_render
        pmod.post_request = make_fake_post(verdict)
        try:
            result = tools.execute_tool(
                "visually_judge",
                {"accusation": "Test.", "text": "a"},
                ctx,
            )
        finally:
            tools._render_layer_run = original_render
            pmod.post_request = original_post

        d = json.loads(result)
        assert d["verdict"] == verdict, "expected %s, got %s" % (verdict, d["verdict"])


def _test_visually_judge_no_mutations():
    """visually_judge does not mutate snapshot or font geometry."""
    import tools
    import provider as pmod

    font = _build_fake_font()
    ctx = _make_judge_ctx(font)
    original_render = tools._render_layer_run
    original_post = pmod.post_request
    mutations = []

    original_save = ctx.snapshot_store.save

    def tracked_save(*a, **kw):
        mutations.append("save")
        return original_save(*a, **kw)

    ctx.snapshot_store.save = tracked_save

    tools._render_layer_run = _fake_render
    pmod.post_request = lambda body, url, auth: _make_judge_response("TRUE", "ok")
    try:
        tools.execute_tool("visually_judge", {"accusation": "Test.", "text": "a"}, ctx)
    finally:
        tools._render_layer_run = original_render
        pmod.post_request = original_post

    assert not mutations, "visually_judge must not mutate snapshot: %s" % mutations


def _test_numeric_judge_basic():
    """Sandbox g dict, bbox, and print output are correct."""
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    code = (
        "p0 = g['Dje-cy'][0]\n"
        "print('nodes:', len(p0))\n"
        "bb = bbox(p0)\n"
        "print('x_range:', bb['x1'] - bb['x0'])\n"
    )
    result = tools.execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
        ctx,
    )
    assert "nodes: 4" in result, result
    assert "x_range: 700" in result, result  # 800 - 100 = 700


def _test_numeric_judge_dist_and_area():
    """dist() and area() helpers return correct values for a rectangular path."""
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    # Bold Dje-cy path[0]: rect (100,1230)-(800,1230)-(800,1420)-(100,1420)
    code = (
        "p0 = g['Dje-cy'][0]\n"
        "print('horiz:', int(dist(p0[0], p0[1])))\n"   # 800-100 = 700
        "print('area:', int(area(p0)))\n"              # 700*190 = 133000
    )
    result = tools.execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
        ctx,
    )
    assert "horiz: 700" in result, result
    assert "area: 133000" in result, result


def _test_numeric_judge_runtime_error():
    """Runtime error in snippet returns error message; output printed before error is included."""
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    code = (
        "print('before')\n"
        "x = g['Dje-cy'][99][0]['x']\n"  # IndexError: path 99 does not exist
    )
    result = tools.execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "master": "Bold", "code": code},
        ctx,
    )
    assert "before" in result, result
    assert "[error]" in result, result
    assert "IndexError" in result or "index" in result.lower(), result


def _test_numeric_judge_missing_glyph():
    """Requesting a nonexistent glyph returns an error."""
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    result = tools.execute_tool(
        "numeric_judge",
        {"glyphs": ["NoSuchGlyph"], "code": "print('hi')"},
        ctx,
    )
    assert result.startswith("[error]"), result
    assert "NoSuchGlyph" in result, result


def _test_numeric_judge_no_output_message():
    """Code with no print() returns a helpful message."""
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    result = tools.execute_tool(
        "numeric_judge",
        {"glyphs": ["Dje-cy"], "code": "x = 1 + 1"},
        ctx,
    )
    assert "no output" in result.lower(), result


def _test_numeric_judge_no_mutations():
    """numeric_judge does not mutate font geometry or snapshot."""
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    original_save = ctx.snapshot_store.save
    mutations = []

    def tracked_save(*a, **kw):
        mutations.append("save")
        return original_save(*a, **kw)

    ctx.snapshot_store.save = tracked_save

    layer_bold = font.glyphs["Dje-cy"].layers["M_BOLD"]
    original_x = float(layer_bold.paths[0].nodes[0].position.x)

    # Even if the snippet modifies the sandbox dict, the live font must be unchanged
    tools.execute_tool(
        "numeric_judge",
        {
            "glyphs": ["Dje-cy"],
            "master": "Bold",
            "code": "g['Dje-cy'][0][0]['x'] = 999\nprint('ok')",
        },
        ctx,
    )

    assert float(layer_bold.paths[0].nodes[0].position.x) == original_x, "font was mutated"
    assert not mutations, "snapshot was saved unexpectedly"


def _test_get_glyph_component_transform():
    """get_glyph shows component transform description and 'used as component in' for both glyphs."""
    import tools

    font = _build_composite_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    # Composite: should show transform and report it is not used as a component itself
    out = tools.execute_tool("get_glyph", {"name": "Composite-cy", "master": "Regular"}, ctx)
    assert "components: 1" in out, out
    assert "Dje-cy" in out, out
    assert "offset=" in out or "identity" in out or "mirror" in out or "matrix=" in out, out
    assert "used as component in: (none)" in out, out

    # Base glyph: should report Composite-cy uses it
    out2 = tools.execute_tool("get_glyph", {"name": "Dje-cy", "master": "Bold"}, ctx)
    assert "used as component in (1): Composite-cy" in out2, out2


def _test_numeric_judge_composite_transform():
    """numeric_judge resolves component nodes with their transforms applied."""
    import tools

    font = _build_composite_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    # Dje-cy Regular: single path with nodes at (100, 1158) and (800, 1158)
    # Composite-cy = Dje-cy + offset (100, 50) → nodes at (200, 1208) and (900, 1208)
    code = (
        "p0 = g['Composite-cy'][0]\n"
        "print('component:', p0[0]['component'])\n"
        "print('x0:', p0[0]['x'])\n"
        "print('y0:', p0[0]['y'])\n"
    )
    result = tools.execute_tool(
        "numeric_judge",
        {"glyphs": ["Composite-cy"], "master": "Regular", "code": code},
        ctx,
    )
    assert "component: Dje-cy" in result, result
    assert "x0: 200" in result, result   # 100 + offset 100
    assert "y0: 1208" in result, result  # 1158 + offset 50


def _test_numeric_judge_composite_mirror():
    """numeric_judge applies mirror_x transform correctly."""
    import tools

    # Build a font where the composite uses a horizontal mirror: m11=-1, tX=1000
    font = _build_fake_font()
    comp = _FakeComponent("Dje-cy", transform=(-1, 0, 0, 1, 1000, 0))
    comp_layer = _FakeLayer(width=1000, paths=[], components=[comp])
    mirrored = _FakeGlyph("Mirrored-cy", "FFFD", {"M_REG": comp_layer})
    font.glyphs = _FakeGlyphsList(list(font.glyphs) + [mirrored])
    ctx = tools.ToolContext(font_provider=lambda: font)

    # Dje-cy Regular node at x=100 → mirrored: x = -1*100 + 1000 = 900
    code = (
        "p0 = g['Mirrored-cy'][0]\n"
        "print('x0:', p0[0]['x'])\n"
    )
    result = tools.execute_tool(
        "numeric_judge",
        {"glyphs": ["Mirrored-cy"], "master": "Regular", "code": code},
        ctx,
    )
    assert "x0: 900" in result, result


def _test_get_glyph_no_component_uses():
    """get_glyph on a glyph used by nobody shows the (none) line."""
    import tools

    font = _build_fake_font()  # no composites
    ctx = tools.ToolContext(font_provider=lambda: font)

    out = tools.execute_tool("get_glyph", {"name": "Dje-cy", "master": "Bold"}, ctx)
    assert "used as component in: (none)" in out, out


def _test_render_glyph_missing_glyph():
    """render_glyph returns an error for an unknown glyph name."""
    import tools

    font = _build_fake_font()
    ctx = tools.ToolContext(font_provider=lambda: font)

    result = tools.execute_tool(
        "render_glyph",
        {"name": "NoSuch"},
        ctx,
    )
    assert isinstance(result, str) and result.startswith("[error]"), result
    assert "NoSuch" in result, result


def run_smoke():
    """Single entry point: run all smoke tests that do not require a live Glyphs font."""
    _test_utils_basics()
    _test_parse_provider_response()
    _test_tool_handlers_pure()
    _test_agent_loop_fake()
    _test_snapshot_store_pure()
    _test_provider_image_injection_single()
    _test_provider_image_injection_no_images()
    _test_provider_image_injection_multi_in_one_result()
    _test_provider_image_injection_global_counter()
    _test_visually_judge_request_format()
    _test_visually_judge_valid_response()
    _test_visually_judge_invalid_json_retries()
    _test_visually_judge_fallback_on_double_failure()
    _test_visually_judge_all_verdicts()
    _test_visually_judge_no_mutations()
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
    print("Taipo Chat Resources/test.py: run_smoke() OK")


if __name__ == "__main__":
    run_smoke()
