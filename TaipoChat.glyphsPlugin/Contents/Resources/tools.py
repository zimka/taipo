# encoding: utf-8
"""
Agent tools for Taipo Chat.

Twelve tools are exposed to the model via OpenAI ``tools`` parameter:

Read-only:
- ``list_masters``       — enumerate masters of the current font.
- ``list_glyphs``        — list glyph names (optional substring filter).
- ``get_glyph``          — dump a glyph's paths/nodes/anchors/metrics as text.
- ``render_specimen``    — rasterize a text using the current font state (returns PNG).
- ``visually_judge``     — render specimen and ask a stateless VLM judge for a structured verdict.
- ``render_glyph``       — render one glyph at large scale with node-index and path-label overlays.
- ``numeric_judge``      — run a Python snippet in a geometry sandbox (node coords, dist/bbox/area helpers).

Edit:
- ``move_nodes``         — move specific nodes in a path by index and offset.
- ``set_width``          — set the advance width (spacing metric) of a glyph in one master.

Snapshot / diff:
- ``save_snapshot``      — capture geometry of the listed glyphs (one slot, overwrites).
- ``reset_snapshot``     — restore the saved geometry (revert edits).
- ``render_diff``        — render a red/green overlay (snapshot vs live font).

The rendering and Glyphs-SDK code paths import ``AppKit`` / ``GlyphsApp`` lazily so this
module can still be imported from non-Glyphs unit tests.
"""

import json
import math

DEFAULT_RENDER_CONTRACT = {
    "canvas_w": 900,
    "canvas_h": 260,
    "margin_x": 24,
    "em_px": 160.0,
    "baseline_y": 56.0,
    "unknown_advance_upm": 250.0,
}


