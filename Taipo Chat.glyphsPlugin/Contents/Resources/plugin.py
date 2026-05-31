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
from vanilla import Button, EditText, TextBox, TextEditor, Window

import tools
from _version import __version__ as PLUGIN_VERSION
from state import ChatState, migration_default_strings
from utils import DEFAULT_BASE_URL

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

        y = 12
        self.w.baseUrlLabel = TextBox((12, y, 300, 14), "Base URL (POST → …/v1/chat/completions)")
        y += 18
        self.w.baseUrl = EditText(
            (12, y, -12, 22),
            s["baseUrl"],
            placeholder=DEFAULT_BASE_URL,
            continuous=False,
        )
        y += 30

        self.w.apiKeyLabel = TextBox((12, y, 300, 14), "API key (Authorization: Bearer …)")
        y += 18
        self.w.apiKey = EditText(
            (12, y, -12, 22),
            s["apiKey"],
            placeholder="Paste token",
            continuous=False,
        )
        y += 30

        self.w.modelLabel = TextBox((12, y, 120, 14), "Model")
        y += 18
        self.w.model = EditText(
            (12, y, -12, 22),
            s["model"],
            continuous=False,
        )
        y += 30

        self.w.maxTokensLabel = TextBox((12, y, 200, 14), "Max tokens")
        y += 18
        self.w.maxTokens = EditText(
            (12, y, 120, 22),
            s["maxTokens"],
            continuous=False,
        )
        y += 30

        self.w.systemLabel = TextBox((12, y, 200, 14), "System prompt")
        y += 18
        self.w.systemPrompt = TextEditor(
            (12, y, -12, 96),
            text=s["systemPrompt"],
            checksSpelling=True,
        )
        y += 104

        self.w.transcriptLabel = TextBox((12, y, 200, 14), "Transcript")
        y += 18
        self.w.transcript = TextEditor(
            (12, y, -12, 248),
            text="",
            readOnly=True,
            checksSpelling=False,
        )
        y += 258

        self.w.inputLabel = TextBox((12, y, 200, 14), "Message")
        y += 18
        self.w.inputField = TextEditor(
            (12, y, -12, 72),
            text="",
            readOnly=False,
            checksSpelling=True,
        )
        y += 80

        self.w.modeLabel = TextBox(
            (12, y, 185, 14),
            "Mode: Planning",
            sizeStyle="small",
        )
        self.w.beforeEditLabel = TextBox(
            (205, y, 175, 14),
            "Before: none",
            sizeStyle="small",
        )
        self.w.tokenLabel = TextBox(
            (390, y, -12, 14),
            self._state.usage_caption(),
            sizeStyle="small",
        )
        y += 18
        self.w.statusDetail = TextBox(
            (12, y, -12, 28),
            "Ready. Describe a fix, then Send.",
            sizeStyle="small",
        )
        y += 32

        self.w.primaryButton = Button(
            (12, y, 88, 22),
            "Send",
            callback=self._on_primary_,
        )
        self.w.primaryButton.bind("\r", ["command"])
        self.w.approveButton = Button(
            (108, y, 100, 22),
            "Approve plan",
            callback=self._on_approve_plan_,
        )
        self.w.revertEditsButton = Button(
            (214, y, 118, 22),
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

        _in_tv = self.w.inputField.getNSTextView()
        if _in_tv is not None:
            _in_tv.setDelegate_(self)

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
        self._build_window()
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
        self._refresh_control_ui()

    @objc.python_method
    def _save_settings_from_ui(self):
        self._state.update_settings_from_ui_fields(
            (self.w.baseUrl.get() or "").strip(),
            (self.w.apiKey.get() or "").strip(),
            (self.w.model.get() or "").strip(),
            (self.w.maxTokens.get() or "").strip(),
            self.w.systemPrompt.get() or "",
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
            self._append_plain_text("You: %s\n" % event.get("text", ""))
        elif kind == "assistant_text":
            text = event.get("text") or ""
            if text:
                self._append_plain_text("Assistant: %s\n" % text)
        elif kind == "tool_use":
            line = "[tool_use] %s(%s)\n" % (
                event.get("name", "?"),
                _brief_json(event.get("input") or {}),
            )
            self._append_plain_text(line, color=NSColor.systemBlueColor())
        elif kind == "tool_result":
            blocks = event.get("content") or []
            is_error = bool(event.get("is_error"))
            prefix = "[tool_result%s] %s:\n" % (
                " error" if is_error else "",
                event.get("name", "?"),
            )
            self._append_plain_text(
                prefix,
                color=NSColor.systemRedColor() if is_error else NSColor.systemGrayColor(),
            )
            for b in blocks:
                btype = b.get("type")
                if btype == "text":
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
        self.w.systemPrompt.set(self._state.settings["systemPrompt"])
        self.w.inputField.set("")
        self._plan_pending = False
        self._editing_mode = False
        self._status_override = None
        store = getattr(self._tool_ctx, "snapshot_store", None)
        if store is not None:
            store.clear()
        self._refresh_control_ui()
        self._save_settings_from_ui()

    @objc.python_method
    def __file__(self):
        return __file__
