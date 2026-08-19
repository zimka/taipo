# encoding: utf-8
"""Glyph metadata read/write for GSGlyph font-wide properties."""

from __future__ import annotations

import json
import re
import typing
from dataclasses import asdict, dataclass, field, fields
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from tools.font_access import resolve_glyph, resolve_master
from tools.formatting import int_or_none

READ_ONLY: frozenset[str] = frozenset({"name"})

_CASE_TOKENS = frozenset({"uppercase", "lowercase", "smallCaps"})
_DIRECTION_TOKENS = frozenset({"LTR", "RTL"})

_TIER_B_STRING_FIELDS = (
    ("category", "category", "storeCategory"),
    ("subCategory", "subCategory", "storeSubCategory"),
    ("script", "script", "storeScript"),
)

_TIER_D_STRING_FIELDS = (
    "leftKerningGroup",
    "rightKerningGroup",
    "leftMetricsKey",
    "rightMetricsKey",
    "widthMetricsKey",
)

_SIMPLE_METRICS_REF = re.compile(r"^=?([A-Za-z0-9_.-]+)$")
_KERNING_GROUP_FIELDS = frozenset({"leftKerningGroup", "rightKerningGroup"})


@dataclass
class GlyphMetadata:
    name: str = field(metadata={"description": "Glyph name (read-only)."})
    unicode: str | None = field(
        default=None,
        metadata={
            "description": (
                "Unicode codepoint as uppercase hex without U+ prefix (e.g. '0402'); "
                "null if the glyph is unencoded."
            ),
        },
    )
    export: bool = field(
        default=True,
        metadata={"description": "Whether the glyph is included in exported fonts."},
    )
    note: str | None = field(
        default=None,
        metadata={"description": "Editor note attached to the glyph; null if empty."},
    )
    category: str | None = field(
        default=None,
        metadata={
            "description": (
                "Custom glyph category (e.g. 'Letter', 'Symbol', 'Mark'); null when "
                "inherited from Glyphs auto-classification."
            ),
        },
    )
    subCategory: str | None = field(
        default=None,
        metadata={
            "description": (
                "Custom glyph subcategory (e.g. 'Uppercase', 'Nonspacing'); null when "
                "inherited from Glyphs auto-classification."
            ),
        },
    )
    script: str | None = field(
        default=None,
        metadata={
            "description": (
                "Custom script tag (e.g. 'latin', 'cyrillic'); null when inherited "
                "from Glyphs auto-classification."
            ),
        },
    )
    case: str | None = field(
        default=None,
        metadata={
            "description": (
                "Custom case classification: 'uppercase', 'lowercase', or 'smallCaps'; "
                "null when inherited from Glyphs auto-classification."
            ),
        },
    )
    direction: str | None = field(
        default=None,
        metadata={
            "description": (
                "Custom writing direction: 'LTR' or 'RTL'; null when inherited from "
                "Glyphs auto-classification."
            ),
        },
    )
    leftKerningGroup: str | None = field(
        default=None,
        metadata={
            "description": (
                "Left kerning group label (kern class for the glyph's left side); "
                "null when unset."
            ),
        },
    )
    rightKerningGroup: str | None = field(
        default=None,
        metadata={
            "description": (
                "Right kerning group label (kern class for the glyph's right side); "
                "null when unset."
            ),
        },
    )
    leftMetricsKey: str | None = field(
        default=None,
        metadata={
            "description": (
                "Left sidebearing metrics key (e.g. '=A' to link to glyph A); null when unset."
            ),
        },
    )
    rightMetricsKey: str | None = field(
        default=None,
        metadata={
            "description": (
                "Right sidebearing metrics key (e.g. '=A' to link to glyph A); null when unset."
            ),
        },
    )
    widthMetricsKey: str | None = field(
        default=None,
        metadata={
            "description": (
                "Width metrics key (e.g. '=A' to link width to glyph A); null when unset."
            ),
        },
    )


class MetadataSchema:
    """Annotation marker for JSON Schema generation from ``GlyphMetadata``."""

    __slots__ = ("dataclass_type", "mode")

    def __init__(self, dataclass_type: type, mode: Literal["full", "patch"]):
        self.dataclass_type = dataclass_type
        self.mode = mode


MetadataPatch = Annotated[dict[str, Any], MetadataSchema(GlyphMetadata, "patch")]


