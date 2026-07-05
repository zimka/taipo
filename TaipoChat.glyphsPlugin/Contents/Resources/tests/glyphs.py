# encoding: utf-8
"""
Glyphs integration tests — run inside Glyphs 3 or 4 Macro Panel only.

Prerequisites: a font document open. Tests create and remove a temporary glyph
named ``_TaipoTest`` on the first master.

How to run in Glyphs
--------------------
1. Open the Macro Panel: **Window → Macro Panel** (or **⌥⌘M**).
2. Paste, substituting the absolute path to the plugin's ``Resources`` folder:

    import sys; sys.path.insert(0, "/ABS/PATH/TaipoChat.glyphsPlugin/Contents/Resources")
    import tests; tests.run_glyphs_tests()

On success a single OK line is printed (includes Glyphs and Python versions).
On failure an ``AssertionError`` with traceback is raised.

For tests that do not require Glyphs, see ``tests/smoke.py``.
"""

import os
import sys

_RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)

TEST_GLYPH_NAME = "_TaipoTest"
TEST_ANCHOR_NAME = "top"
TEST_ANCHOR_X = 200.0
TEST_ANCHOR_Y = 700.0


def _require_glyphs():
    from tests._glyphs_sdk import import_glyphs_app

    import_glyphs_app()


def _glyphs_version_str():
    from tests._glyphs_sdk import import_glyphs_app

    Glyphs = import_glyphs_app()

    vn = getattr(Glyphs, "versionNumber", None)
    if vn is not None:
        return str(vn)
    return str(getattr(Glyphs, "appVersion", "?"))


def _python_version_str():
    return "%d.%d.%d" % sys.version_info[:3]


def _open_font():
    from tests._glyphs_sdk import import_glyphs_app

    Glyphs = import_glyphs_app()

    font = Glyphs.font
    if font is None:
        raise RuntimeError("Open a font document before running glyphs tests.")
    if not font.masters:
        raise RuntimeError("Font has no masters.")
    return font


def _tool_context(font):
    import tools

    return tools.ToolContext(font_provider=lambda: font)


class _TaipoTestFixture(object):
    """Create a temporary glyph with one path and one anchor; remove on exit."""

    def __init__(self, font):
        self.font = font
        self.glyph = None
        self._existed = False

    @staticmethod
    def _clear_layer(layer):
        remove_shape = getattr(layer, "removeShape_", None)
        shapes = list(getattr(layer, "shapes", None) or [])
        if remove_shape is not None:
            for shape in shapes:
                try:
                    remove_shape(shape)
                except Exception:
                    pass
        else:
            try:
                for i in range(len(layer.shapes) - 1, -1, -1):
                    del layer.shapes[i]
            except Exception:
                pass

        remove_anchor = getattr(layer, "removeAnchor_", None)
        anchor_names = []
        try:
            anchors = layer.anchors
            if hasattr(anchors, "keys"):
                anchor_names = list(anchors.keys())
            else:
                for item in list(anchors or []):
                    if hasattr(item, "name"):
                        anchor_names.append(str(item.name))
                    else:
                        anchor_names.append(str(item))
        except Exception:
            pass
        if remove_anchor is not None:
            for nm in anchor_names:
                anchor = layer.anchorForName_(nm)
                if anchor is not None:
                    try:
                        remove_anchor(anchor)
                    except Exception:
                        pass
        else:
            try:
                for nm in anchor_names:
                    del layer.anchors[nm]
            except Exception:
                pass

    def __enter__(self):
        from tests._glyphs_sdk import import_glyphs_classes, make_anchor, make_glyph, make_line_node

        sdk = import_glyphs_classes()
        GSPath = sdk["GSPath"]

        font = self.font
        existing = font.glyphs[TEST_GLYPH_NAME]
        if existing is not None:
            self._existed = True
            self.glyph = existing
        else:
            self.glyph = make_glyph(sdk, TEST_GLYPH_NAME)
            font.glyphs.append(self.glyph)

        master = font.masters[0]
        layer = self.glyph.layers[master.id]
        if layer is None:
            raise RuntimeError("Test glyph has no layer for master %s." % master.name)

        self._clear_layer(layer)
        path = GSPath()
        path.nodes.append(make_line_node(sdk, 100, 100))
        path.nodes.append(make_line_node(sdk, 300, 100))
        add_shape = getattr(layer, "addShape_", None)
        if add_shape is not None:
            add_shape(path)
        else:
            layer.shapes.append(path)
        layer.width = 400

        anchor = make_anchor(sdk, TEST_ANCHOR_NAME, TEST_ANCHOR_X, TEST_ANCHOR_Y)
        add_anchor = getattr(layer, "addAnchor_", None)
        if add_anchor is not None:
            add_anchor(anchor)
        else:
            layer.anchors.append(anchor)

        font.updateFeatures()
        return self.glyph, master, layer

    def __exit__(self, exc_type, exc, tb):
        if self._existed:
            return False
        try:
            self.font.glyphs.remove(self.glyph)
            self.font.updateFeatures()
        except Exception:
            pass
        return False


