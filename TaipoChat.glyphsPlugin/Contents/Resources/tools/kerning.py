# encoding: utf-8
"""Kerning table read/write (v2): read_kerning_pairs, edit_kerning_pairs, find_kerning_rules."""

from __future__ import annotations

import json
from typing import Any, Literal

from tools.font_access import resolve_glyph, resolve_master
from tools.formatting import int_or_none

MAX_EDIT_CHANGES = 20
CLASS_PREFIX = "@"
_MMK_L_PREFIX = "@MMK_L_"
_MMK_R_PREFIX = "@MMK_R_"
ROOT_PARENT: dict[str, Any] = {"left": None, "right": None, "stored_value": 0}
# GSFont rejects setattr and weakref; cache by id(font) for the open document lifetime.
_TABLE_CACHE: dict[tuple[int, str], dict[tuple[str, str], float]] = {}
_ID_INDEX_CACHE: dict[int, dict[str, Any]] = {}


def _invalidate_kerning_cache(font, master_id: str | None = None) -> None:
    fid = id(font)
    if master_id is None:
        for key in list(_TABLE_CACHE):
            if key[0] == fid:
                del _TABLE_CACHE[key]
        _ID_INDEX_CACHE.pop(fid, None)
        return
    _TABLE_CACHE.pop((fid, str(master_id)), None)


def _mapping_get(mapping: Any, key: Any) -> Any:
    if mapping is None:
        return None
    if isinstance(mapping, dict):
        return mapping.get(key) or mapping.get(str(key))
    try:
        val = mapping.get(key)  # type: ignore[attr-defined]
        if val is not None:
            return val
        return mapping.get(str(key))  # type: ignore[attr-defined]
    except Exception:
        return None


def _mapping_items(mapping: Any) -> list[tuple[Any, Any]]:
    if mapping is None:
        return []
    if isinstance(mapping, dict):
        return list(mapping.items())
    try:
        return list(mapping.items())  # type: ignore[attr-defined]
    except Exception:
        return []


def _glyph_id_index(font) -> dict[str, Any]:
    fid = id(font)
    cached = _ID_INDEX_CACHE.get(fid)
    if cached is not None:
        return cached
    by_id: dict[str, Any] = {}
    for glyph in font.glyphs:
        gid = str(getattr(glyph, "id", "") or "").strip()
        if gid:
            by_id[gid] = glyph
    _ID_INDEX_CACHE[fid] = by_id
    return by_id


def _normalize_kerning_table_key(
    font, key: str, by_id: dict[str, Any] | None = None
) -> str:
    """Map Glyphs internal kerning keys (glyph UUIDs) to agent-facing names."""
    text = str(key).strip()
    if not text:
        return text
    if text.startswith(_MMK_L_PREFIX) or text.startswith(_MMK_R_PREFIX):
        return text
    if by_id is None:
        by_id = _glyph_id_index(font)
    if text in by_id:
        return str(by_id[text].name)
    if resolve_glyph(font, text) is not None:
        return text
    return text


def _build_kerning_table(font, master_id: str) -> dict[tuple[str, str], float]:
    try:
        raw = font.kerning
    except Exception:
        return {}
    level = _mapping_get(raw, master_id)
    if level is None:
        return {}
    by_id = _glyph_id_index(font)
    out: dict[tuple[str, str], float] = {}
    for left_key, right_map in _mapping_items(level):
        left_norm = _normalize_kerning_table_key(font, str(left_key), by_id)
        for right_key, value in _mapping_items(right_map):
            right_norm = _normalize_kerning_table_key(font, str(right_key), by_id)
            try:
                out[(left_norm, right_norm)] = float(value)
            except (TypeError, ValueError):
                continue
    return out


def _kerning_table(font, master_id: str) -> dict[tuple[str, str], float]:
    cache_key = (id(font), str(master_id))
    cached = _TABLE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    built = _build_kerning_table(font, master_id)
    _TABLE_CACHE[cache_key] = built
    return built


def _table_lookup(
    table: dict[tuple[str, str], float], left_key: str, right_key: str
) -> float | None:
    if (left_key, right_key) in table:
        return table[(left_key, right_key)]
    return None


def _table_get(
    font,
    master_id: str,
    left_key: str,
    right_key: str,
    *,
    table: dict[tuple[str, str], float] | None = None,
) -> float | None:
    if table is None:
        table = _kerning_table(font, master_id)
    return _table_lookup(table, left_key, right_key)


