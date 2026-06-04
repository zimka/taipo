# encoding: utf-8
"""Taipo Chat — agentic Chat Completions API (OpenAI-compatible) with tool use and human-in-the-loop."""

import threading

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSAttributedString,
    NSBlockOperation,
    NSColor,
    NSEventModifierFlagShift,
    NSFont,
    NSImage,
    NSMenuItem,
    NSTextAttachment,
)
from Foundation import NSData, NSOperationQueue, NSSelectorFromString, NSSize
from GlyphsApp import Glyphs, WINDOW_MENU
from GlyphsApp.plugins import GeneralPlugin
from vanilla import Button, CheckBox, EditText, TextBox, TextEditor, Window

import tools
from _version import __version__ as PLUGIN_VERSION
from state import ChatState, migration_default_strings
from utils import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
)

_SETTINGS_TOGGLE_W = 76
_SETTINGS_ROW_H = 22
_SETTINGS_ROW_GAP = 6
_LABEL_ROW_H = 18
_STATUS_ROW_H = 14
_SYSTEM_PROMPT_H = 100
_SECTION_SEP_H = 1
_SECTION_SEP_GAP = 10
_STRIP_TOP = 12
_CHAT_BOTTOM_RESERVE = 290

_DEFAULTS_PREFIX = "com.taipo."

_INSERT_NEWLINE_SEL = NSSelectorFromString("insertNewline:")

_TRANSCRIPT_IMAGE_MAX_W = 440
_TRANSCRIPT_IMAGE_MAX_H = 140


def _defaults_key(name):
    return _DEFAULTS_PREFIX + name


def _get_default(name, fallback=""):
    try:
        d = Glyphs.defaults
        if d is None:
            return fallback
        v = d[_defaults_key(name)]
        if v is None:
            return fallback
        return str(v)
    except Exception:
        return fallback


def _set_default(name, value):
    try:
        Glyphs.defaults[_defaults_key(name)] = value
    except Exception:
        pass


def _show_tool_results_from_default(raw):
    return str(raw).strip().lower() not in ("0", "false", "no")


def _show_alert(title, text):
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(text)
    alert.runModal()


def _load_persistent_settings(state):
    """Load baseUrl / apiKey / model / maxTokens from Glyphs.defaults.

    ``systemPrompt`` is intentionally NOT loaded during active development, so that updates
    to ``DEFAULT_SYSTEM_PROMPT`` in :mod:`utils` take effect on the next Glyphs launch.
    """
    blob = _get_default("settingsJson", "")
    if blob and str(blob).strip():
        state.set_settings_json(str(blob))
    else:
        dm, dmt, _dsp = migration_default_strings()
        state.migrate_from_legacy_flat(
            baseUrl=_get_default("baseUrl", DEFAULT_BASE_URL),
            apiKey=_get_default("apiKey", ""),
            model=_get_default("model", dm),
            maxTokens=_get_default("maxTokens", dmt),
        )


def _run_on_main_sync(fn):
    """Execute ``fn()`` synchronously on the main thread and return its value.

    MUST be called from a background thread only — calling this from the main thread
    self-waits on ``addOperations_waitUntilFinished_`` and deadlocks the UI.
    """
    box = {}

    def wrapper():
        try:
            box["value"] = fn()
        except BaseException as e:
            box["error"] = e

    op = NSBlockOperation.blockOperationWithBlock_(wrapper)
    NSOperationQueue.mainQueue().addOperations_waitUntilFinished_([op], True)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _brief_json(value, limit=180):
    import json

    try:
        s = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        s = str(value)
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def _is_approve_message(text):
    return (text or "").strip().lower() == "approve"


def _set_tooltip(control, message):
    try:
        control.setToolTip(message)
    except Exception:
        pass


def _style_separator(textbox):
    try:
        tf = textbox.getNSTextField()
        tf.setDrawsBackground_(True)
        try:
            tf.setBackgroundColor_(NSColor.separatorColor())
        except Exception:
            tf.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.35, 1.0))
        tf.setBordered_(False)
        tf.setEditable_(False)
        tf.setSelectable_(False)
    except Exception:
        pass


