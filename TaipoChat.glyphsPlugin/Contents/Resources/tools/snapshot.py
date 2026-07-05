# encoding: utf-8

from tools.anchors import anchor_name, iter_layer_anchors
from tools.font_access import resolve_glyph, resolve_master
from tools.geometry import point
from tools.render import render_layer_run, render_overlay_run, tight_canvas_contract

def snapshot_layer_data(layer):
    paths = []
    for path in (layer.paths or []):
        nodes = []
        for node in (path.nodes or []):
            nodes.append(
                {"x": float(node.position.x), "y": float(node.position.y)}
            )
        paths.append({"nodes": nodes})
    anchors = []
    for anchor in iter_layer_anchors(layer):
        nm = anchor_name(anchor)
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

def apply_layer_data(layer, data):
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
            node.position = point(sn["x"], sn["y"])
    w = data.get("width")
    if w is not None:
        try:
            layer.width = w
        except Exception:
            pass
    snap_anchors_by_name = {
        a["name"]: a for a in (data.get("anchors") or []) if a.get("name")
    }
    for anchor in iter_layer_anchors(layer):
        nm = anchor_name(anchor)
        if not nm:
            continue
        sa = snap_anchors_by_name.get(nm)
        if sa is None:
            continue
        anchor.position = point(sa["x"], sa["y"])

def snapshot_glyphs(font, glyph_names):
    """Return ``{glyph_name: {master_id: layer_data}}`` for the requested glyphs."""
    out = {}
    for name in glyph_names:
        glyph = resolve_glyph(font, name)
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
            layers[master.id] = snapshot_layer_data(layer)
        out[name] = layers
    return out

def apply_snapshot(font, snapshot):
    """Apply a snapshot back into the font's live layers."""
    if not snapshot:
        return
    for name, layers in snapshot.items():
        glyph = resolve_glyph(font, name)
        if glyph is None:
            continue
        for master_id, data in layers.items():
            try:
                layer = glyph.layers[master_id]
            except Exception:
                layer = None
            if layer is None:
                continue
            apply_layer_data(layer, data)

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
        self._slot = snapshot_glyphs(font, names)
        self._glyph_names = names
        layers_count = sum(len(v) for v in self._slot.values())
        return {"glyph_names": list(names), "layers": layers_count}

    def reset(self, font):
        if not self.has_snapshot():
            raise ValueError("no active snapshot")
        apply_snapshot(font, self._slot)
        return {"glyph_names": list(self._glyph_names)}

    def render_pre(self, font, master, text, contract):
        """Render ``text`` as if the snapshot were the current font state.

        Strategy: snapshot current geometry for the same glyphs, apply the stored
        snapshot, render, then restore the current geometry. This is synchronous on
        the main thread, so no intermediate UI frame is observable.
        """
        if not self.has_snapshot():
            raise ValueError("no active snapshot")
        current = snapshot_glyphs(font, self._glyph_names)
        apply_snapshot(font, self._slot)
        try:
            return render_layer_run(font, master, text, contract)
        finally:
            apply_snapshot(font, current)

def handle_save_snapshot(args, ctx, font):
    names_raw = args.get("glyph_names")
    if isinstance(names_raw, str):
        names_raw = [names_raw]
    if not isinstance(names_raw, list) or not names_raw:
        return "[error] 'glyph_names' must be a non-empty list of glyph names."
    names = [str(n).strip() for n in names_raw if str(n).strip()]
    if not names:
        return "[error] 'glyph_names' must contain non-empty strings."
    missing = [n for n in names if resolve_glyph(font, n) is None]
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

def handle_reset_snapshot(args, ctx, font):
    if not ctx.snapshot_store.has_snapshot():
        return "[error] No active snapshot — call save_snapshot first."
    info = ctx.snapshot_store.reset(font)
    return "Snapshot restored: %d glyph(s) reverted (%s). Snapshot is still active." % (
        len(info["glyph_names"]),
        ", ".join(info["glyph_names"]),
    )

def handle_render_diff(args, ctx, font):
    text = str(args.get("text") or "")
    if not text:
        return "[error] 'text' is required."
    master = resolve_master(font, args.get("master"))
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
    contract = tight_canvas_contract(font, master, text, contract)
    overlay_png = render_overlay_run(font, master, text, contract, store)
    header = "render_diff master=%s text=%r snapshot_glyphs=%s" % (
        master.name,
        text,
        list(store._glyph_names),
    )
    return [header, overlay_png]
