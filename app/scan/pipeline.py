"""The identification pipeline: a chain of stages over a shared context.

See ARCHITECTURE.md section 4. Stages are pluggable so tests can swap the models for
fakes and so the perceptual-hash fallback can be slotted in later.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    card_id: str
    score: float
    reason: str
    oracle_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"card_id": self.card_id, "score": round(self.score, 4), "reason": self.reason}


@dataclass
class ScanContext:
    image: np.ndarray  # BGR, as OpenCV loads it
    prewarped: bool = False
    hint_set: str | None = None
    crop: Image.Image | None = None  # RGB 448x448
    detection: dict[str, Any] = field(default_factory=dict)
    embedding: np.ndarray | None = None
    hits: list[tuple[float, str]] = field(default_factory=list)  # (score, card_id), best first
    ocr: dict[str, Any] = field(default_factory=dict)
    match: Candidate | None = None
    confident: bool = False
    method: str = "none"
    candidates: list[Candidate] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    versions: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "detection": self.detection,
            "ocr": self.ocr,
            "match": self.match.as_dict() if self.match else None,
            "confident": self.confident,
            "method": self.method,
            "candidates": [c.as_dict() for c in self.candidates],
            "hits": [{"card_id": cid, "score": round(s, 4)} for s, cid in self.hits[:15]],
            "timings_ms": {k: round(v, 1) for k, v in self.timings_ms.items()},
            "errors": self.errors,
            "versions": self.versions,
        }


class Stage(Protocol):
    name: str

    def run(self, ctx: ScanContext) -> None: ...


class Identifier:
    def __init__(self, stages: Sequence[Stage], versions: dict[str, Any] | None = None) -> None:
        self.stages = list(stages)
        self.versions = versions or {}

    def identify(self, ctx: ScanContext) -> ScanContext:
        ctx.versions = dict(self.versions)
        total = time.perf_counter()
        for stage in self.stages:
            started = time.perf_counter()
            try:
                stage.run(ctx)
            except Exception as exc:  # a failed stage must not lose the scan
                log.exception("Scan stage %s failed", stage.name)
                ctx.errors.append(f"{stage.name}: {exc}")
            ctx.timings_ms[stage.name] = (time.perf_counter() - started) * 1000
        ctx.timings_ms["total"] = (time.perf_counter() - total) * 1000
        return ctx


def decode_image(data: bytes) -> np.ndarray:
    """Bytes (JPEG or PNG) to a BGR array, honouring EXIF orientation."""
    import io

    import cv2
    from PIL import ImageOps

    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        rgb = np.asarray(im.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def square_crop(image: np.ndarray, size: int = 448) -> Image.Image:
    """Letterbox-free fallback: resize the whole frame to the crop size."""
    import cv2

    resized = cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)
    return Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
