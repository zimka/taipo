# encoding: utf-8
from tools.font_access import resolve_glyph, resolve_master
from tools.geometry import apply_transform, get_component_transform


def handle_numeric_judge(args, ctx, font):
    import builtins
    import contextlib
    import io
    import math as _math

    glyphs_raw = args.get("glyphs")
    if not glyphs_raw:
        return "[error] 'glyphs' is required."
    if isinstance(glyphs_raw, str):
        glyphs_raw = [glyphs_raw]
    if not isinstance(glyphs_raw, list):
        return "[error] 'glyphs' must be a list of glyph names."

    code = str(args.get("code") or "").strip()
    if not code:
        return "[error] 'code' is required."
    if len(code) > 4000:
        return "[error] 'code' too long (max 4000 chars)."

    master = resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")

    # Build geometry dict: g[name][path_idx][node_idx] = {x, y, type, smooth, component}
    # For composite glyphs, component nodes are included with their transforms applied.
    # x, y are always in the requested glyph's coordinate space (for measurement).
    # "component" is the base glyph name (None for direct paths) — use this as the
    # glyph argument to move_nodes when editing.
    g = {}
    for name_raw in glyphs_raw:
        name = str(name_raw).strip()
        if not name:
            continue
        glyph = resolve_glyph(font, name)
        if glyph is None:
            return "[error] Glyph not found: %s" % name
        layer = glyph.layers[master.id]
        if layer is None:
            return "[error] Glyph %s has no layer for master %s." % (name, master.name)
        paths_data = []

        # Direct paths
        for path in (layer.paths or []):
            nodes_data = []
            for node in (path.nodes or []):
                nodes_data.append({
                    "x": int(round(float(node.position.x))),
                    "y": int(round(float(node.position.y))),
                    "type": getattr(node, "type", "line") or "line",
                    "smooth": bool(getattr(node, "smooth", False)),
                    "component": None,
                })
            paths_data.append(nodes_data)

        # Component paths (transformed into this glyph's coordinate space)
        for comp in (getattr(layer, "components", None) or []):
            comp_name = getattr(comp, "componentName", None) or "?"
            base_glyph = resolve_glyph(font, comp_name)
            if base_glyph is None:
                continue
            try:
                base_layer = base_glyph.layers[master.id]
            except Exception:
                base_layer = None
            if base_layer is None:
                continue
            m11, m12, m21, m22, ctx_, cty = get_component_transform(comp)
            for path in (base_layer.paths or []):
                nodes_data = []
                for node in (path.nodes or []):
                    bx, by = float(node.position.x), float(node.position.y)
                    cx, cy = apply_transform(m11, m12, m21, m22, ctx_, cty, bx, by)
                    nodes_data.append({
                        "x": int(round(cx)),
                        "y": int(round(cy)),
                        "type": getattr(node, "type", "line") or "line",
                        "smooth": bool(getattr(node, "smooth", False)),
                        "component": comp_name,
                    })
                paths_data.append(nodes_data)

        g[name] = paths_data

    # Helper functions injected into sandbox
    def dist(a, b):
        return _math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)

    def seg_len(path, i, j):
        return dist(path[i], path[j])

    def bbox(path):
        pts = [n for n in path if n.get("type") != "offcurve"] or list(path)
        if not pts:
            return {"x0": 0, "y0": 0, "x1": 0, "y1": 0}
        return {
            "x0": min(n["x"] for n in pts),
            "y0": min(n["y"] for n in pts),
            "x1": max(n["x"] for n in pts),
            "y1": max(n["y"] for n in pts),
        }

    def area(path):
        pts = [n for n in path if n.get("type") != "offcurve"] or list(path)
        n = len(pts)
        if n < 3:
            return 0.0
        s = sum(
            pts[i]["x"] * pts[(i + 1) % n]["y"] - pts[(i + 1) % n]["x"] * pts[i]["y"]
            for i in range(n)
        )
        return abs(s) / 2.0

    def angle(a, b):
        """Bearing in degrees from node a to node b. Range: (-180, 180]."""
        return _math.degrees(_math.atan2(b["y"] - a["y"], b["x"] - a["x"]))

    def perpendicular_distance(p, a, b):
        """Distance from node p to the infinite line through a and b.

        Correct for measuring stem width along any stroke direction.
        Returns dist(p, a) if a and b coincide.
        """
        dx = b["x"] - a["x"]
        dy = b["y"] - a["y"]
        mag = _math.sqrt(dx * dx + dy * dy)
        if mag < 1e-10:
            return dist(p, a)
        return abs(dx * (a["y"] - p["y"]) - (a["x"] - p["x"]) * dy) / mag

    def projection(p, a, b):
        """Foot of the perpendicular from node p onto the line through a and b.

        Returns a node dict {x, y} at the closest point on the line.
        """
        dx = b["x"] - a["x"]
        dy = b["y"] - a["y"]
        mag2 = dx * dx + dy * dy
        if mag2 < 1e-10:
            return {"x": float(a["x"]), "y": float(a["y"])}
        t = ((p["x"] - a["x"]) * dx + (p["y"] - a["y"]) * dy) / mag2
        return {"x": a["x"] + t * dx, "y": a["y"] + t * dy}

    def lerp(a, b, t):
        """Linear interpolation between nodes a and b at parameter t.

        t=0 returns a, t=1 returns b, t=0.5 returns the midpoint.
        Returns a node dict {x, y}.
        """
        return {
            "x": a["x"] + t * (b["x"] - a["x"]),
            "y": a["y"] + t * (b["y"] - a["y"]),
        }

    def reflect(node, axis_x):
        """Mirror node about the vertical line x = axis_x.

        Returns a node dict {x, y}.
        """
        return {"x": 2.0 * axis_x - node["x"], "y": float(node["y"])}

    def tangent_at(path, node_idx):
        """Unit tangent vector (dx, dy) at node_idx in path.

        For curve nodes: direction from the preceding offcurve handle to the node.
        For line nodes: direction of the incoming segment.
        Returns None for offcurve nodes (undefined).
        Returns (0.0, 0.0) if the segment has zero length.
        """
        n = len(path)
        node = path[node_idx]
        if node.get("type") == "offcurve":
            return None
        prev = path[(node_idx - 1) % n]
        dx = node["x"] - prev["x"]
        dy = node["y"] - prev["y"]
        mag = _math.sqrt(dx * dx + dy * dy)
        if mag < 1e-10:
            return (0.0, 0.0)
        return (dx / mag, dy / mag)

    def transformpoint(node, m11, m12, m21, m22, tx, ty):
        """Apply a 2-D affine transform to a node dict.

        Same convention as GSComponent.transform:
          x' = m11*x + m21*y + tx
          y' = m12*x + m22*y + ty
        Returns a node dict {x, y}.
        """
        x, y = float(node["x"]), float(node["y"])
        return {"x": m11 * x + m21 * y + tx, "y": m12 * x + m22 * y + ty}

    _SAFE_BUILTINS = {
        k: getattr(builtins, k)
        for k in (
            "abs", "min", "max", "round", "int", "float", "len",
            "range", "enumerate", "zip", "sum", "sorted", "print",
            "str", "bool", "list", "tuple", "dict", "set",
            "True", "False", "None", "isinstance",
        )
    }
    _SAFE_BUILTINS["math"] = _math

    sandbox = {
        "__builtins__": _SAFE_BUILTINS,
        "g": g,
        "dist": dist,
        "seg_len": seg_len,
        "bbox": bbox,
        "area": area,
        "angle": angle,
        "perpendicular_distance": perpendicular_distance,
        "projection": projection,
        "lerp": lerp,
        "reflect": reflect,
        "tangent_at": tangent_at,
        "transformpoint": transformpoint,
        "transform_point": transformpoint,
    }

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<numeric_judge>", "exec"), sandbox)
    except Exception as e:
        prefix = buf.getvalue()
        err = "[error] %s: %s" % (type(e).__name__, e)
        return (prefix + "\n" + err).strip() if prefix else err

    output = buf.getvalue()
    return output if output.strip() else "(no output — add print() statements to your code)"
