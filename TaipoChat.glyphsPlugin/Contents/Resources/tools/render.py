# encoding: utf-8

import math

from tools.font_access import lookup_char, resolve_glyph, resolve_master
from tools.formatting import fmt_num

# encoding: utf-8
def handle_render_specimen(args, ctx, font):
    text = str(args.get("text") or "")
    if not text:
        return "[error] 'text' is required."
    master = resolve_master(font, args.get("master"))
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
    png_bytes = render_layer_run(font, master, text, contract)
    header = (
        "render_specimen master=%s size=%s text=%r canvas=%dx%d"
        % (master.name, int(contract.get("em_px", 0)), text,
           contract.get("canvas_w"), contract.get("canvas_h"))
    )
    return [header, png_bytes]

def handle_render_glyph(args, ctx, font):
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

    try:
        size = int(args.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    em_px = float(size) if size > 0 else _GLYPH_RENDER_EM_PX

    png_bytes = render_glyph_annotated(font, master, glyph, layer, em_px)
    paths = list(layer.paths or [])
    n_nodes = sum(len(list(p.nodes or [])) for p in paths)
    header = (
        "render_glyph %s@%s em_px=%d paths=%d nodes=%d"
        % (name, master.name, int(em_px), len(paths), n_nodes)
    )
    return [header, png_bytes]

def make_bitmap_rep(canvas_w, canvas_h):
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

def encode_png(rep):
    png_type = 4  # NSBitmapImageFileTypePNG
    png_data = rep.representationUsingType_properties_(png_type, {})
    if png_data is None:
        raise RuntimeError("failed to encode PNG")
    return bytes(png_data)

def draw_glyphs_run(font, master, text, contract):
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
        glyph = lookup_char(font, ch)
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

def compute_text_advance_px(font, master, text, contract):
    """Sum of glyph advance widths in canvas pixels (no AppKit needed).

    Mirrors the advance logic in ``draw_glyphs_run`` so the caller can
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
        glyph = lookup_char(font, ch)
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

def tight_canvas_contract(font, master, text, contract):
    """Return a copy of *contract* with canvas_w/canvas_h/margin_x derived from glyph advances.

    Mirrors the tight-crop logic in ``render_layer_run`` so other renderers
    (e.g. render_diff) can reuse the same sizing before allocating bitmaps.
    """
    text_w_px = compute_text_advance_px(font, master, text, contract)
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

def guide_color_ycbcr(idx, n, y=0.55, radius=0.25):
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

_RENDER_GUIDE_STEP = 32   # pixels between horizontal guide lines

_RENDER_PAD_FRAC = 0.06   # side padding as a fraction of text advance width

_GLYPH_RENDER_EM_PX = 400.0

_PATH_PALETTE = [
    (0.85, 0.18, 0.18),  # red
    (0.15, 0.45, 0.85),  # blue
    (0.10, 0.62, 0.22),  # green
    (0.82, 0.48, 0.08),  # orange
    (0.58, 0.14, 0.76),  # purple
    (0.08, 0.58, 0.60),  # teal
    (0.88, 0.28, 0.60),  # pink
]

def draw_text_label(text, x, y, font_size, color):
    """Draw a text label at AppKit canvas position (x, y)."""
    from Foundation import NSString
    from AppKit import NSFont, NSFontAttributeName, NSForegroundColorAttributeName
    attrs = {
        NSFontAttributeName: NSFont.systemFontOfSize_(font_size),
        NSForegroundColorAttributeName: color,
    }
    NSString.stringWithString_(text).drawAtPoint_withAttributes_((x, y), attrs)

def render_glyph_annotated(font, master, glyph, layer, em_px):
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
        base_g = resolve_glyph(font, comp_name)
        if base_g is None:
            continue
        try:
            base_lyr = base_g.layers[master.id]
        except Exception:
            base_lyr = None
        if base_lyr is None:
            continue
        _m11, _m12, _m21, _m22, _tx, _ty = get_component_transform(comp)
        for path in (base_lyr.paths or []):
            for node in (path.nodes or []):
                _, ty = apply_transform(
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

    rep = make_bitmap_rep(canvas_w, canvas_h)
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
            r, g, b = guide_color_ycbcr(i, n_guides)
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
                draw_text_label("path[%d]" % pi, lx - 18, ly + 4, path_lbl_size, lbl_dark)
            for ni, node in enumerate(nodes):
                nt = getattr(node, "type", "line") or "line"
                cx, cy = to_canvas(node.position.x, node.position.y)
                r = _dot_r(nt)
                _draw_node_shape(nt, cx, cy, r, color)
                draw_text_label(str(ni), cx + r + 1, cy + 1, lbl_size, color)

        # --- Component paths (same shapes at 70% alpha) ---
        for comp in list(getattr(layer, "components", None) or []):
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

            for pi, path in enumerate(list(base_layer.paths or [])):
                color = _path_color(global_pi, alpha=0.7)
                global_pi += 1
                nodes = list(path.nodes or [])
                if nodes:
                    cx_f = sum(n.position.x for n in nodes) / len(nodes)
                    cy_f = sum(n.position.y for n in nodes) / len(nodes)
                    tx_f, ty_f = apply_transform(m11, m12, m21, m22, ctx_, cty, cx_f, cy_f)
                    lx, ly = to_canvas(tx_f, ty_f)
                    draw_text_label(
                        "(%s)path[%d]" % (comp_name, pi), lx - 18, ly + 4,
                        path_lbl_size, lbl_dark,
                    )
                for ni, node in enumerate(nodes):
                    nt = getattr(node, "type", "line") or "line"
                    nx, ny = apply_transform(
                        m11, m12, m21, m22, ctx_, cty,
                        float(node.position.x), float(node.position.y),
                    )
                    cx, cy = to_canvas(nx, ny)
                    r = _dot_r(nt)
                    _draw_node_shape(nt, cx, cy, r, color)
                    draw_text_label(str(ni), cx + r + 1, cy + 1, lbl_size, color)

    finally:
        NSGraphicsContext.restoreGraphicsState()

    return encode_png(rep)

def render_layer_run(font, master, text, contract):
    """Rasterize ``text`` at ``master``.

    Canvas width is computed from actual glyph advances (tight crop);
    horizontal guide lines are drawn behind the glyphs for dimension reference.
    Returns PNG bytes.
    """
    from AppKit import NSBezierPath, NSColor, NSGraphicsContext

    tight_contract = tight_canvas_contract(font, master, text, contract)
    canvas_w = tight_contract["canvas_w"]
    canvas_h = tight_contract["canvas_h"]

    rep = make_bitmap_rep(canvas_w, canvas_h)
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
            r, g, b = guide_color_ycbcr(i, n_guides)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 0.55).set()
            guide = NSBezierPath.bezierPath()
            guide.moveToPoint_((0, gy))
            guide.lineToPoint_((canvas_w, gy))
            guide.setLineWidth_(1.5)
            guide.stroke()

        # Glyphs on top of guides
        NSColor.blackColor().set()
        draw_glyphs_run(font, master, text, tight_contract)
    finally:
        NSGraphicsContext.restoreGraphicsState()

    return encode_png(rep)

def render_white_on_black_rep(font, master, text, contract):
    """Rasterize glyph fills as white on an opaque black background (single mask pass)."""
    from AppKit import (
        NSBezierPath,
        NSColor,
        NSCompositingOperationSourceOver,
        NSGraphicsContext,
    )

    canvas_w = int(contract.get("canvas_w", 900))
    canvas_h = int(contract.get("canvas_h", 260))
    rep = make_bitmap_rep(canvas_w, canvas_h)
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
        draw_glyphs_run(font, master, text, contract)
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return rep

def bitmap_rep_row_bytes(rep):
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

def merge_silhouettes_to_overlay_rg(pre_buf, post_buf, out_buf, bpr, h, w):
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

def bitmap_rep_write_row_bytes(rep, buf):
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

def render_overlay_run(font, master, text, contract, store):
    """Render the R/G overlay: red = pre (snapshot), green = post (live), yellow = overlap.

    Implementation: draw snapshot and live glyphs as white-on-black masks, then merge
    pixels so channels are independent (no additive compositing in the graphics state).
    Returns PNG bytes."""
    from tools.snapshot import apply_snapshot, snapshot_glyphs

    canvas_w = int(contract.get("canvas_w", 900))
    canvas_h = int(contract.get("canvas_h", 260))

    current_snap = snapshot_glyphs(font, store._glyph_names)
    apply_snapshot(font, store._slot)
    try:
        pre_rep = render_white_on_black_rep(font, master, text, contract)
    finally:
        apply_snapshot(font, current_snap)

    post_rep = render_white_on_black_rep(font, master, text, contract)

    bpr = int(pre_rep.bytesPerRow())
    h = int(pre_rep.pixelsHigh())
    w = int(pre_rep.pixelsWide())
    if (
        bpr != int(post_rep.bytesPerRow())
        or h != int(post_rep.pixelsHigh())
        or w != int(post_rep.pixelsWide())
    ):
        raise RuntimeError("overlay silhouette bitmaps differ in layout")

    pre_buf = bitmap_rep_row_bytes(pre_rep)
    post_buf = bitmap_rep_row_bytes(post_rep)
    out_buf = bytearray(bpr * h)
    merge_silhouettes_to_overlay_rg(pre_buf, post_buf, out_buf, bpr, h, w)

    out_rep = make_bitmap_rep(canvas_w, canvas_h)
    bitmap_rep_write_row_bytes(out_rep, out_buf)
    return encode_png(out_rep)
