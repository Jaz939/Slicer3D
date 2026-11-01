"""Simple test shapes for milestone 1.

Every shape is built with its base on z = 0 and centred on x = y = 0, in
millimetres.
"""

from __future__ import annotations

import numpy as np
import trimesh


def cube(size: float = 10.0) -> trimesh.Trimesh:
    return box(size, size, size)


def box(x: float, y: float, z: float) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=(x, y, z))
    mesh.apply_translation((0, 0, z / 2))
    return mesh


def cylinder(diameter: float = 10.0, height: float = 10.0, sections: int = 128) -> trimesh.Trimesh:
    mesh = trimesh.creation.cylinder(radius=diameter / 2, height=height, sections=sections)
    mesh.apply_translation((0, 0, height / 2))
    return mesh


def cone(diameter: float = 10.0, height: float = 10.0, sections: int = 128) -> trimesh.Trimesh:
    return trimesh.creation.cone(radius=diameter / 2, height=height, sections=sections)


def orientation_l(size: float = 20.0, thickness: float = 5.0, height: float = 3.0) -> trimesh.Trimesh:
    """An L shape, seen from above: long arm along +X, short arm along +Y.

    It tells you whether the printer mirrors the image. Looking down on the
    build plate (as the model sits on the slicer screen), the corner is at the
    bottom left and the arms point right and away from you.
    """
    long_arm = box(size, thickness, height)
    long_arm.apply_translation((size / 2, thickness / 2, 0))
    short_arm = box(thickness, size - thickness, height)
    short_arm.apply_translation((thickness / 2, thickness + (size - thickness) / 2, 0))
    mesh = trimesh.util.concatenate([long_arm, short_arm])
    mesh.apply_translation((-size / 2, -size / 2, 0))
    return mesh


SHAPES = {
    "cube": cube,
    "cylinder": cylinder,
    "cone": cone,
    "orientation": orientation_l,
}


def test_plate(size: float = 10.0) -> list[trimesh.Trimesh]:
    """Cube, cylinder, cone and the orientation L spread out on the plate."""
    gap = size * 2.0
    items = [
        (cube(size), (-gap * 1.5, 0)),
        (cylinder(size, size), (-gap * 0.5, 0)),
        (cone(size, size), (gap * 0.5, 0)),
        (orientation_l(size * 2, size / 2, size / 3), (gap * 1.5 + size / 2, 0)),
    ]
    meshes = []
    for mesh, (dx, dy) in items:
        mesh.apply_translation((dx, dy, 0))
        meshes.append(mesh)
    return meshes


def merge(meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    if len(meshes) == 1:
        return meshes[0]
    return trimesh.util.concatenate(meshes)


def bounds_size(mesh: trimesh.Trimesh) -> np.ndarray:
    return mesh.bounds[1] - mesh.bounds[0]
