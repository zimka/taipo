# encoding: utf-8

from tools.font_access import resolve_glyph, resolve_master
from tools.formatting import int_or_none
from tools.geometry import point

# encoding: utf-8
def handle_move_nodes(args, ctx, font):
    name = str(args.get("glyph") or "").strip()
    if not name:
        return "[error] 'glyph' is required."
    glyph = resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    master = resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    layer = glyph.layers[master.id]
    if layer is None:
        return "[error] Glyph %s has no layer for master %s." % (name, master.name)

    path_idx = args.get("path")
    if path_idx is None:
        return "[error] 'path' is required."
    try:
        path_idx = int(path_idx)
    except (TypeError, ValueError):
        return "[error] 'path' must be an integer."

    node_indices_raw = args.get("nodes")
    if node_indices_raw is None:
        return "[error] 'nodes' is required."
    if not isinstance(node_indices_raw, list):
        return "[error] 'nodes' must be a list."
    if len(node_indices_raw) == 0:
        return "[error] 'nodes' must be non-empty."
    try:
        node_indices = [int(i) for i in node_indices_raw]
    except (TypeError, ValueError):
        return "[error] 'nodes' must be a list of integers."

    dx = int_or_none(args.get("dx")) or 0
    dy = int_or_none(args.get("dy")) or 0
    if dx == 0 and dy == 0:
        return "[error] dx and dy cannot both be 0."

    paths = list(layer.paths or [])
    if path_idx < 0 or path_idx >= len(paths):
        return "[error] path %d out of range (glyph has %d path(s))." % (path_idx, len(paths))

    path = paths[path_idx]
    nodes = list(path.nodes or [])
    n_nodes = len(nodes)

    bad = [i for i in node_indices if i < 0 or i >= n_nodes]
    if bad:
        return "[error] node index/indices out of range for path %d (has %d nodes): %s" % (
            path_idx, n_nodes, bad
        )

    moved = []
    for ni in node_indices:
        node = nodes[ni]
        ox = int(round(node.position.x))
        oy = int(round(node.position.y))
        new_x = ox + dx
        new_y = oy + dy
        node.position = point(new_x, new_y)
        moved.append((ni, ox, oy, new_x, new_y))

    lines = [
        "Moved %d node(s) in %s@%s path[%d] by dx=%d, dy=%d:"
        % (len(moved), name, master.name, path_idx, dx, dy)
    ]
    for ni, ox, oy, nx, ny in moved:
        lines.append("  node[%d] (%d,%d) -> (%d,%d)" % (ni, ox, oy, nx, ny))
    return "\n".join(lines)

def handle_set_width(args, ctx, font):
    name = str(args.get("glyph") or "").strip()
    if not name:
        return "[error] 'glyph' is required."
    glyph = resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    master = resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    layer = glyph.layers[master.id]
    if layer is None:
        return "[error] Glyph %s has no layer for master %s." % (name, master.name)

    width_raw = args.get("width")
    if width_raw is None:
        return "[error] 'width' is required."
    width = int_or_none(width_raw)
    if width is None:
        return "[error] 'width' must be an integer."
    if width < 0:
        return "[error] 'width' must be non-negative."

    old_width = int(round(float(layer.width)))
    layer.width = width
    return "set_width %s@%s: %d -> %d" % (name, master.name, old_width, width)
