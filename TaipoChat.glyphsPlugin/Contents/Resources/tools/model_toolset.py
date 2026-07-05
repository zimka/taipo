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
from typing import Any, get_args, get_origin

from tools.context import ToolContext
from tools.edit import handle_move_nodes, handle_set_width
from tools.judge import handle_numeric_judge
from tools.read import handle_get_glyph, handle_list_glyphs, handle_list_masters
from tools.render import handle_render_glyph, handle_render_specimen
from tools.snapshot import handle_render_diff, handle_reset_snapshot, handle_save_snapshot

class ModelToolSpec:
    """Introspection for @model_tool methods: docstring + signature → tool schema."""

    _MARKER = "_is_model_tool"
    _NAME_ATTR = "_model_tool_name"
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
        hints = typing.get_type_hints(func)
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

        raise TypeError("unsupported type annotation for model tool schema: %r" % (annotation,))


def model_tool(func=None, *, name=None):
    """Mark a method as exposed to the LLM as a tool."""

    def decorate(fn):
        setattr(fn, ModelToolSpec._MARKER, True)
        setattr(fn, ModelToolSpec._NAME_ATTR, name)
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
        font = self._ctx.font
        if font is None:
            return "[error] No font is open in Glyphs."
        for attr, member in type(self).__dict__.items():
            if callable(member) and ModelToolSpec.is_marked(member):
                if ModelToolSpec.tool_name(member) == name:
                    return getattr(self, attr)(**(args or {}))
        return "[error] Unknown tool: %s" % name

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

    @model_tool
    def render_specimen(self, text: str, master: str | None = None, size: int | None = None):
        """
        Render a short text using the CURRENT state of the open font and return a PNG
        image. Use the SAME text and master before and after a fix so renders are
        comparable by eye.

        Args:
            text: Specimen text (short; 1-20 characters is typical).
            master: Master name or id. Defaults to the first master.
            size: Em size in pixels. Default 160.
        """
        return handle_render_specimen(
            {"text": text, "master": master, "size": size}, self._ctx, self._ctx.font
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
        Call save_snapshot FIRST before any move_nodes so the user can undo.
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
        Call save_snapshot FIRST so the user can undo.

        Args:
            glyph: Glyph name.
            master: Master name or id.
            width: New advance width in font units. Must be non-negative.
        """
        return handle_set_width(
            {"glyph": glyph, "master": master, "width": width}, self._ctx, self._ctx.font
        )

    @model_tool
    def save_snapshot(self, glyph_names: list[str]) -> str:
        """
        Capture the current geometry (node positions, anchors, widths across all masters)
        of the listed glyphs. One slot only — a second call overwrites. You MUST call
        this BEFORE the first move_nodes in a fix so the user (or you) can revert
        via reset_snapshot and so render_diff can render the overlay comparison.

        Args:
            glyph_names: Glyph names you plan to edit. Must be non-empty.
        """
        return handle_save_snapshot({"glyph_names": glyph_names}, self._ctx, self._ctx.font)

    @model_tool
    def reset_snapshot(self) -> str:
        """
        Restore the geometry saved by save_snapshot. Use when your edits went the wrong
        way and you want to revise the plan, or to undo an exploratory attempt. The
        snapshot itself is kept (a reset can be applied multiple times).
        """
        return handle_reset_snapshot({}, self._ctx, self._ctx.font)

    @model_tool
    def render_diff(self, text: str, master: str | None = None, size: int | None = None):
        """
        Render a red/green overlay comparing the snapshot geometry (red) against the
        current live font (green). Yellow pixels are overlap.
        Requires an active snapshot — call save_snapshot first.
        Call this after move_nodes to show the user the visual before/after difference.

        Args:
            text: Specimen text (same as was used for render_specimen).
            master: Master name or id. Defaults to the first master.
            size: Em size in pixels. Default 160.
        """
        return handle_render_diff(
            {"text": text, "master": master, "size": size}, self._ctx, self._ctx.font
        )

