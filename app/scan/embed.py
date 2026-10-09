"""Embedding and catalog search with CollectorVision's milo embedder."""

from __future__ import annotations

from typing import Any

from app.scan.pipeline import ScanContext


class EmbedStage:
    name = "embed"

    def __init__(self, catalog: Any, top_k: int = 15, both_orientations: bool = True) -> None:
        """``catalog`` has ``embedder.embed(image)`` and ``search(embedding, top_k)``."""
        self.catalog = catalog
        self.top_k = top_k
        self.both_orientations = both_orientations

    def run(self, ctx: ScanContext) -> None:
        if ctx.crop is None:
            return
        images = [ctx.crop]
        if self.both_orientations:
            images.append(ctx.crop.rotate(180))
        embeddings = self.catalog.embedder.embed(images)
        if getattr(embeddings, "ndim", 1) == 1:
            embeddings = [embeddings]
        best: dict[str, float] = {}
        for embedding in embeddings:
            for score, card_id in self.catalog.search(embedding, top_k=self.top_k):
                if best.get(card_id, -1.0) < float(score):
                    best[card_id] = float(score)
        ctx.embedding = embeddings[0]
        ctx.hits = sorted(((s, cid) for cid, s in best.items()), key=lambda h: -h[0])[: self.top_k]
