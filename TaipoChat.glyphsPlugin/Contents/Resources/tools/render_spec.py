# encoding: utf-8
"""Frozen specimen camera. Implementation detail — not an agent-facing type."""

from __future__ import annotations

from dataclasses import dataclass

from tools.font_access import resolve_master
from tools.render_coretext import resolve_specimen_lines


@dataclass(frozen=True)
class RenderSpec:
    """Resolved specimen args plus a measured layout origin.

    Built once from agent args against the font at that moment, then replayed
    without re-measuring pad or baseline.
    """

    lines: tuple[str, ...]
    master_id: str
    em_px: float
    canvas_w: int
    canvas_h: int
    pad: float
    baseline_y0: float
    line_height: float
    origin_locked: bool = False

    def with_canvas(self, canvas_w: int, canvas_h: int) -> "RenderSpec":
        """Copy with a larger bitmap; origin fields stay the same."""
        w = max(int(canvas_w), self.canvas_w)
        h = max(int(canvas_h), self.canvas_h)
        if w == self.canvas_w and h == self.canvas_h:
            return self
        return RenderSpec(
            lines=self.lines,
            master_id=self.master_id,
            em_px=self.em_px,
            canvas_w=w,
            canvas_h=h,
            pad=self.pad,
            baseline_y0=self.baseline_y0,
            line_height=self.line_height,
            origin_locked=self.origin_locked,
        )

    def with_locked_camera(
        self, canvas_w, canvas_h, pad, baseline_y0, line_height
    ) -> "RenderSpec":
        """Freeze the camera actually used by the first raster."""
        return RenderSpec(
            lines=self.lines,
            master_id=self.master_id,
            em_px=self.em_px,
            canvas_w=int(canvas_w),
            canvas_h=int(canvas_h),
            pad=float(pad),
            baseline_y0=float(baseline_y0),
            line_height=float(line_height),
            origin_locked=True,
        )

    @classmethod
    def from_agent_args(cls, args, font, ctx):
        """Resolve tool args and measure layout against the current font.

        Returns ``(RenderSpec, None)`` or ``(None, error_string)``.
        """
        lines, err = resolve_specimen_lines(args)
        if err:
            return None, err
        master = resolve_master(font, args.get("master"))
        if master is None:
            return None, "[error] Master not found: %s" % args.get("master")
        try:
            size = int(args.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        em_px = float(size) if size > 0 else float(ctx.render_contract.get("em_px", 160.0))

        from tools.render import measure_geometry_layout

        layout = measure_geometry_layout(font, master, lines, em_px)
        return (
            cls(
                lines=tuple(lines),
                master_id=str(master.id),
                em_px=em_px,
                canvas_w=int(layout["canvas_w"]),
                canvas_h=int(layout["canvas_h"]),
                pad=float(layout["pad"]),
                baseline_y0=float(layout["baseline_y0"]),
                line_height=float(layout["line_height"]),
            ),
            None,
        )


def font_identity(font) -> str:
    """Stable-enough label for the open font (family + path)."""
    family = str(getattr(font, "familyName", None) or "")
    path = str(getattr(font, "filepath", None) or "")
    if not path:
        parent = getattr(font, "parent", None)
        if parent is not None:
            path = str(getattr(parent, "filepath", None) or "")
    return "%s|%s" % (family, path)
