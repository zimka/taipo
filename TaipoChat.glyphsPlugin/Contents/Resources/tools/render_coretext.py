# encoding: utf-8
"""CoreText specimen rendering via ephemeral OTF export (Glyphs 3/4)."""

from __future__ import annotations

import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

from session_log import get_logger

_SPECIMEN_PAD_EM = 0.5


def specimen_pad_px(em_px):
    """Padding on all four sides of a specimen, in canvas pixels (0.5 em)."""
    return _SPECIMEN_PAD_EM * float(em_px)

_render_logger = get_logger("render")


def _log_export_tiers(tier: RenderTier, tier1_err: str | None, tier2_err: str | None) -> None:
    if tier1_err:
        _render_logger.warning("Tier 1 export failed: %s", tier1_err)
    if tier2_err:
        _render_logger.warning("Tier 2 export failed: %s", tier2_err)
    if tier == RenderTier.GEOMETRY and (tier1_err or tier2_err):
        _render_logger.warning("Using geometry tier (no successful OTF export)")


def _log_coretext_fallback(coretext_err: str | None, context: str) -> None:
    if coretext_err:
        _render_logger.warning(
            "CoreText render failed (%s), falling back to geometry: %s",
            context,
            coretext_err,
        )


class RenderTier(str, Enum):
    """Specimen render fidelity tier (best first)."""

    CORETEXT_FULL = "coretext_full"
    CORETEXT_NO_FEATURES = "coretext_no_features"
    GEOMETRY = "geometry"


RENDER_TIER_LABELS = {
    RenderTier.CORETEXT_FULL: "Tier 1: coretext_full",
    RenderTier.CORETEXT_NO_FEATURES: "Tier 2: coretext_no_features",
    RenderTier.GEOMETRY: "Tier 3: geometry",
}

RENDER_TIER_RELIABLE = {
    RenderTier.CORETEXT_FULL: (
        "glyph outlines, advance widths, multiline layout, pair kerning, "
        "OpenType features (liga, calt, ccmp, case, stylistic sets, mark positioning)"
    ),
    RenderTier.CORETEXT_NO_FEATURES: (
        "glyph outlines, advance widths, multiline layout, pair kerning (usually); "
        "precomposed Unicode characters"
    ),
    RenderTier.GEOMETRY: (
        "glyph outlines, advance widths, multiline layout; "
        "precomposed Unicode characters; shape edits visible in render_specimen_diff"
    ),
}

RENDER_TIER_NOT_RELIABLE = {
    RenderTier.CORETEXT_FULL: "(none — full export succeeded)",
    RenderTier.CORETEXT_NO_FEATURES: (
        "ligatures, contextual alternates (calt), ccmp composition, "
        "case features (smcp/c2sc), stylistic sets, localized forms (locl), "
        "mark/mkmk positioning when accents rely on OT features"
    ),
    RenderTier.GEOMETRY: (
        "pair kerning, all OpenType features, "
        "decomposed accents (base + combining mark without precomposed glyph)"
    ),
}


@dataclass
class RenderTiming:
    compile_ms: float = 0.0
    render_ms: float = 0.0
    total_ms: float = 0.0
    compile_count: int = 1

    def debug_suffix(self) -> str:
        if self.compile_count > 1:
            return (
                " compile_ms=%.1f (%d exports) render_ms=%.1f total_ms=%.1f"
                % (self.compile_ms, self.compile_count, self.render_ms, self.total_ms)
            )
        return (
            " compile_ms=%.1f render_ms=%.1f total_ms=%.1f"
            % (self.compile_ms, self.render_ms, self.total_ms)
        )


@dataclass
class RenderSpecimenResult:
    png_bytes: bytes
    canvas_w: int
    canvas_h: int
    tier: RenderTier
    timing: RenderTiming
    tier1_error: str | None = None
    tier2_error: str | None = None
    coretext_error: str | None = None
    resolved_spec: object | None = None


