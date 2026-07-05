# encoding: utf-8

from tools.font_access import master_axes_text, resolve_glyph, resolve_master
from tools.formatting import fmt_num
from tools.geometry import apply_transform, describe_transform, get_component_transform
from tools.anchors import anchor_name, iter_layer_anchors

# encoding: utf-8
def handle_list_masters(args, ctx, font):
    lines = []
    for i, m in enumerate(font.masters):
        axes = master_axes_text(font, m)
        lines.append("[%d] id=%s name=%s%s" % (i, m.id, m.name, (" " + axes) if axes else ""))
    if not lines:
        return "(no masters)"
    return "masters:\n" + "\n".join(lines)

def handle_list_glyphs(args, ctx, font):
    raw_flt = str(args.get("filter") or "").strip()
    flt = raw_flt.lower()

    # If the filter is a single character, also match its unicode codepoint as a hex
    # substring so e.g. filter='Ђ' matches the glyph whose unicode field is '0402'.
    char_hex = None
    if len(raw_flt) == 1:
        char_hex = "%04X" % ord(raw_flt)

    try:
        limit = int(args.get("limit") or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(2000, limit))

    out = []
    for g in font.glyphs:
        name = g.name or ""
        uni = (g.unicode or "") if hasattr(g, "unicode") else ""
        if flt:
            uni_lower = str(uni).lower()
            if (flt not in name.lower()
                    and flt not in uni_lower
                    and (char_hex is None or char_hex.lower() not in uni_lower)):
                continue
        out.append("%s%s" % (name, (" U+" + uni) if uni else ""))
        if len(out) >= limit:
            break
    if not out:
        return "(no glyphs matched filter=%r)" % raw_flt
    header = "glyphs (%d shown%s):" % (len(out), ", filter=%r" % raw_flt if flt else "")
    return header + "\n" + "\n".join(out)

def handle_get_glyph(args, ctx, font):
    name = str(args.get("name") or "").strip()
    if not name:
        return "[error] 'name' is required."
    glyph = resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    master = resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    layer = glyph.layers[master.id]
    if layer is None:
        return "[error] Glyph %s has no layer for master %s." % (name, master.name)
    return dump_layer(glyph, master, layer, font=font)

def dump_layer(glyph, master, layer, font=None):
    uni = (glyph.unicode or "") if hasattr(glyph, "unicode") else ""
    header = "glyph: %s%s" % (glyph.name, (" U+" + uni) if uni else "")
    lines = [
        header,
        "master: %s (id=%s)" % (master.name, master.id),
        "width: %s" % fmt_num(layer.width),
    ]
    paths = list(layer.paths or [])
    lines.append("paths: %d" % len(paths))
    for pi, path in enumerate(paths):
        closed = getattr(path, "closed", True)
        nodes = list(path.nodes or [])
        n = len(nodes)
        lines.append("  path[%d] closed=%s nodes=%d" % (pi, bool(closed), n))

        # Pass 1: build handle↔curve index maps for Bézier nodes.
        # For each curve node at index i, the two immediately preceding nodes
        # (cyclically) are its offcurve handles at (i-2)%n and (i-1)%n.
        offcurve_for = {}   # handle_index → curve_index
        curve_handles = {}  # curve_index → (handle_a_index, handle_b_index)
        if n >= 3:
            for ni, node in enumerate(nodes):
                t = getattr(node, "type", "") or ""
                if t == "curve":
                    a = (ni - 2) % n
                    b = (ni - 1) % n
                    offcurve_for[a] = ni
                    offcurve_for[b] = ni
                    curve_handles[ni] = (a, b)

        # Pass 2: render each node with relationship annotations.
        for ni, node in enumerate(nodes):
            t = getattr(node, "type", "") or ""
            x = fmt_num(node.position.x)
            y = fmt_num(node.position.y)
            smooth = " smooth" if getattr(node, "smooth", False) else ""
            if t == "offcurve":
                curve_idx = offcurve_for.get(ni)
                suffix = (" offcurve=%d" % curve_idx) if curve_idx is not None else " offcurve=?"
                lines.append("    node[%d] x=%s y=%s%s" % (ni, x, y, suffix))
            elif t == "curve":
                handles = curve_handles.get(ni)
                if handles is not None:
                    lines.append(
                        "    node[%d] x=%s y=%s curve=[%d,%d]%s"
                        % (ni, x, y, handles[0], handles[1], smooth)
                    )
                else:
                    lines.append("    node[%d] x=%s y=%s curve%s" % (ni, x, y, smooth))
            else:
                # line node (default on-curve) — no type keyword
                lines.append("    node[%d] x=%s y=%s%s" % (ni, x, y, smooth))

    anchors = list(iter_layer_anchors(layer))
    lines.append("anchors: %d" % len(anchors))
    for a in anchors:
        nm = anchor_name(a) or "?"
        lines.append(
            "  %s (x=%s, y=%s)"
            % (nm, fmt_num(a.position.x), fmt_num(a.position.y))
        )
    comps = list(getattr(layer, "components", []) or [])
    lines.append("components: %d" % len(comps))
    for c in comps:
        cname = getattr(c, "componentName", "?")
        m11, m12, m21, m22, tx, ty = get_component_transform(c)
        tdesc = describe_transform(m11, m12, m21, m22, tx, ty)
        lines.append("  %s [%s]" % (cname, tdesc))
    if font is not None:
        users = set()
        for g in font.glyphs:
            for m in font.masters:
                try:
                    lyr = g.layers[m.id]
                except Exception:
                    lyr = None
                if lyr is None:
                    continue
                for comp in (getattr(lyr, "components", None) or []):
                    if getattr(comp, "componentName", None) == glyph.name:
                        users.add(g.name)
                        break
        if users:
            lines.append(
                "used as component in (%d): %s" % (len(users), ", ".join(sorted(users)))
            )
        else:
            lines.append("used as component in: (none)")

    return "\n".join(lines)
