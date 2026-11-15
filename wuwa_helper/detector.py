"""Offline, multi-scale matching of the observed gold ready-chest icon."""
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .paths import PROJECT_ROOT


@dataclass(frozen=True)
class Match:
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def center(self):
        return self.x + self.width // 2, self.y + self.height // 2


class Detector:
    def __init__(self, template_path=None, threshold=0.88, color_error=28):
        path = template_path or PROJECT_ROOT / 'assets/chest_ready.png'
        self.template = np.array(Image.open(path).convert('RGB'))
        self.threshold = threshold
        self.color_error = color_error
        self.cache = {}

    def find(self, image):
        rgb = np.asarray(image.convert('RGB') if isinstance(image, Image.Image) else image)
        ratio = min(1.0, 1280 / rgb.shape[1])
        small = cv2.resize(rgb, None, fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        candidates = []
        # Physical scale relative to the reference, including common Windows DPIs.
        scales = sorted(set([round(v, 2) for v in np.arange(.5, 3.01, .1)] + [1.25, 1.5, 1.75, 2.25]))
        for scale in scales:
            w = max(10, round(self.template.shape[1] * scale * ratio))
            h = max(8, round(self.template.shape[0] * scale * ratio))
            if w > gray.shape[1] or h > gray.shape[0]:
                continue
            if (w, h) not in self.cache:
                color = cv2.resize(self.template, (w, h), interpolation=cv2.INTER_AREA)
                self.cache[w, h] = color, cv2.cvtColor(color, cv2.COLOR_RGB2GRAY)
            color, template = self.cache[w, h]
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            for _ in range(8):
                _, score, _, (x, y) = cv2.minMaxLoc(result)
                if score < self.threshold:
                    break
                patch = small[y:y+h, x:x+w]
                # Chests require their ready-state color; stable UI anchors may
                # opt out when the application changes an icon's background.
                error = np.abs(patch.astype(np.float32) - color).mean()
                if self.color_error is None or error <= self.color_error:
                    candidates.append(Match(round(x / ratio), round(y / ratio),
                                            round(w / ratio), round(h / ratio), score))
                result[max(0, y-h//2):y+h//2+1, max(0, x-w//2):x+w//2+1] = -1
        kept = []
        for match in sorted(candidates, key=lambda m: m.score, reverse=True):
            if all(abs(match.center[0]-other.center[0]) > max(match.width, other.width)//2
                   or abs(match.center[1]-other.center[1]) > max(match.height, other.height)//2
                   for other in kept):
                kept.append(match)
        return kept