def format_render_tier_block(
    tier: RenderTier,
    *,
    tier1_error: str | None = None,
    tier2_error: str | None = None,
    coretext_error: str | None = None,
) -> str:
    """Human-readable tier report for tool result headers (agent-facing)."""
    lines = [
        "render_tier=%s" % tier.value,
        "render_tier_label=%s" % RENDER_TIER_LABELS[tier],
        "render_tier_reliable_for: %s" % RENDER_TIER_RELIABLE[tier],
        "render_tier_not_reliable_for: %s" % RENDER_TIER_NOT_RELIABLE[tier],
    ]
    if tier == RenderTier.CORETEXT_NO_FEATURES and tier1_error:
        lines.append("render_tier_fallback_reason: full export failed (%s)" % tier1_error)
    if tier == RenderTier.GEOMETRY:
        if tier1_error:
            lines.append(
                "render_tier_fallback_reason: full export failed (%s)" % tier1_error
            )
        if tier2_error:
            lines.append(
                "render_tier_fallback_reason_2: stripped export failed (%s)" % tier2_error
            )
        if coretext_error:
            lines.append(
                "render_tier_fallback_reason_3: CoreText load failed (%s)" % coretext_error
            )
    return "\n".join(lines)


def resolve_specimen_lines(args) -> tuple[list[str] | None, str | None]:
    """Normalize ``text`` or ``lines`` tool args into a list of specimen rows."""
    raw_lines = args.get("lines")
    if raw_lines is not None:
        if not isinstance(raw_lines, list):
            return None, "[error] 'lines' must be a list of strings."
        lines = [str(line) for line in raw_lines]
        if not lines or not any(line.strip() for line in lines):
            return None, "[error] 'lines' must contain at least one non-empty string."
        return lines, None

    text = str(args.get("text") or "")
    if not text:
        return None, "[error] 'text' or 'lines' is required."
    lines = text.splitlines() or [text]
    if not any(line.strip() for line in lines):
        return None, "[error] specimen text must not be empty."
    return lines, None


def _normalized_axes(raw) -> tuple[float, ...]:
    if raw is None:
        return ()
    try:
        return tuple(float(v) for v in list(raw))
    except (TypeError, ValueError):
        return ()


def _resolve_export_instance(font, master):
    master_axes = _normalized_axes(getattr(master, "axes", None))
    active_instances = []
    for inst in font.instances:
        if getattr(inst, "active", True) is False:
            continue
        active_instances.append(inst)
        if _normalized_axes(getattr(inst, "axes", None)) == master_axes:
            return inst

    masters = list(font.masters)
    if len(masters) == 1 and active_instances:
        return active_instances[0]

    try:
        from GlyphsApp import GSInstance
    except ImportError:
        GSInstance = None

    if GSInstance is not None:
        inst = GSInstance()
        inst.font = font
        inst.name = str(getattr(master, "name", "Master") or "Master")
        try:
            inst.axes = list(master_axes)
        except Exception:
            pass
        return inst

    return active_instances[0] if active_instances else None


def _glyph_by_name(font, name):
    glyphs = getattr(font, "glyphs", None)
    if glyphs is None:
        return None
    try:
        glyph = glyphs[name]
        if glyph is not None:
            return glyph
    except (KeyError, TypeError, IndexError):
        pass
    try:
        for glyph in glyphs:
            if getattr(glyph, "name", None) == name:
                return glyph
    except TypeError:
        pass
    return None


def _export_unicode_fixup_glyph_names():
    """Glyphs that must not carry Unicode for OTF export (see Glyphs forum / export errors)."""
    return (".notdef",)


@contextmanager
def _temporary_export_fixups(font):
    """Clear export-blocking Unicode assignments; restore when the context exits."""
    from tools.glyph_metadata import _read_glyph_unicode_hex, _write_glyph_unicode

    restores: list[tuple[object, str]] = []
    try:
        for name in _export_unicode_fixup_glyph_names():
            glyph = _glyph_by_name(font, name)
            if glyph is None:
                continue
            uni = _read_glyph_unicode_hex(glyph) or ""
            if not uni:
                continue
            restores.append((glyph, uni))
            _write_glyph_unicode(glyph, "")
        yield
    finally:
        for glyph, uni in restores:
            try:
                _write_glyph_unicode(glyph, uni)
            except Exception:
                pass


