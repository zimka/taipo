# encoding: utf-8
"""
Model-facing tool interface for Taipo.

``ModelToolset`` exposes font tools as typed methods. Methods decorated with
``@model_tool`` are registered for schema export and ``execute()`` dispatch.
"""

from __future__ import annotations

import inspect
import re
import typing
from typing import Annotated, Any, get_args, get_origin

from tools.context import ToolContext
from tools.edit import handle_move_nodes, handle_set_width
from tools.kerning import (
    handle_edit_kerning_pairs,
    handle_find_kerning_rules,
    handle_read_kerning_pairs,
)
from session_log import brief_tool_args, get_logger
from tools.glyph_metadata import (
    MetadataPatch,
    handle_get_glyph_metadata,
    handle_edit_glyph_metadata,
    metadata_schema_doc,
)
from tools.judge import handle_numeric_judge
from tools.read import handle_get_glyph, handle_list_glyphs, handle_list_masters
from tools.render import (
    handle_render_glyph,
    handle_render_specimen,
    handle_render_specimen_diff,
)

_tools_logger = get_logger("tools")


class ModelToolSpec:
    """Introspection for @model_tool methods: docstring + signature → tool schema."""

    _MARKER = "_is_model_tool"
    _NAME_ATTR = "_model_tool_name"
    _METADATA_RETURN_ATTR = "_model_tool_metadata_return"
    _SECTION_HEADERS = frozenset(
        {"Args", "Arguments", "Returns", "Raises", "Note", "Notes", "Examples", "Yields", "Attributes"}
    )

    @classmethod
    def is_marked(cls, func) -> bool:
        return bool(getattr(func, cls._MARKER, False))

    @classmethod
    def tool_name(cls, func) -> str:
        override = getattr(func, cls._NAME_ATTR, None)
        return override if override else func.__name__

    @classmethod
    def parse_docstring(cls, doc: str | None) -> tuple[str, dict[str, str]]:
        """
        Parse a Google-style docstring.

        Returns ``(summary, args_dict)`` where *summary* is the text before the
        first recognised section header and *args_dict* maps parameter names to
        descriptions from the ``Args:`` block.
        """
        if not doc:
            return "", {}

        text = inspect.cleandoc(doc)
        lines = text.split("\n")
        summary_lines: list[str] = []
        args_lines: list[str] = []
        in_args = False

        for line in lines:
            stripped = line.strip()
            header = stripped.rstrip(":")
            if header in cls._SECTION_HEADERS and not line.startswith((" ", "\t")):
                if header in ("Args", "Arguments"):
                    in_args = True
                elif in_args:
                    break
                else:
                    break
                continue
            if in_args:
                args_lines.append(line)
            else:
                summary_lines.append(line)

        summary = cls._normalize_summary("\n".join(summary_lines))
        return summary, cls._parse_args_block("\n".join(args_lines))

    @classmethod
    def input_schema_from_signature(
        cls, func, param_descriptions: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Build a JSON Schema object from a function's typed signature."""
        param_descriptions = param_descriptions or {}
        hints = typing.get_type_hints(func, include_extras=True)
        sig = inspect.signature(func)
        properties: dict[str, Any] = {}
        required: list[str] = []

        for name, param in sig.parameters.items():
            if name == "self":
                continue
            if param.annotation is inspect.Parameter.empty and name not in hints:
                raise TypeError("%s parameter %r must have a type hint" % (func.__qualname__, name))

            annotation = hints.get(name, param.annotation)
            _, is_optional = cls._unwrap_optional(annotation)
            prop = cls._json_type_for_annotation(annotation)
            if name in param_descriptions:
                prop["description"] = param_descriptions[name]
            properties[name] = prop

            has_default = param.default is not inspect.Parameter.empty
            if not has_default and not is_optional:
                required.append(name)

        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    @classmethod
    def from_method(cls, method) -> dict[str, Any]:
        """Build an Anthropic-style tool schema dict from a @model_tool method."""
        if not cls.is_marked(method):
            raise TypeError("%r is not a model tool" % (method,))
        summary, args_dict = cls.parse_docstring(method.__doc__)
        if getattr(method, cls._METADATA_RETURN_ATTR, False):
            summary = (
                summary
                + "\n\nReturn value JSON schema:\n"
                + metadata_schema_doc(mode="full")
            )
        return {
            "name": cls.tool_name(method),
            "description": summary,
            "input_schema": cls.input_schema_from_signature(method, args_dict),
        }

    @staticmethod
    def _normalize_summary(text: str) -> str:
        """Collapse soft-wrapped docstring lines to spaces; keep blank lines and indented blocks."""
        text = text.strip()
        if not text:
            return ""

        lines = text.split("\n")
        blocks: list[str] = []
        current: list[str] = []

        def flush_current():
            if current:
                blocks.append(" ".join(current))
                current.clear()

        for line in lines:
            if not line.strip():
                flush_current()
                blocks.append("")
                continue
            if line.startswith((" ", "\t")):
                flush_current()
                blocks.append(line.rstrip())
            else:
                current.append(line.strip())

        flush_current()
        return "\n".join(blocks).strip()

    @staticmethod
    def _parse_args_block(block: str) -> dict[str, str]:
        args_dict: dict[str, str] = {}
        current_name: str | None = None
        current_parts: list[str] = []

        for line in block.split("\n"):
            if not line.strip():
                continue
            match = re.match(r"^\s*(\w+)\s*:\s*(.*)$", line)
            if match:
                if current_name is not None:
                    args_dict[current_name] = " ".join(current_parts).strip()
                current_name = match.group(1)
                rest = match.group(2).strip()
                current_parts = [rest] if rest else []
            elif current_name is not None and (line.startswith(" ") or line.startswith("\t")):
                current_parts.append(line.strip())

        if current_name is not None:
            args_dict[current_name] = " ".join(current_parts).strip()
        return args_dict

    @staticmethod
    def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
        origin = get_origin(annotation)
        union_type = getattr(__import__("types"), "UnionType", None)
        if origin is typing.Union or origin is union_type:
            args = get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1 and len(args) == 2:
                return non_none[0], True
        return annotation, False

    @staticmethod
    def _json_type_for_annotation(annotation: Any) -> dict[str, Any]:
        if annotation is inspect.Parameter.empty:
            raise TypeError("model tool parameters must have type annotations")

        origin = get_origin(annotation)
        if origin is Annotated:
            args = get_args(annotation)
            if args:
                for meta in args[1:]:
                    meta_schema = ModelToolSpec._metadata_schema_from_marker(meta)
                    if meta_schema is not None:
                        return meta_schema
                return ModelToolSpec._json_type_for_annotation(args[0])

        annotation, _optional = ModelToolSpec._unwrap_optional(annotation)
        origin = get_origin(annotation)

        if origin is list:
            item_args = get_args(annotation)
            item_type = item_args[0] if item_args else Any
            return {"type": "array", "items": ModelToolSpec._json_type_for_annotation(item_type)}

        if annotation is str:
            return {"type": "string"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return {"type": "number"}
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is dict:
            return {"type": "object"}

        origin_dict = get_origin(annotation)
        if origin_dict is dict:
            return {"type": "object"}

        raise TypeError("unsupported type annotation for model tool schema: %r" % (annotation,))

    @staticmethod
    def _metadata_schema_from_marker(meta: Any) -> dict[str, Any] | None:
        try:
            from tools.glyph_metadata import MetadataSchema, metadata_json_schema
        except ImportError:
            return None
        if isinstance(meta, MetadataSchema):
            return metadata_json_schema(mode=meta.mode)
        return None


def model_tool(func=None, *, name=None, metadata_return=False):
    """Mark a method as exposed to the LLM as a tool."""

    def decorate(fn):
        setattr(fn, ModelToolSpec._MARKER, True)
        setattr(fn, ModelToolSpec._NAME_ATTR, name)
        setattr(fn, ModelToolSpec._METADATA_RETURN_ATTR, metadata_return)
        return fn

    if func is not None:
        return decorate(func)
    return decorate


class ModelToolset:
    """Font tools exposed as typed methods for the agent."""

    def __init__(self, ctx: ToolContext):
        self._ctx = ctx

    @property
    def ctx(self) -> ToolContext:
        return self._ctx

    @classmethod
    def schemas(cls) -> list[dict[str, Any]]:
        """Return tool schemas in class-body definition order."""
        schemas: list[dict[str, Any]] = []
        for _attr, member in cls.__dict__.items():
            if callable(member) and ModelToolSpec.is_marked(member):
                schemas.append(ModelToolSpec.from_method(member))
        return schemas

    def execute(self, name: str, args: dict | None = None):
        """Dispatch a tool call. Returns content accepted by ``normalize_tool_result_content``."""
        _tools_logger.info("execute %s args=%s", name, brief_tool_args(args or {}))
        font = self._ctx.font
        if font is None:
            result = "[error] No font is open in Glyphs."
            _tools_logger.warning("Tool %s returned error: %s", name, result)
            return result
        for attr, member in type(self).__dict__.items():
            if callable(member) and ModelToolSpec.is_marked(member):
                if ModelToolSpec.tool_name(member) == name:
                    result = getattr(self, attr)(**(args or {}))
                    if isinstance(result, str) and (
                        result.startswith("[error]")
                        or result.startswith("[tool error]")
                    ):
                        _tools_logger.warning(
                            "Tool %s returned error: %s", name, result[:500]
                        )
                    return result
        result = "[error] Unknown tool: %s" % name
        _tools_logger.warning("Tool %s returned error: %s", name, result)
        return result

    @model_tool
    def list_masters(self) -> str:
        """
        List all masters (weight/width/custom axes) of the currently open font.
        Returns master name, id and axis values.
        """
        return handle_list_masters({}, self._ctx, self._ctx.font)

    @model_tool
    def list_glyphs(self, filter: str | None = None, limit: int = 200) -> str:
        """
        List glyph names in the current font, optionally filtered.

        Filter modes (all case-insensitive):
          By name substring:  filter='cy'     → Dje-cy, Zhe-cy, ...
          By unicode hex:     filter='0402'   → glyph at U+0402
          By character:       filter='Ђ'      → glyph at U+0402
          No filter:          returns all glyphs up to limit.

        Args:
            filter: Optional. Name substring ('cy'), unicode hex ('0402'), or a literal character ('Ђ'). All modes are case-insensitive.
            limit: Max entries to return. Default 200.
        """
        return handle_list_glyphs({"filter": filter, "limit": limit}, self._ctx, self._ctx.font)

    @model_tool
    def get_glyph(self, name: str, master: str | None = None) -> str:
        """
        Return paths, nodes, anchors, components and metrics of a single glyph at a
        specific master, as structured text. Use this to reason about geometry.

        Node conventions: offcurve=N means this handle controls the curve node at index N.
        curve=[A,B] means the curve's two Bézier handles are at nodes A and B.
        Handles always immediately precede their curve node in path order (wrapping around
        for closed paths). smooth on a node means its tangent is continuous
        (handles on both sides are collinear; moving one adjusts the other automatically).

        Args:
            name: Glyph name (e.g. 'Dje-cy') or a single character.
            master: Master name or id. Defaults to the first master.
        """
        return handle_get_glyph({"name": name, "master": master}, self._ctx, self._ctx.font)

    @model_tool(metadata_return=True)
    def get_glyph_metadata(self, glyph: str, master: str | None = None) -> str:
        """
        Return font-wide glyph metadata as a JSON object.

        Fields include unicode, export, note, classification (category, subCategory,
        script, case, direction), kerning groups (leftKerningGroup, rightKerningGroup),
        and metrics keys (leftMetricsKey, rightMetricsKey, widthMetricsKey). When
        master is provided, also includes spacing (lsb, rsb, width) for that master.
        Use before edit_glyph_metadata to inspect current values and valid field names.
        Unicode values use uppercase hex without a U+ prefix; null means unencoded.
        Classification fields are null when inherited from Glyphs auto-classification.

        Args:
            glyph: Glyph name (e.g. '.notdef', 'Dje-cy') or a single character.
            master: Optional master name or id. When set, per-master lsb/rsb/width are included.
        """
        return handle_get_glyph_metadata(
            {"glyph": glyph, "master": master}, self._ctx, self._ctx.font
        )

    @model_tool
    def edit_glyph_metadata(
        self,
        glyph: str,
        changes: MetadataPatch,
        master: str | None = None,
    ) -> str:
        """
        Apply a partial metadata update to a glyph. Requires user approval (same as move_nodes).

        Only keys present in changes are applied. Use null on nullable fields to clear them
        (e.g. clear unicode on .notdef, clear kerning groups, revert classification to
        auto-inheritance). lsb, rsb, and width require master.

        Args:
            glyph: Glyph name.
            changes: JSON object with fields to change (see get_glyph_metadata schema).
            master: Master name or id. Required when changes include lsb, rsb, or width.
        """
        return handle_edit_glyph_metadata(
            {"glyph": glyph, "changes": changes, "master": master},
            self._ctx,
            self._ctx.font,
        )

    @model_tool(metadata_return=True)
    def read_kerning_pairs(
        self,
        master: str,
        pairs: list[dict[str, str]],
    ) -> str:
        """
        Read kerning slots: stored_value, effective_value, and parent for each pair.

        Operands use bare glyph names or @Group for kerning classes (e.g. @A). Never pass
        @MMK_L_* / @MMK_R_* — those are rejected.

        stored_value: null means no record at this exact slot; 0 means a stored zero (not
        missing). Read at write keys (glyph names for glyph×glyph, MMK keys for classes).

        effective_value: for glyph×glyph pairs, layout truth from Glyphs kerningForPair;
        otherwise stored_value if set, else parent.stored_value.

        parent: always present — nearest coarser stored slot, or root {null,null,0}. Use to
        spot blocking zeros (stored_value 0 shadowing parent -100).

        WARNING (glyph×glyph only): present when table cascade expected value differs from
        kerningForPair. Trust effective_value for spacing; investigate WARNING before editing.

        Always call read_kerning_pairs on the same {left, right} before edit_kerning_pairs.
        Pair-operand left/right differs from glyph leftKerningGroup/rightKerningGroup naming
        (see find_kerning_rules).

        Args:
            master: Master name or id.
            pairs: Non-empty list of {left, right} slot specs.
        """
        return handle_read_kerning_pairs(
            {"master": master, "pairs": pairs},
            self._ctx,
            self._ctx.font,
        )

    @model_tool
    def edit_kerning_pairs(
        self,
        master: str,
        changes: list[dict[str, Any]],
    ) -> str:
        """
        Write kerning slots via stored_value (null removes the record). Requires approval.

        Operands identical to read_kerning_pairs (@Group or glyph name). Always
        read_kerning_pairs on the same {left, right} before editing — do not infer slots
        from find_kerning_rules or get_glyph_metadata alone.

        Impact levels (disclose in approval plan):
        - glyph × glyph: normal
        - class × glyph or glyph × class: high — name class and affected glyphs
        - class × class: highest — name both groups and estimated pair count

        Args:
            master: Master name or id.
            changes: List of {left, right, stored_value} objects (stored_value null = remove).
        """
        return handle_edit_kerning_pairs(
            {"master": master, "changes": changes},
            self._ctx,
            self._ctx.font,
        )

    @model_tool(metadata_return=True)
    def find_kerning_rules(
        self,
        master: str,
        target: str,
        side: str = "all",
        neighbor_kind: str = "all",
    ) -> str:
        """
        Discover direct kerning table neighbours for a glyph or @Group (no values).

        Lists stored edges touching target only — no transitivity through class membership.
        After find, call read_kerning_pairs on specific {left, right} slots for values;
        call edit_kerning_pairs only after read_kerning_pairs on that slot.

        Naming nuance: left/right neighbour buckets are pair-operand positions, not the same
        as glyph leftKerningGroup/rightKerningGroup (shape sides). A glyph's rightKerningGroup
        (@T) applies when it is the left operand. Do not infer edit operands from bucket names.

        Args:
            master: Master name or id.
            target: Glyph name or @Group (e.g. @T).
            side: Filter buckets: left, right, or all (default all).
            neighbor_kind: Filter lists: glyph, class, or all (default all).
        """
        return handle_find_kerning_rules(
            {
                "master": master,
                "target": target,
                "side": side,
                "neighbor_kind": neighbor_kind,
            },
            self._ctx,
            self._ctx.font,
        )

    @model_tool
    def render_specimen(
        self,
        text: str | None = None,
        lines: list[str] | None = None,
        master: str | None = None,
        size: int | None = None,
    ):
        """
        Render a short text using the CURRENT state of the open font and return a PNG
        image. Picks the best available render tier automatically:

        Tier 1 (coretext_full): full OTF export + CoreText — kerning and all OpenType features.
        Tier 2 (coretext_no_features): export with OT features stripped — outlines, spacing,
          pair kerning (usually); no ligatures, ccmp, calt, stylistic sets, etc.
        Tier 3 (geometry): live master outlines, no export — outlines and sidebearings only;
          no kerning or OpenType features.

        The tool result header starts with render_specimen_id=N, then includes
        render_tier=..., render_tier_reliable_for, and render_tier_not_reliable_for.
        Read the tier fields before trusting the image for kerning, ligatures, or
        composed accents.

        Note render_specimen_id from the result. Call this BEFORE mutations if you
        will later call render_specimen_diff with that id. Keep the same specimen
        (text/lines, master, size) for the pair you intend to compare.

        Args:
            text: Single-line or multiline specimen (use \\n between rows). Prefer lines for multiple rows.
            lines: Multiline specimen as a list of strings, one row per entry.
            master: Master name or id. Defaults to the first master.
            size: Em size in pixels. Default 160.
        """
        return handle_render_specimen(
            {"text": text, "lines": lines, "master": master, "size": size},
            self._ctx,
            self._ctx.font,
        )

    @model_tool
    def render_glyph(self, name: str, master: str | None = None, size: int | None = None):
        """
        Render a single glyph at large size with every node annotated by index number.
        Each path has a distinct color (7-color palette). Node shape encodes type:
        filled circle=line, filled circle with white halo=curve, hollow square=offcurve.
        Direct paths labeled path[N]; component nodes at 70% opacity labeled (BaseName)path[N].
        Use this together with get_glyph to map node indices to their visual positions
        before writing numeric_judge code.

        Args:
            name: Glyph name (e.g. 'Dje-cy') or a single character.
            master: Master name or id. Defaults to the first master.
            size: Em size in pixels. Default 400.
        """
        return handle_render_glyph(
            {"name": name, "master": master, "size": size}, self._ctx, self._ctx.font
        )

    @model_tool
    def numeric_judge(
        self,
        glyphs: list[str],
        code: str,
        master: str | None = None,
    ) -> str:
        """
        Run a Python snippet in a read-only geometry sandbox to measure distances,
        areas, angles, or ratios from node coordinates. The primary tool for confirming
        issues and validating fixes. Use print() for output; the captured stdout is
        returned. Runtime errors are returned as error messages.

        Sandbox bindings:
          g[glyph_name][path_idx][node_idx] → {x, y, type, smooth, component}
          dist(a, b)                    — Euclidean distance between two node dicts
          seg_len(path, i, j)           — distance between nodes i and j in a path
          bbox(path)                    — {x0, y0, x1, y1} of on-curve nodes
          area(path)                    — shoelace area (on-curve nodes only)
          angle(a, b)                   — bearing in degrees from a to b, range (-180, 180]
          perpendicular_distance(p,a,b) — distance from node p to the line through a–b
          projection(p, a, b)           — {x,y} foot of perpendicular from p onto line a–b
          lerp(a, b, t)                 — {x,y} linear interpolation (t=0→a, t=1→b)
          reflect(node, axis_x)         — {x,y} mirror of node about the vertical x=axis_x
          tangent_at(path, node_idx)    — (dx,dy) unit tangent at a node; None for offcurve
          transform_point(node,m11,m12,m21,m22,tx,ty) — {x,y} affine transform
          math                          — full math module

        No imports. No file or network access.

        Args:
            glyphs: Glyph names to load into the sandbox.
            master: Master name or id. Defaults to the first master.
            code: Python snippet. Use print() to output results. Max 4000 chars.
        """
        return handle_numeric_judge(
            {"glyphs": glyphs, "master": master, "code": code}, self._ctx, self._ctx.font
        )

    @model_tool
    def move_nodes(
        self,
        glyph: str,
        master: str,
        path: int,
        nodes: list[int],
        dx: int,
        dy: int,
    ) -> str:
        """
        Move specific nodes in a path of a glyph by an offset.
        Addresses nodes by path index and node index (from get_glyph output).
        Multiple nodes in the same path can be shifted in one call.
        For nodes in different paths or different glyphs, use parallel tool calls.
        Use set_width when the advance width also needs to change.

        Args:
            glyph: Glyph name.
            master: Master name or id.
            path: Path index (0-based) from get_glyph output.
            nodes: Node indices within the path (0-based). Must be non-empty.
            dx: X offset in font units.
            dy: Y offset in font units.
        """
        return handle_move_nodes(
            {
                "glyph": glyph,
                "master": master,
                "path": path,
                "nodes": nodes,
                "dx": dx,
                "dy": dy,
            },
            self._ctx,
            self._ctx.font,
        )

    @model_tool
    def set_width(self, glyph: str, master: str, width: int) -> str:
        """
        Set the advance width (spacing metric) of a glyph in one master.
        The advance width is separate from the outline — moving nodes does not change it.
        Use this together with move_nodes when widening or narrowing a glyph.

        Args:
            glyph: Glyph name.
            master: Master name or id.
            width: New advance width in font units. Must be non-negative.
        """
        return handle_set_width(
            {"glyph": glyph, "master": master, "width": width}, self._ctx, self._ctx.font
        )

    @model_tool
    def render_specimen_diff(self, reference_render_specimen_id: int):
        """
        Render a red/green overlay comparing an earlier render_specimen (red) against
        the current live font (green). Yellow pixels are overlap.
        The id must come from an earlier render_specimen in this session. Specimen
        text, master, and size are taken from that stored render — do not pass them.

        If the current render tier or open font differs from the reference, the overlay
        is skipped and a text explanation is returned. Call render_specimen for a
        current image in that case; do not treat a skipped result as a visual diff.

        Args:
            reference_render_specimen_id: render_specimen_id from an earlier
                render_specimen result in this session.
        """
        return handle_render_specimen_diff(
            {"reference_render_specimen_id": reference_render_specimen_id},
            self._ctx,
            self._ctx.font,
        )

