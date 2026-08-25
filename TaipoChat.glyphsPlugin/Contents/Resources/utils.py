# encoding: utf-8
"""Pure helpers: URL, TLS, API payload, messages request, response parsing (no Glyphs / UI)."""

import json
import os

DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_MAX_TOKENS = "8192"

REASONING_EFFORT_NONE = "none"
REASONING_EFFORT_OPTIONS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

MARKER_ISSUE_RECOGNIZED = "ISSUE RECOGNIZED"
MARKER_ISSUE_NOT_RECOGNIZED = "ISSUE NOT RECOGNIZED"
MARKER_DOD_PASSED = "DOD PASSED"
MARKER_DOD_FAILED = "DOD FAILED"

SESSION_MODE_INSPECT = "inspect"
SESSION_MODE_EDIT = "edit"
TOOL_TAG_EDIT_ONLY = "[WORKS IN EDIT MODE ONLY]"
TOOL_TAG_BOTH_MODES = "[WORKS IN INSPECT AND EDIT MODES]"

_MODE_CONTRACT_INSPECT = (
    "Current mode: Inspect.\n"
    "Tools tagged %s will be rejected until the user switches to Edit.\n"
    "If they want a change, propose a plan and ask them to switch. "
    "Do not claim you will edit."
) % TOOL_TAG_EDIT_ONLY

_MODE_CONTRACT_EDIT = (
    "Current mode: Edit.\n"
    "Edit-only tools will run. Users often dislike unannounced font changes — "
    "propose a short plan and ask if they are fine with it before mutating, "
    "unless they already agreed this plan or asked you to apply it now."
)

_MODE_NOTICE_INSPECT = "Mode is now Inspect. The font will not be changed."
_MODE_NOTICE_EDIT = "Mode is now Edit. Mutation tools are available."


def normalize_session_mode(mode):
    if (mode or "").strip().lower() == SESSION_MODE_EDIT:
        return SESSION_MODE_EDIT
    return SESSION_MODE_INSPECT


def mode_contract_text(session_mode):
    if normalize_session_mode(session_mode) == SESSION_MODE_EDIT:
        return _MODE_CONTRACT_EDIT
    return _MODE_CONTRACT_INSPECT


def mode_switch_notice(session_mode):
    if normalize_session_mode(session_mode) == SESSION_MODE_EDIT:
        return _MODE_NOTICE_EDIT
    return _MODE_NOTICE_INSPECT

MAX_AGENT_ITERATIONS = 20

_SYSTEM_PROMPT_BASENAME = "system_prompt.md"


def _system_prompt_candidate_paths():
    """Repo ``assets/`` in dev checkout; ``Resources/assets/`` in installed plugin."""
    resources_dir = os.path.dirname(os.path.abspath(__file__))
    repo_asset = os.path.normpath(
        os.path.join(resources_dir, "..", "..", "..", "assets", _SYSTEM_PROMPT_BASENAME)
    )
    bundled_asset = os.path.join(resources_dir, "assets", _SYSTEM_PROMPT_BASENAME)
    if os.path.isfile(repo_asset):
        yield repo_asset
    yield bundled_asset


def load_default_system_prompt():
    """Load the default system prompt from ``assets/system_prompt.md``."""
    for path in _system_prompt_candidate_paths():
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
    raise FileNotFoundError(
        "System prompt asset not found. Expected assets/system_prompt.md in the repo "
        "or TaipoChat.glyphsPlugin/Contents/Resources/assets/."
    )


DEFAULT_SYSTEM_PROMPT = load_default_system_prompt()


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _chat_endpoint(base_url):
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return base + "/v1/chat/completions"


def parse_max_tokens(raw, default_str=DEFAULT_MAX_TOKENS):
    s = (raw or "").strip() or default_str
    try:
        return max(1, min(200000, int(s)))
    except ValueError:
        return int(default_str)


def normalize_reasoning_effort(raw):
    """Return a known reasoning effort string; unknown/empty → ``none``."""
    if isinstance(raw, bool):
        return "medium" if raw else REASONING_EFFORT_NONE
    value = (raw or "").strip().lower()
    if value in REASONING_EFFORT_OPTIONS:
        return value
    return REASONING_EFFORT_NONE


def reasoning_effort_menu_index(value):
    """Map a reasoning effort string to a PopUpButton menu index."""
    return REASONING_EFFORT_OPTIONS.index(normalize_reasoning_effort(value))


def reasoning_effort_from_menu_index(index):
    """Map a PopUpButton menu index to a reasoning effort string."""
    try:
        return REASONING_EFFORT_OPTIONS[int(index)]
    except (TypeError, ValueError, IndexError):
        return REASONING_EFFORT_NONE


def normalize_usage(usage):
    """Return Anthropic ``usage`` dict with known integer keys defaulting to 0."""
    out = {k: 0 for k in _USAGE_KEYS}
    if not isinstance(usage, dict):
        return out
    for k in _USAGE_KEYS:
        v = usage.get(k)
        if v is None:
            continue
        try:
            out[k] = max(0, int(v))
        except (TypeError, ValueError):
            continue
    return out


def format_usage_caption(last_usage, session_totals):
    """One-line English caption for the token usage TextBox."""
    z = {k: 0 for k in _USAGE_KEYS}
    if isinstance(session_totals, dict):
        for k in _USAGE_KEYS:
            try:
                z[k] = max(0, int(session_totals.get(k, 0)))
            except (TypeError, ValueError):
                z[k] = 0

    def fmt(n):
        n = int(n)
        if n >= 10000:
            return "%.1fk" % (n / 1000.0)
        return str(n)

    sess_in = z["input_tokens"] + z["cache_read_input_tokens"] + z["cache_creation_input_tokens"]
    sess_out = z["output_tokens"]
    session_part = "session: %s in + %s out" % (fmt(sess_in), fmt(sess_out))

    if last_usage is None:
        return "Tokens — last: — · %s" % session_part

    lu = normalize_usage(last_usage)
    last_in = lu["input_tokens"] + lu["cache_read_input_tokens"] + lu["cache_creation_input_tokens"]
    last_out = lu["output_tokens"]
    last_part = "last: %s in + %s out" % (fmt(last_in), fmt(last_out))
    return "Tokens — %s · %s" % (last_part, session_part)




def normalize_tool_result_content(raw):
    """
    Normalize a tool executor's return value into a list of Anthropic ``tool_result`` content blocks.

    Accepts:
      - str            → ``[{"type":"text","text":raw}]``
      - bytes (PNG)    → ``[{"type":"image","source":{"type":"base64","media_type":"image/png","data":<b64>}}]``
      - dict (single block)  → ``[dict]``
      - list of dicts / strs → normalized elementwise
      - None           → ``[{"type":"text","text":"(no content)"}]``
    """
    import base64

    def _block_for_item(item):
        if isinstance(item, (bytes, bytearray)):
            b64 = base64.b64encode(bytes(item)).decode("ascii")
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            }
        if isinstance(item, str):
            return {"type": "text", "text": item}
        if isinstance(item, dict):
            if item.get("type") in ("text", "image"):
                return item
            return {"type": "text", "text": json.dumps(item, ensure_ascii=False)}
        return {"type": "text", "text": str(item)}

    if raw is None:
        return [{"type": "text", "text": "(no content)"}]
    if isinstance(raw, list):
        return [_block_for_item(x) for x in raw] or [{"type": "text", "text": "(empty)"}]
    return [_block_for_item(raw)]