def _find_exported_otf(export_dir, instance):
    try:
        names = os.listdir(export_dir)
    except OSError:
        names = []
    otf_names = sorted(n for n in names if n.lower().endswith(".otf"))
    if otf_names:
        return os.path.join(export_dir, otf_names[0])
    last_path = getattr(instance, "lastExportedFilePath", None)
    if last_path and os.path.isfile(last_path):
        return last_path
    return None


def _strip_opentype_layout(font) -> None:
    """Remove manual OT features/classes/prefixes on *font* (in-memory only)."""
    for attr in ("features", "featurePrefixes", "classes"):
        coll = getattr(font, attr, None)
        if coll is None:
            continue
        try:
            setattr(font, attr, [])
        except Exception:
            try:
                setattr(font, attr, None)
            except Exception:
                pass


def _generate_instance_otf(instance, export_dir, font) -> tuple[bool | object, str | None]:
    """Run ``instance.generate`` into *export_dir*. Returns ``(result, exception_msg)``."""
    try:
        with _temporary_export_fixups(font):
            try:
                result = instance.generate(
                    format="OTF",
                    fontPath=export_dir,
                    autoHint=False,
                    removeOverlap=True,
                    useProductionNames=True,
                    decomposeSmartStuff=True,
                )
            except TypeError:
                result = instance.generate(
                    Format="OTF",
                    FontPath=export_dir,
                    AutoHint=False,
                    RemoveOverlap=True,
                    UseProductionNames=True,
                    DecomposeSmartStuff=True,
                )
    except Exception as exc:
        shutil.rmtree(export_dir, ignore_errors=True)
        return None, "Font export failed: %s" % exc
    return result, None


def _export_error_message(result) -> str:
    msg = "Font export failed: %s" % (result,)
    if ".notdef" in str(result) and "Unicode" in str(result):
        msg += (
            " Remove Unicode from the .notdef glyph (Font > Glyphs) and "
            "delete a separate uni0000 glyph if present."
        )
    return msg


def compile_font_to_temp_otf(font, master) -> tuple[str | None, str | None]:
    """Export *master* to a temp OTF. Returns ``(path, error)``."""
    import tempfile

    instance = _resolve_export_instance(font, master)
    if instance is None:
        return None, (
            "No export instance for master %r — add an instance in Font Info > Exports."
            % getattr(master, "name", master)
        )

    export_dir = tempfile.mkdtemp(prefix="taipo_otf_")
    result, exc_msg = _generate_instance_otf(instance, export_dir, font)
    if exc_msg:
        return None, exc_msg

    if result is not True:
        shutil.rmtree(export_dir, ignore_errors=True)
        return None, _export_error_message(result)

    otf_path = _find_exported_otf(export_dir, instance)
    if otf_path is None:
        shutil.rmtree(export_dir, ignore_errors=True)
        return None, "Font export produced no OTF file."

    _render_logger.debug("Exported OTF: %s", otf_path)
    return otf_path, None


def compile_font_stripped_to_temp_otf(font, master) -> tuple[str | None, str | None]:
    """Export on ``font.copy()`` with OT features/classes/prefixes cleared."""
    copy_fn = getattr(font, "copy", None)
    if copy_fn is None:
        return None, "font.copy() unavailable — cannot strip OpenType features for export."

    work_font = copy_fn()
    _strip_opentype_layout(work_font)
    return compile_font_to_temp_otf(work_font, master)


def compile_for_render(font, master) -> tuple[str | None, RenderTier, str | None, str | None]:
    """Try tier-1 then tier-2 export. Returns ``(otf_path, tier, tier1_err, tier2_err)``."""
    otf_path, err1 = compile_font_to_temp_otf(font, master)
    if otf_path:
        return otf_path, RenderTier.CORETEXT_FULL, None, None

    otf_path, err2 = compile_font_stripped_to_temp_otf(font, master)
    if otf_path:
        return otf_path, RenderTier.CORETEXT_NO_FEATURES, err1, None

    return None, RenderTier.GEOMETRY, err1, err2


