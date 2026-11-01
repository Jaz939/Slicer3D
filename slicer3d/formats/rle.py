"""Run-length helpers shared by the printer file formats."""

from __future__ import annotations

import numpy as np


def runs(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split a 1-D array into runs of equal values. Returns (values, lengths)."""
    values = np.asarray(values).ravel()
    if values.size == 0:
        return values[:0], np.zeros(0, dtype=np.int64)
    change = np.flatnonzero(values[1:] != values[:-1]) + 1
    starts = np.concatenate(([0], change))
    lengths = np.diff(np.concatenate((starts, [values.size])))
    return values[starts], lengths


def windowed_runs(window_pixels: np.ndarray, window, width: int, height: int,
                  transform=None) -> tuple[np.ndarray, np.ndarray]:
    """Runs of a full plate image that is black outside a window.

    Avoids building the full image: the rows of the window are padded with
    black to full width, and the black areas above and below become single runs.
    """
    rows, cols = window_pixels.shape
    values = window_pixels if transform is None else transform(window_pixels)
    band = np.zeros((rows, width), dtype=values.dtype)
    band[:, window.col0:window.col0 + cols] = values
    v, n = runs(band)
    before = window.row0 * width
    after = (height - window.row0 - rows) * width
    if before:
        if v.size and v[0] == 0:
            n[0] += before
        else:
            v = np.concatenate(([0], v)).astype(values.dtype)
            n = np.concatenate(([before], n))
    if after:
        if v.size and v[-1] == 0:
            n[-1] += after
        else:
            v = np.concatenate((v, [0])).astype(values.dtype)
            n = np.concatenate((n, [after]))
    return v, n


def expand(values: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    return np.repeat(np.asarray(values), np.asarray(lengths))
