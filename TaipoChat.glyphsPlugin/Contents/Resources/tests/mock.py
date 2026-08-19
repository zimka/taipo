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
    def __init__(self, width, paths, anchors=None, components=None, lsb=0, rsb=0):
        self.width = width
        self.LSB = lsb
        self.RSB = rsb
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
    def __init__(self, name, unicode_hex, layers_by_id, export=True, note="", glyph_id=None):
        self.name = name
        self.id = glyph_id or ("mock-id-" + name)
        self.unicode = unicode_hex
        self.unicodes = [unicode_hex] if unicode_hex else []
        self.export = export
        self.note = note
        self.layers = _LayerMap(layers_by_id)
        self.category = ""
        self.subCategory = ""
        self.script = ""
        self.case = 0
        self.direction = 0
        self.storeCategory = False
        self.storeSubCategory = False
        self.storeScript = False
        self.storeCase = False
        self.storeDirection = False
        self.leftKerningGroup = ""
        self.rightKerningGroup = ""
        self.leftMetricsKey = ""
        self.rightMetricsKey = ""
        self.widthMetricsKey = ""

    @property
    def rightKerningKey(self):
        group = (self.rightKerningGroup or "").strip()
        if group:
            return "@MMK_L_" + group
        return self.name

    @property
    def leftKerningKey(self):
        group = (self.leftKerningGroup or "").strip()
        if group:
            return "@MMK_R_" + group
        return self.name


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
        self._kerning_flat = {}

    @property
    def kerning(self):
        nested = {}
        for (master_id, left_key, right_key), value in self._kerning_flat.items():
            master_map = nested.setdefault(master_id, {})
            right_map = master_map.setdefault(left_key, {})
            right_map[right_key] = value
        return nested

    def kerningForPair(self, master_id, left_key, right_key):
        from tools.kerning import cascade_effective_for_glyph_pair

        if left_key.startswith("@MMK_") or right_key.startswith("@MMK_"):
            return self._kerning_flat.get((master_id, left_key, right_key), 0)
        return cascade_effective_for_glyph_pair(
            self, master_id, str(left_key), str(right_key)
        )

    def setKerningForPair(self, master_id, left_key, right_key, value):
        self._kerning_flat[(master_id, left_key, right_key)] = float(value)

    def removeKerningForPair(self, master_id, left_key, right_key):
        self._kerning_flat.pop((master_id, left_key, right_key), None)

    def beginUndo(self):
        pass

    def endUndo(self):
        pass

    def updateFeatures(self):
        pass

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


def build_kerning_mock_font():
    """Font with T, A, Aacute and a class-class T×A kerning rule."""
    m_regular = _MockMaster("M_REG", "Regular", axes=[400])
    font = _MockFont(upm=1000)
    font.masters = [m_regular]
    layer = _MockLayer(width=700, paths=[], lsb=28, rsb=0)

    glyph_t = _MockGlyph("T", "0054", {m_regular.id: layer})
    glyph_t.rightKerningGroup = "T"
    glyph_t.leftKerningGroup = "T"

    glyph_a = _MockGlyph("A", "0041", {m_regular.id: _MockLayer(width=712, paths=[], lsb=28, rsb=0)})
    glyph_a.leftKerningGroup = "A"
    glyph_a.rightKerningGroup = "A"

    glyph_aacute = _MockGlyph(
        "Aacute",
        "00C1",
        {m_regular.id: _MockLayer(width=702, paths=[], lsb=28, rsb=0)},
    )
    glyph_aacute.leftKerningGroup = "A"

    font.glyphs = _MockGlyphsList([glyph_t, glyph_a, glyph_aacute])
    font.setKerningForPair(m_regular.id, "@MMK_L_T", "@MMK_R_A", -49)
    return font


def build_pair_only_mock_font():
    """T×A=-100 only (parent is root)."""
    m_regular = _MockMaster("M_REG", "Regular", axes=[400])
    font = _MockFont(upm=1000)
    font.masters = [m_regular]
    layer = _MockLayer(width=700, paths=[], lsb=28, rsb=0)
    glyph_t = _MockGlyph("T", "0054", {m_regular.id: layer})
    glyph_a = _MockGlyph("A", "0041", {m_regular.id: _MockLayer(width=712, paths=[], lsb=28, rsb=0)})
    font.glyphs = _MockGlyphsList([glyph_t, glyph_a])
    font.setKerningForPair(m_regular.id, "T", "A", -100)
    return font


