# encoding: utf-8
def resolve_master(font, key):
    """Resolve a master by id or name, or return the first master if ``key`` is empty."""
    if key is None or str(key).strip() == "":
        masters = list(font.masters)
        return masters[0] if masters else None
    key_s = str(key).strip()
    for m in font.masters:
        if getattr(m, "id", None) == key_s:
            return m
    for m in font.masters:
        if getattr(m, "name", None) == key_s:
            return m
    key_low = key_s.lower()
    for m in font.masters:
        mname = (m.name or "").lower()
        if key_low in mname:
            return m
    return None

def resolve_glyph(font, name):
    """Look up a glyph by name, or by single-character unicode."""
    if not name:
        return None
    g = font.glyphs[name]
    if g is not None:
        return g
    if len(name) == 1:
        try:
            return font.glyphForCharacter_(ord(name))
        except Exception:
            return None
    return None

def master_axes_text(font, master):
    try:
        values = list(master.axes or [])
    except Exception:
        values = []
    if not values:
        return ""
    try:
        axes = list(getattr(font, "axes", []) or [])
        names = [getattr(a, "name", "?") for a in axes]
    except Exception:
        names = []
    parts = []
    for i, v in enumerate(values):
        label = names[i] if i < len(names) else ("axis%d" % i)
        parts.append("%s=%s" % (label, v))
    return "(" + ", ".join(parts) + ")"

def lookup_char(font, ch):
    if not ch:
        return None
    try:
        return font.glyphForCharacter_(ord(ch))
    except Exception:
        return None
