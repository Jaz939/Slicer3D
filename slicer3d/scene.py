"""Models on the build plate, with their transforms. No GUI code here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import trimesh

from .printers import PrinterProfile
from .supports import SupportSettings, add_supports


class ImportErrorMesh(ValueError):
    pass


def load_mesh(path: str) -> trimesh.Trimesh:
    """Load STL (or OBJ / 3MF / PLY when trimesh can read them) as one mesh."""
    loaded = trimesh.load(path, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ImportErrorMesh(f"No triangles found in {os.path.basename(path)}")
    loaded.remove_unreferenced_vertices()
    return loaded


@dataclass
class SceneObject:
    name: str
    mesh: trimesh.Trimesh  # as loaded
    x: float = 0.0  # centre of the footprint on the plate, mm
    y: float = 0.0
    rot_x: float = 0.0  # degrees, applied X then Y then Z
    rot_y: float = 0.0
    rot_z: float = 0.0
    scale: float = 1.0  # uniform
    supports: bool = True
    _cache_key: tuple = field(default=(), repr=False)
    _cache: trimesh.Trimesh | None = field(default=None, repr=False)

    def _key(self, settings: SupportSettings) -> tuple:
        return (self.x, self.y, self.rot_x, self.rot_y, self.rot_z, self.scale, self.supports, settings)

    def oriented(self) -> trimesh.Trimesh:
        """Rotated and scaled, sitting on z = 0, footprint centred at (x, y)."""
        m = self.mesh.copy()
        m.apply_scale(self.scale)
        for axis, angle in ((0, self.rot_x), (1, self.rot_y), (2, self.rot_z)):
            if angle:
                direction = np.zeros(3)
                direction[axis] = 1
                m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(angle), direction))
        lo, hi = m.bounds
        m.apply_translation((self.x - (lo[0] + hi[0]) / 2, self.y - (lo[1] + hi[1]) / 2, -lo[2]))
        return m

    def placed(self, settings: SupportSettings = SupportSettings()) -> trimesh.Trimesh:
        """What gets printed: the oriented model, plus raft and supports if on."""
        key = self._key(settings)
        if self._cache is None or key != self._cache_key:
            m = self.oriented()
            self._cache = add_supports(m, settings) if self.supports else m
            self._cache_key = key
        return self._cache

    @property
    def size(self) -> np.ndarray:
        lo, hi = self.oriented().bounds
        return hi - lo

    def lay_flat(self) -> bool:
        """Rotate so the largest flat face of the outer hull rests on the plate.

        Returns False (and changes nothing) when no face is reasonably flat.
        """
        m = self.mesh.convex_hull
        normals, areas = m.face_normals, m.area_faces
        # Group faces by rounded normal and pick the biggest group.
        keys = np.round(normals, 3)
        uniq, inv = np.unique(keys, axis=0, return_inverse=True)
        totals = np.bincount(inv.ravel(), weights=areas)
        best = int(np.argmax(totals))
        if totals[best] < 0.02 * areas.sum():
            return False
        n = uniq[best]
        n = n / np.linalg.norm(n)
        # Rotation taking n to -Z, expressed as XYZ Euler angles in degrees.
        matrix = trimesh.geometry.align_vectors(n, [0, 0, -1])
        ax, ay, az = trimesh.transformations.euler_from_matrix(matrix, "sxyz")
        self.rot_x, self.rot_y, self.rot_z = (round(float(np.degrees(a)), 3) for a in (ax, ay, az))
        return True


@dataclass
class Scene:
    printer: PrinterProfile
    objects: list[SceneObject] = field(default_factory=list)
    support_settings: SupportSettings = field(default_factory=SupportSettings)

    def add_file(self, path: str) -> SceneObject:
        obj = SceneObject(os.path.splitext(os.path.basename(path))[0], load_mesh(path))
        return self.add(obj)

    def add(self, obj: SceneObject) -> SceneObject:
        self.objects.append(obj)
        self.arrange()
        return obj

    def remove(self, obj: SceneObject) -> None:
        self.objects.remove(obj)

    def arrange(self, gap: float = 6.0) -> None:
        """Lay objects out left to right in rows, centred on the plate."""
        if not self.objects:
            return
        width = self.printer.display_width_mm - 4
        rows, row, row_w = [], [], 0.0
        for obj in self.objects:
            obj.x = obj.y = 0.0
            lo, hi = obj.placed(self.support_settings).bounds
            w, h = hi[0] - lo[0], hi[1] - lo[1]
            if row and row_w + gap + w > width:
                rows.append(row)
                row, row_w = [], 0.0
            row.append((obj, w, h))
            row_w += (gap if len(row) > 1 else 0) + w
        rows.append(row)
        heights = [max(h for _, _, h in r) for r in rows]
        total_h = sum(heights) + gap * (len(rows) - 1)
        y = total_h / 2
        for r, rh in zip(rows, heights):
            total_w = sum(w for _, w, _ in r) + gap * (len(r) - 1)
            x = -total_w / 2
            for obj, w, _ in r:
                obj.x = x + w / 2
                obj.y = y - rh / 2
                x += w + gap
            y -= rh + gap

    def build_mesh(self) -> trimesh.Trimesh:
        if not self.objects:
            raise ValueError("The plate is empty.")
        meshes = [o.placed(self.support_settings) for o in self.objects]
        return meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)

    def problems(self) -> list[str]:
        """Plain-language reasons the plate cannot be sliced as it is."""
        p = self.printer
        out = []
        hw, hh = p.display_width_mm / 2, p.display_height_mm / 2
        for o in self.objects:
            lo, hi = o.placed(self.support_settings).bounds
            if lo[0] < -hw or hi[0] > hw or lo[1] < -hh or hi[1] > hh:
                out.append(f"{o.name} is partly off the plate.")
            if hi[2] > p.machine_z_mm:
                out.append(f"{o.name} is {hi[2]:.1f} mm tall; the printer allows {p.machine_z_mm:g} mm.")
        return out
