# encoding: utf-8
"""AppKit-free stubs for render_diff smoke tests (uv Python has no PyObjC)."""

from contextlib import contextmanager


@contextmanager
def stub_render_overlay_deps():
    import tools.snapshot as snapshot_mod
    from tools.render_coretext import RenderOverlayResult, RenderTier, RenderTiming

    def _fake_overlay(_font, _master, _lines, _size, _store):
        return RenderOverlayResult(
            png_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 8,
            tier=RenderTier.CORETEXT_FULL,
            timing=RenderTiming(
                compile_ms=12.5, render_ms=3.0, total_ms=20.0, compile_count=2
            ),
        )

    saved = snapshot_mod.render_overlay_tiered
    snapshot_mod.render_overlay_tiered = _fake_overlay
    try:
        yield
    finally:
        snapshot_mod.render_overlay_tiered = saved
