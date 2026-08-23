# encoding: utf-8
"""AppKit-free stubs for render_specimen / render_specimen_diff smoke tests."""

from contextlib import contextmanager

from tools.render_coretext import RenderSpecimenResult, RenderTier, RenderTiming

_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


@contextmanager
def stub_render_with_spec_deps(tiers=None):
    """Stub raster + overlay so smoke tests run without PyObjC.

    *tiers* is an optional list of ``RenderTier`` values popped per
    ``render_with_spec`` call (defaults to ``full``).
    """
    import tools.render as render_mod

    remaining = list(tiers) if tiers is not None else None

    def _fake_render(_font, spec):
        if remaining is not None:
            tier = remaining.pop(0) if remaining else RenderTier.CORETEXT_FULL
        else:
            tier = RenderTier.CORETEXT_FULL
        return RenderSpecimenResult(
            png_bytes=_FAKE_PNG,
            canvas_w=spec.canvas_w,
            canvas_h=spec.canvas_h,
            tier=tier,
            timing=RenderTiming(
                compile_ms=12.5, render_ms=3.0, total_ms=20.0, compile_count=2
            ),
        )

    def _fake_overlay(_pre, _post):
        return _FAKE_PNG, 0

    def _fake_pad(png, _sw, _sh, _tw, _th):
        return png

    saved_render = render_mod.render_with_spec
    saved_overlay = render_mod.overlay_from_specimen_pngs
    saved_pad = render_mod.pad_png_to_canvas
    render_mod.render_with_spec = _fake_render
    render_mod.overlay_from_specimen_pngs = _fake_overlay
    render_mod.pad_png_to_canvas = _fake_pad
    try:
        yield
    finally:
        render_mod.render_with_spec = saved_render
        render_mod.overlay_from_specimen_pngs = saved_overlay
        render_mod.pad_png_to_canvas = saved_pad
