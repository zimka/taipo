# encoding: utf-8
"""
Frozen copy of ``tools.TOOL_SCHEMAS`` at ModelToolset interface migration.

Delete this fixture once the plugin uses ``ModelToolset.schemas()`` and the
legacy ``TOOL_SCHEMAS`` list is removed from ``tools.py``.
"""

LEGACY_TOOL_SCHEMAS = [
    {
        "name": "list_masters",
        "description": (
            "List all masters (weight/width/custom axes) of the currently open font. "
            "Returns master name, id and axis values."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_glyphs",
        "description": (
            "List glyph names in the current font, optionally filtered.\n\n"
            "Filter modes (all case-insensitive):\n"
            "  By name substring:  filter='cy'     → Dje-cy, Zhe-cy, ...\n"
            "  By unicode hex:     filter='0402'   → glyph at U+0402\n"
            "  By character:       filter='Ђ'      → glyph at U+0402\n"
            "  No filter:          returns all glyphs up to limit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": (
                        "Optional. Name substring ('cy'), unicode hex ('0402'), "
                        "or a literal character ('Ђ'). All modes are case-insensitive."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return. Default 200.",
                },
            },
        },
    },
    {
        "name": "get_glyph",
        "description": (
            "Return paths, nodes, anchors, components and metrics of a single glyph at a "
            "specific master, as structured text. Use this to reason about geometry.\n\n"
            "Node conventions: offcurve=N means this handle controls the curve node at index N. "
            "curve=[A,B] means the curve's two Bézier handles are at nodes A and B. "
            "Handles always immediately precede their curve node in path order (wrapping around "
            "for closed paths). smooth on a node means its tangent is continuous "
            "(handles on both sides are collinear; moving one adjusts the other automatically)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Glyph name (e.g. 'Dje-cy') or a single character.",
                },
                "master": {
                    "type": "string",
                    "description": "Master name or id. Defaults to the first master.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "render_specimen",
        "description": (
            "Render a short text using the CURRENT state of the open font and return a PNG "
            "image. Use the SAME text and master before and after a fix so renders are "
            "comparable by eye."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Specimen text (short; 1-20 characters is typical).",
                },
                "master": {
                    "type": "string",
                    "description": "Master name or id. Defaults to the first master.",
                },
                "size": {
                    "type": "integer",
                    "description": "Em size in pixels. Default 160.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "render_glyph",
        "description": (
            "Render a single glyph at large size with every node annotated by index number. "
            "Each path has a distinct color (7-color palette). Node shape encodes type: "
            "filled circle=line, filled circle with white halo=curve, hollow square=offcurve. "
            "Direct paths labeled path[N]; component nodes at 70% opacity labeled (BaseName)path[N]. "
            "Use this together with get_glyph to map node indices to their visual positions "
            "before writing numeric_judge code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Glyph name (e.g. 'Dje-cy') or a single character.",
                },
                "master": {
                    "type": "string",
                    "description": "Master name or id. Defaults to the first master.",
                },
                "size": {
                    "type": "integer",
                    "description": "Em size in pixels. Default 400.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "numeric_judge",
        "description": (
            "Run a Python snippet in a read-only geometry sandbox to measure distances, "
            "areas, angles, or ratios from node coordinates. The primary tool for confirming "
            "issues and validating fixes. Use print() for output; the captured stdout is "
            "returned. Runtime errors are returned as error messages.\n\n"
            "Sandbox bindings:\n"
            "  g[glyph_name][path_idx][node_idx] → {x, y, type, smooth, component}\n"
            "  dist(a, b)                    — Euclidean distance between two node dicts\n"
            "  seg_len(path, i, j)           — distance between nodes i and j in a path\n"
            "  bbox(path)                    — {x0, y0, x1, y1} of on-curve nodes\n"
            "  area(path)                    — shoelace area (on-curve nodes only)\n"
            "  angle(a, b)                   — bearing in degrees from a to b, range (-180, 180]\n"
            "  perpendicular_distance(p,a,b) — distance from node p to the line through a–b\n"
            "  projection(p, a, b)           — {x,y} foot of perpendicular from p onto line a–b\n"
            "  lerp(a, b, t)                 — {x,y} linear interpolation (t=0→a, t=1→b)\n"
            "  reflect(node, axis_x)         — {x,y} mirror of node about the vertical x=axis_x\n"
            "  tangent_at(path, node_idx)    — (dx,dy) unit tangent at a node; None for offcurve\n"
            "  transform_point(node,m11,m12,m21,m22,tx,ty) — {x,y} affine transform\n"
            "  math                          — full math module\n\n"
            "No imports. No file or network access."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "glyphs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Glyph names to load into the sandbox.",
                },
                "master": {
                    "type": "string",
                    "description": "Master name or id. Defaults to the first master.",
                },
                "code": {
                    "type": "string",
                    "description": "Python snippet. Use print() to output results. Max 4000 chars.",
                },
            },
            "required": ["glyphs", "code"],
        },
    },
    {
        "name": "move_nodes",
        "description": (
            "Move specific nodes in a path of a glyph by an offset. "
            "Addresses nodes by path index and node index (from get_glyph output). "
            "Multiple nodes in the same path can be shifted in one call. "
            "For nodes in different paths or different glyphs, use parallel tool calls. "
            "Call save_snapshot FIRST before any move_nodes so the user can undo. "
            "Use set_width when the advance width also needs to change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "glyph": {"type": "string", "description": "Glyph name."},
                "master": {"type": "string", "description": "Master name or id."},
                "path": {
                    "type": "integer",
                    "description": "Path index (0-based) from get_glyph output.",
                },
                "nodes": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Node indices within the path (0-based). Must be non-empty.",
                },
                "dx": {"type": "integer", "description": "X offset in font units."},
                "dy": {"type": "integer", "description": "Y offset in font units."},
            },
            "required": ["glyph", "master", "path", "nodes", "dx", "dy"],
        },
    },
    {
        "name": "set_width",
        "description": (
            "Set the advance width (spacing metric) of a glyph in one master. "
            "The advance width is separate from the outline — moving nodes does not change it. "
            "Use this together with move_nodes when widening or narrowing a glyph. "
            "Call save_snapshot FIRST so the user can undo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "glyph": {"type": "string", "description": "Glyph name."},
                "master": {"type": "string", "description": "Master name or id."},
                "width": {
                    "type": "integer",
                    "description": "New advance width in font units. Must be non-negative.",
                },
            },
            "required": ["glyph", "master", "width"],
        },
    },
    {
        "name": "save_snapshot",
        "description": (
            "Capture the current geometry (node positions, anchors, widths across all masters) "
            "of the listed glyphs. One slot only — a second call overwrites. You MUST call "
            "this BEFORE the first move_nodes in a fix so the user (or you) can revert "
            "via reset_snapshot and so render_diff can render the overlay comparison."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "glyph_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Glyph names you plan to edit. Must be non-empty.",
                }
            },
            "required": ["glyph_names"],
        },
    },
    {
        "name": "reset_snapshot",
        "description": (
            "Restore the geometry saved by save_snapshot. Use when your edits went the wrong "
            "way and you want to revise the plan, or to undo an exploratory attempt. The "
            "snapshot itself is kept (a reset can be applied multiple times)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "render_diff",
        "description": (
            "Render a red/green overlay comparing the snapshot geometry (red) against the "
            "current live font (green). Yellow pixels are overlap. "
            "Requires an active snapshot — call save_snapshot first. "
            "Call this after move_nodes to show the user the visual before/after difference."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Specimen text (same as was used for render_specimen)."},
                "master": {"type": "string", "description": "Master name or id. Defaults to the first master."},
                "size": {"type": "integer", "description": "Em size in pixels. Default 160."},
            },
            "required": ["text"],
        },
    },
]
