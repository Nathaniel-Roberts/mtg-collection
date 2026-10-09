"""Card detection and dewarp with CollectorVision's corner detector."""

from __future__ import annotations

from typing import Any

from app.scan.pipeline import ScanContext, square_crop

CROP_SIZE = 448


class DetectStage:
    name = "detect"

    def __init__(self, detector: Any) -> None:
        """``detector`` has ``detect(bgr) -> DetectionResult`` (CollectorVision contract)."""
        self.detector = detector

    def run(self, ctx: ScanContext) -> None:
        if ctx.prewarped:
            ctx.crop = square_crop(ctx.image, CROP_SIZE)
            ctx.detection = {"card_present": True, "source": "client"}
            return
        result = self.detector.detect(ctx.image)
        ctx.detection = {
            "card_present": bool(result.card_present and result.corners is not None),
            "confidence": round(float(result.confidence), 3),
            "sharpness": round(float(result.sharpness), 4)
            if result.sharpness is not None
            else None,
            "corners": result.corners.round(4).tolist() if result.corners is not None else None,
            "source": "detector",
        }
        if ctx.detection["card_present"]:
            ctx.crop = result.dewarp(ctx.image)
        else:
            # Match the whole frame anyway; a tightly framed photo often still works.
            ctx.crop = square_crop(ctx.image, CROP_SIZE)
            ctx.detection["source"] = "fallback_full_frame"
