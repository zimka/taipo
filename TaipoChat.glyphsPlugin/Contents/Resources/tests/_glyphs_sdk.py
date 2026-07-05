# encoding: utf-8
"""Resolve the Glyphs SDK inside Glyphs.app (GlyphsApp module, not top-level Glyphs)."""

import sys


def import_glyphs_app():
    """Return the Glyphs application object (GSApplication wrapper)."""
    try:
        from GlyphsApp import Glyphs as app
        return app
    except ImportError:
        pass

    try:
        import Glyphs as app
        return app
    except ImportError as exc:
        raise RuntimeError(
            "Glyphs SDK not available. Run in Glyphs Macro Panel with Plugin Manager Python "
            "(from GlyphsApp import Glyphs). sys.executable=%r" % sys.executable
        ) from exc


_CLASS_NAMES = ("GSGlyph", "GSPath", "GSNode", "GSAnchor", "GSLINE", "LINE")


def import_glyphs_classes():
    """Return Glyphs object-model classes for use inside imported test modules."""
    try:
        from GlyphsApp import GSGlyph, GSPath, GSNode, GSAnchor, GSLINE, LINE
        return {
            "GSGlyph": GSGlyph,
            "GSPath": GSPath,
            "GSNode": GSNode,
            "GSAnchor": GSAnchor,
            "GSLINE": GSLINE,
            "LINE": LINE,
        }
    except ImportError:
        pass

    try:
        import GlyphsApp as ga
        return {name: getattr(ga, name) for name in _CLASS_NAMES}
    except Exception as exc:
        raise RuntimeError(
            "Could not import GSGlyph/GSPath/GSNode from GlyphsApp."
        ) from exc


def _line_node_type(sdk):
    return sdk.get("GSLINE") or sdk.get("LINE") or "line"


def make_line_node(sdk, x, y, node_type=None):
    """Create a GSNode via property assignment (Glyphs 3 and 4 / PyObjC 10.3+ safe)."""
    GSNode = sdk["GSNode"]
    line_type = node_type if node_type is not None else _line_node_type(sdk)
    node = GSNode()
    node.position = (float(x), float(y))
    node.type = line_type
    return node


def make_glyph(sdk, name):
    """Create a GSGlyph via property assignment (PyObjC positional-arg safe)."""
    GSGlyph = sdk["GSGlyph"]
    glyph = GSGlyph()
    glyph.name = str(name)
    return glyph


def make_anchor(sdk, name, x, y):
    """Create a GSAnchor via property assignment (PyObjC positional-arg safe)."""
    GSAnchor = sdk["GSAnchor"]
    anchor = GSAnchor()
    anchor.name = str(name)
    anchor.position = (float(x), float(y))
    return anchor
