# encoding: utf-8
"""Ensure plugin Resources dir is on sys.path for tools/utils imports."""

import os
import sys

_RESOURCES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_resources_path():
    if _RESOURCES_DIR not in sys.path:
        sys.path.insert(0, _RESOURCES_DIR)
    return _RESOURCES_DIR


def resources_dir():
    """Absolute path to the plugin Resources folder."""
    return _RESOURCES_DIR
