"""Collector-line OCR: the bottom-left text that pins the exact printing.

Modern (M15 frame onwards) cards print "0123/0281 R" and "SET • EN" in the bottom-left
corner. Older cards have only a number or nothing, so every field is optional.

The strip is cut from a fresh, aspect-correct warp of the original photo when the
detector found corners; the 448 px square crop the embedder uses is too small for text
this size (about 6 px tall), and upscaling it only enlarges the blur.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import numpy as np

from app.scan.pipeline import ScanContext

# Card aspect 63:88. The warp used for OCR.
WARP_W, WARP_H = 744, 1040
# Fractions of the warped card: the bottom 11 percent, full width. M15 frames put the
# collector line bottom-left; 2003 to 2014 frames put the number at the end of the
# copyright line on the right.
STRIP = (0.89, 1.0, 0.0, 1.0)  # top, bottom, left, right
UPSCALE = 2

_NUMBER = re.compile(r"(?<![A-Z0-9])(\d{1,4}[A-Za-z★†]?)\s*[/1lLI|]\s*(\d{2,4})")
# A bare number, possibly with the rarity letter glued on either side ("U0410", "0410U").
_NUMBER_ALONE = re.compile(r"(?<![A-Z0-9/])[CURMLST]?(\d{3,4})[CURMLST]?(?![0-9/])")
# Copyright noise: years and year ranges ("1993-2009", "© 2023"), trademark marks.
_NOISE = re.compile(
    r"\d{4}\s*[-\u2013]\s*\d{4}"  # year ranges such as 1993-2009 (OCR often misreads the digits)
    r"|(?<!\d)(?:19|20)\d{2}(?!\d)"  # a lone year
    r"|[\u00a9\u00ae\u2122&]"
)
_LANG = {"EN", "ES", "FR", "DE", "IT", "PT", "JA", "JP", "KO", "KR", "RU", "ZHS", "ZHT", "CS", "CT"}
_TOKEN = re.compile(r"[A-Z0-9]{2,6}")


def parse_collector_line(text: str, known_sets: set[str] | None = None) -> dict[str, Any]:
    """Pull collector number, set code and language out of OCR text."""
    upper = _NOISE.sub(" ", text.upper().replace("O/", "0/").replace("/O", "/0"))
    out: dict[str, Any] = {
        "collector_number": None,
        "set_total": None,
        "set_code": None,
        "language": None,
    }
    m = _NUMBER.search(upper)
    if m:
        out["collector_number"] = m.group(1).lstrip("0") or "0"
        out["set_total"] = int(m.group(2))
        if out["collector_number"] and out["collector_number"][-1].isalpha():
            out["collector_number"] = (
                out["collector_number"][:-1] + out["collector_number"][-1].lower()
            )
    else:
        m2 = _NUMBER_ALONE.search(upper)
        if m2:
            out["collector_number"] = m2.group(1).lstrip("0") or "0"
    tokens = _TOKEN.findall(upper)
    for token in tokens:
        if token in _LANG and out["language"] is None:
            out["language"] = {"JP": "JA", "KR": "KO", "CS": "ZHS", "CT": "ZHT"}.get(
                token, token
            ).lower()
    candidates = [t for t in tokens if t not in _LANG and not t.isdigit() and 3 <= len(t) <= 5]
    if known_sets:
        for token in candidates:
            if token.lower() in known_sets:
                out["set_code"] = token.lower()
                break
    elif candidates:
        out["set_code"] = candidates[0].lower()
    return out


def warp_card(
    image_bgr: np.ndarray, corners: np.ndarray, width: int = WARP_W, height: int = WARP_H
) -> np.ndarray:
    """Aspect-correct perspective warp from normalised corners (CollectorVision order)."""
    import cv2

    h, w = image_bgr.shape[:2]
    src = np.asarray(corners, dtype=np.float32) * np.array([w, h], dtype=np.float32)
    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(image_bgr, matrix, (width, height))


class OCRStage:
    name = "ocr"

    def __init__(self, engine: Any, known_sets: Callable[[], set[str]] | None = None) -> None:
        """``engine(image_ndarray, use_cls=False)`` returns ``.txts`` and ``.scores`` (RapidOCR)."""
        self.engine = engine
        self.known_sets = known_sets

    @staticmethod
    def strip(card_rgb: np.ndarray, upscale: int = UPSCALE) -> np.ndarray:
        """Bottom-left strip of a warped card (RGB), enlarged and contrast-normalised."""
        import cv2

        h, w = card_rgb.shape[:2]
        top, bottom, left, right = STRIP
        region = card_rgb[int(h * top) : int(h * bottom), int(w * left) : int(w * right)]
        if upscale > 1:
            region = cv2.resize(region, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        grey = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
        if grey.mean() < 128:  # light text on a dark footer
            grey = 255 - grey
        if int(grey.max()) - int(grey.min()) > 16:  # leave flat (blank) strips alone
            grey = cv2.normalize(grey, None, 0, 255, cv2.NORM_MINMAX)
        return cv2.cvtColor(grey, cv2.COLOR_GRAY2RGB)

    def source(self, ctx: ScanContext) -> np.ndarray | None:
        import cv2

        corners = ctx.detection.get("corners")
        if corners is not None and not ctx.prewarped:
            return cv2.cvtColor(warp_card(ctx.image, np.asarray(corners)), cv2.COLOR_BGR2RGB)
        if ctx.crop is not None:
            card = np.asarray(ctx.crop)
            # The square crop is squashed; stretch it back to card proportions first.
            return cv2.resize(card, (WARP_W, WARP_H), interpolation=cv2.INTER_CUBIC)
        return None

    def run(self, ctx: ScanContext) -> None:
        card = self.source(ctx)
        if card is None:
            return
        strip = self.strip(card)
        result = self.engine(strip, use_cls=False)
        texts = list(getattr(result, "txts", None) or [])
        scores = list(getattr(result, "scores", None) or [])
        raw = " ".join(texts)
        parsed = parse_collector_line(raw, self.known_sets() if self.known_sets else None)
        ctx.ocr = {
            **parsed,
            "raw": raw,
            "confidence": round(float(min(scores)), 3) if scores else None,
        }