def unregister_temp_font(otf_path):
    """Unregister a temp OTF from the process font manager."""
    if not otf_path or not os.path.isfile(otf_path):
        return
    import CoreText as CT
    from Foundation import NSURL

    unregister = getattr(CT, "CTFontManagerUnregisterFontsForURL", None)
    if unregister is None:
        return
    url = NSURL.fileURLWithPath_(otf_path)
    scope = getattr(CT, "kCTFontManagerScopeProcess", 1)
    try:
        import objc

        unregister(url, scope, objc.NULL)
    except TypeError:
        try:
            unregister(url, scope, None)
        except Exception:
            pass
    except Exception:
        pass


def cleanup_temp_otf(otf_path):
    """Remove temp export directory containing *otf_path*."""
    if not otf_path:
        return
    unregister_temp_font(otf_path)
    parent = os.path.dirname(otf_path)
    if parent and os.path.basename(parent).startswith("taipo_otf_"):
        _render_logger.debug("Cleaning temp OTF dir: %s", parent)
        shutil.rmtree(parent, ignore_errors=True)


def _make_ct_font(otf_path, font_size_px):
    try:
        return _make_ct_font_impl(otf_path, font_size_px)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "CTFontCreateWithURL failed for %s: %s" % (otf_path, exc)
        ) from exc


def _make_ct_font_impl(otf_path, font_size_px):
    import CoreText as CT
    from Foundation import NSURL

    if not otf_path or not os.path.isfile(otf_path):
        raise RuntimeError("OTF not found: %s" % otf_path)
    try:
        if os.path.getsize(otf_path) < 256:
            raise RuntimeError("OTF file too small: %s" % otf_path)
    except OSError as exc:
        raise RuntimeError("OTF not readable: %s (%s)" % (otf_path, exc))

    url = NSURL.fileURLWithPath_(otf_path)
    scope = getattr(CT, "kCTFontManagerScopeProcess", 1)
    register = getattr(CT, "CTFontManagerRegisterFontsForURL", None)
    if register is not None:
        try:
            import objc

            reg_result = register(url, scope, objc.NULL)
        except TypeError:
            reg_result = register(url, scope, None)
        ok = reg_result[0] if isinstance(reg_result, tuple) else bool(reg_result)
        if not ok:
            ct_font = CT.CTFontCreateWithURL(url, float(font_size_px), None)
            if ct_font is not None:
                return ct_font

    desc_attrs = {
        getattr(CT, "kCTFontURLAttribute", "NSFontURL"): url,
        getattr(CT, "kCTFontSizeAttribute", "NSFontSize"): float(font_size_px),
    }
    create_desc = getattr(CT, "CTFontDescriptorCreateWithAttributes", None)
    create_from_desc = getattr(CT, "CTFontCreateWithFontDescriptor", None)
    if create_desc is not None and create_from_desc is not None:
        desc = create_desc(desc_attrs)
        ct_font = create_from_desc(desc, 0.0, None)
        if ct_font is not None:
            return ct_font

    ct_font = CT.CTFontCreateWithURL(url, float(font_size_px), None)
    if ct_font is None:
        raise RuntimeError("CTFontCreateWithURL failed for %s" % otf_path)
    return ct_font


def _line_metrics(ct_font):
    import CoreText as CT

    ascent = float(CT.CTFontGetAscent(ct_font))
    descent = float(CT.CTFontGetDescent(ct_font))
    leading = float(CT.CTFontGetLeading(ct_font))
    line_height = ascent + descent + max(leading, 0.0)
    return ascent, descent, line_height


def _build_ct_lines(ct_font, lines, *, white_on_black):
    import CoreText as CT
    from AppKit import NSColor

    k_font = CT.kCTFontAttributeName
    k_color = CT.kCTForegroundColorAttributeName
    fg = NSColor.whiteColor() if white_on_black else NSColor.blackColor()

    ct_lines = []
    widths = []
    for line in lines:
        text = line if line else " "
        attrs = {k_font: ct_font, k_color: fg.CGColor()}
        astr = CT.CFAttributedStringCreate(None, text, attrs)
        ctline = CT.CTLineCreateWithAttributedString(astr)
        width = float(CT.CTLineGetTypographicBounds(ctline, None, None, None)[0])
        ct_lines.append(ctline)
        widths.append(width)
    return ct_lines, widths