TOOL_SCHEMAS = [
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
            "List glyph names in the current font. Optionally filter by a case-insensitive "
            "substring match against glyph name or unicode value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional substring, e.g. 'cy', 'Dje', '0402'.",
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
        "name": "visually_judge",
        "description": (
            "Render the specimen text internally and send it to a stateless visual judge model "
            "for perceptual evaluation. Provide a specific TRUE/FALSE accusation about the "
            "font's visual state. The judge returns a structured verdict.\n\n"
            "Use this BEFORE making changes to confirm an issue is real, and AFTER changes "
            "to confirm the issue is resolved (accusation should then be FALSE).\n\n"
            'Returns JSON: {"verdict": "TRUE|FALSE|UNCERTAIN|INVALID", "reasoning": "..."}\n'
            "- TRUE: accusation is visually confirmed\n"
            "- FALSE: accusation is visually refuted (use this to confirm DoD after a fix)\n"
            "- UNCERTAIN: question is valid but answer is ambiguous\n"
            "- INVALID: text doesn't contain referenced glyphs or accusation is not visually verifiable"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "accusation": {
                    "type": "string",
                    "description": (
                        "A specific, visually verifiable TRUE/FALSE claim about the current font state. "
                        "Example: 'The left serif of the lowercase n is shorter than the right serif.'"
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Specimen text to render. Must contain the relevant glyphs.",
                },
                "master": {
                    "type": "string",
                    "description": "Master name or id. Defaults to the first master.",
                },
            },
            "required": ["accusation", "text"],
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
            "areas, or ratios from node coordinates. Use print() for output; the captured "
            "stdout is returned. Runtime errors are returned as error messages.\n\n"
            "Sandbox bindings:\n"
            "  g[glyph_name][path_idx][node_idx] → {x, y, type, smooth}\n"
            "  dist(a, b)         — Euclidean distance between two node dicts\n"
            "  seg_len(path, i, j)— distance between node i and j in a path\n"
            "  bbox(path)         — {x0, y0, x1, y1} of on-curve nodes\n"
            "  area(path)         — shoelace area (on-curve nodes only)\n"
            "  math               — full math module\n\n"
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


class ToolContext:
    """Plugin-level state passed to every tool call."""

    def __init__(self, font_provider, render_contract=None, snapshot_store=None, api_settings=None):
        self._font_provider = font_provider
        self.render_contract = dict(render_contract or DEFAULT_RENDER_CONTRACT)
        self.snapshot_store = snapshot_store if snapshot_store is not None else SnapshotStore()
        self.api_settings = api_settings if api_settings is not None else {}

    @property
    def font(self):
        return self._font_provider()


def execute_tool(name, args, ctx):
    """Dispatch a tool call. Returns content accepted by ``normalize_tool_result_content``."""
    font = ctx.font
    if font is None:
        return "[error] No font is open in Glyphs."
    handler = _HANDLERS.get(name)
    if handler is None:
        return "[error] Unknown tool: %s" % name
    return handler(args or {}, ctx, font)


def _handle_list_masters(args, ctx, font):
    lines = []
    for i, m in enumerate(font.masters):
        axes = _master_axes_text(font, m)
        lines.append("[%d] id=%s name=%s%s" % (i, m.id, m.name, (" " + axes) if axes else ""))
    if not lines:
        return "(no masters)"
    return "masters:\n" + "\n".join(lines)


def _handle_list_glyphs(args, ctx, font):
    flt = str(args.get("filter") or "").strip().lower()
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
            if flt not in name.lower() and flt not in str(uni).lower():
                continue
        out.append("%s%s" % (name, (" U+" + uni) if uni else ""))
        if len(out) >= limit:
            break
    if not out:
        return "(no glyphs matched filter=%r)" % flt
    header = "glyphs (%d shown%s):" % (len(out), ", filter=%r" % flt if flt else "")
    return header + "\n" + "\n".join(out)


def _handle_get_glyph(args, ctx, font):
    name = str(args.get("name") or "").strip()
    if not name:
        return "[error] 'name' is required."
    glyph = _resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    master = _resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    layer = glyph.layers[master.id]
    if layer is None:
        return "[error] Glyph %s has no layer for master %s." % (name, master.name)
    return _dump_layer(glyph, master, layer, font=font)


def _handle_render_specimen(args, ctx, font):
    text = str(args.get("text") or "")
    if not text:
        return "[error] 'text' is required."
    master = _resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    contract = dict(ctx.render_contract)
    try:
        size = int(args.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > 0:
        contract["em_px"] = float(size)
        contract["canvas_h"] = int(max(contract["canvas_h"], size * 1.6))
    png_bytes = _render_layer_run(font, master, text, contract)
    header = (
        "render_specimen master=%s size=%s text=%r canvas=%dx%d"
        % (master.name, int(contract.get("em_px", 0)), text,
           contract.get("canvas_w"), contract.get("canvas_h"))
    )
    return [header, png_bytes]


def _handle_render_glyph(args, ctx, font):
    name = str(args.get("name") or "").strip()
    if not name:
        return "[error] 'name' is required."
    glyph = _resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    master = _resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    layer = glyph.layers[master.id]
    if layer is None:
        return "[error] Glyph %s has no layer for master %s." % (name, master.name)

    try:
        size = int(args.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    em_px = float(size) if size > 0 else _GLYPH_RENDER_EM_PX

    png_bytes = _render_glyph_annotated(font, master, glyph, layer, em_px)
    paths = list(layer.paths or [])
    n_nodes = sum(len(list(p.nodes or [])) for p in paths)
    header = (
        "render_glyph %s@%s em_px=%d paths=%d nodes=%d"
        % (name, master.name, int(em_px), len(paths), n_nodes)
    )
    return [header, png_bytes]


def _handle_move_nodes(args, ctx, font):
    name = str(args.get("glyph") or "").strip()
    if not name:
        return "[error] 'glyph' is required."
    glyph = _resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    master = _resolve_master(font, args.get("master"))
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

    dx = _int_or_none(args.get("dx")) or 0
    dy = _int_or_none(args.get("dy")) or 0
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
        node.position = _point(new_x, new_y)
        moved.append((ni, ox, oy, new_x, new_y))

    lines = [
        "Moved %d node(s) in %s@%s path[%d] by dx=%d, dy=%d:"
        % (len(moved), name, master.name, path_idx, dx, dy)
    ]
    for ni, ox, oy, nx, ny in moved:
        lines.append("  node[%d] (%d,%d) -> (%d,%d)" % (ni, ox, oy, nx, ny))
    return "\n".join(lines)


def _handle_set_width(args, ctx, font):
    name = str(args.get("glyph") or "").strip()
    if not name:
        return "[error] 'glyph' is required."
    glyph = _resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    master = _resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    layer = glyph.layers[master.id]
    if layer is None:
        return "[error] Glyph %s has no layer for master %s." % (name, master.name)

    width_raw = args.get("width")
    if width_raw is None:
        return "[error] 'width' is required."
    width = _int_or_none(width_raw)
    if width is None:
        return "[error] 'width' must be an integer."
    if width < 0:
        return "[error] 'width' must be non-negative."

    old_width = int(round(float(layer.width)))
    layer.width = width
    return "set_width %s@%s: %d -> %d" % (name, master.name, old_width, width)


def _handle_save_snapshot(args, ctx, font):
    names_raw = args.get("glyph_names")
    if isinstance(names_raw, str):
        names_raw = [names_raw]
    if not isinstance(names_raw, list) or not names_raw:
        return "[error] 'glyph_names' must be a non-empty list of glyph names."
    names = [str(n).strip() for n in names_raw if str(n).strip()]
    if not names:
        return "[error] 'glyph_names' must contain non-empty strings."
    missing = [n for n in names if _resolve_glyph(font, n) is None]
    if missing:
        return "[error] Glyph not found: %s" % ", ".join(missing)

    had_prev = ctx.snapshot_store.has_snapshot()
    info = ctx.snapshot_store.save(font, names)
    prefix = "Overwrote previous snapshot. " if had_prev else ""
    return "%sSnapshot saved for %d glyph(s) across %d layer(s): %s" % (
        prefix,
        len(info["glyph_names"]),
        info["layers"],
        ", ".join(info["glyph_names"]),
    )


def _handle_reset_snapshot(args, ctx, font):
    if not ctx.snapshot_store.has_snapshot():
        return "[error] No active snapshot — call save_snapshot first."
    info = ctx.snapshot_store.reset(font)
    return "Snapshot restored: %d glyph(s) reverted (%s). Snapshot is still active." % (
        len(info["glyph_names"]),
        ", ".join(info["glyph_names"]),
    )


def _handle_render_diff(args, ctx, font):
    text = str(args.get("text") or "")
    if not text:
        return "[error] 'text' is required."
    master = _resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")
    if not ctx.snapshot_store.has_snapshot():
        return "[error] No active snapshot — call save_snapshot([...]) before render_diff."

    contract = dict(ctx.render_contract)
    size = args.get("size")
    if size is not None:
        try:
            contract["em_px"] = float(size)
        except (TypeError, ValueError):
            return "[error] 'size' must be a number."

    store = ctx.snapshot_store
    contract = _tight_canvas_contract(font, master, text, contract)
    overlay_png = _render_overlay_run(font, master, text, contract, store)
    header = "render_diff master=%s text=%r snapshot_glyphs=%s" % (
        master.name,
        text,
        list(store._glyph_names),
    )
    return [header, overlay_png]


def _handle_visually_judge(args, ctx, font):
    import base64
    import json as _json

    accusation = str(args.get("accusation") or "").strip()
    text = str(args.get("text") or "").strip()
    if not accusation:
        return "[error] 'accusation' is required."
    if not text:
        return "[error] 'text' is required."

    master = _resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")

    api_settings = getattr(ctx, "api_settings", None) or {}
    base_url = str(api_settings.get("baseUrl") or "").strip()
    api_key = str(api_settings.get("apiKey") or "").strip()
    model = str(api_settings.get("model") or "").strip()

    if not base_url:
        return "[error] visually_judge: no base URL configured."
    if not api_key:
        return "[error] visually_judge: no API key configured."
    if not model:
        return "[error] visually_judge: no model configured."

    judge_contract = dict(ctx.render_contract)
    judge_contract["em_px"] = _JUDGE_EM_PX
    try:
        png_bytes = _render_layer_run(font, master, text, judge_contract)
    except Exception as e:
        return "[error] visually_judge: render failed: %s" % e

    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_url = "data:image/png;base64,%s" % b64

    # Collect full glyph geometry for every unique character in text.
    glyph_dumps = []
    seen_chars = set()
    for ch in text:
        if ch in seen_chars:
            continue
        seen_chars.add(ch)
        g = _lookup_char(font, ch)
        if g is None:
            continue
        try:
            lyr = g.layers[master.id]
        except Exception:
            lyr = None
        if lyr is None:
            continue
        glyph_dumps.append(_dump_layer(g, master, lyr))

    _JUDGE_SYSTEM = (
        "You are a type-design visual reviewer.\n"
        "Your task: evaluate whether the stated accusation is true for the current font state.\n"
        "You are given both a rendered image and exact glyph geometry (paths, node coordinates).\n"
        "Use both: the image for overall visual impression, the geometry for precise measurements.\n"
        "Focus on: stroke thickness, serif dimensions, counter openness,\n"
        "spacing, optical weight, rhythm, proportions.\n"
        "Be accurate and unbiased — report what you observe.\n"
        "Do not lean toward confirming or denying.\n"
        'Return ONLY valid JSON: {"verdict": "...", "reasoning": "..."}\n'
        "verdict must be exactly one of: TRUE, FALSE, UNCERTAIN, INVALID\n"
        "Do not include markdown fences or extra keys."
    )

    _VALID_VERDICTS = {"TRUE", "FALSE", "UNCERTAIN", "INVALID"}
    _FALLBACK = _json.dumps({
        "verdict": "UNCERTAIN",
        "reasoning": "Judge could not produce a valid response.",
    })

    def _validate(raw):
        raw = (raw or "").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(l for l in lines if not l.startswith("```")).strip()
        try:
            d = _json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(d, dict):
            return None
        if d.get("verdict") not in _VALID_VERDICTS:
            return None
        if not isinstance(d.get("reasoning"), str):
            return None
        return _json.dumps({"verdict": d["verdict"], "reasoning": d["reasoning"]})

    from utils import _chat_endpoint
    import provider as _pmod

    url = _chat_endpoint(base_url)

    user_text_parts = ["Accusation: %s" % accusation]
    if glyph_dumps:
        user_text_parts.append("Glyph geometry:\n\n" + "\n\n".join(glyph_dumps))
    user_text_parts.append(
        "Does the image and glyph geometry confirm this accusation?"
    )
    user_text = "\n\n".join(user_text_parts)

    def _call(prompt_text):
        body = {
            "model": model,
            "max_completion_tokens": 512,
            "messages": [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
        }
        return _pmod.post_request(body, url, api_key)

    try:
        payload = _call(user_text)
    except Exception as e:
        return "[error] visually_judge: HTTP error: %s" % e

    parsed = _pmod.parse_response(payload)
    if parsed.get("error"):
        return "[error] visually_judge: %s" % parsed["error"]

    result = _validate(parsed.get("text") or "")
    if result is not None:
        return result

    # Retry once with explicit reprompt
    retry_text = (
        user_text
        + "\n\nYour previous response was not valid JSON. "
        'Return ONLY valid JSON with verdict and reasoning keys. '
        'Example: {"verdict": "TRUE", "reasoning": "The counter is clearly too tight."}'
    )
    try:
        payload2 = _call(retry_text)
    except Exception:
        return _FALLBACK

    parsed2 = _pmod.parse_response(payload2)
    if parsed2.get("error"):
        return _FALLBACK

    result2 = _validate(parsed2.get("text") or "")
    return result2 if result2 is not None else _FALLBACK


def _handle_numeric_judge(args, ctx, font):
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

    master = _resolve_master(font, args.get("master"))
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
        glyph = _resolve_glyph(font, name)
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
            base_glyph = _resolve_glyph(font, comp_name)
            if base_glyph is None:
                continue
            try:
                base_layer = base_glyph.layers[master.id]
            except Exception:
                base_layer = None
            if base_layer is None:
                continue
            m11, m12, m21, m22, ctx_, cty = _get_component_transform(comp)
            for path in (base_layer.paths or []):
                nodes_data = []
                for node in (path.nodes or []):
                    bx, by = float(node.position.x), float(node.position.y)
                    cx, cy = _apply_transform(m11, m12, m21, m22, ctx_, cty, bx, by)
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



_HANDLERS = {
    "list_masters": _handle_list_masters,
    "list_glyphs": _handle_list_glyphs,
    "get_glyph": _handle_get_glyph,
    "render_specimen": _handle_render_specimen,
    "visually_judge": _handle_visually_judge,
    "render_glyph": _handle_render_glyph,
    "numeric_judge": _handle_numeric_judge,
    "move_nodes": _handle_move_nodes,
    "set_width": _handle_set_width,
    "save_snapshot": _handle_save_snapshot,
    "reset_snapshot": _handle_reset_snapshot,
    "render_diff": _handle_render_diff,
}


def _resolve_master(font, key):
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


def _resolve_glyph(font, name):
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


def _master_axes_text(font, master):
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


def _int_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None


class _PyPoint:
    """Fallback point type when Foundation is unavailable (unit tests outside Glyphs)."""

    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


def _point(x, y):
    """Build a point value that ``GSNode.position`` accepts."""
    try:
        from Foundation import NSMakePoint

        return NSMakePoint(x, y)
    except Exception:
        return _PyPoint(x, y)


# ---------------------------------------------------------------------------
# Snapshot store: one slot of per-glyph-per-master geometry (node positions,
# anchors, widths). Sufficient to undo any move_nodes_where sequence because
# that tool only changes positions, not topology. Stored as plain dicts so we
# don't keep references to Objective-C objects and the snapshot survives any
# subsequent Glyphs-internal mutations.
# ---------------------------------------------------------------------------


def _snapshot_layer_data(layer):
    paths = []
    for path in (layer.paths or []):
        nodes = []
        for node in (path.nodes or []):
            nodes.append(
                {"x": float(node.position.x), "y": float(node.position.y)}
            )
        paths.append({"nodes": nodes})
    anchors = []
    for anchor in (getattr(layer, "anchors", None) or []):
        try:
            nm = anchor.name
        except Exception:
            nm = None
        if not nm:
            continue
        anchors.append(
            {"name": nm, "x": float(anchor.position.x), "y": float(anchor.position.y)}
        )
    width = None
    try:
        width = float(layer.width)
    except Exception:
        pass
    return {"paths": paths, "anchors": anchors, "width": width}


def _apply_layer_data(layer, data):
    live_paths = list(layer.paths or [])
    snap_paths = data.get("paths") or []
    for pi, path in enumerate(live_paths):
        if pi >= len(snap_paths):
            break
        snap_nodes = snap_paths[pi].get("nodes") or []
        live_nodes = list(path.nodes or [])
        for ni, node in enumerate(live_nodes):
            if ni >= len(snap_nodes):
                break
            sn = snap_nodes[ni]
            node.position = _point(sn["x"], sn["y"])
    w = data.get("width")
    if w is not None:
        try:
            layer.width = w
        except Exception:
            pass
    snap_anchors_by_name = {
        a["name"]: a for a in (data.get("anchors") or []) if a.get("name")
    }
    for anchor in (getattr(layer, "anchors", None) or []):
        try:
            nm = anchor.name
        except Exception:
            nm = None
        if not nm:
            continue
        sa = snap_anchors_by_name.get(nm)
        if sa is None:
            continue
        anchor.position = _point(sa["x"], sa["y"])


def _snapshot_glyphs(font, glyph_names):
    """Return ``{glyph_name: {master_id: layer_data}}`` for the requested glyphs."""
    out = {}
    for name in glyph_names:
        glyph = _resolve_glyph(font, name)
        if glyph is None:
            continue
        layers = {}
        for master in font.masters:
            try:
                layer = glyph.layers[master.id]
            except Exception:
                layer = None
            if layer is None:
                continue
            layers[master.id] = _snapshot_layer_data(layer)
        out[name] = layers
    return out


def _apply_snapshot(font, snapshot):
    """Apply a snapshot back into the font's live layers."""
    if not snapshot:
        return
    for name, layers in snapshot.items():
        glyph = _resolve_glyph(font, name)
        if glyph is None:
            continue
        for master_id, data in layers.items():
            try:
                layer = glyph.layers[master_id]
            except Exception:
                layer = None
            if layer is None:
                continue
            _apply_layer_data(layer, data)


class SnapshotStore:
    """One-slot geometry snapshot for a subset of glyphs.

    - ``save(font, glyph_names)``: capture node positions / anchors / widths for all
      masters of the listed glyphs. Overwrites any previous snapshot.
    - ``reset(font)``: write the snapshot back into the live font. The snapshot is
      kept — resetting twice is allowed.
    - ``render_pre(font, master, text, contract)``: temporarily install the snapshot
      into the live font, render, then restore the live state. Used by ``diff_pre_post``.
    - ``has_snapshot()`` / ``clear()``: lifecycle helpers.
    """

    def __init__(self):
        self._slot = None
        self._glyph_names = []

    def has_snapshot(self):
        return self._slot is not None

    def clear(self):
        self._slot = None
        self._glyph_names = []

    def save(self, font, glyph_names):
        names = [str(n).strip() for n in (glyph_names or []) if str(n).strip()]
        if not names:
            raise ValueError("glyph_names must be a non-empty list")
        self._slot = _snapshot_glyphs(font, names)
        self._glyph_names = names
        layers_count = sum(len(v) for v in self._slot.values())
        return {"glyph_names": list(names), "layers": layers_count}

    def reset(self, font):
        if not self.has_snapshot():
            raise ValueError("no active snapshot")
        _apply_snapshot(font, self._slot)
        return {"glyph_names": list(self._glyph_names)}

    def render_pre(self, font, master, text, contract):
        """Render ``text`` as if the snapshot were the current font state.

        Strategy: snapshot current geometry for the same glyphs, apply the stored
        snapshot, render, then restore the current geometry. This is synchronous on
        the main thread, so no intermediate UI frame is observable.
        """
        if not self.has_snapshot():
            raise ValueError("no active snapshot")
        current = _snapshot_glyphs(font, self._glyph_names)
        _apply_snapshot(font, self._slot)
        try:
            return _render_layer_run(font, master, text, contract)
        finally:
            _apply_snapshot(font, current)


def _dump_layer(glyph, master, layer, font=None):
    uni = (glyph.unicode or "") if hasattr(glyph, "unicode") else ""
    header = "glyph: %s%s" % (glyph.name, (" U+" + uni) if uni else "")
    lines = [
        header,
        "master: %s (id=%s)" % (master.name, master.id),
        "width: %s" % _fmt_num(layer.width),
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
            x = _fmt_num(node.position.x)
            y = _fmt_num(node.position.y)
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

    anchors = list(getattr(layer, "anchors", []) or [])
    lines.append("anchors: %d" % len(anchors))
    for a in anchors:
        lines.append(
            "  %s (x=%s, y=%s)"
            % (a.name, _fmt_num(a.position.x), _fmt_num(a.position.y))
        )
    comps = list(getattr(layer, "components", []) or [])
    lines.append("components: %d" % len(comps))
    for c in comps:
        cname = getattr(c, "componentName", "?")
        m11, m12, m21, m22, tx, ty = _get_component_transform(c)
        tdesc = _describe_transform(m11, m12, m21, m22, tx, ty)
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


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return str(int(f))
    return "%.3f" % f


def _make_bitmap_rep(canvas_w, canvas_h):
    from AppKit import NSBitmapImageRep, NSDeviceRGBColorSpace

    rep = (
        NSBitmapImageRep.alloc()
        .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None,
            canvas_w,
            canvas_h,
            8,
            4,
            True,
            False,
            NSDeviceRGBColorSpace,
            0,
            32,
        )
    )
    if rep is None:
        raise RuntimeError("failed to allocate NSBitmapImageRep")
    return rep


def _encode_png(rep):
    png_type = 4  # NSBitmapImageFileTypePNG
    png_data = rep.representationUsingType_properties_(png_type, {})
    if png_data is None:
        raise RuntimeError("failed to encode PNG")
    return bytes(png_data)


def _draw_glyphs_run(font, master, text, contract):
    """Draw the specimen glyph outlines onto the current NSGraphicsContext, filled with
    the currently set color. Factored out so both plain rendering and the R/G overlay
    can reuse the exact same layout and glyph-lookup logic."""
    from AppKit import NSAffineTransform

    canvas_w = int(contract.get("canvas_w", 900))
    margin_x = int(contract.get("margin_x", 24))
    em_px = float(contract.get("em_px", 160.0))
    baseline_y = float(contract.get("baseline_y", 56.0))
    unknown_advance_upm = float(contract.get("unknown_advance_upm", 250.0))

    upm_raw = getattr(font, "upm", 1000) or 1000
    try:
        upm = float(upm_raw)
    except (TypeError, ValueError):
        upm = 1000.0
    scale = em_px / upm

    x = float(margin_x)
    right_limit = float(canvas_w - margin_x)
    for ch in text:
        glyph = _lookup_char(font, ch)
        layer = None
        if glyph is not None:
            try:
                layer = glyph.layers[master.id]
            except Exception:
                layer = None

        if layer is not None:
            try:
                path = layer.completeBezierPath
            except Exception:
                path = None
            if path is not None:
                tr = NSAffineTransform.alloc().init()
                tr.translateXBy_yBy_(x, baseline_y)
                tr.scaleXBy_yBy_(scale, scale)
                transformed = tr.transformBezierPath_(path)
                transformed.fill()
            try:
                adv = float(layer.width)
            except Exception:
                adv = unknown_advance_upm
            x += adv * scale
        else:
            x += unknown_advance_upm * scale

        if x > right_limit:
            break


def _compute_text_advance_px(font, master, text, contract):
    """Sum of glyph advance widths in canvas pixels (no AppKit needed).

    Mirrors the advance logic in ``_draw_glyphs_run`` so the caller can
    compute the exact rendered text width before allocating a bitmap.
    """
    upm_raw = getattr(font, "upm", 1000) or 1000
    try:
        upm = float(upm_raw)
    except (TypeError, ValueError):
        upm = 1000.0
    em_px = float(contract.get("em_px", 160.0))
    scale = em_px / upm
    unknown_advance_upm = float(contract.get("unknown_advance_upm", 250.0))
    total = 0.0
    for ch in text:
        glyph = _lookup_char(font, ch)
        layer = None
        if glyph is not None:
            try:
                layer = glyph.layers[master.id]
            except Exception:
                layer = None
        if layer is not None:
            try:
                total += float(layer.width)
            except Exception:
                total += unknown_advance_upm
        else:
            total += unknown_advance_upm
    return total * scale


def _tight_canvas_contract(font, master, text, contract):
    """Return a copy of *contract* with canvas_w/canvas_h/margin_x derived from glyph advances.

    Mirrors the tight-crop logic in ``_render_layer_run`` so other renderers
    (e.g. render_diff) can reuse the same sizing before allocating bitmaps.
    """
    text_w_px = _compute_text_advance_px(font, master, text, contract)
    pad_px = max(8.0, text_w_px * _RENDER_PAD_FRAC)
    canvas_w = max(120, int(text_w_px + 2.0 * pad_px))
    baseline_y = float(contract.get("baseline_y", 56.0))
    em_px = float(contract.get("em_px", 160.0))
    canvas_h = max(80, int(baseline_y + em_px * 1.1))
    c = dict(contract)
    c["canvas_w"] = canvas_w
    c["canvas_h"] = canvas_h
    c["margin_x"] = int(pad_px)
    return c


def _guide_color_ycbcr(idx, n, y=0.55, radius=0.25):
    """Return (r, g, b) for guide line ``idx`` of ``n`` total.

    Colors are evenly spaced around the Cb-Cr hue circle at fixed luminance Y,
    giving perceptually equal brightness across all lines.

    BT.601 full-range YCbCr → RGB (values in [0, 1]).
    radius=0.25 keeps all computed RGB values within [0, 1].
    """
    theta = 2.0 * math.pi * idx / max(n, 1)
    cb = 0.5 + radius * math.cos(theta)
    cr = 0.5 + radius * math.sin(theta)
    r = y + 1.402 * (cr - 0.5)
    g = y - 0.344136 * (cb - 0.5) - 0.714136 * (cr - 0.5)
    b = y + 1.772 * (cb - 0.5)
    return max(0.0, min(1.0, r)), max(0.0, min(1.0, g)), max(0.0, min(1.0, b))


# Render settings shared by render_specimen and visually_judge.
_RENDER_GUIDE_STEP = 32   # pixels between horizontal guide lines
_RENDER_PAD_FRAC = 0.06   # side padding as a fraction of text advance width

# visually_judge renders at a larger size for clearer stroke detail.
_JUDGE_EM_PX = 240.0

# render_glyph renders at an even larger size so node labels are readable.
_GLYPH_RENDER_EM_PX = 400.0

# Per-path color palette for render_glyph (7 distinct colors, equal visual weight).
# color = _PATH_PALETTE[path_index % len(_PATH_PALETTE)]
_PATH_PALETTE = [
    (0.85, 0.18, 0.18),  # red
    (0.15, 0.45, 0.85),  # blue
    (0.10, 0.62, 0.22),  # green
    (0.82, 0.48, 0.08),  # orange
    (0.58, 0.14, 0.76),  # purple
    (0.08, 0.58, 0.60),  # teal
    (0.88, 0.28, 0.60),  # pink
]


def _get_component_transform(comp):
    """Return (m11, m12, m21, m22, tX, tY) from a GSComponent transform.

    Tries attribute access (NSAffineTransformStruct) then iteration fallback.
    Returns identity on failure.
    """
    try:
        t = comp.transform
        try:
            return (float(t.m11), float(t.m12), float(t.m21), float(t.m22),
                    float(t.tX), float(t.tY))
        except AttributeError:
            vals = tuple(t)
            return tuple(float(v) for v in vals[:6])
    except Exception:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _apply_transform(m11, m12, m21, m22, tx, ty, x, y):
    """Apply a 2D affine transform to point (x, y)."""
    return m11 * x + m21 * y + tx, m12 * x + m22 * y + ty


def _describe_transform(m11, m12, m21, m22, tx, ty):
    """Return a short human-readable description of a 2D affine transform."""
    eps = 1e-4
    pure_tx = abs(tx) > eps or abs(ty) > eps
    offset = " offset=(%s, %s)" % (_fmt_num(tx), _fmt_num(ty)) if pure_tx else ""
    if abs(m11 - 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 - 1) < eps:
        return ("offset=(%s, %s)" % (_fmt_num(tx), _fmt_num(ty))) if pure_tx else "identity"
    if abs(m11 + 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 - 1) < eps:
        return "mirror_x" + offset
    if abs(m11 - 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 + 1) < eps:
        return "mirror_y" + offset
    if abs(m11 + 1) < eps and abs(m12) < eps and abs(m21) < eps and abs(m22 + 1) < eps:
        return "rotate_180" + offset
    return "matrix=(%s,%s,%s,%s,%s,%s)" % (
        _fmt_num(m11), _fmt_num(m12), _fmt_num(m21), _fmt_num(m22),
        _fmt_num(tx), _fmt_num(ty),
    )


def _draw_text_label(text, x, y, font_size, color):
    """Draw a text label at AppKit canvas position (x, y)."""
    from Foundation import NSString
    from AppKit import NSFont, NSFontAttributeName, NSForegroundColorAttributeName
    attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_(font_size),
        NSForegroundColorAttributeName: color,
    }
    NSString.stringWithString_(text).drawAtPoint_withAttributes_((x, y), attrs)


def _render_glyph_annotated(font, master, glyph, layer, em_px):
    """Render one glyph with node-index and path-label overlays. Returns PNG bytes.

    Canvas height is computed from actual node y-bounds (not a fixed em fraction).
    Node shape encodes type: filled circle=line, filled+halo=curve, hollow square=offcurve.
    Node color encodes path index (7-color palette); component nodes at 70% alpha.
    """
    from AppKit import NSAffineTransform, NSBezierPath, NSColor, NSGraphicsContext

    upm_raw = getattr(font, "upm", 1000) or 1000
    try:
        upm = float(upm_raw)
    except (TypeError, ValueError):
        upm = 1000.0
    scale = em_px / upm

    try:
        adv = float(layer.width)
    except Exception:
        adv = upm

    # --- Fix 1: dynamic canvas height from actual node y-bounds ---
    all_ys = []
    for path in (layer.paths or []):
        for node in (path.nodes or []):
            all_ys.append(float(node.position.y))
    for comp in (getattr(layer, "components", None) or []):
        comp_name = getattr(comp, "componentName", None)
        if not comp_name:
            continue
        base_g = _resolve_glyph(font, comp_name)
        if base_g is None:
            continue
        try:
            base_lyr = base_g.layers[master.id]
        except Exception:
            base_lyr = None
        if base_lyr is None:
            continue
        _m11, _m12, _m21, _m22, _tx, _ty = _get_component_transform(comp)
        for path in (base_lyr.paths or []):
            for node in (path.nodes or []):
                _, ty = _apply_transform(
                    _m11, _m12, _m21, _m22, _tx, _ty,
                    float(node.position.x), float(node.position.y),
                )
                all_ys.append(ty)

    if all_ys:
        y_min = min(all_ys)
        y_max = max(all_ys)
    else:
        y_min = 0.0
        y_max = upm * 0.7

    pad_y = max(16.0, (y_max - y_min) * scale * 0.06)
    baseline_y = max(40.0, -y_min * scale + pad_y)
    canvas_h = max(80, int(baseline_y + y_max * scale + pad_y))

    pad_px = max(20.0, adv * scale * 0.08)
    canvas_w = max(120, int(adv * scale + 2.0 * pad_px))
    margin_x = int(pad_px)

    def to_canvas(xf, yf):
        return margin_x + xf * scale, baseline_y + yf * scale

    rep = _make_bitmap_rep(canvas_w, canvas_h)
    gc = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(gc)
    try:
        # White background
        NSColor.whiteColor().set()
        NSBezierPath.bezierPathWithRect_(((0, 0), (canvas_w, canvas_h))).fill()

        # Guide lines (lighter alpha than render_specimen)
        guide_ys = list(range(0, canvas_h + 1, _RENDER_GUIDE_STEP))
        n_guides = len(guide_ys)
        for i, gy in enumerate(guide_ys):
            r, g, b = _guide_color_ycbcr(i, n_guides)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.25).set()
            gline = NSBezierPath.bezierPath()
            gline.moveToPoint_((0, gy))
            gline.lineToPoint_((canvas_w, gy))
            gline.setLineWidth_(1.0)
            gline.stroke()

        # Baseline
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.5, 0.5, 0.55).set()
        bline = NSBezierPath.bezierPath()
        bline.moveToPoint_((0, baseline_y))
        bline.lineToPoint_((canvas_w, baseline_y))
        bline.setLineWidth_(1.5)
        bline.stroke()

        # Advance-width boundary (thin vertical line)
        adv_x = margin_x + adv * scale
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.65, 0.65, 0.65, 0.75).set()
        vline = NSBezierPath.bezierPath()
        vline.moveToPoint_((adv_x, 0))
        vline.lineToPoint_((adv_x, canvas_h))
        vline.setLineWidth_(1.0)
        vline.stroke()

        # Glyph outline: light grey fill + darker stroke
        try:
            outline = layer.completeBezierPath
        except Exception:
            outline = None
        if outline is not None:
            tr = NSAffineTransform.alloc().init()
            tr.translateXBy_yBy_(margin_x, baseline_y)
            tr.scaleXBy_yBy_(scale, scale)
            transformed = tr.transformBezierPath_(outline)
            NSColor.colorWithCalibratedWhite_alpha_(0.78, 1.0).set()
            transformed.fill()
            NSColor.colorWithCalibratedWhite_alpha_(0.35, 1.0).set()
            transformed.setLineWidth_(0.8)
            transformed.stroke()

        dot_r_on = max(3.5, em_px * 0.012)
        dot_r_off = max(2.5, em_px * 0.008)
        lbl_size = max(9.0, em_px * 0.030)
        path_lbl_size = max(10.0, em_px * 0.036)

        # --- Fix 4: path-indexed palette + shape-coded nodes ---

        def _path_color(pi, alpha=1.0):
            pr, pg, pb = _PATH_PALETTE[pi % len(_PATH_PALETTE)]
            return NSColor.colorWithCalibratedRed_green_blue_alpha_(pr, pg, pb, alpha)

        def _dot_r(node_type):
            return dot_r_off if node_type == "offcurve" else dot_r_on

        def _draw_node_shape(nt, cx, cy, r, color):
            if nt == "offcurve":
                # Hollow axis-aligned square
                sq_r = r * 1.3
                sq = NSBezierPath.bezierPathWithRect_(
                    ((cx - sq_r, cy - sq_r), (sq_r * 2, sq_r * 2))
                )
                color.set()
                sq.setLineWidth_(2.0)
                sq.stroke()
            elif nt == "curve":
                # Filled circle with white halo ring
                halo_r = r * 1.5
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.85).set()
                NSBezierPath.bezierPathWithOvalInRect_(
                    ((cx - halo_r, cy - halo_r), (halo_r * 2, halo_r * 2))
                ).fill()
                color.set()
                NSBezierPath.bezierPathWithOvalInRect_(
                    ((cx - r, cy - r), (r * 2, r * 2))
                ).fill()
            else:
                # line: plain filled circle
                color.set()
                NSBezierPath.bezierPathWithOvalInRect_(
                    ((cx - r, cy - r), (r * 2, r * 2))
                ).fill()

        lbl_dark = NSColor.colorWithCalibratedWhite_alpha_(0.2, 0.75)
        global_pi = 0  # increments for every path drawn (direct + component)

        # --- Direct paths ---
        for pi, path in enumerate(list(layer.paths or [])):
            color = _path_color(global_pi)
            global_pi += 1
            nodes = list(path.nodes or [])
            if nodes:
                cx_f = sum(n.position.x for n in nodes) / len(nodes)
                cy_f = sum(n.position.y for n in nodes) / len(nodes)
                lx, ly = to_canvas(cx_f, cy_f)
                _draw_text_label("path[%d]" % pi, lx - 18, ly + 4, path_lbl_size, lbl_dark)
            for ni, node in enumerate(nodes):
                nt = getattr(node, "type", "line") or "line"
                cx, cy = to_canvas(node.position.x, node.position.y)
                r = _dot_r(nt)
                _draw_node_shape(nt, cx, cy, r, color)
                _draw_text_label(str(ni), cx + r + 1, cy + 1, lbl_size, color)

        # --- Component paths (same shapes at 70% alpha) ---
        for comp in list(getattr(layer, "components", None) or []):
            comp_name = getattr(comp, "componentName", None) or "?"
            base_glyph = _resolve_glyph(font, comp_name)
            if base_glyph is None:
                continue
            try:
                base_layer = base_glyph.layers[master.id]
            except Exception:
                base_layer = None
            if base_layer is None:
                continue
            m11, m12, m21, m22, ctx_, cty = _get_component_transform(comp)

            for pi, path in enumerate(list(base_layer.paths or [])):
                color = _path_color(global_pi, alpha=0.7)
                global_pi += 1
                nodes = list(path.nodes or [])
                if nodes:
                    cx_f = sum(n.position.x for n in nodes) / len(nodes)
                    cy_f = sum(n.position.y for n in nodes) / len(nodes)
                    tx_f, ty_f = _apply_transform(m11, m12, m21, m22, ctx_, cty, cx_f, cy_f)
                    lx, ly = to_canvas(tx_f, ty_f)
                    _draw_text_label(
                        "(%s)path[%d]" % (comp_name, pi), lx - 18, ly + 4,
                        path_lbl_size, lbl_dark,
                    )
                for ni, node in enumerate(nodes):
                    nt = getattr(node, "type", "line") or "line"
                    nx, ny = _apply_transform(
                        m11, m12, m21, m22, ctx_, cty,
                        float(node.position.x), float(node.position.y),
                    )
                    cx, cy = to_canvas(nx, ny)
                    r = _dot_r(nt)
                    _draw_node_shape(nt, cx, cy, r, color)
                    _draw_text_label(str(ni), cx + r + 1, cy + 1, lbl_size, color)

    finally:
        NSGraphicsContext.restoreGraphicsState()

    return _encode_png(rep)


