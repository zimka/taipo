# encoding: utf-8
"""
Unit tests for session_log (no Glyphs required).

Run from the repo root::

    uv run python TaipoChat.glyphsPlugin/Contents/Resources/tests/test_session_log.py
"""

import logging
import os
import sys
import tempfile

_RESOURCES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)

import session_log


def _test_configure_off_is_silent():
    ui_calls = []
    session_log.configure(False, ui_sink=ui_calls.append)
    logger = session_log.get_logger("agent")
    logger.warning("should not appear")
    logger.info("also silent")
    assert ui_calls == []
    assert len(logging.getLogger("taipo").handlers) == 1
    assert isinstance(logging.getLogger("taipo").handlers[0], logging.NullHandler)


def _test_file_gets_debug_ui_gets_warning_only():
    ui_calls = []
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "assets", "last_session.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        old_path = session_log.session_log_path
        session_log.session_log_path = lambda: log_path
        try:
            session_log.configure(True, ui_sink=ui_calls.append)
            session_log.begin_session({"plugin_version": "0.0-test"})

            logger = session_log.get_logger("tools")
            logger.debug("debug line")
            logger.info("info line")
            logger.warning("warn line")
            try:
                raise ValueError("boom")
            except ValueError:
                logger.exception("tool failed")

            for handler in logging.getLogger("taipo").handlers:
                if isinstance(handler, logging.FileHandler):
                    handler.flush()

            text = open(log_path, encoding="utf-8").read()
            assert "debug line" in text
            assert "info line" in text
            assert "warn line" in text
            assert "Traceback" in text
            assert "ValueError: boom" in text
            assert any("warn line" in msg for msg in ui_calls)
            assert not any("debug line" in msg for msg in ui_calls)
            assert not any("info line" in msg for msg in ui_calls)
        finally:
            session_log.session_log_path = old_path
            session_log.configure(False)


def _test_log_chat_message_writes_user_and_assistant():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "assets", "last_session.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        old_path = session_log.session_log_path
        session_log.session_log_path = lambda: log_path
        try:
            session_log.configure(True)
            session_log.begin_session({"plugin_version": "0.0-test"})
            session_log.log_chat_message("user", "Check kerning on A")
            session_log.log_chat_message(
                "assistant",
                "**Finding**\n\nKerning is **not consistent**.",
            )
            session_log.log_chat_message("assistant", "")
            session_log.log_chat_message("user", None)
            for handler in logging.getLogger("taipo").handlers:
                if isinstance(handler, logging.FileHandler):
                    handler.flush()
            text = open(log_path, encoding="utf-8").read()
            assert "user:\nCheck kerning on A" in text
            assert "assistant:\n**Finding**" in text
            assert "Kerning is **not consistent**." in text
            assert text.count("user:\n") == 1
            assert text.count("assistant:\n") == 1
        finally:
            session_log.session_log_path = old_path
            session_log.configure(False)


def _test_probe_log_file_writable():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "nested", "last_session.log")
        old_path = session_log.session_log_path
        session_log.session_log_path = lambda: log_path
        try:
            assert session_log.probe_log_file_writable() is True
            assert os.path.isfile(log_path)
        finally:
            session_log.session_log_path = old_path


def _test_unwritable_log_skips_file_handler():
    ui_calls = []
    old_path = session_log.session_log_path
    session_log.session_log_path = lambda: "/nonexistent_readonly_path/last_session.log"
    old_probe = session_log.probe_log_file_writable
    session_log.probe_log_file_writable = lambda: False
    try:
        session_log.configure(True, ui_sink=ui_calls.append)
        assert session_log._file_logging_enabled is False
        assert any("insufficient permissions" in msg for msg in ui_calls)
        logger = session_log.get_logger("render")
        logger.warning("visible")
        assert any("visible" in msg for msg in ui_calls)
    finally:
        session_log.probe_log_file_writable = old_probe
        session_log.session_log_path = old_path
        session_log.configure(False)


def run_tests():
    _test_configure_off_is_silent()
    _test_file_gets_debug_ui_gets_warning_only()
    _test_log_chat_message_writes_user_and_assistant()
    _test_probe_log_file_writable()
    _test_unwritable_log_skips_file_handler()
    print("Taipo Chat Resources/tests/test_session_log.py: run_tests() OK")


if __name__ == "__main__":
    run_tests()
