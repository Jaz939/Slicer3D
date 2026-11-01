"""Thumbnail images shown on the printer's screen."""

from __future__ import annotations

import numpy as np
from PIL import Image

BACKGROUND = (28, 32, 40)
MODEL_LOW = (70, 110, 160)
MODEL_HIGH = (150, 210, 255)


class HeightMap:
    """Top-down height map collected while slicing, at reduced resolution."""

    def __init__(self, window, pixel_w_mm: float, pixel_h_mm: float, target: int = 600):
        rows, cols = window.shape
        self.step = max(1, int(max(rows, cols) / target))
        self.pixel_w_mm = pixel_w_mm * self.step
        self.pixel_h_mm = pixel_h_mm * self.step
        self.top = np.full((-(-rows // self.step), -(-cols // self.step)), -1, dtype=np.int32)

    def add(self, index: int, pixels: np.ndarray) -> None:
        lit = pixels[::self.step, ::self.step] > 0
        self.top[lit] = index

    def render(self, width: int, height: int) -> np.ndarray:
        top = self.top
        rows, cols = top.shape
        # Real-world aspect ratio of the sampled grid.
        w_mm, h_mm = cols * self.pixel_w_mm, rows * self.pixel_h_mm
        scale = 0.85 * min(width / max(w_mm, 1e-6), height / max(h_mm, 1e-6))
        out_w = max(1, int(round(w_mm * scale)))
        out_h = max(1, int(round(h_mm * scale)))

        max_top = max(1, int(top.max()))
        shade = np.clip(top / max_top, 0, 1)[..., None]
        low = np.array(MODEL_LOW, dtype=np.float32)
        high = np.array(MODEL_HIGH, dtype=np.float32)
        color = (low + (high - low) * shade).astype(np.uint8)
        color[top < 0] = BACKGROUND
        small = Image.fromarray(color).resize((out_w, out_h), Image.NEAREST)

        canvas = Image.new("RGB", (width, height), BACKGROUND)
        canvas.paste(small, ((width - out_w) // 2, (height - out_h) // 2))
        return np.asarray(canvas)