def build_hunck_like_mock_font():
    """Hunck-like: T×A=-100, T×@A=-100, A×T=-135; Aacute inherits @A left group absent."""
    m_regular = _MockMaster("M_REG", "Regular", axes=[400])
    font = _MockFont(upm=1000)
    font.masters = [m_regular]
    layer = _MockLayer(width=700, paths=[], lsb=28, rsb=0)

    glyph_t = _MockGlyph("T", "0054", {m_regular.id: layer})
    glyph_t.rightKerningGroup = "T"

    glyph_a = _MockGlyph("A", "0041", {m_regular.id: _MockLayer(width=712, paths=[], lsb=28, rsb=0)})
    glyph_a.leftKerningGroup = "A"

    glyph_aacute = _MockGlyph(
        "Aacute",
        "00C1",
        {m_regular.id: _MockLayer(width=702, paths=[], lsb=28, rsb=0)},
    )
    glyph_aacute.leftKerningGroup = "A"

    font.glyphs = _MockGlyphsList([glyph_t, glyph_a, glyph_aacute])
    font.setKerningForPair(m_regular.id, "T", "A", -100)
    font.setKerningForPair(m_regular.id, "T", "@MMK_R_A", -100)
    font.setKerningForPair(m_regular.id, "A", "T", -135)
    return font


def build_blocking_zero_mock_font():
    font = build_hunck_like_mock_font()
    m_regular = font.masters[0]
    font.setKerningForPair(m_regular.id, "T", "Aacute", 0)
    return font


def build_redundant_pair_mock_font():
    font = build_hunck_like_mock_font()
    return font


def build_class_slot_mock_font():
    """Only @T×@A in table; read {T,@A} inherits parent {@T,@A}."""
    m_regular = _MockMaster("M_REG", "Regular", axes=[400])
    font = _MockFont(upm=1000)
    font.masters = [m_regular]
    layer = _MockLayer(width=700, paths=[], lsb=28, rsb=0)

    glyph_t = _MockGlyph("T", "0054", {m_regular.id: layer})
    glyph_t.rightKerningGroup = "T"

    glyph_a = _MockGlyph("A", "0041", {m_regular.id: _MockLayer(width=712, paths=[], lsb=28, rsb=0)})
    glyph_a.leftKerningGroup = "A"

    font.glyphs = _MockGlyphsList([glyph_t, glyph_a])
    font.setKerningForPair(m_regular.id, "@MMK_L_T", "@MMK_R_A", -49)
    return font


def build_ghe_cy_mock_font():
    """Ghe-cy has groups but only class-keyed table rows."""
    m_regular = _MockMaster("M_REG", "Regular", axes=[400])
    font = _MockFont(upm=1000)
    font.masters = [m_regular]
    layer = _MockLayer(width=700, paths=[], lsb=28, rsb=0)

    ghe = _MockGlyph("Ghe-cy", "0403", {m_regular.id: layer})
    ghe.rightKerningGroup = "T"
    ghe.leftKerningGroup = "I"

    that = _MockGlyph("That", "0054", {m_regular.id: _MockLayer(width=700, paths=[])})
    that.rightKerningGroup = "T"

    font.glyphs = _MockGlyphsList([ghe, that])
    font.setKerningForPair(m_regular.id, "@MMK_L_V", "@MMK_R_T", -80)
    font.setKerningForPair(m_regular.id, "@MMK_L_T", "@MMK_R_V", -90)
    font.setKerningForPair(m_regular.id, "That", "@MMK_R_A", -50)
    return font


class _MismatchKerningFont(_MockFont):
    """Mock font whose kerningForPair diverges from table cascade (WARNING tests)."""

    def kerningForPair(self, master_id, left_key, right_key):
        if left_key == "T" and right_key == "Aacute":
            return 0
        return super().kerningForPair(master_id, left_key, right_key)


def build_warning_mock_font():
    font = build_hunck_like_mock_font()
    mismatch = _MismatchKerningFont(upm=font.upm)
    mismatch.masters = font.masters
    mismatch.glyphs = font.glyphs
    mismatch._kerning_flat = dict(font._kerning_flat)
    return mismatch
