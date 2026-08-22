# encoding: utf-8
"""Session map of render_specimen ids to stored rasters and cameras."""

from __future__ import annotations

from dataclasses import dataclass

from tools.render_coretext import RenderTier
from tools.render_spec import RenderSpec

REGISTRY_CAP = 64


@dataclass
class RenderSpecimenRecord:
    spec: RenderSpec
    png_bytes: bytes
    tier: RenderTier
    font_identity: str


class RenderRegistry:
    """1-based incrementing ids. Evicts oldest when over ``REGISTRY_CAP``."""

    def __init__(self):
        self._next_id = 1
        self._items: dict[int, RenderSpecimenRecord] = {}
        self._order: list[int] = []

    def put(
        self,
        spec: RenderSpec,
        png_bytes: bytes,
        tier: RenderTier,
        font_identity: str,
    ) -> int:
        rid = self._next_id
        self._next_id += 1
        self._items[rid] = RenderSpecimenRecord(
            spec=spec,
            png_bytes=png_bytes,
            tier=tier,
            font_identity=font_identity,
        )
        self._order.append(rid)
        while len(self._order) > REGISTRY_CAP:
            old = self._order.pop(0)
            self._items.pop(old, None)
        return rid

    def get(self, specimen_id) -> RenderSpecimenRecord | None:
        try:
            rid = int(specimen_id)
        except (TypeError, ValueError):
            return None
        return self._items.get(rid)

    def valid_ids(self) -> list[int]:
        return [i for i in self._order if i in self._items]

    def unknown_id_error(self, specimen_id) -> str:
        ids = self.valid_ids()
        if not ids:
            return (
                "[error] Unknown render_specimen_id=%s. No specimens stored in this "
                "session. Call render_specimen first."
                % specimen_id
            )
        return "[error] Unknown render_specimen_id=%s. Valid ids: %s." % (
            specimen_id,
            ", ".join(str(i) for i in ids),
        )

    def clear(self):
        self._next_id = 1
        self._items.clear()
        self._order.clear()