def _render_layer_run(font, master, text, contract):
    """Rasterize ``text`` at ``master``.

    Canvas width is computed from actual glyph advances (tight crop);
    horizontal guide lines are drawn behind the glyphs for dimension reference.
    Returns PNG bytes.
    """
    from AppKit import NSBezierPath, NSColor, NSGraphicsContext

    tight_contract = _tight_canvas_contract(font, master, text, contract)
    canvas_w = tight_contract["canvas_w"]
    canvas_h = tight_contract["canvas_h"]

    rep = _make_bitmap_rep(canvas_w, canvas_h)
    gc = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(gc)
    try:
        NSColor.whiteColor().set()
        NSBezierPath.bezierPathWithRect_(((0, 0), (canvas_w, canvas_h))).fill()

        # Guide lines drawn FIRST (behind glyphs), each with a distinct equally-bright
        # color from the YCbCr colormap (fixed luminance Y, hue evenly spaced).
        guide_ys = list(range(0, canvas_h + 1, _RENDER_GUIDE_STEP))
        n_guides = len(guide_ys)
        for i, gy in enumerate(guide_ys):
            r, g, b = _guide_color_ycbcr(i, n_guides)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.55).set()
            guide = NSBezierPath.bezierPath()
            guide.moveToPoint_((0, gy))
            guide.lineToPoint_((canvas_w, gy))
            guide.setLineWidth_(1.5)
            guide.stroke()

        # Glyphs on top of guides
        NSColor.blackColor().set()
        _draw_glyphs_run(font, master, text, tight_contract)
    finally:
        NSGraphicsContext.restoreGraphicsState()

    return _encode_png(rep)


