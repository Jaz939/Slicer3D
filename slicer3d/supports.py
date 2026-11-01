"""Basic supports: a raft on the plate and straight pillars with pointed tips.

The part is lifted off the plate so it is never glued to it. Pillars stand on
a raft (which takes the plate adhesion and pops off with a scraper) and touch
the part with small tips that snap off cleanly.

This milestone-1 version supports every downward-facing surface on a regular
grid. Islands, overhang angles and branching ("tree") supports come later.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass(frozen=True)
class SupportSettings:
    lift_mm: float = 5.0           # gap between raft top and the part's lowest point
    spacing_mm: float = 2.5        # grid spacing of support points
    edge_inset_mm: float = 0.8     # keep tips this far inside the part's outline
    tip_diameter_mm: float = 0.4   # contact point on the part
    tip_length_mm: float = 1.5     # tapered part below the contact
    tip_penetration_mm: float = 0.2
    pillar_diameter_mm: float = 1.2
    base_diameter_mm: float = 2.4  # wider foot on the raft
    base_height_mm: float = 0.6
    raft_thickness_mm: float = 1.0
    raft_margin_mm: float = 2.0
    raft_chamfer_mm: float = 0.6   # sloped raft edge, easier to get a scraper under
    min_downward_normal: float = 0.5  # surfaces facing down steeper than ~60 deg get supports


def frustum(z0: float, z1: float, d0: float, d1: float, x: float, y: float, sections: int = 16) -> trimesh.Trimesh:
    """Closed truncated cone from (x, y, z0) with diameter d0 to z1 with d1."""
    a = np.linspace(0, 2 * np.pi, sections, endpoint=False)
    ring = np.stack([np.cos(a), np.sin(a)], axis=1)
    bottom = np.column_stack([x + ring[:, 0] * d0 / 2, y + ring[:, 1] * d0 / 2, np.full(sections, z0)])
    top = np.column_stack([x + ring[:, 0] * d1 / 2, y + ring[:, 1] * d1 / 2, np.full(sections, z1)])
    verts = np.vstack([bottom, top, [[x, y, z0], [x, y, z1]]])
    cb, ct = 2 * sections, 2 * sections + 1
    faces = []
    for i in range(sections):
        j = (i + 1) % sections
        faces += [[i, j, sections + j], [i, sections + j, sections + i]]  # side
        faces += [[cb, j, i]]  # bottom cap (normal down)
        faces += [[ct, sections + i, sections + j]]  # top cap (normal up)
    return trimesh.Trimesh(verts, np.array(faces), process=False)


def chamfered_slab(x0, y0, x1, y1, thickness, chamfer) -> trimesh.Trimesh:
    """Raft: full size on the plate, shrunk by ``chamfer`` on top."""
    c = min(chamfer, thickness, (x1 - x0) / 4, (y1 - y0) / 4)
    verts = np.array([
        [x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0],
        [x0 + c, y0 + c, thickness], [x1 - c, y0 + c, thickness],
        [x1 - c, y1 - c, thickness], [x0 + c, y1 - c, thickness],
    ])
    faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
             [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
             [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return trimesh.Trimesh(verts, np.array(faces), process=False)


def _lowest_surface(mesh: trimesh.Trimesh, xy: np.ndarray, min_down: float) -> np.ndarray:
    """Lowest z of downward-facing triangles above each xy point (nan if none)."""
    tris = mesh.triangles
    n = mesh.face_normals
    down = n[:, 2] <= -min_down
    tris = tris[down]
    out = np.full(len(xy), np.nan)
    if len(tris) == 0:
        return out
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    v0 = (b - a)[:, :2]
    v1 = (c - a)[:, :2]
    den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
    ok = np.abs(den) > 1e-12
    a, b, c, v0, v1, den = a[ok], b[ok], c[ok], v0[ok], v1[ok], den[ok]
    for k, p in enumerate(xy):
        v2 = p - a[:, :2]
        u = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
        v = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
        inside = (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
        if inside.any():
            z = a[inside, 2] + u[inside] * (b[inside, 2] - a[inside, 2]) + v[inside] * (c[inside, 2] - a[inside, 2])
            out[k] = z.min()
    return out


def _inside_outline(mesh: trimesh.Trimesh, xy: np.ndarray, inset: float, min_down: float) -> np.ndarray:
    """Points whose whole neighbourhood (radius ``inset``) lies over the part."""
    keep = ~np.isnan(_lowest_surface(mesh, xy, min_down))
    for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        shifted = xy + inset * np.array([np.cos(ang), np.sin(ang)])
        keep &= ~np.isnan(_lowest_surface(mesh, shifted, min_down))
    return keep


def support_points(mesh: trimesh.Trimesh, s: SupportSettings) -> np.ndarray:
    """(N, 3) contact points on the underside of ``mesh``."""
    lo, hi = mesh.bounds
    xs = np.arange(lo[0], hi[0] + 1e-9, s.spacing_mm)
    ys = np.arange(lo[1], hi[1] + 1e-9, s.spacing_mm)
    # Centre the grid on the part.
    xs += (hi[0] - xs[-1] - (xs[0] - lo[0])) / 2
    ys += (hi[1] - ys[-1] - (ys[0] - lo[1])) / 2
    gx, gy = np.meshgrid(xs, ys)
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    keep = _inside_outline(mesh, xy, s.edge_inset_mm, s.min_downward_normal)
    xy = xy[keep]
    z = _lowest_surface(mesh, xy, s.min_downward_normal)
    return np.column_stack([xy, z])


def add_supports(mesh: trimesh.Trimesh, s: SupportSettings = SupportSettings()) -> trimesh.Trimesh:
    """Lift ``mesh``, add a raft and pillars, and return everything as one mesh.

    ``mesh`` should sit on z = 0 (see slicer.place_on_plate); x/y are kept.
    """
    part = mesh.copy()
    part.apply_translation((0, 0, s.raft_thickness_mm + s.lift_mm - part.bounds[0][2]))
    points = support_points(part, s)
    if len(points) == 0:
        raise ValueError("No downward-facing surface found to support.")

    pieces = [part]
    raft_top = s.raft_thickness_mm
    for x, y, z in points:
        tip_top = z + s.tip_penetration_mm
        tip_bottom = max(raft_top + s.base_height_mm, z - s.tip_length_mm)
        pieces.append(frustum(raft_top - 0.1, raft_top + s.base_height_mm, s.base_diameter_mm,
                              s.pillar_diameter_mm, x, y))
        if tip_bottom > raft_top + s.base_height_mm:
            pieces.append(frustum(raft_top + s.base_height_mm - 0.05, tip_bottom + 0.05,
                                  s.pillar_diameter_mm, s.pillar_diameter_mm, x, y))
        pieces.append(frustum(tip_bottom, tip_top, s.pillar_diameter_mm, s.tip_diameter_mm, x, y))

    x0, y0 = points[:, :2].min(axis=0) - s.raft_margin_mm
    x1, y1 = points[:, :2].max(axis=0) + s.raft_margin_mm
    pieces.append(chamfered_slab(x0, y0, x1, y1, s.raft_thickness_mm, s.raft_chamfer_mm))
    combined = trimesh.util.concatenate(pieces)
    combined.metadata["support_count"] = len(points)
    return combined
