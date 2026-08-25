# encoding: utf-8
"""Taipo Chat — agentic Chat Completions API (OpenAI-compatible) with tool use and Inspect/Edit modes."""

import threading

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSAttachmentAttributeName,
    NSAttributedString,
    NSBlockOperation,
    NSColor,
    NSEventModifierFlagShift,
    NSFont,
    NSImage,
    NSImageScaleNone,
    NSImageView,
    NSMenuItem,
    NSScrollView,
    NSSegmentedControl,
    NSSegmentStyleRounded,
    NSSegmentSwitchTrackingSelectOne,
    NSTextAttachment,
    NSViewHeightSizable,
    NSViewWidthSizable,
)
from Foundation import NSData, NSMakeRect, NSOperationQueue, NSSelectorFromString
from GlyphsApp import Glyphs, WINDOW_MENU
from GlyphsApp.plugins import GeneralPlugin
from vanilla import Button, CheckBox, EditText, Group, PopUpButton, TextBox, TextEditor, Window

from tools import DEFAULT_RENDER_CONTRACT, ToolContext
from tools.model_toolset import ModelToolset
from _version import __version__ as PLUGIN_VERSION
from session_log import begin_session, configure as configure_session_log
from state import ChatState, migration_default_strings
from transcript_format import attributed_markdown, thumbnail_size
from utils import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    REASONING_EFFORT_OPTIONS,
    SESSION_MODE_EDIT,
    SESSION_MODE_INSPECT,
    mode_switch_notice,
    normalize_reasoning_effort,
    normalize_session_mode,
    reasoning_effort_from_menu_index,
    reasoning_effort_menu_index,
)

_SETTINGS_TOGGLE_W = 76
_SETTINGS_ROW_H = 22
_SETTINGS_ROW_GAP = 6
_LABEL_ROW_H = 18
_STATUS_ROW_H = 14
_MODE_CONTROL_W = 168
_MODE_CONTROL_H = 22
_SYSTEM_PROMPT_H = 100
_SECTION_SEP_H = 1
_SECTION_SEP_GAP = 10
_STRIP_TOP = 12
_CHAT_BOTTOM_RESERVE = 290

_DEFAULTS_PREFIX = "com.taipo."

_INSERT_NEWLINE_SEL = NSSelectorFromString("insertNewline:")

