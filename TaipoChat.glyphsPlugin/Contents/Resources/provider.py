# encoding: utf-8
"""OpenAI Chat Completions API provider. Add new LLM providers here."""

import base64
import json

from http_client import requests_post


def build_request_body(model, max_tokens, messages, system_text, tools=None):
    """
    Build an OpenAI Chat Completions request body from provider-neutral messages.

    ``messages`` is a list of dicts with provider-neutral content blocks.
    ``tools`` is a list in Anthropic schema format (will be converted).
    Returns a dict ready to POST.
    """
    gpt_messages = _convert_messages(messages, system_text)
    body = {
        "model": model,
        "max_completion_tokens": max_tokens,
        "messages": gpt_messages,
    }
    if tools:
        body["tools"] = [_convert_tool_schema(t) for t in tools]
    return body


def post_request(body, url, auth_value):
    """
    POST request body to OpenAI Chat Completions endpoint.
    Returns parsed JSON response dict.
    """
    data = json.dumps(body).encode("utf-8")
    resp = requests_post(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % auth_value.strip(),
        },
        timeout=600,
    )
    raw_bytes = resp.content
    encoding = (resp.headers.get("Content-Encoding") or "").lower().strip()
    if encoding == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
        import gzip as _gzip
        try:
            raw_bytes = _gzip.decompress(raw_bytes)
        except Exception:
            pass
    raw = raw_bytes.decode("utf-8", errors="replace")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        snippet = raw[:400].replace("\r\n", " ").replace("\n", " ")
        raise ValueError(
            "API response is not valid JSON (%s): %r" % (exc, snippet)
        ) from exc


def parse_response(payload):
    """
    Parse an OpenAI Chat Completions response.

    Returns a dict with keys:
      - ``content_blocks``: list of provider-neutral content blocks (text, tool_use)
      - ``text``: concatenated text from content
      - ``tool_uses``: list of {"id", "name", "input"} dicts
      - ``stop_reason``: normalized string ("end_turn", "tool_use", "max_tokens")
      - ``usage``: normalized dict {"input_tokens", "output_tokens", ...}
      - ``error``: None on success, else error message string
    """
    out = {
        "content_blocks": [],
        "text": "",
        "tool_uses": [],
        "stop_reason": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "error": None,
    }

    if not isinstance(payload, dict):
        out["error"] = "[error] unexpected response: %s" % str(payload)[:400]
        return out

    # Error in payload
    if "error" in payload:
        err = payload["error"]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or json.dumps(err)
        else:
            msg = str(err)
        out["error"] = "[error] %s" % msg
        return out

    # Extract choice
    choices = payload.get("choices")
    if not choices or not isinstance(choices, list):
        out["error"] = "[error] response has no choices"
        return out

    choice = choices[0]
    message = choice.get("message") or {}
    finish_reason = choice.get("finish_reason", "stop")

    # Map OpenAI stop reason to internal format
    reason_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
    out["stop_reason"] = reason_map.get(finish_reason, finish_reason)

    # Text content
    text = message.get("content")
    if isinstance(text, str):
        out["text"] = text.strip()

    # Tool calls → provider-neutral tool_use blocks
    tool_calls = message.get("tool_calls")
    if tool_calls and isinstance(tool_calls, list):
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"])
            except (KeyError, json.JSONDecodeError, TypeError):
                args = {}
            out["tool_uses"].append({
                "id": tc.get("id") or "",
                "name": tc["function"].get("name") or "",
                "input": args,
            })

    # Build content blocks from text + tool_calls (provider-neutral format)
    if out["text"]:
        out["content_blocks"].append({"type": "text", "text": out["text"]})
    for tu in out["tool_uses"]:
        out["content_blocks"].append({
            "type": "tool_use",
            "id": tu["id"],
            "name": tu["name"],
            "input": tu["input"],
        })

    # Parse usage
    usage = payload.get("usage") or {}
    out["usage"] = _normalize_usage(usage)

    return out


def _convert_tool_schema(tool):
    """Convert Anthropic tool schema to OpenAI function schema."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name") or "",
            "description": tool.get("description") or "",
            "parameters": tool.get("input_schema") or {},
        }
    }


def _convert_messages(neutral_messages, system_text):
    """
    Convert provider-neutral message list to OpenAI chat messages.

    Images in tool results are not supported in the ``tool`` role by all models.
    Instead, each image is replaced with a ``[TOOL_IMAGE_N]`` placeholder in the
    tool message text, and a single ``user`` message is injected immediately after
    the tool batch with interleaved ``(text: [TOOL_IMAGE_N])(image_url: data:...)``
    blocks.  The counter N is global across the full conversation so placeholders
    are unique and the model can correlate them across turns.
    """
    result = []
    image_counter = [0]  # mutable box so the inner helper can increment it

    if system_text:
        result.append({"role": "system", "content": system_text})

    for msg in neutral_messages:
        role = msg.get("role")

        if role == "user":
            content = msg.get("content")
            if isinstance(content, list) and content and content[0].get("type") == "tool_result":
                # --- tool result batch ---
                # Collect (placeholder, img_block) for every image found across
                # all tool results in this batch so we can build one user message.
                pending_images = []

                for block in content:
                    if block.get("type") != "tool_result":
                        continue
                    text_parts = []
                    for b in block.get("content") or []:
                        btype = b.get("type")
                        if btype == "text":
                            text_parts.append(b.get("text") or "")
                        elif btype == "image":
                            image_counter[0] += 1
                            placeholder = "[TOOL_IMAGE_%d]" % image_counter[0]
                            text_parts.append(placeholder)
                            pending_images.append((placeholder, b))
                    result.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_call_id") or "",
                        "content": "\n".join(p for p in text_parts if p),
                    })

                # Inject one user message with interleaved text+image blocks.
                if pending_images:
                    user_content = []
                    for placeholder, img_block in pending_images:
                        user_content.append({"type": "text", "text": placeholder})
                        src = img_block.get("source") or {}
                        if src.get("type") == "base64":
                            data_url = "data:%s;base64,%s" % (
                                src.get("media_type", "image/png"),
                                src.get("data", ""),
                            )
                            user_content.append({
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            })
                    result.append({"role": "user", "content": user_content})
            else:
                result.append({"role": "user", "content": content})

        elif role == "assistant":
            gpt_msg = {"role": "assistant"}
            content_blocks = msg.get("content") or []
            text_parts = []
            tool_uses = []

            for block in content_blocks:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text") or "")
                elif btype == "tool_use":
                    tool_uses.append({
                        "id": block.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "",
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    })

            if text_parts:
                gpt_msg["content"] = "\n".join(p for p in text_parts if p).strip() or ""
            if tool_uses:
                gpt_msg["tool_calls"] = tool_uses

            result.append(gpt_msg)

    return result


def _normalize_usage(usage):
    """Map OpenAI usage keys to internal canonical format."""
    out = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    if not isinstance(usage, dict):
        return out

    try:
        out["input_tokens"] = max(0, int(usage.get("prompt_tokens") or 0))
        out["output_tokens"] = max(0, int(usage.get("completion_tokens") or 0))
    except (TypeError, ValueError):
        pass

    return out
