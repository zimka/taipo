# encoding: utf-8

from tools.render_registry import RenderRegistry

DEFAULT_RENDER_CONTRACT = {
    "canvas_w": 900,
    "canvas_h": 260,
    "margin_x": 24,
    "em_px": 160.0,
    "baseline_y": 56.0,
    "unknown_advance_upm": 250.0,
}

class ToolContext:
    """Plugin-level state passed to every tool call."""

    def __init__(
        self,
        font_provider,
        render_contract=None,
        render_registry=None,
        api_settings=None,
    ):
        self._font_provider = font_provider
        self.render_contract = dict(render_contract or DEFAULT_RENDER_CONTRACT)
        self.render_registry = (
            render_registry if render_registry is not None else RenderRegistry()
        )
        self.api_settings = api_settings if api_settings is not None else {}
        self.debug_info = False

    def debug_info_enabled(self):
        return bool(self.debug_info)

    @property
    def font(self):
        return self._font_provider()