def _render_white_on_black_rep(font, master, text, contract):
    """Rasterize glyph fills as white on an opaque black background (single mask pass)."""
    from AppKit import (
        NSBezierPath,
        NSColor,
        NSCompositingOperationSourceOver,
        NSGraphicsContext,
    )

    canvas_w = int(contract.get("canvas_w", 900))
    canvas_h = int(contract.get("canvas_h", 260))
    rep = _make_bitmap_rep(canvas_w, canvas_h)
    gc = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(gc)
    try:
        NSColor.blackColor().set()
        NSBezierPath.bezierPathWithRect_(((0, 0), (canvas_w, canvas_h))).fill()
        NSGraphicsContext.currentContext().setCompositingOperation_(
            NSCompositingOperationSourceOver
        )
        NSColor.whiteColor().set()
        _draw_glyphs_run(font, master, text, contract)
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return rep


def _bitmap_rep_row_bytes(rep):
    """Copy packed row bytes from a non-planar 32-bpp ``NSBitmapImageRep`` (includes row padding)."""
    bpr = int(rep.bytesPerRow())
    h = int(rep.pixelsHigh())
    n = bpr * h
    ptr = rep.bitmapData()
    if ptr is None:
        raise RuntimeError("NSBitmapImageRep.bitmapData() is None")
    try:
        return bytearray(memoryview(ptr).tobytes()[:n])
    except TypeError:
        from ctypes import string_at

        addr = int(ptr)
        return bytearray(string_at(addr, n))