def _mmk_reject_message(operand: str) -> str:
    if operand.startswith(_MMK_L_PREFIX):
        group = operand[len(_MMK_L_PREFIX) :]
        return "use @%s not %s" % (group, operand)
    if operand.startswith(_MMK_R_PREFIX):
        group = operand[len(_MMK_R_PREFIX) :]
        return "use @%s not %s" % (group, operand)
    return "use @Group not %s" % operand


def _parse_side_ref(
    side_str: str,
    side: Literal["left", "right"],
    font,
    *,
    for_write: bool = False,
) -> tuple[str, str, str]:
    """Return (kind, display, api_key). Raises ValueError on invalid input."""
    text = str(side_str or "").strip()
    if not text:
        raise ValueError("empty %s name" % side)

    if text.startswith(CLASS_PREFIX):
        rest = text[1:].strip()
        if not rest:
            raise ValueError("empty class name after '@'")
        if rest.startswith("MMK_"):
            raise ValueError(_mmk_reject_message(text))
        api_key = (_MMK_L_PREFIX if side == "left" else _MMK_R_PREFIX) + rest
        return "class", CLASS_PREFIX + rest, api_key

    glyph = resolve_glyph(font, text)
    if glyph is None:
        raise ValueError("unknown glyph: %r" % text)
    return "glyph", str(glyph.name), str(glyph.name)


def _resolve_pair_keys(
    font, left_str: str, right_str: str, *, for_write: bool = False
) -> tuple[str, str, str, str, str, str]:
    left_kind, left_display, left_key = _parse_side_ref(
        left_str, "left", font, for_write=for_write
    )
    right_kind, right_display, right_key = _parse_side_ref(
        right_str, "right", font, for_write=for_write
    )
    return left_kind, left_display, left_key, right_kind, right_display, right_key


def _glyph_explicit_kerning_group(glyph, side: Literal["left", "right"]) -> str | None:
    attr = "leftKerningGroup" if side == "left" else "rightKerningGroup"
    text = str(getattr(glyph, attr, "") or "").strip()
    return text or None


def _glyph_ladder_kerning_group(glyph, side: Literal["left", "right"]) -> str | None:
    return _glyph_explicit_kerning_group(glyph, side)


def _display_class(group: str) -> str:
    return CLASS_PREFIX + group


def _slot_ref(left: str | None, right: str | None) -> dict[str, Any]:
    return {"left": left, "right": right}


def _parent_from_stored(left: str | None, right: str | None, value: float) -> dict[str, Any]:
    return {"left": left, "right": right, "stored_value": int(round(value))}


def _coarser_rungs(
    font,
    left_kind: str,
    left_display: str,
    right_kind: str,
    right_display: str,
) -> list[tuple[str | None, str | None, str, str]]:
    """Return coarser ladder rungs below the requested slot as (left_disp, right_disp, lk, rk)."""
    rungs: list[tuple[str | None, str | None, str, str]] = []

    if left_kind == "glyph" and right_kind == "glyph":
        left_g = resolve_glyph(font, left_display)
        right_g = resolve_glyph(font, right_display)
        if left_g is None or right_g is None:
            return rungs
        rg = _glyph_ladder_kerning_group(right_g, "left")
        if rg:
            rungs.append(
                (left_display, _display_class(rg), left_display, _MMK_R_PREFIX + rg)
            )
        lg = _glyph_ladder_kerning_group(left_g, "right")
        if lg:
            rungs.append(
                (_display_class(lg), right_display, _MMK_L_PREFIX + lg, right_display)
            )
        if lg and rg:
            rungs.append(
                (
                    _display_class(lg),
                    _display_class(rg),
                    _MMK_L_PREFIX + lg,
                    _MMK_R_PREFIX + rg,
                )
            )
        return rungs

    if left_kind == "glyph" and right_kind == "class":
        left_g = resolve_glyph(font, left_display)
        if left_g is None:
            return rungs
        lg = _glyph_ladder_kerning_group(left_g, "right")
        if lg:
            _, _, right_key = _parse_side_ref(right_display, "right", font, for_write=True)
            rungs.append(
                (
                    _display_class(lg),
                    right_display,
                    _MMK_L_PREFIX + lg,
                    right_key,
                )
            )
        return rungs

    if left_kind == "class" and right_kind == "glyph":
        right_g = resolve_glyph(font, right_display)
        if right_g is None:
            return rungs
        rg = _glyph_ladder_kerning_group(right_g, "left")
        if rg:
            _, _, left_key = _parse_side_ref(left_display, "left", font, for_write=True)
            rungs.append(
                (
                    left_display,
                    _display_class(rg),
                    left_key,
                    _MMK_R_PREFIX + rg,
                )
            )
        return rungs

    return rungs