def render_coretext_lines_rep(
    otf_path,
    lines,
    font_size_px,
    *,
    white_on_black=False,
    canvas_w=None,
    canvas_h=None,
    pad=None,
    baseline_y0=None,
    line_height_override=None,
):
    """Shape and rasterize *lines* from *otf_path*. Returns PNG bytes and canvas size."""
    import CoreText as CT
    import Quartz
    from AppKit import NSBezierPath, NSColor, NSGraphicsContext

    from tools.render import make_bitmap_rep

    ct_font = _make_ct_font(otf_path, font_size_px)
    ascent, _descent, line_height = _line_metrics(ct_font)
    if line_height_override is not None:
        line_height = float(line_height_override)
    ct_lines, widths = _build_ct_lines(ct_font, lines, white_on_black=white_on_black)

    max_w = max(widths) if widths else float(font_size_px)
    if pad is None:
        pad = specimen_pad_px(font_size_px)
    else:
        pad = float(pad)
    if canvas_w is None:
        canvas_w = max(120, int(max_w + 2.0 * pad))
    else:
        canvas_w = max(int(canvas_w), max(120, int(max_w + 2.0 * pad)))
    if canvas_h is None:
        canvas_h = max(80, int(pad + max(1, len(ct_lines)) * line_height + pad))
    else:
        canvas_h = int(canvas_h)

    if baseline_y0 is None:
        first_baseline = canvas_h - pad - ascent
        baselines = [first_baseline - i * line_height for i in range(len(ct_lines))]
    else:
        baselines = [float(baseline_y0) - i * line_height for i in range(len(ct_lines))]

    rep = make_bitmap_rep(int(canvas_w), int(canvas_h))
    gc = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(gc)
    try:
        if white_on_black:
            NSColor.blackColor().set()
        else:
            NSColor.whiteColor().set()
        NSBezierPath.bezierPathWithRect_(((0, 0), (canvas_w, canvas_h))).fill()

        ctx = gc.CGContext()
        for ctline, baseline_y in zip(ct_lines, baselines):
            Quartz.CGContextSetTextPosition(ctx, pad, baseline_y)
            CT.CTLineDraw(ctline, ctx)
    finally:
        NSGraphicsContext.restoreGraphicsState()

    camera = {
        "pad": float(pad),
        "baseline_y0": float(baselines[0]) if baselines else float(canvas_h - pad - ascent),
        "line_height": float(line_height),
    }
    return rep, int(canvas_w), int(canvas_h), camera


def render_coretext_lines_png(
    otf_path,
    lines,
    font_size_px,
    *,
    white_on_black=False,
    canvas_w=None,
    canvas_h=None,
    pad=None,
    baseline_y0=None,
    line_height_override=None,
) -> tuple[bytes, int, int, dict]:
    from tools.render import encode_png

    rep, cw, ch, camera = render_coretext_lines_rep(
        otf_path,
        lines,
        font_size_px,
        white_on_black=white_on_black,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        pad=pad,
        baseline_y0=baseline_y0,
        line_height_override=line_height_override,
    )
    return encode_png(rep), cw, ch, camera


def _spec_layout_kwargs(spec):
    if spec is None or not getattr(spec, "origin_locked", False):
        return {}
    return {
        "canvas_w": spec.canvas_w,
        "canvas_h": spec.canvas_h,
        "pad": spec.pad,
        "baseline_y0": spec.baseline_y0,
        "line_height_override": spec.line_height,
    }


def _resolve_spec(spec, canvas_w, canvas_h, camera):
    if spec is None or camera is None:
        return None
    return spec.with_locked_camera(
        canvas_w,
        canvas_h,
        camera["pad"],
        camera["baseline_y0"],
        camera["line_height"],
    )


