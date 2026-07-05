# encoding: utf-8
"""Mock Glyphs font/layer graph for smoke tests (no Glyphs SDK required)."""


class _MockTransform:
    """Minimal stand-in for NSAffineTransformStruct."""

    def __init__(self, m11, m12, m21, m22, tX, tY):
        self.m11 = m11
        self.m12 = m12
        self.m21 = m21
        self.m22 = m22
        self.tX = tX
        self.tY = tY


class _MockComponent:
    def __init__(self, name, transform=(1, 0, 0, 1, 0, 0)):
        self.componentName = name
        self.transform = _MockTransform(*transform)
        self.position = _MockPosition(transform[4], transform[5])


class _MockAxis:
    def __init__(self, name):
        self.name = name


class _MockMaster:
    def __init__(self, mid, name, axes=None):
        self.id = mid
        self.name = name
        self.axes = list(axes or [])


class _MockPosition:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _MockNode:
    def __init__(self, x, y, t="line", smooth=False):
        self.position = _MockPosition(x, y)
        self.type = t
        self.smooth = smooth


class _MockPath:
    def __init__(self, nodes, closed=True):
        self.nodes = list(nodes)
        self.closed = closed


class _MockLayer:
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


class _MockGlyph:
    def __init__(self, name, unicode_hex, layers_by_id):
        self.name = name
        self.unicode = unicode_hex
        self.layers = _LayerMap(layers_by_id)


class _MockGlyphsList:
    def __init__(self, glyphs):
        self._glyphs = list(glyphs)
        self._by_name = {g.name: g for g in self._glyphs}

    def __iter__(self):
        return iter(self._glyphs)

    def __getitem__(self, key):
        return self._by_name.get(key)


class _MockFont:
    def __init__(self, upm=1000):
        self.upm = upm
        self.axes = [_MockAxis("Weight")]
        self.masters = []
        self.glyphs = _MockGlyphsList([])

    def glyphForCharacter_(self, code):
        for g in self.glyphs:
            if g.unicode and int(g.unicode, 16) == code:
                return g
        return None


def build_mock_font():
    m_regular = _MockMaster("M_REG", "Regular", axes=[400])
    m_bold = _MockMaster("M_BOLD", "Bold", axes=[700])
    font = _MockFont(upm=1000)
    font.masters = [m_regular, m_bold]

    nodes_bold_dje = [
        _MockNode(100, 1230),
        _MockNode(800, 1230),
        _MockNode(800, 1420),
        _MockNode(100, 1420),
    ]
    layer_bold = _MockLayer(width=1200, paths=[_MockPath(nodes_bold_dje)])
    layer_regular = _MockLayer(
        width=1200,
        paths=[_MockPath([_MockNode(100, 1158), _MockNode(800, 1158)])],
    )
    dje = _MockGlyph(
        "Dje-cy",
        "0402",
        {m_regular.id: layer_regular, m_bold.id: layer_bold},
    )
    font.glyphs = _MockGlyphsList([dje])
    return font


def build_composite_mock_font():
    """Font with Dje-cy (base) and Composite-cy (= Dje-cy with translation offset)."""
    font = build_mock_font()
    comp = _MockComponent("Dje-cy", transform=(1, 0, 0, 1, 100, 50))
    comp_layer_reg = _MockLayer(width=1400, paths=[], components=[comp])
    composite_glyph = _MockGlyph(
        "Composite-cy", "FFFE",
        {"M_REG": comp_layer_reg},
    )
    all_glyphs = list(font.glyphs) + [composite_glyph]
    font.glyphs = _MockGlyphsList(all_glyphs)
    return font