def _find_parent(
    font,
    master_id: str,
    left_kind: str,
    left_display: str,
    right_kind: str,
    right_display: str,
    *,
    table: dict[tuple[str, str], float],
) -> dict[str, Any]:
    for left_disp, right_disp, lk, rk in _coarser_rungs(
        font, left_kind, left_display, right_kind, right_display
    ):
        val = _table_lookup(table, lk, rk)
        if val is not None:
            return _parent_from_stored(left_disp, right_disp, val)
    return dict(ROOT_PARENT)


def _cascade_expected_glyph_pair(
    font,
    master_id: str,
    left_glyph_name: str,
    right_glyph_name: str,
    *,
    table: dict[tuple[str, str], float],
) -> int:
    left_g = resolve_glyph(font, left_glyph_name)
    right_g = resolve_glyph(font, right_glyph_name)
    if left_g is None or right_g is None:
        return 0

    ladder: list[tuple[str, str]] = [(left_glyph_name, right_glyph_name)]
    rg = _glyph_ladder_kerning_group(right_g, "left")
    if rg:
        ladder.append((left_glyph_name, _MMK_R_PREFIX + rg))
    lg = _glyph_ladder_kerning_group(left_g, "right")
    if lg:
        ladder.append((_MMK_L_PREFIX + lg, right_glyph_name))
    if lg and rg:
        ladder.append((_MMK_L_PREFIX + lg, _MMK_R_PREFIX + rg))

    for lk, rk in ladder:
        val = _table_lookup(table, lk, rk)
        if val is not None:
            return int(round(val))
    return 0


def cascade_effective_for_glyph_pair(
    font,
    master_id: str,
    left_glyph_name: str,
    right_glyph_name: str,
    *,
    table: dict[tuple[str, str], float] | None = None,
) -> int:
    """Public cascade helper (shared with tests/mock.py)."""
    if table is None:
        table = _kerning_table(font, master_id)
    return _cascade_expected_glyph_pair(
        font, master_id, left_glyph_name, right_glyph_name, table=table
    )


def _glyphs_api_pair_value(font, master, left_name: str, right_name: str) -> int:
    try:
        val = font.kerningForPair(master.id, left_name, right_name)
    except Exception:
        val = 0
    if val is None:
        return 0
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return 0


def _warning_message(expected: int, actual: int) -> str:
    return (
        "EXPECTED EFFECTIVE_VALUE TO BE %d BASED ON KERNING TABLE BUT FOUND %d IN GLYPHS API"
        % (expected, actual)
    )


def _read_one_pair(
    font,
    master,
    left_str: str,
    right_str: str,
    *,
    table: dict[tuple[str, str], float],
) -> dict[str, Any]:
    left_kind, left_display, left_key, right_kind, right_display, right_key = (
        _resolve_pair_keys(font, left_str, right_str, for_write=True)
    )

    stored_raw = _table_lookup(table, left_key, right_key)
    stored_value = None if stored_raw is None else int(round(stored_raw))
    parent = _find_parent(
        font, master.id, left_kind, left_display, right_kind, right_display, table=table
    )

    result: dict[str, Any] = {
        "left": left_display,
        "right": right_display,
        "stored_value": stored_value,
        "parent": parent,
    }

    if left_kind == "glyph" and right_kind == "glyph":
        expected = _cascade_expected_glyph_pair(
            font, master.id, left_display, right_display, table=table
        )
        actual = _glyphs_api_pair_value(font, master, left_display, right_display)
        result["effective_value"] = actual
        if expected != actual:
            result["WARNING"] = _warning_message(expected, actual)
    else:
        if stored_value is not None:
            result["effective_value"] = stored_value
        else:
            result["effective_value"] = int(parent["stored_value"])

    return result


def handle_read_kerning_pairs(args, ctx, font):
    master = resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")

    pairs_raw = args.get("pairs")
    if not isinstance(pairs_raw, list) or not pairs_raw:
        return "[error] 'pairs' must be a non-empty list."

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    table = _kerning_table(font, master.id)

    for item in pairs_raw:
        if not isinstance(item, dict):
            continue
        left = str(item.get("left") or "").strip()
        right = str(item.get("right") or "").strip()
        if not left or not right:
            errors.append(
                {
                    "left": left or None,
                    "right": right or None,
                    "error": "empty left or right",
                }
            )
            continue
        try:
            results.append(_read_one_pair(font, master, left, right, table=table))
        except ValueError as exc:
            errors.append({"left": left, "right": right, "error": str(exc)})

    if not results and not errors:
        return "[error] No valid pairs to lookup."

    return json.dumps(
        {"master": master.name, "results": results, "errors": errors},
        indent=2,
    )