def render_specimen_tiered(
    font, master, lines, font_size_px, spec=None
) -> RenderSpecimenResult:
    """Render specimen using best available tier (CoreText export or geometry fallback)."""
    timing = RenderTiming()
    t0 = time.perf_counter()

    t_compile = time.perf_counter()
    otf_path, tier, tier1_err, tier2_err = compile_for_render(font, master)
    _log_export_tiers(tier, tier1_err, tier2_err)
    timing.compile_ms = (time.perf_counter() - t_compile) * 1000.0
    timing.compile_count = 1
    if tier == RenderTier.CORETEXT_NO_FEATURES:
        timing.compile_count = 2
    elif tier == RenderTier.GEOMETRY:
        timing.compile_count = 0

    if tier != RenderTier.GEOMETRY:
        coretext_err = None
        try:
            t_render = time.perf_counter()
            png_bytes, canvas_w, canvas_h, camera = render_coretext_lines_png(
                otf_path,
                lines,
                font_size_px,
                white_on_black=False,
                **_spec_layout_kwargs(spec),
            )
            timing.render_ms = (time.perf_counter() - t_render) * 1000.0
            timing.total_ms = (time.perf_counter() - t0) * 1000.0
            return RenderSpecimenResult(
                png_bytes=png_bytes,
                canvas_w=canvas_w,
                canvas_h=canvas_h,
                tier=tier,
                timing=timing,
                tier1_error=tier1_err,
                tier2_error=tier2_err,
                resolved_spec=_resolve_spec(spec, canvas_w, canvas_h, camera),
            )
        except RuntimeError as exc:
            coretext_err = str(exc)
        finally:
            cleanup_temp_otf(otf_path)

        _log_coretext_fallback(coretext_err, "render_specimen")

        from tools.render import render_specimen_geometry

        t_render = time.perf_counter()
        png_bytes, canvas_w, canvas_h = render_specimen_geometry(
            font, master, lines, font_size_px, spec=spec
        )
        timing.render_ms = (time.perf_counter() - t_render) * 1000.0
        timing.total_ms = (time.perf_counter() - t0) * 1000.0
        return RenderSpecimenResult(
            png_bytes=png_bytes,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            tier=RenderTier.GEOMETRY,
            timing=timing,
            tier1_error=tier1_err,
            tier2_error=tier2_err,
            coretext_error=coretext_err,
            resolved_spec=_resolve_spec(
                spec,
                canvas_w,
                canvas_h,
                {
                    "pad": spec.pad if spec is not None else 0.0,
                    "baseline_y0": spec.baseline_y0 if spec is not None else 56.0,
                    "line_height": spec.line_height if spec is not None else 0.0,
                },
            ),
        )

    from tools.render import render_specimen_geometry

    t_render = time.perf_counter()
    png_bytes, canvas_w, canvas_h = render_specimen_geometry(
        font, master, lines, font_size_px, spec=spec
    )
    timing.render_ms = (time.perf_counter() - t_render) * 1000.0
    timing.total_ms = (time.perf_counter() - t0) * 1000.0
    return RenderSpecimenResult(
        png_bytes=png_bytes,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        tier=RenderTier.GEOMETRY,
        timing=timing,
        tier1_error=tier1_err,
        tier2_error=tier2_err,
        resolved_spec=_resolve_spec(
            spec,
            canvas_w,
            canvas_h,
            {
                "pad": spec.pad if spec is not None else 0.0,
                "baseline_y0": spec.baseline_y0 if spec is not None else 56.0,
                "line_height": spec.line_height if spec is not None else 0.0,
            },
        ),
    )


def render_specimen_with_coretext(font, master, lines, font_size_px) -> tuple[bytes, int, int, RenderTiming]:
    """Compile temp OTF and render *lines* with CoreText (tier 1 only; prefer ``render_specimen_tiered``)."""
    result = render_specimen_tiered(font, master, lines, font_size_px)
    if result.tier != RenderTier.CORETEXT_FULL:
        raise RuntimeError(
            result.tier2_error or result.tier1_error or "CoreText export failed."
        )
    return result.png_bytes, result.canvas_w, result.canvas_h, result.timing
