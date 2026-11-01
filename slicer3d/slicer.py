"""Turn a triangle mesh into layer bitmaps.

Each layer is sampled at the middle of its thickness. Triangles crossing that
height give line segments; every pixel row is then filled using the non-zero
winding rule, so overlapping bodies (for example two touching shapes, or a
model made of several shells) merge instead of cancelling each other out.

Image layout: row 0 is the +Y edge of the plate, column 0 is the -X edge,
and the plate centre is the image centre. A printer profile can mirror either
axis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import trimesh

from .printers import PrinterProfile


class SliceError(ValueError):
    pass


def place_on_plate(mesh: trimesh.Trimesh, center: bool = True) -> trimesh.Trimesh:
    """Copy of the mesh sitting on z = 0, centred on the plate if asked."""
    mesh = mesh.copy()
    lo, hi = mesh.bounds
    dx = -(lo[0] + hi[0]) / 2 if center else 0.0
    dy = -(lo[1] + hi[1]) / 2 if center else 0.0
    mesh.apply_translation((dx, dy, -lo[2]))
    return mesh


@dataclass
class LayerWindow:
    """The part of the plate that can hold model pixels, in image indices."""

    row0: int
    row1: int  # exclusive
    col0: int
    col1: int  # exclusive

    @property
    def shape(self) -> tuple[int, int]:
        return self.row1 - self.row0, self.col1 - self.col0


@dataclass
class Layer:
    index: int
    z_mm: float  # top of the layer, as printers expect
    window: LayerWindow
    pixels: np.ndarray  # uint8, window.shape, 0 or 255
    resolution: tuple[int, int]  # (width, height)

    @property
    def lit_pixels(self) -> int:
        return int(np.count_nonzero(self.pixels))

    def full_image(self) -> np.ndarray:
        width, height = self.resolution
        image = np.zeros((height, width), dtype=np.uint8)
        w = self.window
        image[w.row0:w.row1, w.col0:w.col1] = self.pixels
        return image


class Slicer:
    def __init__(self, mesh: trimesh.Trimesh, printer: PrinterProfile, layer_height_mm: float | None = None):
        self.printer = printer
        self.layer_height = layer_height_mm or printer.exposure.layer_height_mm
        self.mesh = mesh
        self._prepare()

    # -- setup -------------------------------------------------------------
    def _prepare(self) -> None:
        p = self.printer
        tris = np.asarray(self.mesh.triangles, dtype=np.float64)  # (T, 3, 3)
        if len(tris) == 0:
            raise SliceError("The model has no triangles.")
        lo = tris.reshape(-1, 3).min(axis=0)
        hi = tris.reshape(-1, 3).max(axis=0)
        if lo[2] < -1e-6:
            raise SliceError("The model goes below the build plate (z < 0).")
        if hi[2] > p.machine_z_mm:
            raise SliceError(f"The model is {hi[2]:.1f} mm tall; the printer allows {p.machine_z_mm} mm.")
        half_w, half_h = p.display_width_mm / 2, p.display_height_mm / 2
        if lo[0] < -half_w or hi[0] > half_w or lo[1] < -half_h or hi[1] > half_h:
            raise SliceError(
                f"The model ({hi[0]-lo[0]:.1f} x {hi[1]-lo[1]:.1f} mm) does not fit on the "
                f"{p.display_width_mm:.1f} x {p.display_height_mm:.1f} mm plate where it is placed."
            )

        normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        # Drop degenerate triangles.
        keep = np.linalg.norm(normals, axis=1) > 1e-12
        tris, normals = tris[keep], normals[keep]

        # Convert x/y to continuous pixel coordinates now; z stays in mm.
        px = self._to_col(tris[..., 0])
        py = self._to_row(tris[..., 1])
        self._tris = np.stack([px, py, tris[..., 2]], axis=-1)
        # Direction of the slice segment in pixel space: n x z, in mm space,
        # mapped through the axis flips.
        dx = normals[:, 1]
        dy = -normals[:, 0]
        self._seg_dir = np.stack([dx * self._col_sign, dy * self._row_sign], axis=-1)
        self._zmin = self._tris[..., 2].min(axis=1)
        self._zmax = self._tris[..., 2].max(axis=1)
        order = np.argsort(self._zmin)
        self._tris, self._seg_dir = self._tris[order], self._seg_dir[order]
        self._zmin, self._zmax = self._zmin[order], self._zmax[order]

        self.height_mm = float(hi[2])
        self.layer_count = max(1, math.ceil(self.height_mm / self.layer_height - 1e-6))

        cols = self._tris[..., 0]
        rows = self._tris[..., 1]
        self.window = LayerWindow(
            row0=max(0, int(math.floor(rows.min())) - 1),
            row1=min(p.resolution_y, int(math.ceil(rows.max())) + 1),
            col0=max(0, int(math.floor(cols.min())) - 1),
            col1=min(p.resolution_x, int(math.ceil(cols.max())) + 1),
        )

    @property
    def _col_sign(self) -> float:
        return -1.0 if self.printer.mirror_x else 1.0

    @property
    def _row_sign(self) -> float:
        # Image rows grow towards -Y unless mirrored.
        return 1.0 if self.printer.mirror_y else -1.0

    def _to_col(self, x_mm: np.ndarray) -> np.ndarray:
        p = self.printer
        return p.resolution_x / 2 + self._col_sign * x_mm / p.pixel_width_mm

    def _to_row(self, y_mm: np.ndarray) -> np.ndarray:
        p = self.printer
        return p.resolution_y / 2 + self._row_sign * y_mm / p.pixel_height_mm

    # -- slicing -----------------------------------------------------------
    def layers(self) -> Iterator[Layer]:
        for i in range(self.layer_count):
            yield self.slice_layer(i)

    def slice_layer(self, index: int) -> Layer:
        z = (index + 0.5) * self.layer_height
        pixels = self._rasterize(z)
        return Layer(
            index=index,
            z_mm=round((index + 1) * self.layer_height, 6),
            window=self.window,
            pixels=pixels,
            resolution=(self.printer.resolution_x, self.printer.resolution_y),
        )

    def _segments(self, z: float) -> tuple[np.ndarray, np.ndarray]:
        hi = np.searchsorted(self._zmin, z, side="right")
        cand = np.nonzero(self._zmax[:hi] > z)[0]
        if len(cand) == 0:
            return np.empty((0, 2, 2)), np.empty((0, 2))
        t = self._tris[cand]
        above = t[..., 2] > z  # (C, 3)
        pts = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            cross = above[:, a] != above[:, b]
            za, zb = t[:, a, 2], t[:, b, 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                f = np.where(cross, (z - za) / (zb - za), 0.0)
            p = t[:, a, :2] + f[:, None] * (t[:, b, :2] - t[:, a, :2])
            pts.append((cross, p))
        # Each crossing triangle has exactly two crossing edges.
        mask = np.stack([c for c, _ in pts], axis=1)  # (C, 3)
        points = np.stack([p for _, p in pts], axis=1)  # (C, 3, 2)
        good = mask.sum(axis=1) == 2
        mask, points = mask[good], points[good]
        idx = np.argsort(~mask, axis=1, kind="stable")[:, :2]
        seg = np.take_along_axis(points, idx[..., None], axis=1)  # (C, 2, 2)
        return seg, self._seg_dir[cand][good]

    def _rasterize(self, z: float) -> np.ndarray:
        w = self.window
        rows, cols = w.shape
        seg, direction = self._segments(z)
        out = np.zeros((rows, cols), dtype=np.uint8)
        if len(seg) == 0:
            return out
        p0, p1 = seg[:, 0], seg[:, 1]
        # Orient each segment along its outward direction.
        flip = np.einsum("ij,ij->i", p1 - p0, direction) < 0
        p0, p1 = np.where(flip[:, None], p1, p0), np.where(flip[:, None], p0, p1)

        r0, r1 = p0[:, 1] - w.row0, p1[:, 1] - w.row0
        c0, c1 = p0[:, 0] - w.col0, p1[:, 0] - w.col0
        lo = np.minimum(r0, r1)
        hi = np.maximum(r0, r1)
        first = np.ceil(lo - 0.5).astype(np.int64)
        last = np.ceil(hi - 0.5).astype(np.int64)  # exclusive
        first = np.clip(first, 0, rows)
        last = np.clip(last, 0, rows)
        counts = last - first
        valid = counts > 0
        if not valid.any():
            return out
        first, counts = first[valid], counts[valid]
        r0, r1, c0, c1 = r0[valid], r1[valid], c0[valid], c1[valid]
        winding = np.where(r1 > r0, 1, -1).astype(np.int16)

        seg_id = np.repeat(np.arange(len(first)), counts)
        offsets = np.arange(seg_id.size) - np.repeat(np.cumsum(counts) - counts, counts)
        row = first[seg_id] + offsets
        rc = row + 0.5
        t = (rc - r0[seg_id]) / (r1[seg_id] - r0[seg_id])
        xc = c0[seg_id] + t * (c1[seg_id] - c0[seg_id])
        col = np.clip(np.ceil(xc - 0.5).astype(np.int64), 0, cols)

        acc = np.zeros((rows, cols + 1), dtype=np.int16)
        np.add.at(acc, (row, col), winding[seg_id])
        wind = np.cumsum(acc[:, :cols], axis=1)
        out[wind != 0] = 255
        return out


def slice_mesh(mesh: trimesh.Trimesh, printer: PrinterProfile, layer_height_mm: float | None = None) -> Slicer:
    return Slicer(mesh, printer, layer_height_mm)