def _is_nullable(hint: Any) -> bool:
    origin = get_origin(hint)
    union_type = getattr(__import__("types"), "UnionType", None)
    if origin is typing.Union or origin is union_type:
        return type(None) in get_args(hint)
    return False


def _base_hint(hint: Any) -> Any:
    origin = get_origin(hint)
    union_type = getattr(__import__("types"), "UnionType", None)
    if origin is typing.Union or origin is union_type:
        non_none = [a for a in get_args(hint) if a is not type(None)]
        return non_none[0] if non_none else str
    return hint


def _json_type_for_hint(hint: Any) -> str:
    base = _base_hint(hint)
    if base is str:
        return "string"
    if base is bool:
        return "boolean"
    if base is int:
        return "integer"
    if base is float:
        return "number"
    return "string"


def _field_to_json_property(dc_field, hint: Any) -> dict[str, Any]:
    json_type = _json_type_for_hint(hint)
    prop: dict[str, Any] = (
        {"type": [json_type, "null"]} if _is_nullable(hint) else {"type": json_type}
    )
    description = dc_field.metadata.get("description")
    if description:
        prop["description"] = description
    return prop


def json_schema_from_dataclass(cls: type, *, mode: Literal["full", "patch"]) -> dict[str, Any]:
    """Build a JSON Schema object from a dataclass definition."""
    hints = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for dc_field in fields(cls):
        if mode == "patch" and dc_field.name in READ_ONLY:
            continue
        hint = hints.get(dc_field.name, str)
        properties[dc_field.name] = _field_to_json_property(dc_field, hint)
        if mode == "full" and not _is_nullable(hint):
            required.append(dc_field.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if mode == "full" and required:
        schema["required"] = required
    return schema


def metadata_json_schema(*, mode: Literal["full", "patch"]) -> dict[str, Any]:
    return json_schema_from_dataclass(GlyphMetadata, mode=mode)


def metadata_schema_doc(*, mode: Literal["full", "patch"]) -> str:
    return json.dumps(metadata_json_schema(mode=mode), indent=2)


def writable_field_names() -> frozenset[str]:
    return frozenset(f.name for f in fields(GlyphMetadata) if f.name not in READ_ONLY)


def _glyphs_case_map() -> dict[str, int]:
    try:
        from GlyphsApp import GSUppercase, GSLowercase, GSSmallcaps

        return {
            "uppercase": int(GSUppercase),
            "lowercase": int(GSLowercase),
            "smallCaps": int(GSSmallcaps),
        }
    except Exception:
        return {"uppercase": 1, "lowercase": 2, "smallCaps": 3}


def _glyphs_case_reverse() -> dict[int, str]:
    return {value: token for token, value in _glyphs_case_map().items()}


def _glyphs_direction_map() -> dict[str, int]:
    return {"LTR": 0, "RTL": 1}


def _glyphs_direction_reverse() -> dict[int, str]:
    return {value: token for token, value in _glyphs_direction_map().items()}


def _normalize_unicode_from_glyph(raw: Any) -> str | None:
    uni = str(raw or "").strip().upper()
    if uni.startswith("U+"):
        uni = uni[2:]
    return uni if uni else None


def _read_glyph_unicode_hex(glyph) -> str | None:
    """Read effective unicode hex from GSGlyph (unicode + unicodes list)."""
    uni = _normalize_unicode_from_glyph(getattr(glyph, "unicode", None))
    if uni:
        return uni
    try:
        for item in list(getattr(glyph, "unicodes", None) or []):
            normalized = _normalize_unicode_from_glyph(item)
            if normalized:
                return normalized
    except Exception:
        pass
    return None


def _write_glyph_unicode(glyph, hex_value: str) -> None:
    """Write unicode consistently to GSGlyph.unicode and GSGlyph.unicodes."""
    uni = str(hex_value or "").strip().upper()
    if uni.startswith("U+"):
        uni = uni[2:]
    glyph.unicode = uni
    try:
        glyph.unicodes = [uni] if uni else []
    except Exception:
        pass


def _normalize_optional_str(raw: Any) -> str | None:
    text = str(raw or "").strip()
    return text if text else None


def _read_stored_str(glyph, value_attr: str, store_attr: str) -> str | None:
    if not bool(getattr(glyph, store_attr, False)):
        return None
    return _normalize_optional_str(getattr(glyph, value_attr, None))


def _read_case(glyph) -> str | None:
    if not bool(getattr(glyph, "storeCase", False)):
        return None
    raw = getattr(glyph, "case", None)
    if raw is None:
        return None
    try:
        return _glyphs_case_reverse().get(int(raw))
    except (TypeError, ValueError):
        return None


def _read_direction(glyph) -> str | None:
    if not bool(getattr(glyph, "storeDirection", False)):
        return None
    raw = getattr(glyph, "direction", None)
    if raw is None:
        return None
    try:
        return _glyphs_direction_reverse().get(int(raw))
    except (TypeError, ValueError):
        return None


def dump_glyph_metadata(glyph) -> dict[str, Any]:
    """Read font-wide glyph metadata into a JSON-serializable dict."""
    export_val = getattr(glyph, "export", True)
    if export_val is None:
        export_val = True
    tier_b = {
        json_field: _read_stored_str(glyph, value_attr, store_attr)
        for json_field, value_attr, store_attr in _TIER_B_STRING_FIELDS
    }
    tier_d: dict[str, Any] = {}
    for field_name in _TIER_D_STRING_FIELDS:
        raw = _normalize_optional_str(getattr(glyph, field_name, None))
        if field_name in _KERNING_GROUP_FIELDS:
            tier_d[field_name] = _format_kerning_group_api(raw)
        else:
            tier_d[field_name] = raw
    return asdict(
        GlyphMetadata(
            name=str(getattr(glyph, "name", "") or ""),
            unicode=_read_glyph_unicode_hex(glyph),
            export=bool(export_val),
            note=_normalize_optional_str(getattr(glyph, "note", None)),
            case=_read_case(glyph),
            direction=_read_direction(glyph),
            **tier_b,
            **tier_d,
        )
    )


def _format_kerning_group_api(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return "@" + text


def _coerce_kerning_group_field(field_name: str, value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("%s must be a string or null." % field_name)
    text = value.strip()
    if not text:
        return ""
    if text.startswith("@"):
        rest = text[1:].strip()
        if not rest:
            raise ValueError("%s must be a non-empty string or null to clear." % field_name)
        if rest.startswith("MMK_"):
            raise ValueError("use @Group not %s" % text)
        return rest
    return text


def _validate_unicode_change(glyph_name: str, value: Any) -> None:
    if glyph_name == ".notdef" and value is not None:
        raise ValueError(".notdef unicode may only be cleared (null), never assigned.")


def _coerce_unicode_value(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("unicode must be a string or null.")
    uni = value.strip().upper()
    if uni.startswith("U+"):
        uni = uni[2:]
    if not uni:
        return ""
    try:
        int(uni, 16)
    except ValueError as exc:
        raise ValueError(
            "unicode must be hex digits without U+ prefix (e.g. '0402')."
        ) from exc
    return uni


def _coerce_optional_str_field(field_name: str, value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("%s must be a string or null." % field_name)
    text = value.strip()
    if not text:
        raise ValueError("%s must be a non-empty string or null to clear." % field_name)
    return text


def _coerce_case_value(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("case must be a string or null.")
    token = value.strip()
    if token not in _CASE_TOKENS:
        raise ValueError(
            "case must be one of %s or null." % sorted(_CASE_TOKENS)
        )
    return token


def _coerce_direction_value(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("direction must be a string or null.")
    token = value.strip()
    if token not in _DIRECTION_TOKENS:
        raise ValueError(
            "direction must be one of %s or null." % sorted(_DIRECTION_TOKENS)
        )
    return token


def _validate_metrics_key_reference(font, value: str) -> None:
    match = _SIMPLE_METRICS_REF.match(value.strip())
    if not match:
        return
    glyph_name = match.group(1)
    if font.glyphs[glyph_name] is None:
        raise ValueError(
            "Metrics key %r references unknown glyph %r." % (value, glyph_name)
        )


def _find_duplicate_unicode(font, hex_value: str, exclude_glyph) -> str | None:
    if not hex_value:
        return None
    target = hex_value.upper()
    for other in font.glyphs:
        if other is exclude_glyph:
            continue
        other_uni = _read_glyph_unicode_hex(other)
        if other_uni == target:
            return str(getattr(other, "name", "") or "")
    return None


_SPACING_FIELDS = frozenset({"lsb", "rsb", "width"})


def dump_spacing_for_master(glyph, master, layer) -> dict[str, Any]:
    lsb = int(round(float(getattr(layer, "LSB", 0) or 0)))
    rsb = int(round(float(getattr(layer, "RSB", 0) or 0)))
    width = int(round(float(getattr(layer, "width", 0) or 0)))
    return {
        "master": master.name,
        "lsb": lsb,
        "rsb": rsb,
        "width": width,
    }


def apply_spacing_changes(glyph, master, layer, spacing_changes: dict[str, Any]) -> list[str]:
    applied: list[str] = []
    if "lsb" in spacing_changes:
        val = int_or_none(spacing_changes["lsb"])
        if val is None:
            raise ValueError("lsb must be an integer.")
        old = int(round(float(getattr(layer, "LSB", 0) or 0)))
        layer.LSB = val
        applied.append("lsb: %d -> %d" % (old, val))
    if "rsb" in spacing_changes:
        val = int_or_none(spacing_changes["rsb"])
        if val is None:
            raise ValueError("rsb must be an integer.")
        old = int(round(float(getattr(layer, "RSB", 0) or 0)))
        layer.RSB = val
        applied.append("rsb: %d -> %d" % (old, val))
    if "width" in spacing_changes:
        val = int_or_none(spacing_changes["width"])
        if val is None:
            raise ValueError("width must be an integer.")
        if val < 0:
            raise ValueError("width must be non-negative.")
        old = int(round(float(getattr(layer, "width", 0) or 0)))
        layer.width = val
        applied.append("width: %d -> %d" % (old, val))
    return applied


def apply_glyph_metadata(glyph, changes: dict[str, Any], font, master=None) -> str:
    """Apply a partial metadata patch. Returns a human-readable summary or error string."""
    if not isinstance(changes, dict):
        return "[error] 'changes' must be a JSON object."
    if not changes:
        return "[error] 'changes' must include at least one writable field."

    spacing_keys = _SPACING_FIELDS & set(changes.keys())
    metadata_changes = {k: v for k, v in changes.items() if k not in _SPACING_FIELDS}

    if spacing_keys and master is None:
        return "[error] lsb, rsb, and width require 'master' in edit_glyph_metadata."

    writable = writable_field_names()
    unknown = sorted(set(metadata_changes.keys()) - writable)
    if unknown:
        return (
            "[error] Unknown or read-only field(s): %s. Writable fields: %s."
            % (unknown, sorted(writable | _SPACING_FIELDS))
        )

    parsed: dict[str, Any] = {}
    try:
        if "unicode" in metadata_changes:
            raw = metadata_changes["unicode"]
            _validate_unicode_change(glyph.name or "", raw)
            new_uni = _coerce_unicode_value(raw)
            duplicate = _find_duplicate_unicode(font, new_uni, glyph)
            if duplicate:
                raise ValueError(
                    "Unicode %s is already assigned to glyph %r." % (new_uni, duplicate)
                )
            parsed["unicode"] = new_uni

        if "export" in metadata_changes:
            raw = metadata_changes["export"]
            if raw is None:
                raise ValueError("export cannot be null.")
            if not isinstance(raw, bool):
                raise ValueError("export must be a boolean.")
            parsed["export"] = raw

        if "note" in metadata_changes:
            raw = metadata_changes["note"]
            if raw is None:
                parsed["note"] = ""
            elif isinstance(raw, str):
                parsed["note"] = raw
            else:
                raise ValueError("note must be a string or null.")

        for json_field, _, _ in _TIER_B_STRING_FIELDS:
            if json_field in metadata_changes:
                parsed[json_field] = _coerce_optional_str_field(
                    json_field, metadata_changes[json_field]
                )

        if "case" in metadata_changes:
            parsed["case"] = _coerce_case_value(metadata_changes["case"])

        if "direction" in metadata_changes:
            parsed["direction"] = _coerce_direction_value(metadata_changes["direction"])

        for field_name in _TIER_D_STRING_FIELDS:
            if field_name in metadata_changes:
                if field_name in _KERNING_GROUP_FIELDS:
                    value = _coerce_kerning_group_field(
                        field_name, metadata_changes[field_name]
                    )
                else:
                    value = _coerce_optional_str_field(
                        field_name, metadata_changes[field_name]
                    )
                if value and field_name not in _KERNING_GROUP_FIELDS:
                    _validate_metrics_key_reference(font, value)
                parsed[field_name] = value
    except ValueError as exc:
        return "[error] %s" % exc

    if not parsed and not spacing_keys:
        return "[error] No changes applied."

    applied: list[str] = []
    undo_started = False

    try:
        try:
            font.beginUndo()
            undo_started = True
        except Exception:
            undo_started = False

        if "unicode" in parsed:
            new_uni = parsed["unicode"]
            old_uni = _read_glyph_unicode_hex(glyph) or ""
            _write_glyph_unicode(glyph, new_uni)
            applied.append("unicode: %r -> %r" % (old_uni or None, new_uni or None))
            try:
                font.updateFeatures()
            except Exception:
                pass

        if "export" in parsed:
            old_export = getattr(glyph, "export", True)
            glyph.export = parsed["export"]
            applied.append("export: %s -> %s" % (old_export, parsed["export"]))

        if "note" in parsed:
            glyph.note = parsed["note"]
            applied.append("note: updated")

        for json_field, value_attr, store_attr in _TIER_B_STRING_FIELDS:
            if json_field not in parsed:
                continue
            value = parsed[json_field]
            if value:
                setattr(glyph, store_attr, True)
                setattr(glyph, value_attr, value)
                applied.append("%s: %r" % (json_field, value))
            else:
                setattr(glyph, store_attr, False)
                setattr(glyph, value_attr, "")
                applied.append("%s: cleared (inherit auto)" % json_field)

        if "case" in parsed:
            token = parsed["case"]
            if token is None:
                glyph.storeCase = False
                glyph.case = 0
                applied.append("case: cleared (inherit auto)")
            else:
                glyph.storeCase = True
                glyph.case = _glyphs_case_map()[token]
                applied.append("case: %r" % token)

        if "direction" in parsed:
            token = parsed["direction"]
            if token is None:
                glyph.storeDirection = False
                glyph.direction = 0
                applied.append("direction: cleared (inherit auto)")
            else:
                glyph.storeDirection = True
                glyph.direction = _glyphs_direction_map()[token]
                applied.append("direction: %r" % token)

        for field_name in _TIER_D_STRING_FIELDS:
            if field_name not in parsed:
                continue
            value = parsed[field_name]
            old_value = _normalize_optional_str(getattr(glyph, field_name, None))
            setattr(glyph, field_name, value)
            applied.append(
                "%s: %r -> %r" % (field_name, old_value, value or None)
            )

        if spacing_keys:
            if master is None:
                raise ValueError("master is required for spacing fields.")
            layer = glyph.layers[master.id]
            if layer is None:
                raise ValueError(
                    "Glyph %s has no layer for master %s." % (glyph.name, master.name)
                )
            applied.extend(
                apply_spacing_changes(
                    glyph, master, layer, {k: changes[k] for k in spacing_keys}
                )
            )

    except Exception as exc:
        if undo_started:
            try:
                font.endUndo()
            except Exception:
                pass
        return "[error] %s" % exc

    if undo_started:
        try:
            font.endUndo()
        except Exception:
            pass

    if not applied:
        return "[error] No changes applied."
    return "edit_glyph_metadata %s:\n%s" % (
        glyph.name,
        "\n".join("  %s" % line for line in applied),
    )


def handle_get_glyph_metadata(args, ctx, font):
    name = str(args.get("glyph") or "").strip()
    if not name:
        return "[error] 'glyph' is required."
    glyph = resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    payload = dump_glyph_metadata(glyph)
    master_key = args.get("master")
    if master_key is not None and str(master_key).strip():
        master = resolve_master(font, master_key)
        if master is None:
            return "[error] Master not found: %s" % master_key
        layer = glyph.layers[master.id]
        if layer is None:
            return "[error] Glyph %s has no layer for master %s." % (name, master.name)
        payload["spacing"] = dump_spacing_for_master(glyph, master, layer)
    return json.dumps(payload, indent=2)


def handle_edit_glyph_metadata(args, ctx, font):
    name = str(args.get("glyph") or "").strip()
    if not name:
        return "[error] 'glyph' is required."
    glyph = resolve_glyph(font, name)
    if glyph is None:
        return "[error] Glyph not found: %s" % name
    changes = args.get("changes")
    if changes is None:
        return "[error] 'changes' is required."
    master = None
    master_key = args.get("master")
    if master_key is not None and str(master_key).strip():
        master = resolve_master(font, master_key)
        if master is None:
            return "[error] Master not found: %s" % master_key
    return apply_glyph_metadata(glyph, changes, font, master=master)
