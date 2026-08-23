# encoding: utf-8
"""Session logging for Taipo Chat.

Writes the conversation (user and assistant text), tool calls, and errors to
``assets/last_session.log``. Attach that file when reporting bugs.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_ROOT_LOGGER_NAME = "taipo"
_file_handler: logging.FileHandler | None = None
_ui_handler: logging.Handler | None = None
_file_logging_enabled = False
_ui_sink: Callable[[str], None] | None = None
_file_write_warning_shown = False


def _ensure_default_silent() -> None:
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.addHandler(logging.NullHandler())


_ensure_default_silent()


def resources_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def session_log_path() -> str:
    return os.path.join(resources_dir(), "assets", "last_session.log")


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under ``taipo`` (e.g. ``agent``, ``tools``, ``render``)."""
    _ensure_default_silent()
    if name.startswith(_ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger("%s.%s" % (_ROOT_LOGGER_NAME, name))


def log_chat_message(role: str, text: str) -> None:
    """Append a user or assistant message body to the session log."""
    if text is None:
        return
    body = str(text)
    if not body:
        return
    get_logger("agent").info("%s:\n%s", role, body)


def brief_tool_args(value: Any, limit: int = 180) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


class _UIHandler(logging.Handler):
    """Emit WARNING+ log records to the plugin transcript sink."""

    def __init__(self, ui_sink: Callable[[str], None]):
        super().__init__(level=logging.WARNING)
        self._ui_sink = ui_sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if msg:
                self._ui_sink(msg)
        except Exception:
            self.handleError(record)


def _log_formatter() -> logging.Formatter:
    return logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)


def _clear_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def probe_log_file_writable() -> bool:
    path = session_log_path()
    assets_dir = os.path.dirname(path)
    try:
        os.makedirs(assets_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass
        return True
    except OSError:
        return False


def _show_file_write_warning() -> None:
    global _file_write_warning_shown
    if _file_write_warning_shown or _ui_sink is None:
        return
    _file_write_warning_shown = True
    path = session_log_path()
    _ui_sink(
        "Cannot write session log at %s (insufficient permissions). File logging disabled."
        % path
    )


def configure(debug_enabled: bool, ui_sink: Callable[[str], None] | None = None) -> None:
    """Configure logging handlers. Debug off: silent NullHandler only."""
    global _file_handler, _ui_handler, _file_logging_enabled, _ui_sink, _file_write_warning_shown

    _ui_sink = ui_sink
    _file_logging_enabled = False
    _file_write_warning_shown = False

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _clear_handlers(logger)

    if not debug_enabled:
        logger.addHandler(logging.NullHandler())
        return

    formatter = _log_formatter()

    if probe_log_file_writable():
        _file_handler = logging.FileHandler(session_log_path(), mode="a", encoding="utf-8")
        _file_handler.setLevel(logging.DEBUG)
        _file_handler.setFormatter(formatter)
        logger.addHandler(_file_handler)
        _file_logging_enabled = True
    else:
        _file_handler = None
        _show_file_write_warning()

    if ui_sink is not None:
        _ui_handler = _UIHandler(ui_sink)
        _ui_handler.setFormatter(formatter)
        logger.addHandler(_ui_handler)
    else:
        _ui_handler = None


def _format_session_header(header: dict[str, Any]) -> str:
    lines = [
        "=" * 72,
        "Taipo Chat session log",
        "log_path: %s" % session_log_path(),
        "=" * 72,
    ]
    for key in (
        "timestamp",
        "plugin_version",
        "python_version",
        "glyphs_version",
        "font_name",
        "glyph_count",
        "master_count",
        "model",
    ):
        if key in header and header[key] is not None:
            lines.append("%s: %s" % (key, header[key]))
    lines.append("-" * 72)
    return "\n".join(lines) + "\n"


def _truncate_log_file(header_text: str) -> None:
    global _file_handler
    path = session_log_path()
    if _file_handler is not None:
        _file_handler.acquire()
        try:
            _file_handler.flush()
            if _file_handler.stream:
                _file_handler.stream.close()
            stream = open(path, "w", encoding="utf-8")
            stream.write(header_text)
            stream.flush()
            _file_handler.stream = stream
        finally:
            _file_handler.release()
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(header_text)


def begin_session(header: dict[str, Any] | None = None) -> None:
    """Start a new session log (truncate file when file logging is enabled)."""
    header = dict(header or {})
    if "timestamp" not in header:
        header["timestamp"] = datetime.now(timezone.utc).isoformat()
    if "python_version" not in header:
        header["python_version"] = "%d.%d.%d" % sys.version_info[:3]

    header_text = _format_session_header(header)
    if _file_logging_enabled:
        _truncate_log_file(header_text)

    logger = get_logger("agent")
    logger.info("Session started")
    if header.get("font_name"):
        logger.info(
            "Font %r glyphs=%s masters=%s",
            header.get("font_name"),
            header.get("glyph_count"),
            header.get("master_count"),
        )
    if header.get("model"):
        logger.info("Model %s", header.get("model"))
    if _file_logging_enabled and _ui_sink is not None:
        _ui_sink("Session log: %s" % session_log_path())
