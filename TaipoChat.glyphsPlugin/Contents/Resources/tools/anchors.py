# encoding: utf-8
def anchor_name(anchor):
    try:
        return str(anchor.name)
    except Exception:
        return None

def iter_layer_anchors(layer):
    """Yield GSAnchor objects from ``layer.anchors``.

    Glyphs 3 exposes anchors as a list of GSAnchor. Glyphs 4 exposes an NSDictionary
    where iteration yields anchor name strings; resolve via ``anchorForName_`` or subscript.
    """
    raw = getattr(layer, "anchors", None)
    if not raw:
        return
    anchor_for_name = getattr(layer, "anchorForName_", None)
    for item in raw:
        if hasattr(item, "position"):
            yield item
            continue
        nm = str(item)
        anchor = None
        if anchor_for_name is not None:
            try:
                anchor = anchor_for_name(nm)
            except Exception:
                anchor = None
        if anchor is None:
            try:
                anchor = raw[nm]
            except Exception:
                try:
                    anchor = raw[item]
                except Exception:
                    anchor = None
        if anchor is not None and hasattr(anchor, "position"):
            yield anchor
