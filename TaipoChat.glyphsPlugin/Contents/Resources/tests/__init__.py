# encoding: utf-8
"""
Taipo test suite.

Smoke tests (no Glyphs)::

    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/smoke.py

Glyphs integration tests (Macro Panel, font open)::

    import sys; sys.path.insert(0, "/ABS/PATH/TaipoChat.glyphsPlugin/Contents/Resources")
    import tests; tests.run_glyphs_tests()
"""

from tests._bootstrap import ensure_resources_path, resources_dir

ensure_resources_path()

from tests.glyphs import run_glyphs_tests
from tests.smoke import run_smoke

__all__ = ["run_smoke", "run_glyphs_tests", "resources_dir"]