_TRANSCRIPT_IMAGE_MAX_W = 440
_TRANSCRIPT_IMAGE_MAX_H = 140
_PREVIEW_WINDOW_SIZE = (960, 420)
_PREVIEW_WINDOW_MIN_SIZE = (640, 280)
_PREVIEW_MIN_MAGNIFICATION = 0.25
_PREVIEW_MAX_MAGNIFICATION = 8.0


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
    to ``assets/system_prompt.md`` take effect on the next Glyphs launch.
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
    def _build_toolset(self):
        return ModelToolset(
            ToolContext(
                font_provider=self._font_provider,
                render_contract=DEFAULT_RENDER_CONTRACT,
                api_settings=self._state.settings,
                session_mode=SESSION_MODE_INSPECT,
            )
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
            "Show Debug Info",
            value=self._debug_info,
            callback=self._on_debug_info_toggle_,
        )
        self.w.reasoningEffortLabel = TextBox(
            (12, 0, -12, _LABEL_ROW_H),
            "Reasoning effort:",
        )
        self.w.reasoningEffort = PopUpButton(
            (12, 0, -12, _SETTINGS_ROW_H),
            list(REASONING_EFFORT_OPTIONS),
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

        self.w.modeControl = Group((12, 0, _MODE_CONTROL_W, _MODE_CONTROL_H))
        self._install_mode_segment()
        self.w.tokenLabel = TextBox(
            (205, 0, -12, _STATUS_ROW_H),
            self._state.usage_caption(),
            sizeStyle="small",
        )
        self.w.statusDetail = TextBox(
            (12, 0, -12, 28),
            "Inspecting only. The font will not change.",
            sizeStyle="small",
        )

        self.w.primaryButton = Button(
            (12, 0, 88, 22),
            "Send",
            callback=self._on_primary_,
        )
        self.w.primaryButton.bind("\r", ["command"])

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
            "Show or hide API Base URL, model, max tokens, reasoning effort, "
            "transcript options, and system prompt.",
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
            self.w.showToolResults,
            "When on, shows tool inputs/outputs, turn-finished markers, and full error detail. "
            "Specimen and diff images always appear.",
        )
        _set_tooltip(
            self.w.reasoningEffort,
            "Sent as reasoning_effort on every request. "
            "If the provider rejects the value, the error appears in the transcript.",
        )

        self._sync_settings_controls_from_state()

        _in_tv = self.w.inputField.getNSTextView()
        if _in_tv is not None:
            _in_tv.setDelegate_(self)
        _tr_tv = self._transcript_text_view()
        if _tr_tv is not None:
            _tr_tv.setDelegate_(self)

        self._layout_settings_strip()

    @objc.python_method
    def _install_mode_segment(self):
        seg = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(0, 0, _MODE_CONTROL_W, _MODE_CONTROL_H)
        )
        seg.setSegmentCount_(2)
        seg.setLabel_forSegment_("Inspect", 0)
        seg.setLabel_forSegment_("Edit", 1)
        seg.setSelectedSegment_(0)
        try:
            seg.setSegmentStyle_(NSSegmentStyleRounded)
        except Exception:
            pass
        try:
            seg.setTrackingMode_(NSSegmentSwitchTrackingSelectOne)
        except Exception:
            pass
        try:
            seg.setToolTip_forSegment_(
                "Read and compare only. The font will not change.",
                0,
            )
            seg.setToolTip_forSegment_(
                "Editing tools on. Taipo can change the font; "
                "it should still ask before applying a plan.",
                1,
            )
        except Exception:
            pass
        self._apply_mode_segment_color(seg, SESSION_MODE_INSPECT)
        seg.setTarget_(self)
        seg.setAction_("modeSegmentChanged:")
        try:
            self.w.modeControl.getNSView().addSubview_(seg)
        except Exception:
            pass
        self._mode_segment = seg

    @objc.python_method
    def _apply_mode_segment_color(self, seg, mode):
        if seg is None:
            return
        try:
            if normalize_session_mode(mode) == SESSION_MODE_EDIT:
                color = NSColor.systemOrangeColor()
            else:
                color = NSColor.systemGreenColor()
            seg.setSelectedSegmentBezelColor_(color)
        except Exception:
            pass

    @objc.python_method
    def _layout_mode_segment(self):
        seg = getattr(self, "_mode_segment", None)
        if seg is None:
            return
        try:
            seg.setFrame_(NSMakeRect(0, 0, _MODE_CONTROL_W, _MODE_CONTROL_H))
        except Exception:
            pass

    @objc.python_method
    def _set_session_mode(self, mode):
        mode = normalize_session_mode(mode)
        self._session_mode = mode
        try:
            self._toolset.ctx.session_mode = mode
        except Exception:
            pass
        seg = getattr(self, "_mode_segment", None)
        if seg is not None:
            try:
                seg.setSelectedSegment_(0 if mode == SESSION_MODE_INSPECT else 1)
            except Exception:
                pass
            self._apply_mode_segment_color(seg, mode)
        self._refresh_control_ui()

    def modeSegmentChanged_(self, sender):
        selected = 0
        try:
            selected = int(sender.selectedSegment())
        except Exception:
            selected = 0
        self._set_session_mode(
            SESSION_MODE_INSPECT if selected == 0 else SESSION_MODE_EDIT
        )

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
        self._toolset = self._build_toolset()
        self._cancel_event = None
        self._worker_busy = False
        self._session_mode = SESSION_MODE_INSPECT
        self._mode_announced = SESSION_MODE_INSPECT
        self._mode_segment = None
        self._status_override = None
        self._settings_expanded = False
        self._debug_info = _show_tool_results_from_default(
            _get_default("debugInfo", "0")
        )
        self._preview = None
        self._preview_scroll = None
        self._preview_image_view = None
        self._build_window()
        self._refresh_setup_ui()
        self._refresh_control_ui()
        self._configure_session_logging()

    @objc.python_method
    def _session_log_header(self):
        import sys

        font = None
        try:
            font = Glyphs.font
        except Exception:
            pass
        glyphs_ver = "?"
        try:
            vn = getattr(Glyphs, "versionNumber", None)
            if vn is not None:
                glyphs_ver = str(vn)
        except Exception:
            pass
        glyph_count = None
        master_count = None
        font_name = None
        if font is not None:
            font_name = getattr(font, "familyName", None) or getattr(font, "name", None)
            try:
                glyph_count = len(list(font.glyphs))
            except Exception:
                pass
            try:
                master_count = len(list(font.masters))
            except Exception:
                pass
        return {
            "plugin_version": PLUGIN_VERSION,
            "python_version": "%d.%d.%d" % sys.version_info[:3],
            "glyphs_version": glyphs_ver,
            "font_name": font_name or "(none)",
            "glyph_count": glyph_count,
            "master_count": master_count,
            "model": (self._state.settings.get("model") or "").strip(),
        }

    @objc.python_method
    def _configure_session_logging(self):
        configure_session_log(
            self._debug_info,
            ui_sink=self._append_debug if self._debug_info else None,
        )
        if self._debug_info:
            begin_session(self._session_log_header())

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
            h += _LABEL_ROW_H + _SETTINGS_ROW_GAP + _SETTINGS_ROW_H
            h += _SETTINGS_ROW_GAP
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
            self.w.reasoningEffortLabel,
            self.w.reasoningEffort,
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

            self.w.reasoningEffortLabel.setPosSize((12, y, -12, _LABEL_ROW_H))
            y += _LABEL_ROW_H + _SETTINGS_ROW_GAP
            self.w.reasoningEffort.setPosSize((12, y, -12, _SETTINGS_ROW_H))
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
        self.w.modeControl.setPosSize((12, y, _MODE_CONTROL_W, _MODE_CONTROL_H))
        self._layout_mode_segment()
        self.w.tokenLabel.setPosSize(
            (12 + _MODE_CONTROL_W + 12, y + 4, -12, _STATUS_ROW_H)
        )
        y += _MODE_CONTROL_H + 6
        self.w.statusDetail.setPosSize((12, y, -12, 28))
        y += 32
        self.w.primaryButton.setPosSize((12, y, 88, 22))

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
        self.w.reasoningEffort.set(
            reasoning_effort_menu_index(s.get("reasoningEffort"))
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
    def _on_debug_info_toggle_(self, sender):
        self._debug_info = bool(self.w.showToolResults.get())
        _set_default("debugInfo", "1" if self._debug_info else "0")
        self._configure_session_logging()

    @objc.python_method
    def _save_settings_from_ui(self):
        self._state.update_settings_from_ui_fields(
            (self.w.baseUrl.get() or "").strip() or DEFAULT_BASE_URL,
            (self.w.apiKey.get() or "").strip(),
            (self.w.model.get() or "").strip() or DEFAULT_MODEL,
            (self.w.maxTokens.get() or "").strip(),
            (self.w.systemPrompt.get() or "").strip() or DEFAULT_SYSTEM_PROMPT,
            reasoning_effort_from_menu_index(self.w.reasoningEffort.get()),
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

    def textView_clickedOnCell_inRect_atIndex_(self, textView, cell, rect, index):
        try:
            transcript = self._transcript_text_view()
        except Exception:
            transcript = None
        if transcript is None or textView != transcript:
            return
        storage = textView.textStorage()
        if storage is None or index < 0 or index >= storage.length():
            return
        attachment = storage.attribute_atIndex_effectiveRange_(
            NSAttachmentAttributeName, index, None
        )
        if attachment is None:
            return
        img = attachment.image()
        if img is not None:
            self._show_image_preview(img)

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
    def _default_status_detail(self):
        if self._status_override:
            return self._status_override
        if self._worker_busy:
            return "Assistant is working…"
        if self._session_mode == SESSION_MODE_EDIT:
            return "Edit is on. Taipo can change the font after you agree a plan."
        return "Inspecting only. The font will not change."

    @objc.python_method
    def _refresh_control_ui(self):
        if not getattr(self, "w", None):
            return

        self.w.tokenLabel.set(self._state.usage_caption())
        self.w.statusDetail.set(self._default_status_detail())

        if self._worker_busy:
            self._set_primary_button("Cancel", True)
            _set_tooltip(self.w.primaryButton, "Stop the current request.")
        else:
            has_text = bool(self._message_text().strip())
            self._set_primary_button("Send", has_text)
            _set_tooltip(self.w.primaryButton, "Send your message to the assistant.")

        seg = getattr(self, "_mode_segment", None)
        if seg is not None:
            try:
                seg.setEnabled_(not self._worker_busy)
            except Exception:
                pass

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
    def _append_debug(self, text):
        line = text if text.endswith("\n") else text + "\n"
        self._append_plain_text("[debug]: " + line, color=NSColor.secondaryLabelColor())

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
    def _append_role_line(self, role_label, body, label_color, markdown=False):
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
        body_text = body or ""
        md = attributed_markdown(body_text) if markdown else None
        if md is not None:
            storage.appendAttributedString_(md)
            storage.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_("\n", body_attrs)
            )
        else:
            storage.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    "%s\n" % body_text, body_attrs
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
        thumb_w, thumb_h = thumbnail_size(
            w, h, _TRANSCRIPT_IMAGE_MAX_W, _TRANSCRIPT_IMAGE_MAX_H
        )
        attachment = NSTextAttachment.alloc().init()
        attachment.setImage_(img)
        if thumb_w > 0 and thumb_h > 0:
            attachment.setBounds_(NSMakeRect(0, 0, thumb_w, thumb_h))
        attr = NSAttributedString.attributedStringWithAttachment_(attachment)
        tv.textStorage().appendAttributedString_(attr)
        self._append_plain_text("\n")

    @objc.python_method
    def _ensure_preview_window(self):
        preview = getattr(self, "_preview", None)
        if preview is not None and getattr(preview, "_window", None) is not None:
            return
        self._preview = Window(
            _PREVIEW_WINDOW_SIZE,
            "Specimen",
            minSize=_PREVIEW_WINDOW_MIN_SIZE,
        )
        self._preview.body = Group((0, 0, -0, -0))
        scroll = NSScrollView.alloc().init()
        scroll.setHasHorizontalScroller_(True)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(False)
        scroll.setAllowsMagnification_(True)
        scroll.setMinMagnification_(_PREVIEW_MIN_MAGNIFICATION)
        scroll.setMaxMagnification_(_PREVIEW_MAX_MAGNIFICATION)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        image_view = NSImageView.alloc().init()
        image_view.setImageScaling_(NSImageScaleNone)
        scroll.setDocumentView_(image_view)
        host = self._preview.body.getNSView()
        scroll.setFrame_(host.bounds())
        host.addSubview_(scroll)
        self._preview_scroll = scroll
        self._preview_image_view = image_view

    @objc.python_method
    def _show_image_preview(self, image):
        if image is None:
            return
        self._ensure_preview_window()
        iv = self._preview_image_view
        scroll = self._preview_scroll
        iv.setImage_(image)
        sz = image.size()
        w, h = float(sz.width), float(sz.height)
        if w <= 0 or h <= 0:
            return
        iv.setFrame_(NSMakeRect(0, 0, w, h))
        scroll.setMagnification_(1.0)
        self._preview.open()
        ns_win = self._preview.getNSWindow()
        if ns_win is not None:
            ns_win.makeKeyAndOrderFront_(None)

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
                    markdown=True,
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
            if self._debug_info or has_image:
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
                    if btype == "text" and self._debug_info:
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
        elif kind == "usage_updated":
            self.w.tokenLabel.set(self._state.usage_caption())
        elif kind == "done":
            if self._debug_info:
                reason = event.get("stop_reason") or "end_turn"
                self._append_plain_text("\n[turn finished: %s]\n\n" % reason)
            self._refresh_control_ui()
        elif kind == "iteration_limit":
            self._append_plain_text(
                "\n[iteration limit reached]\n\n",
                color=NSColor.systemOrangeColor(),
            )
            self._status_override = "Iteration limit reached."
            self._refresh_control_ui()
        elif kind == "cancelled":
            self._append_plain_text("\n[cancelled by user]\n\n", color=NSColor.systemOrangeColor())
            self._status_override = "Cancelled."
            self._refresh_control_ui()
        elif kind == "error":
            self._append_plain_text(
                "\n[error] %s\n\n" % (event.get("text") or ""),
                color=NSColor.systemRedColor(),
            )
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
        def run():
            self._toolset._ctx.debug_info = self._debug_info
            self._toolset._ctx.session_mode = self._session_mode
            return self._toolset.execute(name, args)

        return _run_on_main_sync(run)

    @objc.python_method
    def _start_turn(self, user_text):
        if self._worker_busy:
            return
        self._save_settings_from_ui()
        err = self._state.validate_setting_errors()
        if err:
            _show_alert("Taipo Chat", err)
            return
        if user_text and self._session_mode != self._mode_announced:
            user_text = "%s\n\n%s" % (
                mode_switch_notice(self._session_mode),
                user_text,
            )
            self._mode_announced = self._session_mode
        self._status_override = None
        self._cancel_event = threading.Event()
        self._set_busy(True)

        def worker():
            try:
                self._state.run_agent_turn(
                    user_text=user_text,
                    tool_executor=self._tool_executor,
                    tool_schemas=ModelToolset.schemas(),
                    on_event=self._dispatch_event,
                    cancel_event=self._cancel_event,
                    session_mode=self._session_mode,
                )
            except Exception as e:
                from session_log import get_logger

                get_logger("agent").exception("Worker turn failed")
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
    def _on_cancel_(self, sender):
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.w.primaryButton.enable(False)

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
        self._mode_announced = SESSION_MODE_INSPECT
        self._set_session_mode(SESSION_MODE_INSPECT)
        self._status_override = None
        registry = getattr(self._toolset.ctx, "render_registry", None)
        if registry is not None:
            registry.clear()
        if self._debug_info:
            begin_session(self._session_log_header())
        self._refresh_setup_ui()
        self._refresh_control_ui()
        self._save_settings_from_ui()

    @objc.python_method
    def __file__(self):
        return __file__
