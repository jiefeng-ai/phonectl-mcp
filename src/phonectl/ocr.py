"""Text recognition on a screenshot with Apple Vision, in this process (no helper binary).

Lines come back with their box centre and size in the image's pixels, origin top-left.
"""

from __future__ import annotations

import os

import objc
import Vision
from Foundation import NSData

# Recognition languages in priority order, e.g. "en-US" or "ja-JP,en-US".
LANGUAGES = [s.strip() for s in os.environ.get("PHONECTL_OCR_LANGS", "zh-Hans,en-US").split(",") if s.strip()]


def recognize(png: bytes, width: int, height: int) -> list[dict]:
    """One dict per recognised line: {"t": text, "x": cx, "y": cy, "w": w, "h": h} in pixels."""
    with objc.autorelease_pool():
        data = NSData.dataWithBytes_length_(png, len(png))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(LANGUAGES)
        req.setUsesLanguageCorrection_(False)
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError(f"text recognition failed: {err}")
        lines = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            (bx, by), (bw, bh) = obs.boundingBox()
            lines.append({
                "t": cands[0].string(),
                "x": round((bx + bw / 2) * width),
                "y": round((1 - (by + bh / 2)) * height),
                "w": round(bw * width),
                "h": round(bh * height),
            })
        return lines