def _impact_label(left_kind: str, right_kind: str) -> str:
    if left_kind == "class" and right_kind == "class":
        return "class-class (highest)"
    if left_kind == "class" or right_kind == "class":
        return "class-glyph or glyph-class (high)"
    return "glyph-glyph (normal)"


def handle_edit_kerning_pairs(args, ctx, font):
    master = resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")

    changes_raw = args.get("changes")
    if not isinstance(changes_raw, list) or not changes_raw:
        return "[error] 'changes' must be a non-empty list."
    if len(changes_raw) > MAX_EDIT_CHANGES:
        return (
            "[error] At most %d changes per call (got %d)."
            % (MAX_EDIT_CHANGES, len(changes_raw))
        )

    parsed: list[dict[str, Any]] = []
    for i, item in enumerate(changes_raw):
        if not isinstance(item, dict):
            return "[error] changes[%d] must be an object." % i
        left = item.get("left")
        right = item.get("right")
        if left is None or right is None:
            return "[error] changes[%d] requires 'left' and 'right'." % i
        if "stored_value" not in item:
            return "[error] changes[%d] requires 'stored_value' (use null to remove)." % i
        try:
            left_kind, _, left_key, right_kind, _, right_key = _resolve_pair_keys(
                font, str(left), str(right), for_write=True
            )
        except ValueError as exc:
            return "[error] changes[%d]: %s" % (i, exc)
        raw_val = item.get("stored_value")
        remove = raw_val is None
        value = None
        if not remove:
            value = int_or_none(raw_val)
            if value is None:
                return "[error] changes[%d].stored_value must be an integer or null." % i
        parsed.append(
            {
                "left": str(left),
                "right": str(right),
                "left_kind": left_kind,
                "right_kind": right_kind,
                "left_key": left_key,
                "right_key": right_key,
                "remove": remove,
                "value": value,
            }
        )

    applied: list[str] = []
    undo_started = False
    table = _kerning_table(font, master.id)
    try:
        try:
            font.beginUndo()
            undo_started = True
        except Exception:
            undo_started = False

        for change in parsed:
            lk = change["left_key"]
            rk = change["right_key"]
            impact = _impact_label(change["left_kind"], change["right_kind"])
            old = _table_get(font, master.id, lk, rk, table=table)
            if change["remove"]:
                try:
                    font.removeKerningForPair(master.id, lk, rk)
                except Exception as exc:
                    if undo_started:
                        try:
                            font.endUndo()
                        except Exception:
                            pass
                    return "[error] %s" % exc
                applied.append(
                    "removed %s × %s (%s) keys %s × %s"
                    % (change["left"], change["right"], impact, lk, rk)
                )
            else:
                try:
                    font.setKerningForPair(master.id, lk, rk, change["value"])
                except Exception as exc:
                    if undo_started:
                        try:
                            font.endUndo()
                        except Exception:
                            pass
                    return "[error] %s" % exc
                old_label = "absent" if old is None else str(int(round(old)))
                applied.append(
                    "set %s × %s (%s): %s -> %d  [%s × %s]"
                    % (
                        change["left"],
                        change["right"],
                        impact,
                        old_label,
                        change["value"],
                        lk,
                        rk,
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
    _invalidate_kerning_cache(font, master.id)
    return "edit_kerning_pairs @%s:\n%s" % (
        master.name,
        "\n".join("  %s" % line for line in applied),
    )


def _key_to_display(key: str) -> tuple[str, str]:
    if key.startswith(_MMK_L_PREFIX):
        return CLASS_PREFIX + key[len(_MMK_L_PREFIX) :], "class"
    if key.startswith(_MMK_R_PREFIX):
        return CLASS_PREFIX + key[len(_MMK_R_PREFIX) :], "class"
    return key, "glyph"


def _format_group_api(label: str | None) -> str | None:
    text = str(label or "").strip()
    if not text:
        return None
    return CLASS_PREFIX + text


def _glyphs_in_group(font, group: str, side: Literal["left", "right"]) -> list[str]:
    names: list[str] = []
    for glyph in font.glyphs:
        if _glyph_explicit_kerning_group(glyph, side) == group:
            names.append(str(glyph.name))
    return sorted(names)


def _glyph_table_match_keys(glyph) -> tuple[set[str], set[str]]:
    keys: set[str] = {str(glyph.name)}
    gid = str(getattr(glyph, "id", "") or "").strip()
    if gid:
        keys.add(gid)
    return keys, keys


def _table_key_on_operand_side(
    font,
    table_key: str,
    operand_side: Literal["left", "right"],
    *,
    is_class_target: bool,
    class_group: str | None,
    match_keys: set[str],
) -> bool:
    if table_key in match_keys:
        return True
    if is_class_target and class_group:
        if operand_side == "left" and table_key == _MMK_L_PREFIX + class_group:
            return True
        if operand_side == "right" and table_key == _MMK_R_PREFIX + class_group:
            return True
        glyph = resolve_glyph(font, table_key)
        if glyph is not None:
            if _glyph_explicit_kerning_group(glyph, operand_side) == class_group:
                return True
            if str(glyph.name) == class_group:
                return True
    return False


def _neighbor_bucket(side_filter: str, want: str) -> bool:
    return side_filter == "all" or side_filter == want


def handle_find_kerning_rules(args, ctx, font):
    master = resolve_master(font, args.get("master"))
    if master is None:
        return "[error] Master not found: %s" % args.get("master")

    target_raw = str(args.get("target") or "").strip()
    if not target_raw:
        return "[error] 'target' is required."

    side_filter = str(args.get("side") or "all").strip().lower()
    if side_filter not in ("left", "right", "all"):
        return "[error] side must be 'left', 'right', or 'all'."

    neighbor_kind = str(args.get("neighbor_kind") or "all").strip().lower()
    if neighbor_kind not in ("glyph", "class", "all"):
        return "[error] neighbor_kind must be 'glyph', 'class', or 'all'."

    left_neighbors: dict[str, set[str]] = {"class": set(), "glyph": set()}
    right_neighbors: dict[str, set[str]] = {"class": set(), "glyph": set()}

    is_class_target = target_raw.startswith(CLASS_PREFIX)
    class_group: str | None = None
    if is_class_target:
        group = target_raw[1:].strip()
        if not group:
            return "[error] empty class name after '@'"
        if group.startswith("MMK_"):
            return "[error] " + _mmk_reject_message(target_raw)
        class_group = group
        target_display = CLASS_PREFIX + group
        match_left_keys = {_MMK_L_PREFIX + group}
        match_right_keys = {_MMK_R_PREFIX + group}
    else:
        glyph = resolve_glyph(font, target_raw)
        if glyph is None:
            return "[error] Glyph not found: %s" % target_raw
        target_display = glyph.name
        match_left_keys, match_right_keys = _glyph_table_match_keys(glyph)

    table = _kerning_table(font, master.id)
    for left_key, right_key in table:
        on_left = _table_key_on_operand_side(
            font,
            left_key,
            "left",
            is_class_target=is_class_target,
            class_group=class_group,
            match_keys=match_left_keys,
        )
        on_right = _table_key_on_operand_side(
            font,
            right_key,
            "right",
            is_class_target=is_class_target,
            class_group=class_group,
            match_keys=match_right_keys,
        )

        if not on_left and not on_right:
            continue

        left_disp, left_kind = _key_to_display(left_key)
        right_disp, right_kind = _key_to_display(right_key)

        if on_left:
            nb_disp, nb_kind = right_disp, right_kind
            bucket = right_neighbors
        else:
            nb_disp, nb_kind = left_disp, left_kind
            bucket = left_neighbors

        if nb_kind == "class":
            bucket["class"].add(nb_disp)
        else:
            bucket["glyph"].add(nb_disp)

    def _pack_bucket(data: dict[str, set[str]]) -> dict[str, list[str]] | None:
        out: dict[str, list[str]] = {}
        if neighbor_kind in ("class", "all") and data["class"]:
            out["class"] = sorted(data["class"])
        if neighbor_kind in ("glyph", "all") and data["glyph"]:
            out["glyph"] = sorted(data["glyph"])
        return out or None

    payload: dict[str, Any] = {"master": master.name, "target": target_display}

    if is_class_target and class_group:
        payload["right_kerning_group"] = _glyphs_in_group(font, class_group, "right")
        payload["left_kerning_group"] = _glyphs_in_group(font, class_group, "left")
    else:
        glyph = resolve_glyph(font, target_display)
        if glyph is not None:
            payload["right_kerning_group"] = _format_group_api(
                _glyph_explicit_kerning_group(glyph, "right")
            )
            payload["left_kerning_group"] = _format_group_api(
                _glyph_explicit_kerning_group(glyph, "left")
            )

    if _neighbor_bucket(side_filter, "left"):
        packed = _pack_bucket(left_neighbors)
        if packed:
            payload["left"] = packed
    if _neighbor_bucket(side_filter, "right"):
        packed = _pack_bucket(right_neighbors)
        if packed:
            payload["right"] = packed

    return json.dumps(payload, indent=2)
