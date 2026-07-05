# encoding: utf-8
"""AppKit-free stubs for render_diff smoke tests (uv Python has no PyObjC)."""

from contextlib import contextmanager


class _FakeBitmapRep:
    def __init__(self, w=8, h=4):
        self._w = w
        self._h = h

    def bytesPerRow(self):
        return self._w * 4

    def pixelsHigh(self):
        return self._h

    def pixelsWide(self):
        return self._w


@contextmanager
def stub_render_overlay_deps():
    import tools.render as render_mod

    def _fake_white_on_black(*_args, **_kwargs):
        return _FakeBitmapRep()

    def _fake_row_bytes(rep):
        return bytearray(rep.bytesPerRow() * rep.pixelsHigh())

    def _fake_make_rep(w, h):
        return _FakeBitmapRep(w, h)

    def _fake_encode(_rep):
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8

    saved = {
        "render_white_on_black_rep": render_mod.render_white_on_black_rep,
        "bitmap_rep_row_bytes": render_mod.bitmap_rep_row_bytes,
        "make_bitmap_rep": render_mod.make_bitmap_rep,
        "bitmap_rep_write_row_bytes": render_mod.bitmap_rep_write_row_bytes,
        "encode_png": render_mod.encode_png,
    }
    render_mod.render_white_on_black_rep = _fake_white_on_black
    render_mod.bitmap_rep_row_bytes = _fake_row_bytes
    render_mod.make_bitmap_rep = _fake_make_rep
    render_mod.bitmap_rep_write_row_bytes = lambda _rep, _buf: None
    render_mod.encode_png = _fake_encode
    try:
        yield
    finally:
        for name, fn in saved.items():
            setattr(render_mod, name, fn)