def _test_environment():
    font = _open_font()
    assert font.masters, "font must have masters"
    assert _glyphs_version_str() != "?"


def _test_get_glyph_anchors():
    import tools

    font = _open_font()
    with _TaipoTestFixture(font) as (glyph, master, layer):
        ctx = _tool_context(font)
        out = tools.execute_tool(
            "get_glyph",
            {"name": glyph.name, "master": master.name},
            ctx,
        )
        assert "glyph: %s" % TEST_GLYPH_NAME in out, out
        assert "anchors: 1" in out, out
        assert "  %s (x=%s, y=%s)" % (
            TEST_ANCHOR_NAME,
            tools._fmt_num(TEST_ANCHOR_X),
            tools._fmt_num(TEST_ANCHOR_Y),
        ) in out, out


def _test_snapshot_anchors_roundtrip():
    import tools

    font = _open_font()
    with _TaipoTestFixture(font) as (glyph, master, layer):
        ctx = _tool_context(font)
        store = tools.SnapshotStore()
        ctx.snapshot_store = store

        out = tools.execute_tool(
            "save_snapshot",
            {"glyph_names": [glyph.name]},
            ctx,
        )
        assert "Snapshot saved" in out, out

        anchor = layer.anchorForName_(TEST_ANCHOR_NAME)
        assert anchor is not None, "test anchor missing"
        anchor.position = (TEST_ANCHOR_X + 50, TEST_ANCHOR_Y + 25)

        out = tools.execute_tool("reset_snapshot", {}, ctx)
        assert "Snapshot restored" in out, out

        anchor = layer.anchorForName_(TEST_ANCHOR_NAME)
        assert anchor is not None
        assert abs(float(anchor.position.x) - TEST_ANCHOR_X) < 0.01
        assert abs(float(anchor.position.y) - TEST_ANCHOR_Y) < 0.01


def _test_render_glyph_png():
    import tools

    font = _open_font()
    with _TaipoTestFixture(font) as (glyph, master, layer):
        ctx = _tool_context(font)
        result = tools.execute_tool(
            "render_glyph",
            {"name": glyph.name, "master": master.name, "size": 256},
            ctx,
        )
        assert isinstance(result, list), result
        assert len(result) == 2, result
        header, png_bytes = result
        assert isinstance(header, str) and header.startswith("render_glyph"), header
        assert isinstance(png_bytes, bytes), type(png_bytes)
        assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n", png_bytes[:16]


def _test_http_client_https():
    import http_client

    try:
        resp = http_client.requests_get("https://www.apple.com/library/test/success.html", timeout=15)
    except Exception as exc:
        print("SKIP _test_http_client_https (offline or blocked): %s" % exc)
        return
    assert resp.status_code == 200, resp.status_code
    assert len(resp.content) > 0


def run_glyphs_tests():
    """Run integration tests that require the live Glyphs SDK and an open font."""
    _require_glyphs()
    _test_environment()
    _test_get_glyph_anchors()
    _test_snapshot_anchors_roundtrip()
    _test_render_glyph_png()
    _test_http_client_https()
    print(
        "Taipo Chat Resources/tests/glyphs.py: run_glyphs_tests() OK (Glyphs %s, Python %s)"
        % (_glyphs_version_str(), _python_version_str())
    )


if __name__ == "__main__":
    run_glyphs_tests()
