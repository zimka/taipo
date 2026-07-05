# encoding: utf-8
"""
Taipo test suite.

Smoke tests (no Glyphs)::

    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/smoke.py
    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/test_model_toolset.py
    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/smoke_model_toolset.py

Glyphs integration tests (Macro Panel, font open)::

    import sys; sys.path.insert(0, "/ABS/PATH/TaipoChat.glyphsPlugin/Contents/Resources")
    import tests; tests.run_glyphs_tests()
"""

from tests._bootstrap import ensure_resources_path, resources_dir

ensure_resources_path()


def run_smoke():
    from tests.smoke import run_smoke as _run

    return _run()


def run_smoke_model_toolset():
    from tests.smoke_model_toolset import run_smoke_model_toolset as _run

    return _run()


def run_glyphs_tests():
    from tests.glyphs import run_glyphs_tests as _run

    return _run()


__all__ = ["run_smoke", "run_smoke_model_toolset", "run_glyphs_tests", "resources_dir"]