def _merge_silhouettes_to_overlay_rg(pre_buf, post_buf, out_buf, bpr, h, w):
    """Combine two white-on-black masks into red/green overlay (yellow = overlap).

    Avoids ``NSCompositingOperationPlusLighter`` on bitmap contexts, which can drop
    interior pixels (premultiplied alpha / compositing quirks) so only edge fringes
    remain visible.
    """
    for y in range(h):
        row = y * bpr
        for x in range(w):
            i = row + x * 4
            lp = (pre_buf[i] + pre_buf[i + 1] + pre_buf[i + 2]) / (3.0 * 255.0)
            lq = (post_buf[i] + post_buf[i + 1] + post_buf[i + 2]) / (3.0 * 255.0)
            lp = max(0.0, min(1.0, lp))
            lq = max(0.0, min(1.0, lq))
            out_buf[i] = int(round(lp * 255.0))
            out_buf[i + 1] = int(round(lq * 255.0))
            out_buf[i + 2] = 0
            out_buf[i + 3] = 255


def _bitmap_rep_write_row_bytes(rep, buf):
    bpr = int(rep.bytesPerRow())
    h = int(rep.pixelsHigh())
    n = bpr * h
    if len(buf) != n:
        raise RuntimeError("buffer length does not match bitmap")
    ptr = rep.bitmapData()
    if ptr is None:
        raise RuntimeError("NSBitmapImageRep.bitmapData() is None")
    try:
        memoryview(ptr).cast("B")[:n] = buf
    except TypeError:
        from ctypes import addressof, c_char, memmove

        raw = (c_char * n).from_buffer(buf)
        memmove(int(ptr), addressof(raw), n)


