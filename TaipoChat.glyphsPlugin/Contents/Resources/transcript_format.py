# encoding: utf-8
"""Transcript formatting helpers (no Glyphs / Vanilla imports)."""


def thumbnail_size(width, height, max_width, max_height):
    """Return integer ``(w, h)`` that fits in max bounds without upscaling."""
    if width <= 0 or height <= 0:
        return 0, 0
    scale = min(float(max_width) / width, float(max_height) / height, 1.0)
    return int(width * scale), int(height * scale)


def attributed_markdown(text):
    """Parse ``text`` as markdown into an ``NSAttributedString``, or ``None``.

    Uses Foundation's markdown API (macOS 12+). Returns ``None`` when the API
    is missing, ``text`` is empty, or parsing fails. Tables are not laid out
    as grids; callers should treat that as unsupported.
    """
    if not text:
        return None
    try:
        from Foundation import (
            NSAttributedString,
            NSAttributedStringMarkdownParsingOptions,
        )
    except Exception:
        return None
    if not hasattr(NSAttributedString, "alloc"):
        return None
    if not hasattr(
        NSAttributedString.alloc(),
        "initWithMarkdownString_options_baseURL_error_",
    ):
        return None
    try:
        opts = NSAttributedStringMarkdownParsingOptions.alloc().init()
        # Full block syntax stores list/heading breaks only as presentation
        # intents. NSTextView does not turn those into newlines, so words
        # glue together (e.g. "Finding" + "Kerning" -> "FindingKerning").
        # Inline+whitespace keeps source newlines and still styles bold/italic.
        try:
            from Foundation import (
                NSAttributedStringMarkdownInterpretedSyntaxInlineOnlyPreservingWhitespace,
            )

            opts.setInterpretedSyntax_(
                NSAttributedStringMarkdownInterpretedSyntaxInlineOnlyPreservingWhitespace
            )
        except Exception:
            if hasattr(opts, "setInterpretedSyntax_"):
                opts.setInterpretedSyntax_(2)
        result = NSAttributedString.alloc().initWithMarkdownString_options_baseURL_error_(
            text, opts, None, None
        )
    except Exception:
        return None
    if isinstance(result, tuple):
        result = result[0]
    if result is None:
        return None
    try:
        if result.length() == 0:
            return None
    except Exception:
        return None
    return _apply_dynamic_text_color(result)


def _apply_dynamic_text_color(attr_str):
    """Force system ``textColor`` so markdown is readable in dark mode.

    Foundation's markdown parser emits a fixed dark foreground. Replacing it
    with the dynamic catalog color keeps bold/links/intents and tracks appearance.
    """
    try:
        from AppKit import NSColor, NSForegroundColorAttributeName
    except Exception:
        return attr_str
    try:
        mutable = attr_str.mutableCopy()
        length = mutable.length()
        if length <= 0:
            return attr_str
        mutable.addAttribute_value_range_(
            NSForegroundColorAttributeName,
            NSColor.textColor(),
            (0, length),
        )
        return mutable
    except Exception:
        return attr_str