class TaipoChatPlugin(GeneralPlugin):
    windowName = "com.taipo.TaipoChat.main"
    _frame_autosave_set = False

    @objc.python_method
    def _font_provider(self):
        return Glyphs.font

    @objc.python_method
    def _build_tool_context(self):
        return tools.ToolContext(
            font_provider=self._font_provider,
            render_contract=tools.DEFAULT_RENDER_CONTRACT,
            snapshot_store=tools.SnapshotStore(),
        )

    @objc.python_method
    def _build_window(self):
        self._frame_autosave_set = False
        s = self._state.settings
        self.w = Window((620, 900), self.name, minSize=(580, 800))

        mt = s.get("maxTokens") or ""
        if mt == DEFAULT_MAX_TOKENS:
            mt = ""

        self.w.settingsHeader = TextBox(
            (12, _STRIP_TOP, -12, _LABEL_ROW_H),
            "Model settings",
        )
        self.w.apiKeyLabel = TextBox(
            (12, 0, 200, _LABEL_ROW_H),
            "API key:",
        )
        self.w.apiKey = EditText(
            (12, 0, -12, _SETTINGS_ROW_H),
            s["apiKey"],
            placeholder="Paste token",
            continuous=True,
        )
        self.w.settingsToggle = Button(
            (-(_SETTINGS_TOGGLE_W + 12), 0, _SETTINGS_TOGGLE_W, _SETTINGS_ROW_H),
            "Expand",
            callback=self._on_settings_toggle_,
        )
        self.w.baseUrlLabel = TextBox(
            (12, 0, -12, _LABEL_ROW_H),
            "API Base URL:",
        )
        self.w.baseUrl = EditText(
            (12, 0, -12, _SETTINGS_ROW_H),
            s["baseUrl"],
            placeholder=DEFAULT_BASE_URL,
            continuous=False,
        )
        self.w.modelLabel = TextBox(
            (12, 0, -12, _LABEL_ROW_H),
            "Model:",
        )
        self.w.model = EditText(
            (12, 0, -12, _SETTINGS_ROW_H),
            s["model"],
            continuous=False,
        )
        self.w.maxTokensLabel = TextBox(
            (12, 0, -12, _LABEL_ROW_H),
            "Max tokens:",
        )
        self.w.maxTokens = EditText(
            (12, 0, -12, _SETTINGS_ROW_H),
            mt,
            placeholder="2048",
            continuous=False,
        )
        self.w.systemPromptLabel = TextBox(
            (12, 0, -12, _LABEL_ROW_H),
            "System prompt:",
        )
        self.w.systemPrompt = TextEditor(
            (12, 0, -12, _SYSTEM_PROMPT_H),
            text=s.get("systemPrompt") or DEFAULT_SYSTEM_PROMPT,
            readOnly=False,
            checksSpelling=False,
        )
        self.w.showToolResults = CheckBox(
            (12, 0, -12, _SETTINGS_ROW_H),
            "Show Tool Results",
            value=self._show_tool_results,
            callback=self._on_show_tool_results_toggle_,
        )
        self.w.sectionDivider = TextBox((12, 0, -12, _SECTION_SEP_H), "")
        _style_separator(self.w.sectionDivider)

        self.w.transcriptLabel = TextBox((12, 0, 200, _LABEL_ROW_H), "Transcript")
        self.w.transcript = TextEditor(
            (12, 0, -12, 200),
            text="",
            readOnly=True,
            checksSpelling=False,
        )
        self.w.inputLabel = TextBox((12, 0, 200, _LABEL_ROW_H), "Message")
        self.w.inputField = TextEditor(
            (12, 0, -12, 72),
            text="",
            readOnly=False,
            checksSpelling=True,
        )

        self.w.modeLabel = TextBox(
            (12, 0, 185, _STATUS_ROW_H),
            "Mode: Planning",
            sizeStyle="small",
        )
        self.w.beforeEditLabel = TextBox(
            (205, 0, 175, _STATUS_ROW_H),
            "Before: none",
            sizeStyle="small",
        )
        self.w.tokenLabel = TextBox(
            (390, 0, -12, _STATUS_ROW_H),
            self._state.usage_caption(),
            sizeStyle="small",
        )
        self.w.statusDetail = TextBox(
            (12, 0, -12, 28),
            "Ready. Describe a fix, then Send.",
            sizeStyle="small",
        )

        self.w.primaryButton = Button(
            (12, 0, 88, 22),
            "Send",
            callback=self._on_primary_,
        )
        self.w.primaryButton.bind("\r", ["command"])
        self.w.approveButton = Button(
            (108, 0, 100, 22),
            "Approve plan",
            callback=self._on_approve_plan_,
        )
        self.w.revertEditsButton = Button(
            (214, 0, 118, 22),
            "Revert Edits",
            callback=self._on_reset_snapshot_,
        )

        self.w.versionLabel = TextBox(
            (12, -20, -12, 14),
            "Taipo Chat v%s" % PLUGIN_VERSION,
            sizeStyle="small",
            alignment="right",
        )

        _set_tooltip(
            self.w.apiKey,
            "Paste your OpenAI API key. Stored in Glyphs preferences on this Mac. "
            "OpenAI defaults are already set — expand only to change host, model, or token limit.",
        )
        _set_tooltip(
            self.w.settingsToggle,
            "Show or hide API Base URL, model, max tokens, transcript options, and system prompt.",
        )
        _set_tooltip(
            self.w.baseUrl,
            "Root URL of an OpenAI-compatible API (no /v1/chat/completions suffix).",
        )
        _set_tooltip(self.w.maxTokens, "Leave empty for default 2048.")
        _set_tooltip(
            self.w.systemPrompt,
            "Instructions sent to the model on every turn. Saved when you Send.",
        )
        _set_tooltip(
            self.w.inputField,
            "Return to send. Shift+Return for new line.",
        )
        _set_tooltip(self.w.primaryButton, "Send your message to the assistant.")
        _set_tooltip(
            self.w.approveButton,
            "Authorize the pending plan. You can also type Approve alone.",
        )
        _set_tooltip(
            self.w.revertEditsButton,
            "Restore listed glyphs to their before-edit state. ⌘Z in Glyphs also works.",
        )
        _set_tooltip(
            self.w.showToolResults,
            "When off, hides text tool output and turn-finished markers from new events. "
            "Specimen and diff images still appear.",
        )

        self._sync_settings_controls_from_state()

        _in_tv = self.w.inputField.getNSTextView()
        if _in_tv is not None:
            _in_tv.setDelegate_(self)

        self._layout_settings_strip()

    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize(
            {
                "en": "Taipo Chat",
                "de": "Taipo Chat",
                "fr": "Taipo Chat",
                "es": "Taipo Chat",
            }
        )
        self._state = ChatState()
        _load_persistent_settings(self._state)
        self._tool_ctx = self._build_tool_context()
        self._cancel_event = None
        self._worker_busy = False
        self._plan_pending = False
        self._editing_mode = False
        self._status_override = None
        self._settings_expanded = False
        self._show_tool_results = _show_tool_results_from_default(
            _get_default("showToolResults", "1")
        )
        self._build_window()
        self._refresh_setup_ui()
        self._refresh_control_ui()

    @objc.python_method
    def start(self):
        if Glyphs.buildNumber >= 3320:
            from GlyphsApp.UI import MenuItem

            new_menu_item = MenuItem(self.name, action=self.showWindow_, target=self)
        else:
            new_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                self.name, self.showWindow_, ""
            )
            new_menu_item.setTarget_(self)
        Glyphs.menu[WINDOW_MENU].append(new_menu_item)

    def showWindow_(self, sender):
        if getattr(self.w, "_window", None) is None:
            self._build_window()
        self.w.open()
        ns_win = self.w.getNSWindow()
        if ns_win is not None:
            if not self._frame_autosave_set:
                ns_win.setFrameAutosaveName_(self.windowName)
                self._frame_autosave_set = True
            ns_win.makeKeyAndOrderFront_(self)
        self._refresh_setup_ui()
        self._refresh_control_ui()

    @objc.python_method
    def _settings_strip_height(self):
        h = (
            _LABEL_ROW_H
            + _SETTINGS_ROW_GAP
            + _LABEL_ROW_H
            + _SETTINGS_ROW_GAP
            + _SETTINGS_ROW_H
        )
        if getattr(self, "_settings_expanded", False):
            h += 3 * (
                _LABEL_ROW_H
                + _SETTINGS_ROW_GAP
                + _SETTINGS_ROW_H
                + _SETTINGS_ROW_GAP
            )
            h += _SETTINGS_ROW_H + _SETTINGS_ROW_GAP
            h += _LABEL_ROW_H + _SETTINGS_ROW_GAP + _SYSTEM_PROMPT_H
        return h

    @objc.python_method
    def _chat_top_y(self):
        return (
            _STRIP_TOP
            + self._settings_strip_height()
            + _SECTION_SEP_GAP
            + _SECTION_SEP_H
            + 8
        )

    @objc.python_method
    def _layout_settings_strip(self):
        y = _STRIP_TOP
        self.w.settingsHeader.setPosSize((12, y, -12, _LABEL_ROW_H))
        y += _LABEL_ROW_H + _SETTINGS_ROW_GAP

        self.w.apiKeyLabel.setPosSize((12, y, 200, _LABEL_ROW_H))
        self.w.settingsToggle.setPosSize(
            (-(_SETTINGS_TOGGLE_W + 12), y, _SETTINGS_TOGGLE_W, _SETTINGS_ROW_H)
        )
        y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
        self.w.apiKey.setPosSize((12, y, -12, _SETTINGS_ROW_H))
        y += _SETTINGS_ROW_H + _SETTINGS_ROW_GAP

        expanded = getattr(self, "_settings_expanded", False)
        expanded_controls = (
            self.w.baseUrlLabel,
            self.w.baseUrl,
            self.w.modelLabel,
            self.w.model,
            self.w.maxTokensLabel,
            self.w.maxTokens,
            self.w.showToolResults,
            self.w.systemPromptLabel,
            self.w.systemPrompt,
        )
        for control in expanded_controls:
            control.show(expanded)

        if expanded:
            self.w.baseUrlLabel.setPosSize((12, y, -12, _LABEL_ROW_H))
            y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
            self.w.baseUrl.setPosSize((12, y, -12, _SETTINGS_ROW_H))
            y += _SETTINGS_ROW_H + _SETTINGS_ROW_GAP

            self.w.modelLabel.setPosSize((12, y, -12, _LABEL_ROW_H))
            y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
            self.w.model.setPosSize((12, y, -12, _SETTINGS_ROW_H))
            y += _SETTINGS_ROW_H + _SETTINGS_ROW_GAP

            self.w.maxTokensLabel.setPosSize((12, y, -12, _LABEL_ROW_H))
            y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
            self.w.maxTokens.setPosSize((12, y, -12, _SETTINGS_ROW_H))
            y += _SETTINGS_ROW_H + _SETTINGS_ROW_GAP

            self.w.showToolResults.setPosSize((12, y, -12, _SETTINGS_ROW_H))
            y += _SETTINGS_ROW_H + _SETTINGS_ROW_GAP

            self.w.systemPromptLabel.setPosSize((12, y, -12, _LABEL_ROW_H))
            y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
            self.w.systemPrompt.setPosSize((12, y, -12, _SYSTEM_PROMPT_H))

        try:
            ns_btn = self.w.settingsToggle.getNSButton()
            if ns_btn is not None:
                ns_btn.setTitle_("Collapse" if expanded else "Expand")
        except Exception:
            pass

        sep_y = _STRIP_TOP + self._settings_strip_height() + (_SECTION_SEP_GAP // 2)
        self.w.sectionDivider.setPosSize((12, sep_y, -12, _SECTION_SEP_H))

        self._layout_chat_section()

    @objc.python_method
    def _layout_chat_section(self):
        top = self._chat_top_y()
        try:
            win_h = self.w.getPosSize()[3]
        except Exception:
            win_h = 900
        transcript_h = max(180, win_h - top - _CHAT_BOTTOM_RESERVE)
        y = top
        self.w.transcriptLabel.setPosSize((12, y, 200, _LABEL_ROW_H))
        y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
        self.w.transcript.setPosSize((12, y, -12, transcript_h))
        y += transcript_h + 10
        self.w.inputLabel.setPosSize((12, y, 200, _LABEL_ROW_H))
        y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
        self.w.inputField.setPosSize((12, y, -12, 72))
        y += 80
        self.w.modeLabel.setPosSize((12, y, 185, _STATUS_ROW_H))
        self.w.beforeEditLabel.setPosSize((205, y, 175, _STATUS_ROW_H))
        self.w.tokenLabel.setPosSize((390, y, -12, _STATUS_ROW_H))
        y += 18
        self.w.statusDetail.setPosSize((12, y, -12, 28))
        y += 32
        self.w.primaryButton.setPosSize((12, y, 88, 22))
        self.w.approveButton.setPosSize((108, y, 100, 22))
        self.w.revertEditsButton.setPosSize((214, y, 118, 22))

    @objc.python_method
    def _sync_settings_controls_from_state(self):
        s = self._state.settings
        self.w.baseUrl.set(s.get("baseUrl") or DEFAULT_BASE_URL)
        self.w.model.set((s.get("model") or "").strip() or DEFAULT_MODEL)
        mt = (s.get("maxTokens") or "").strip()
        if mt == DEFAULT_MAX_TOKENS:
            mt = ""
        self.w.maxTokens.set(mt)
        self.w.systemPrompt.set(
            (s.get("systemPrompt") or "").strip() or DEFAULT_SYSTEM_PROMPT
        )

    @objc.python_method
    def _refresh_setup_ui(self):
        if not getattr(self, "w", None):
            return
        self._layout_settings_strip()

    @objc.python_method
    def _on_settings_toggle_(self, sender):
        self._settings_expanded = not self._settings_expanded
        self._refresh_setup_ui()

    @objc.python_method
    def _on_show_tool_results_toggle_(self, sender):
        self._show_tool_results = bool(self.w.showToolResults.get())
        _set_default("showToolResults", "1" if self._show_tool_results else "0")

    @objc.python_method
    def _save_settings_from_ui(self):
        self._state.update_settings_from_ui_fields(
            (self.w.baseUrl.get() or "").strip() or DEFAULT_BASE_URL,
            (self.w.apiKey.get() or "").strip(),
            (self.w.model.get() or "").strip() or DEFAULT_MODEL,
            (self.w.maxTokens.get() or "").strip(),
            (self.w.systemPrompt.get() or "").strip() or DEFAULT_SYSTEM_PROMPT,
        )
        _set_default("settingsJson", self._state.get_settings_json())

    def textView_doCommandBySelector_(self, textView, commandSelector):
        if commandSelector != _INSERT_NEWLINE_SEL:
            return False
        try:
            in_tv = self.w.inputField.getNSTextView()
        except Exception:
            in_tv = None
        if in_tv is None or textView != in_tv:
            return False
        return self._handle_input_insert_newline()

    def textDidChange_(self, notification):
        self._refresh_control_ui()

    @objc.python_method
    def _message_text(self):
        """Live message field text (NSTextView is authoritative while typing)."""
        try:
            tv = self.w.inputField.getNSTextView()
            if tv is not None:
                return str(tv.string() or "")
        except Exception:
            pass
        try:
            return str(self.w.inputField.get() or "")
        except Exception:
            return ""

    @objc.python_method
    def _transcript_text_view(self):
        try:
            return self.w.transcript.getNSTextView()
        except Exception:
            return None

    @objc.python_method
    def _handle_input_insert_newline(self):
        """Delegate helper: True if Return was handled (no newline inserted)."""
        if self._worker_busy:
            return False
        evt = NSApp.currentEvent()
        if evt is not None and evt.modifierFlags() & NSEventModifierFlagShift:
            return False
        if not self._message_text().strip():
            return False
        self._on_send_(None)
        return True

    @objc.python_method
    def _has_snapshot(self):
        store = getattr(self._tool_ctx, "snapshot_store", None)
        return bool(store and store.has_snapshot())

    @objc.python_method
    def _before_edit_caption(self):
        store = getattr(self._tool_ctx, "snapshot_store", None)
        if not store or not store.has_snapshot():
            return "Before: none"
        names = list(getattr(store, "_glyph_names", []) or [])
        preview = ", ".join(names[:3])
        if len(names) > 3:
            preview += ", +%d" % (len(names) - 3)
        return "Before: %s" % (preview or "(saved)")

    @objc.python_method
    def _default_status_detail(self):
        if self._status_override:
            return self._status_override
        if self._worker_busy:
            if self._editing_mode:
                return "Applying approved plan…"
            return "Assistant is working…"
        if self._plan_pending:
            return "Plan ready — review above, then Approve plan or reply to revise."
        if self._has_snapshot():
            return "Edits done. Check diff above. Undo: Revert Edits or ⌘Z in Glyphs."
        return "Ready. Describe a fix, then Send."

    @objc.python_method
    def _refresh_control_ui(self):
        if not getattr(self, "w", None):
            return

        mode = "Editing" if self._editing_mode and self._worker_busy else "Planning"
        self.w.modeLabel.set("Mode: %s" % mode)
        self.w.beforeEditLabel.set(self._before_edit_caption())
        self.w.tokenLabel.set(self._state.usage_caption())
        self.w.statusDetail.set(self._default_status_detail())

        if self._worker_busy:
            self._set_primary_button("Cancel", True)
            _set_tooltip(self.w.primaryButton, "Stop the current request.")
        else:
            has_text = bool(self._message_text().strip())
            self._set_primary_button("Send", has_text)
            _set_tooltip(self.w.primaryButton, "Send your message to the assistant.")

        self.w.approveButton.enable(self._plan_pending and not self._worker_busy)
        self.w.revertEditsButton.enable(self._has_snapshot() and not self._worker_busy)

        try:
            self.w.inputField.enable(not self._worker_busy)
        except Exception:
            pass

    @objc.python_method
    def _set_primary_button(self, title, enabled):
        self.w.primaryButton.enable(enabled)
        try:
            ns_btn = self.w.primaryButton.getNSButton()
            if ns_btn is not None:
                ns_btn.setTitle_(title)
        except Exception:
            pass

    @objc.python_method
    def _append_plain_text(self, text, color=None):
        tv = self._transcript_text_view()
        if tv is None:
            return
        attrs = {}
        attrs["NSColor"] = color if color is not None else NSColor.textColor()
        body_font = NSFont.userFontOfSize_(12.0)
        if body_font is not None:
            attrs["NSFont"] = body_font
        attr_str = NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        tv.textStorage().appendAttributedString_(attr_str)

    @objc.python_method
    def _append_role_line(self, role_label, body, label_color):
        """Append ``role_label: body`` with a colored role prefix and default body color."""
        tv = self._transcript_text_view()
        if tv is None:
            return
        body_font = NSFont.userFontOfSize_(12.0)
        storage = tv.textStorage()
        prefix_attrs = {"NSColor": label_color}
        body_attrs = {"NSColor": NSColor.textColor()}
        if body_font is not None:
            prefix_attrs["NSFont"] = body_font
            body_attrs["NSFont"] = body_font
        storage.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                "%s: " % role_label, prefix_attrs
            )
        )
        storage.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                "%s\n" % (body or ""), body_attrs
            )
        )

    @objc.python_method
    def _append_image(self, png_bytes):
        tv = self._transcript_text_view()
        if tv is None or not png_bytes:
            return
        data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
        img = NSImage.alloc().initWithData_(data)
        if img is None:
            return
        sz = img.size()
        w, h = float(sz.width), float(sz.height)
        if w > 0 and h > 0:
            scale = min(_TRANSCRIPT_IMAGE_MAX_W / w, _TRANSCRIPT_IMAGE_MAX_H / h, 1.0)
            img.setSize_(NSSize(int(w * scale), int(h * scale)))
        attachment = NSTextAttachment.alloc().init()
        attachment.setImage_(img)
        attr = NSAttributedString.attributedStringWithAttachment_(attachment)
        tv.textStorage().appendAttributedString_(attr)
        self._append_plain_text("\n")

    @objc.python_method
    def _scroll_to_end(self):
        tv = self._transcript_text_view()
        if tv is None:
            return
        length = tv.textStorage().length()
        tv.scrollRangeToVisible_((length, 0))

    @objc.python_method
    def _set_busy(self, busy):
        self._worker_busy = busy
        if not busy:
            self._editing_mode = False
            self._status_override = None
        self._refresh_control_ui()

    @objc.python_method
    def _on_event(self, event):
        """Dispatched on main thread. ``event`` is a dict (see ``ChatState.run_agent_turn``)."""
        kind = event.get("kind")

        if kind == "user":
            self._append_role_line(
                "You",
                event.get("text", ""),
                NSColor.systemOrangeColor(),
            )
        elif kind == "assistant_text":
            text = event.get("text") or ""
            if text:
                self._append_role_line(
                    "Assistant",
                    text,
                    NSColor.systemPurpleColor(),
                )
        elif kind == "tool_use":
            line = "[tool_use] %s(%s)\n" % (
                event.get("name", "?"),
                _brief_json(event.get("input") or {}),
            )
            self._append_plain_text(line, color=NSColor.systemBlueColor())
        elif kind == "tool_result":
            blocks = event.get("content") or []
            has_image = any(b.get("type") == "image" for b in blocks)
            if self._show_tool_results or has_image:
                is_error = bool(event.get("is_error"))
                prefix = "[tool_result%s] %s:\n" % (
                    " error" if is_error else "",
                    event.get("name", "?"),
                )
                self._append_plain_text(
                    prefix,
                    color=NSColor.systemRedColor()
                    if is_error
                    else NSColor.systemGrayColor(),
                )
                for b in blocks:
                    btype = b.get("type")
                    if btype == "text" and self._show_tool_results:
                        self._append_plain_text((b.get("text") or "") + "\n")
                    elif btype == "image":
                        src = b.get("source") or {}
                        if src.get("type") == "base64":
                            import base64

                            try:
                                raw = base64.b64decode(src.get("data") or "")
                            except Exception:
                                raw = b""
                            if raw:
                                self._append_image(raw)
        elif kind == "approval_required":
            self._plan_pending = True
            self._refresh_control_ui()
        elif kind == "usage_updated":
            self.w.tokenLabel.set(self._state.usage_caption())
        elif kind == "done":
            if self._show_tool_results:
                reason = event.get("stop_reason") or "end_turn"
                self._append_plain_text("\n[turn finished: %s]\n\n" % reason)
            self._plan_pending = False
            self._refresh_control_ui()
        elif kind == "iteration_limit":
            self._append_plain_text(
                "\n[iteration limit reached]\n\n",
                color=NSColor.systemOrangeColor(),
            )
            self._plan_pending = False
            self._status_override = "Iteration limit reached."
            self._refresh_control_ui()
        elif kind == "cancelled":
            self._append_plain_text("\n[cancelled by user]\n\n", color=NSColor.systemOrangeColor())
            self._plan_pending = False
            self._status_override = "Cancelled."
            self._refresh_control_ui()
        elif kind == "error":
            self._append_plain_text(
                "\n[error] %s\n\n" % (event.get("text") or ""),
                color=NSColor.systemRedColor(),
            )
            self._plan_pending = False
            err = (event.get("text") or "").strip()
            self._status_override = err[:120] if err else "Error."
            self._refresh_control_ui()

        if kind in ("tool_result", "done", "cancelled", "iteration_limit"):
            self._refresh_control_ui()
        self._scroll_to_end()

    @objc.python_method
    def _dispatch_event(self, event):
        NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: self._on_event(event))

    @objc.python_method
    def _tool_executor(self, name, args):
        return _run_on_main_sync(lambda: tools.execute_tool(name, args, self._tool_ctx))

    @objc.python_method
    def _start_turn(self, user_text):
        if self._worker_busy:
            return
        self._save_settings_from_ui()
        err = self._state.validate_setting_errors()
        if err:
            _show_alert("Taipo Chat", err)
            return
        if _is_approve_message(user_text):
            self._editing_mode = True
        self._status_override = None
        self._cancel_event = threading.Event()
        self._set_busy(True)

        def worker():
            try:
                self._state.run_agent_turn(
                    user_text=user_text,
                    tool_executor=self._tool_executor,
                    tool_schemas=tools.TOOL_SCHEMAS,
                    on_event=self._dispatch_event,
                    cancel_event=self._cancel_event,
                )
            except Exception as e:
                self._dispatch_event({"kind": "error", "text": str(e)})
            finally:
                NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda: self._set_busy(False)
                )

        threading.Thread(target=worker, daemon=True).start()

    @objc.python_method
    def _on_primary_(self, sender):
        if self._worker_busy:
            self._on_cancel_(sender)
        else:
            self._on_send_(sender)

    @objc.python_method
    def _on_send_(self, sender):
        text = self._message_text().strip()
        if not text:
            return
        self.w.inputField.set("")
        self._refresh_control_ui()
        self._start_turn(text)

    @objc.python_method
    def _on_approve_plan_(self, sender):
        if self._worker_busy or not self._plan_pending:
            return
        self.w.inputField.set("")
        self._start_turn("Approve")

    @objc.python_method
    def _on_cancel_(self, sender):
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.w.primaryButton.enable(False)

    @objc.python_method
    def _on_reset_snapshot_(self, sender):
        if self._worker_busy:
            return
        store = getattr(self._tool_ctx, "snapshot_store", None)
        if store is None or not store.has_snapshot():
            self._refresh_control_ui()
            return
        font = self._font_provider()
        if font is None:
            _show_alert("Taipo Chat", "No font is open — cannot revert edits.")
            return
        try:
            info = store.reset(font)
        except Exception as e:
            _show_alert("Taipo Chat", "Revert failed: %s" % e)
            return
        names = ", ".join(info.get("glyph_names", []) or [])
        self._append_plain_text(
            "\n[reverted edits] %s\n\n" % names,
            color=NSColor.systemOrangeColor(),
        )
        self._refresh_control_ui()
        self._scroll_to_end()

    @objc.python_method
    def _on_new_chat_(self, sender):
        """Clear session state. No UI button yet — open a fresh window via Window menu."""
        if self._worker_busy and self._cancel_event is not None:
            self._cancel_event.set()
        self._state.clear()
        tv = self._transcript_text_view()
        if tv is not None:
            tv.textStorage().setAttributedString_(NSAttributedString.alloc().initWithString_(""))
        self._state.reset_system_prompt_to_default()
        self.w.inputField.set("")
        self._plan_pending = False
        self._editing_mode = False
        self._status_override = None
        store = getattr(self._tool_ctx, "snapshot_store", None)
        if store is not None:
            store.clear()
        self._refresh_setup_ui()
        self._refresh_control_ui()
        self._save_settings_from_ui()

    @objc.python_method
    def __file__(self):
        return __file__