def _render_overlay_run(font, master, text, contract, store):
    """Render the R/G overlay: red = pre (snapshot), green = post (live), yellow = overlap.

    Implementation: draw snapshot and live glyphs as white-on-black masks, then merge
    pixels so channels are independent (no additive compositing in the graphics state).
    Returns PNG bytes."""
    canvas_w = int(contract.get("canvas_w", 900))
    canvas_h = int(contract.get("canvas_h", 260))

    current_snap = _snapshot_glyphs(font, store._glyph_names)
    _apply_snapshot(font, store._slot)
    try:
        pre_rep = _render_white_on_black_rep(font, master, text, contract)
    finally:
        _apply_snapshot(font, current_snap)

    post_rep = _render_white_on_black_rep(font, master, text, contract)

    bpr = int(pre_rep.bytesPerRow())
    h = int(pre_rep.pixelsHigh())
    w = int(pre_rep.pixelsWide())
    if (
        bpr != int(post_rep.bytesPerRow())
        or h != int(post_rep.pixelsHigh())
        or w != int(post_rep.pixelsWide())
    ):
        raise RuntimeError("overlay silhouette bitmaps differ in layout")

    pre_buf = _bitmap_rep_row_bytes(pre_rep)
    post_buf = _bitmap_rep_row_bytes(post_rep)
    out_buf = bytearray(bpr * h)
    _merge_silhouettes_to_overlay_rg(pre_buf, post_buf, out_buf, bpr, h, w)

    out_rep = _make_bitmap_rep(canvas_w, canvas_h)
    _bitmap_rep_write_row_bytes(out_rep, out_buf)
    return _encode_png(out_rep)


def _lookup_char(font, ch):
    if not ch:
        return None
    try:
        return font.glyphForCharacter_(ord(ch))
    except Exception:
        return None
